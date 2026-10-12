from __future__ import annotations

import unittest
from types import SimpleNamespace

from pasarbot.bot.handlers.customer import addon_eligible
from pasarbot.db.models import Location, PlanModel


class FakeSession:
    async def get(self, model: object, identifier: int) -> SimpleNamespace | None:
        if model is Location:
            return SimpleNamespace(id=identifier, is_active=True, pasarguard_instance_id=2,
                                   group_ids=[7])
        if model is PlanModel:
            return SimpleNamespace(id=identifier, is_active=True, pasarguard_instance_id=2)
        return None


class AddonEligibilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_forbid_cross_instance_and_disabled_options(self) -> None:
        session = FakeSession()
        panel = SimpleNamespace(id=1, status="active", plan_id=3, total_quota_bytes=None,
                                pasarguard_instance_id=1, group_ids=[4])
        plan = SimpleNamespace(can_buy_volume=True, can_renew=True, can_upgrade=True,
                               can_upgrade_location=True)

        def package(kind: str, **kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(kind=kind, is_active=True, quota_bytes=100,
                duration_days=30, location_id=4, from_plan_id=None, to_plan_id=7, **kwargs)

        self.assertFalse(await addon_eligible(session, panel, plan, package("volume")))
        self.assertFalse(await addon_eligible(session, panel, plan, package("plan_upgrade")))
        self.assertFalse(await addon_eligible(session, panel, plan, package("location_upgrade")))
        self.assertTrue(await addon_eligible(session, panel, plan, package("renewal")))
        panel.pasarguard_instance_id = 2
        panel.total_quota_bytes = 1000
        self.assertTrue(await addon_eligible(session, panel, plan, package("volume")))
        self.assertTrue(await addon_eligible(session, panel, plan, package("location_upgrade")))
        plan.can_upgrade_location = False
        self.assertFalse(await addon_eligible(session, panel, plan, package("location_upgrade")))


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(AddonEligibilityTests)
    )
    if result.wasSuccessful():
        print("addon-eligibility-passed")
    raise SystemExit(not result.wasSuccessful())
