from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from pasarbot.domain.enums import LimitMode, ResellerStatus
from pasarbot.domain.models import GIB, TIB, PlanLimits, ResellerAccount
from pasarbot.domain.quota import QuotaGuard, QuotaViolation, UserAllocationChange


def account(**changes: object) -> ResellerAccount:
    values: dict[str, object] = {
        "id": 1,
        "customer_id": 1,
        "plan_id": 1,
        "pasarguard_instance_id": 1,
        "status": ResellerStatus.ACTIVE,
        "total_quota_bytes": 2 * TIB,
        "max_users": None,
        "allocated_bytes": 1 * TIB,
        "users_count": 200,
        "expires_at": datetime.now(UTC) + timedelta(days=10),
    }
    values.update(changes)
    return ResellerAccount(**values)  # type: ignore[arg-type]


class PlanAndQuotaTests(unittest.TestCase):
    def test_volume_only_has_no_user_ceiling(self) -> None:
        limits = PlanLimits(LimitMode.VOLUME_ONLY, total_quota_bytes=2 * TIB)
        decision = QuotaGuard.validate(
            account(), limits, UserAllocationChange(None, 10 * GIB, creates_user=True)
        )
        self.assertEqual(decision.resulting_users_count, 201)
        self.assertEqual(decision.remaining_bytes, 1014 * GIB)

    def test_volume_only_rejects_pool_overage(self) -> None:
        limits = PlanLimits(LimitMode.VOLUME_ONLY, total_quota_bytes=2 * TIB)
        with self.assertRaisesRegex(QuotaViolation, "سهمیه"):
            QuotaGuard.validate(
                account(allocated_bytes=2 * TIB - 10 * GIB),
                limits,
                UserAllocationChange(None, 20 * GIB, creates_user=True),
            )

    def test_user_only_ignores_pool_but_enforces_count(self) -> None:
        limits = PlanLimits(LimitMode.USER_ONLY, max_users=2)
        decision = QuotaGuard.validate(
            account(total_quota_bytes=None, allocated_bytes=50 * TIB, max_users=2, users_count=1),
            limits,
            UserAllocationChange(None, 5 * TIB, creates_user=True),
        )
        self.assertIsNone(decision.remaining_bytes)
        with self.assertRaises(QuotaViolation) as context:
            QuotaGuard.validate(
                account(total_quota_bytes=None, max_users=2, users_count=2),
                limits,
                UserAllocationChange(None, GIB, creates_user=True),
            )
        self.assertEqual(context.exception.code, "USER_LIMIT_EXCEEDED")

    def test_combined_mode_enforces_both_limits(self) -> None:
        limits = PlanLimits(
            LimitMode.USER_AND_VOLUME,
            total_quota_bytes=2 * TIB,
            max_users=50,
            min_user_quota_bytes=GIB,
            max_user_quota_bytes=100 * GIB,
        )
        ok = QuotaGuard.validate(
            account(max_users=50, users_count=49, allocated_bytes=1800 * GIB),
            limits,
            UserAllocationChange(None, 100 * GIB, creates_user=True),
        )
        self.assertEqual(ok.resulting_users_count, 50)
        with self.assertRaises(QuotaViolation) as users_error:
            QuotaGuard.validate(
                account(max_users=50, users_count=50),
                limits,
                UserAllocationChange(None, GIB, creates_user=True),
            )
        self.assertEqual(users_error.exception.code, "USER_LIMIT_EXCEEDED")
        with self.assertRaises(QuotaViolation) as volume_error:
            QuotaGuard.validate(
                account(max_users=50, users_count=20, allocated_bytes=2000 * GIB),
                limits,
                UserAllocationChange(None, 100 * GIB, creates_user=True),
            )
        self.assertEqual(volume_error.exception.code, "VOLUME_LIMIT_EXCEEDED")

    def test_update_counts_only_delta(self) -> None:
        limits = PlanLimits(LimitMode.VOLUME_ONLY, total_quota_bytes=2 * TIB)
        result = QuotaGuard.validate(
            account(allocated_bytes=1000 * GIB),
            limits,
            UserAllocationChange(20 * GIB, 100 * GIB),
        )
        self.assertEqual(result.resulting_allocated_bytes, 1080 * GIB)

    def test_invalid_plan_combinations_fail_fast(self) -> None:
        with self.assertRaises(ValueError):
            PlanLimits(LimitMode.VOLUME_ONLY, total_quota_bytes=2 * TIB, max_users=20)
        with self.assertRaises(ValueError):
            PlanLimits(LimitMode.USER_ONLY, max_users=20, total_quota_bytes=2 * TIB)


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(PlanAndQuotaTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if result.wasSuccessful():
        print("quota-and-plan-tests-passed")
    raise SystemExit(not result.wasSuccessful())
