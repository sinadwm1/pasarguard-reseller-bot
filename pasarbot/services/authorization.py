from __future__ import annotations

from collections.abc import Iterable

from pasarbot.domain.enums import ROLE_PRESETS, Permission


class AccessDenied(PermissionError):
    pass


def effective_permissions(
    telegram_id: int,
    owner_ids: Iterable[int],
    role_name: str | None,
    custom_permissions: Iterable[str] = (),
    *,
    active: bool = True,
) -> frozenset[Permission]:
    if telegram_id in set(owner_ids):
        return ROLE_PRESETS["owner"]
    if not active or role_name is None:
        return frozenset()
    preset = ROLE_PRESETS.get(role_name, frozenset())
    custom = frozenset(Permission(item) for item in custom_permissions if item in Permission._value2member_map_)
    return preset | custom


def require_permission(permissions: Iterable[Permission], required: Permission) -> None:
    if required not in set(permissions):
        raise AccessDenied(f"missing permission: {required.value}")
