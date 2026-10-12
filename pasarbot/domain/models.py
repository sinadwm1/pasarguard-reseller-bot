from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from .enums import LimitMode, ResellerStatus

GIB = 1024**3
TIB = 1024**4


@dataclass(frozen=True, slots=True)
class PlanLimits:
    mode: LimitMode
    total_quota_bytes: int | None = None
    max_users: int | None = None
    min_user_quota_bytes: int | None = None
    max_user_quota_bytes: int | None = None
    min_user_expire_days: int | None = None
    max_user_expire_days: int | None = None
    max_devices: int | None = None
    hwid_enabled: bool = False

    def __post_init__(self) -> None:
        for field_name in (
            "total_quota_bytes",
            "max_users",
            "min_user_quota_bytes",
            "max_user_quota_bytes",
            "min_user_expire_days",
            "max_user_expire_days",
            "max_devices",
        ):
            value = getattr(self, field_name)
            if value is not None and value < 0:
                raise ValueError(f"{field_name} cannot be negative")

        needs_volume = self.mode in {LimitMode.VOLUME_ONLY, LimitMode.USER_AND_VOLUME}
        needs_users = self.mode in {LimitMode.USER_ONLY, LimitMode.USER_AND_VOLUME}
        if needs_volume and not self.total_quota_bytes:
            raise ValueError("this limit mode requires a positive total quota")
        if needs_users and not self.max_users:
            raise ValueError("this limit mode requires a positive max_users")
        if self.mode == LimitMode.VOLUME_ONLY and self.max_users is not None:
            raise ValueError("volume-only plan must not set max_users")
        if self.mode == LimitMode.USER_ONLY and self.total_quota_bytes is not None:
            raise ValueError("user-only plan must not set total quota")
        if (
            self.min_user_quota_bytes is not None
            and self.max_user_quota_bytes is not None
            and self.min_user_quota_bytes > self.max_user_quota_bytes
        ):
            raise ValueError("minimum user quota cannot exceed maximum")
        if (
            self.min_user_expire_days is not None
            and self.max_user_expire_days is not None
            and self.min_user_expire_days > self.max_user_expire_days
        ):
            raise ValueError("minimum user expiry cannot exceed maximum")

    @property
    def enforces_volume(self) -> bool:
        return self.mode in {LimitMode.VOLUME_ONLY, LimitMode.USER_AND_VOLUME}

    @property
    def enforces_users(self) -> bool:
        return self.mode in {LimitMode.USER_ONLY, LimitMode.USER_AND_VOLUME}


@dataclass(frozen=True, slots=True)
class Plan:
    id: int
    name: str
    price_irt: int
    renewal_price_irt: int
    duration_days: int
    limits: PlanLimits
    pasarguard_instance_id: int
    group_ids: tuple[int, ...] = ()
    template_ids: tuple[int, ...] = ()
    force_template: bool = False
    can_renew: bool = True
    can_upgrade: bool = True
    can_buy_volume: bool = True
    can_upgrade_location: bool = True
    active: bool = True
    visible: bool = True

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("plan name is required")
        if self.price_irt < 0 or self.renewal_price_irt < 0:
            raise ValueError("prices cannot be negative")
        if self.duration_days <= 0:
            raise ValueError("duration_days must be positive")


@dataclass(slots=True)
class ResellerAccount:
    id: int
    customer_id: int
    plan_id: int
    pasarguard_instance_id: int
    pasarguard_admin_id: str | None = None
    status: ResellerStatus = ResellerStatus.PENDING_CREATION
    total_quota_bytes: int | None = None
    max_users: int | None = None
    allocated_bytes: int = 0
    actual_used_bytes: int = 0
    users_count: int = 0
    expires_at: datetime | None = None
    group_ids: set[int] = field(default_factory=set)
    template_ids: set[int] = field(default_factory=set)
    override_fields: set[str] = field(default_factory=set)

    @property
    def remaining_bytes(self) -> int | None:
        if self.total_quota_bytes is None:
            return None
        return max(self.total_quota_bytes - self.allocated_bytes, 0)

    @property
    def expired(self) -> bool:
        return bool(self.expires_at and self.expires_at <= datetime.now(UTC))
