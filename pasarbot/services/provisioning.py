from __future__ import annotations

import secrets
import string
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from pasarbot.integrations.pasarguard import AdminSpec, PasarGuardError


class ProvisionState(StrEnum):
    CLAIMED = "claimed"
    COMPLETED = "completed"
    PENDING_RETRY = "pending_retry"
    ALREADY_COMPLETED = "already_completed"
    BUSY = "busy"


@dataclass(frozen=True, slots=True)
class ProvisionRequest:
    order_id: int
    reseller_id: int
    username: str
    role_id: str
    group_ids: tuple[int, ...]
    template_ids: tuple[int, ...]
    dashboard_url: str


@dataclass(frozen=True, slots=True)
class ProvisionResult:
    state: ProvisionState
    admin_id: str | None = None
    username: str | None = None
    password: str | None = None
    dashboard_url: str | None = None
    error: str | None = None


class ProvisionStore(Protocol):
    async def completed_result(self, order_id: int) -> ProvisionResult | None: ...

    async def claim(self, order_id: int) -> bool: ...

    async def complete(
        self, request: ProvisionRequest, admin_id: str, username: str, password: str
    ) -> None: ...

    async def mark_retryable(self, order_id: int, error: str) -> None: ...

    async def mark_failed(self, order_id: int, error: str) -> None: ...


class AdminCreator(Protocol):
    async def create_admin(self, spec: AdminSpec) -> str: ...


class ProvisioningService:
    def __init__(self, store: ProvisionStore, client: AdminCreator) -> None:
        self.store = store
        self.client = client

    async def provision(self, request: ProvisionRequest) -> ProvisionResult:
        existing = await self.store.completed_result(request.order_id)
        if existing is not None:
            return ProvisionResult(
                state=ProvisionState.ALREADY_COMPLETED,
                admin_id=existing.admin_id,
                username=existing.username,
                dashboard_url=existing.dashboard_url,
            )
        if not await self.store.claim(request.order_id):
            return ProvisionResult(state=ProvisionState.BUSY)

        password = self._password()
        try:
            admin_id = await self.client.create_admin(
                AdminSpec(
                    username=request.username,
                    password=password,
                    role_id=request.role_id,
                    group_ids=request.group_ids,
                    template_ids=request.template_ids,
                )
            )
            await self.store.complete(request, admin_id, request.username, password)
            return ProvisionResult(
                state=ProvisionState.COMPLETED,
                admin_id=admin_id,
                username=request.username,
                password=password,
                dashboard_url=request.dashboard_url,
            )
        except PasarGuardError as exc:
            safe_error = f"PasarGuard error ({exc.status_code or 'network'})"
            if exc.retryable:
                await self.store.mark_retryable(request.order_id, safe_error)
                return ProvisionResult(state=ProvisionState.PENDING_RETRY, error=safe_error)
            await self.store.mark_failed(request.order_id, safe_error)
            raise

    @staticmethod
    def _password(length: int = 24) -> str:
        alphabet = string.ascii_letters + string.digits + "-_@#"
        while True:
            value = "".join(secrets.choice(alphabet) for _ in range(length))
            if (
                any(c.islower() for c in value)
                and any(c.isupper() for c in value)
                and any(c.isdigit() for c in value)
                and any(c in "-_@#" for c in value)
            ):
                return value
