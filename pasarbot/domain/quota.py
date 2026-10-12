from __future__ import annotations

from dataclasses import dataclass

from .models import PlanLimits, ResellerAccount


class QuotaViolation(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class UserAllocationChange:
    previous_quota_bytes: int | None
    new_quota_bytes: int | None
    creates_user: bool = False
    deletes_user: bool = False
    expire_days: int | None = None

    @property
    def quota_delta(self) -> int:
        return (self.new_quota_bytes or 0) - (self.previous_quota_bytes or 0)

    @property
    def user_delta(self) -> int:
        return int(self.creates_user) - int(self.deletes_user)


@dataclass(frozen=True, slots=True)
class QuotaDecision:
    resulting_allocated_bytes: int
    resulting_users_count: int
    remaining_bytes: int | None


class QuotaGuard:
    """Pure quota decision engine. Database callers must lock the reseller row first."""

    @staticmethod
    def validate(
        account: ResellerAccount,
        limits: PlanLimits,
        change: UserAllocationChange,
    ) -> QuotaDecision:
        if account.status != account.status.ACTIVE:
            raise QuotaViolation("RESELLER_INACTIVE", "نمایندگی فعال نیست.")
        if account.expired:
            raise QuotaViolation("RESELLER_EXPIRED", "اعتبار نمایندگی تمام شده است.")
        if change.creates_user and change.deletes_user:
            raise QuotaViolation("INVALID_CHANGE", "ساخت و حذف همزمان معتبر نیست.")

        new_quota = change.new_quota_bytes
        if new_quota is not None and new_quota < 0:
            raise QuotaViolation("NEGATIVE_QUOTA", "حجم کاربر نمی‌تواند منفی باشد.")
        if limits.min_user_quota_bytes is not None and (new_quota or 0) < limits.min_user_quota_bytes:
            raise QuotaViolation("USER_QUOTA_TOO_SMALL", "حجم کاربر کمتر از حداقل پلن است.")
        if (
            limits.max_user_quota_bytes is not None
            and new_quota is not None
            and new_quota > limits.max_user_quota_bytes
        ):
            raise QuotaViolation("USER_QUOTA_TOO_LARGE", "حجم کاربر بیشتر از حداکثر پلن است.")
        if change.expire_days is not None:
            if (
                limits.min_user_expire_days is not None
                and change.expire_days < limits.min_user_expire_days
            ):
                raise QuotaViolation("USER_EXPIRE_TOO_SHORT", "اعتبار کاربر کمتر از حداقل پلن است.")
            if (
                limits.max_user_expire_days is not None
                and change.expire_days > limits.max_user_expire_days
            ):
                raise QuotaViolation("USER_EXPIRE_TOO_LONG", "اعتبار کاربر بیشتر از حداکثر پلن است.")

        resulting_users = account.users_count + change.user_delta
        resulting_allocated = account.allocated_bytes + change.quota_delta
        if resulting_users < 0 or resulting_allocated < 0:
            raise QuotaViolation("NEGATIVE_RESULT", "وضعیت سهمیه ناسازگار است؛ ابتدا Sync انجام دهید.")
        if limits.enforces_users and account.max_users is not None:
            if resulting_users > account.max_users:
                raise QuotaViolation("USER_LIMIT_EXCEEDED", "سقف تعداد کاربران نمایندگی پر شده است.")
        if limits.enforces_volume and account.total_quota_bytes is not None:
            if resulting_allocated > account.total_quota_bytes:
                raise QuotaViolation("VOLUME_LIMIT_EXCEEDED", "حجم تخصیص‌یافته از سهمیه نمایندگی بیشتر می‌شود.")

        remaining = None
        if account.total_quota_bytes is not None:
            remaining = account.total_quota_bytes - resulting_allocated
        return QuotaDecision(resulting_allocated, resulting_users, remaining)


def format_bytes(value: int | None) -> str:
    if value is None:
        return "نامحدود"
    units = ("B", "KB", "MB", "GB", "TB", "PB")
    amount = float(value)
    for unit in units:
        if abs(amount) < 1024 or unit == units[-1]:
            return f"{amount:.2f} {unit}".replace(".00", "")
        amount /= 1024
    raise AssertionError("unreachable")
