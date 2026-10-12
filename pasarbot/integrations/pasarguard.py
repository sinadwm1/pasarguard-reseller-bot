from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlencode


class PasarGuardError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, retryable: bool = False) -> None:
        self.status_code = status_code
        self.retryable = retryable
        super().__init__(message)


class PasarGuardLimitMismatch(PasarGuardError):
    """Persisted panel restrictions differ from the paid reseller plan."""


class JsonTransport(Protocol):
    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, Any] | None = None,
        timeout: float = 20,
    ) -> tuple[int, Any]: ...


class HttpxTransport:
    def __init__(self, *, verify_tls: bool = True) -> None:
        self.verify_tls = verify_tls

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, Any] | None = None,
        timeout: float = 20,
    ) -> tuple[int, Any]:
        import httpx

        async with httpx.AsyncClient(verify=self.verify_tls, follow_redirects=False) as client:
            response = await client.request(method, url, headers=headers, json=json, timeout=timeout)
            try:
                payload: Any = response.json()
            except ValueError:
                payload = {"detail": response.text[:1000]}
            return response.status_code, payload


@dataclass(frozen=True, slots=True)
class PasarGuardRoutes:
    health: str = "/api/admin"
    admins: str = "/api/admin"
    admin_list: str = "/api/admins"
    admin_detail: str = "/api/admin/by-id/{admin_id}"
    roles: str = "/api/admin-role"
    role_list: str = "/api/admin-roles"
    role_detail: str = "/api/admin-role/{role_id}"
    users: str = "/api/users"
    current_admin: str = "/api/admin"
    groups: str = "/api/groups"
    templates: str = "/api/user_templates"

    @classmethod
    def from_overrides(cls, overrides: dict[str, str] | None) -> PasarGuardRoutes:
        allowed = set(cls.__dataclass_fields__)
        safe = {key: value for key, value in (overrides or {}).items() if key in allowed}
        return cls(**safe)


@dataclass(frozen=True, slots=True)
class RestrictedRoleSpec:
    name: str
    max_users: int | None
    min_user_quota_bytes: int | None
    max_user_quota_bytes: int | None
    min_user_expire_days: int | None
    max_user_expire_days: int | None
    allowed_group_ids: tuple[int, ...]
    allowed_template_ids: tuple[int, ...]
    hwid_enabled: bool
    max_devices: int | None
    force_template: bool = False
    reset_allowed: bool = False
    total_quota_bytes: int | None = None

    def to_payload(self) -> dict[str, Any]:
        # PasarGuard v5 PermissionScope: NONE=0, OWN=1, ALL=2.
        # Reseller roles must never use ALL here.
        own = {"scope": 1}
        volume_mode = self.total_quota_bytes is not None
        maximum = self.max_user_quota_bytes
        if volume_mode:
            maximum = min(maximum, self.total_quota_bytes) if maximum is not None else self.total_quota_bytes
        return {
            "name": self.name,
            "permissions": {
                "users": {"create": True, "read": own, "read_simple": own,
                          "update": own, "delete": own},
                "groups": {"read": True, "read_simple": True},
                "templates": {"read": True, "read_simple": True},
            },
            "limits": {
                "max_users": self.max_users,
                "data_limit_min": max(self.min_user_quota_bytes or 0, 1) if volume_mode else self.min_user_quota_bytes,
                "data_limit_max": maximum,
                "expire_min": self.min_user_expire_days * 86400 if self.min_user_expire_days is not None else None,
                "expire_max": self.max_user_expire_days * 86400 if self.max_user_expire_days is not None else None,
                "max_hwid_per_user": self.max_devices if self.hwid_enabled else None,
            },
            "features": {"can_use_reset_strategy": self.reset_allowed,
                         "can_use_next_plan": False},
            "access": {"require_template": self.force_template,
                       "allowed_group_ids": list(self.allowed_group_ids),
                       "allowed_template_ids": list(self.allowed_template_ids)},
            "hwid": {"mode": "override", "enabled": self.hwid_enabled, "forced": False,
                     "fallback_limit": self.max_devices if self.hwid_enabled else None},
            "disabled_when_limited": True,
            "disconnect_users_when_limited": True,
            "disconnect_users_when_disabled": True,
        }


@dataclass(frozen=True, slots=True)
class AdminSpec:
    username: str
    password: str
    role_id: str
    group_ids: tuple[int, ...]
    template_ids: tuple[int, ...]
    enabled: bool = True
    data_limit_bytes: int | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "username": self.username,
            "password": self.password,
            "role_id": int(self.role_id),
            "data_limit": self.data_limit_bytes,
            "status": "active" if self.enabled else "disabled",
        }


@dataclass(frozen=True, slots=True)
class UserSnapshot:
    id: str
    username: str
    data_limit_bytes: int
    used_bytes: int
    status: str
    expire_at: str | None = None


@dataclass(frozen=True, slots=True)
class ResellerSnapshot:
    admin_id: str
    users: tuple[UserSnapshot, ...]

    @property
    def users_count(self) -> int:
        return len(self.users)

    @property
    def allocated_bytes(self) -> int:
        return sum(user.data_limit_bytes for user in self.users)

    @property
    def actual_used_bytes(self) -> int:
        return sum(user.used_bytes for user in self.users)

    @property
    def unlimited_users(self) -> int:
        return sum(user.data_limit_bytes == 0 for user in self.users)


@dataclass(slots=True)
class PasarGuardClient:
    base_url: str
    access_token: str
    transport: JsonTransport
    routes: PasarGuardRoutes = field(default_factory=PasarGuardRoutes)
    retry_attempts: int = 3

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")

    @property
    def headers(self) -> dict[str, str]:
        return {
            "X-Api-Key": self.access_token,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "PasarGuard-Reseller-Bot/1.6",
        }

    async def _request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> Any:
        last_error: PasarGuardError | None = None
        for attempt in range(self.retry_attempts):
            try:
                status, result = await self.transport.request(
                    method,
                    f"{self.base_url}{path}",
                    headers=self.headers,
                    json=payload,
                )
            except Exception as exc:
                last_error = PasarGuardError(
                    f"PasarGuard network error during {method} {path}", retryable=True
                )
                if attempt + 1 < self.retry_attempts:
                    await asyncio.sleep(0.25 * (2**attempt))
                    continue
                raise last_error from exc
            if 200 <= status < 300:
                return result
            retryable = status in {408, 429, 500, 502, 503, 504}
            detail = result.get("detail", result) if isinstance(result, dict) else result
            last_error = PasarGuardError(
                f"PasarGuard API {method} {path} returned {status}: {detail}",
                status_code=status,
                retryable=retryable,
            )
            if retryable and attempt + 1 < self.retry_attempts:
                await asyncio.sleep(0.25 * (2**attempt))
                continue
            raise last_error
        raise last_error or PasarGuardError("PasarGuard request failed")

    async def health(self) -> bool:
        try:
            await self._request("GET", self.routes.health)
            return True
        except PasarGuardError:
            return False

    async def preflight(self) -> dict[str, Any]:
        if not self.access_token.startswith("pg_key_"):
            raise PasarGuardError("کلید API معتبر نیست؛ از بخش API Keys پنل، کلید pg_key_ بسازید.")
        admin = await self._request("GET", self.routes.current_admin)
        if not isinstance(admin, dict) or not (admin.get("role") or {}).get("is_owner"):
            raise PasarGuardError("کلید باید متعلق به Owner باشد و دسترسی‌های او را به ارث ببرد.")
        for route in (self.routes.admin_list, self.routes.role_list, self.routes.groups,
                      self.routes.templates, self.routes.users):
            await self._request("GET", f"{route}?limit=1")
        return admin

    async def create_role(self, spec: RestrictedRoleSpec) -> str:
        result = await self._request("POST", self.routes.roles, spec.to_payload())
        return self._required_id(result, "role")

    async def update_role(self, role_id: str, spec: RestrictedRoleSpec) -> None:
        path = self._detail_path(self.routes.role_detail, role_id)
        await self._request("PUT", path, spec.to_payload())

    async def get_role(self, role_id: str) -> dict[str, Any]:
        result = await self._request("GET", self._detail_path(self.routes.role_detail, role_id))
        if not isinstance(result, dict):
            raise PasarGuardError("PasarGuard did not return role details")
        return result

    async def verify_role(self, role_id: str, spec: RestrictedRoleSpec) -> None:
        role = await self.get_role(role_id)
        expected = spec.to_payload()
        limits = role.get("limits")
        if role.get("name") != spec.name or not isinstance(limits, dict):
            raise PasarGuardLimitMismatch("Role limits are absent or role name changed; panel was not activated")
        for key in ("max_users", "data_limit_min", "data_limit_max"):
            if limits.get(key) != expected["limits"][key]:
                raise PasarGuardLimitMismatch(f"PasarGuard role limit {key} was not applied; panel was not activated")
        access = role.get("access")
        if not isinstance(access, dict) or set(access.get("allowed_group_ids") or []) != set(spec.allowed_group_ids):
            raise PasarGuardLimitMismatch("PasarGuard role group access was not applied; panel was not activated")
        permissions = role.get("permissions")
        users = permissions.get("users") if isinstance(permissions, dict) else None
        if not isinstance(users, dict) or users.get("create") is not True or any(
            users.get(action) != {"scope": 1} for action in ("read", "update", "delete")
        ):
            raise PasarGuardLimitMismatch("PasarGuard role own-user permissions were not applied; panel was not activated")
        for key in ("disabled_when_limited", "disconnect_users_when_limited",
                    "disconnect_users_when_disabled"):
            if role.get(key) is not True:
                raise PasarGuardLimitMismatch(
                    f"PasarGuard role safety flag {key} was not applied; panel was not activated"
                )

    async def apply_role(self, role_id: str, spec: RestrictedRoleSpec) -> None:
        await self.update_role(role_id, spec)
        await self.verify_role(role_id, spec)

    async def find_role_by_name(self, name: str) -> dict[str, Any] | None:
        offset = 0
        while True:
            result = await self._request("GET", f"{self.routes.role_list}?{urlencode({'search': name, 'limit': 100, 'offset': offset})}")
            items = self._extract_items(result)
            match = next((item for item in items if item.get("name") == name), None)
            if match:
                return match
            offset += len(items)
            if not items or (isinstance(result, dict) and offset >= result.get("total", offset)):
                return None

    async def create_admin(self, spec: AdminSpec) -> str:
        result = await self._request("POST", self.routes.admins, spec.to_payload())
        return self._required_id(result, "admin")

    async def get_admin(self, admin_id: str) -> dict[str, Any]:
        # PasarGuard v5.4 exposes PUT/DELETE on /api/admin/by-id/{id}, but it
        # does not expose GET on that path. Read one admin through the supported
        # list endpoint instead.
        query = urlencode({"ids": admin_id, "limit": 2, "offset": 0})
        items = self._extract_items(
            await self._request("GET", f"{self.routes.admin_list}?{query}")
        )
        match = next((item for item in items if str(item.get("id")) == str(admin_id)), None)
        if match is None:
            raise PasarGuardError("PasarGuard admin was not found after write")
        return match

    async def verify_admin_limits(self, admin_id: str, role_id: str,
                                  quota_bytes: int | None) -> None:
        admin = await self.get_admin(admin_id)
        role = admin.get("role")
        actual_role = role.get("id") if isinstance(role, dict) else admin.get("role_id")
        if str(actual_role) != str(role_id) or admin.get("data_limit") != quota_bytes:
            raise PasarGuardLimitMismatch("PasarGuard admin quota or restricted role was not applied; panel was not activated")

    async def find_admin_by_username(self, username: str) -> dict[str, Any] | None:
        query = urlencode({"usernames": username, "limit": 2, "offset": 0})
        items = self._extract_items(await self._request("GET", f"{self.routes.admin_list}?{query}"))
        for item in items:
            if str(item.get("username", "")).casefold() == username.casefold():
                return item
        return None

    async def update_admin(self, admin_id: str, changes: dict[str, Any]) -> None:
        path = self._detail_path(self.routes.admin_detail, admin_id)
        allowed = {"password", "status", "role_id", "data_limit", "permission_overrides"}
        if set(changes) - allowed:
            raise PasarGuardError("Unsupported PasarGuard admin update fields")
        await self._request("PUT", path, changes)

    async def suspend_admin(self, admin_id: str) -> None:
        await self.update_admin(admin_id, {"status": "disabled"})

    async def activate_admin(self, admin_id: str) -> None:
        await self.update_admin(admin_id, {"status": "active"})

    async def delete_admin(self, admin_id: str) -> None:
        path = self._detail_path(self.routes.admin_detail, admin_id)
        await self._request("DELETE", path)

    async def list_groups(self) -> list[dict[str, Any]]:
        return await self._all_items(self.routes.groups)

    async def list_templates(self) -> list[dict[str, Any]]:
        return await self._all_items(self.routes.templates)

    async def _all_items(self, route: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        page_size = 200
        while True:
            result = await self._request("GET", f"{route}?{urlencode({'limit': page_size, 'offset': len(items)})}")
            page = self._extract_items(result)
            items.extend(page)
            total = result.get("total") if isinstance(result, dict) else None
            if not page and isinstance(total, int) and len(items) < total:
                raise PasarGuardError("PasarGuard list pagination stopped before total")
            if not page or (isinstance(total, int) and len(items) >= total) or (
                not isinstance(total, int) and len(page) < page_size
            ):
                return items
            if len({str(item.get("id")) for item in items}) != len(items):
                raise PasarGuardError("PasarGuard list pagination repeated a page")

    async def reseller_snapshot(self, admin_id: str) -> ResellerSnapshot:
        offset = 0
        page_size = 200
        users: list[UserSnapshot] = []
        while True:
            query = urlencode({"admin_ids": admin_id, "limit": page_size, "offset": offset})
            result = await self._request("GET", f"{self.routes.users}?{query}")
            items = self._extract_items(result)
            if not items:
                if isinstance(result, dict) and result.get("total", 0) > offset:
                    raise PasarGuardError("PasarGuard user pagination stopped before total")
                break
            for item in items:
                owner = item.get("admin")
                if not isinstance(owner, dict) or str(owner.get("id")) != str(admin_id):
                    raise PasarGuardError("PasarGuard returned a user without the requested admin owner")
                users.append(self._parse_user(item))
            total = result.get("total") if isinstance(result, dict) else None
            if isinstance(total, int) and len(users) >= total:
                break
            if not isinstance(total, int) and len(items) < page_size:
                break
            if len({user.id for user in users}) != len(users):
                raise PasarGuardError("PasarGuard user pagination repeated a page")
            offset += len(items)
        return ResellerSnapshot(admin_id=admin_id, users=tuple(users))

    @staticmethod
    def _required_id(payload: Any, resource: str) -> str:
        if isinstance(payload, dict):
            nested = payload.get(resource)
            candidates = (
                payload.get("id"),
                payload.get(f"{resource}_id"),
                nested.get("id") if isinstance(nested, dict) else None,
            )
            for candidate in candidates:
                if candidate is not None:
                    return str(candidate)
        raise PasarGuardError(f"PasarGuard {resource} response did not include an id")

    @staticmethod
    def _extract_items(payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            for key in ("items", "users", "admins", "roles", "groups", "templates", "user_templates", "results", "data"):
                value = payload.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
        raise PasarGuardError("PasarGuard list response has an unsupported shape")

    @staticmethod
    def _parse_user(item: dict[str, Any]) -> UserSnapshot:
        def integer(*keys: str) -> int:
            for key in keys:
                value = item.get(key)
                if value is not None:
                    return int(value)
            return 0

        identifier = item.get("id", item.get("uuid", item.get("username")))
        if identifier is None or item.get("username") is None:
            raise PasarGuardError("PasarGuard user response is missing id/username")
        return UserSnapshot(
            id=str(identifier),
            username=str(item["username"]),
            data_limit_bytes=integer("data_limit", "data_limit_bytes", "quota"),
            used_bytes=integer("used_traffic", "used_bytes", "usage"),
            status=str(item.get("status", "unknown")),
            expire_at=item.get("expire") or item.get("expire_at"),
        )

    @staticmethod
    def _detail_path(template: str, identifier: str) -> str:
        if not re.search(r"\{[^{}]+\}", template):
            raise PasarGuardError("PasarGuard detail route has no path parameter")
        if not identifier.isdecimal():
            raise PasarGuardError("PasarGuard id must be numeric")
        return re.sub(r"\{[^{}]+\}", identifier, template, count=1)
