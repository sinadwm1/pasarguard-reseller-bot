from __future__ import annotations

import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

from pasarbot.domain.enums import OrderStatus
from pasarbot.integrations.pasarguard import PasarGuardError
from pasarbot.services.runtime_provisioning import RuntimeProvisioner


def plan() -> NS:
    return NS(
        id=5,
        pasarguard_instance_id=3,
        total_quota_bytes=2 * 1024**4,
        max_users=50,
        group_ids=[4],
        template_ids=[],
        duration_days=30,
        min_user_quota_bytes=1024**3,
        max_user_quota_bytes=100 * 1024**3,
        min_user_expire_days=None,
        max_user_expire_days=30,
        hwid_enabled=False,
        max_devices=None,
        force_template=False,
        reset_allowed=False,
    )


class RuntimeProvisioningV16Tests(unittest.IsolatedAsyncioTestCase):
    async def test_local_reseller_identity_is_committed_before_remote_write(self) -> None:
        current_plan = plan()
        instance = NS(id=3, is_active=True, dashboard_url="https://pg.example/dashboard/")
        order = NS(id=9, plan_id=5, reseller_id=None, status=OrderStatus.PROVISIONING.value)
        customer = NS(id=6, telegram_id=12345)
        session = NS(
            scalar=AsyncMock(return_value=current_plan),
            get=AsyncMock(return_value=instance),
            flush=AsyncMock(),
            commit=AsyncMock(),
        )

        def add(reseller: object) -> None:
            reseller.id = 42

        session.add = add
        remote = NS(
            find_admin_by_username=AsyncMock(return_value=None),
            find_role_by_name=AsyncMock(return_value=None),
            create_role=AsyncMock(return_value="19"),
            verify_role=AsyncMock(),
        )

        async def fail_after_asserting_commit(_spec: object) -> str:
            self.assertEqual(session.commit.await_count, 1)
            self.assertEqual(order.reseller_id, 42)
            raise PasarGuardError("network stopped", retryable=True)

        remote.create_admin = AsyncMock(side_effect=fail_after_asserting_commit)
        provisioner = RuntimeProvisioner(NS(), NS(encrypt=lambda value: value))
        provisioner._make_client = lambda _instance: remote
        with self.assertRaises(PasarGuardError):
            await provisioner._initial_sale(session, order, customer)

    async def test_startup_requeues_v15_405_and_stale_claims(self) -> None:
        results = [NS(rowcount=2), NS(rowcount=1)]
        session = NS(execute=AsyncMock(side_effect=results))

        @asynccontextmanager
        async def begin():
            yield

        session.begin = begin

        class Database:
            @asynccontextmanager
            async def session(self):
                yield session

        recovered = await RuntimeProvisioner(Database(), NS()).recover_incomplete_orders()
        self.assertEqual(recovered, (2, 1))
        statements = [str(call.args[0]) for call in session.execute.await_args_list]
        self.assertIn("orders.error_message", statements[0])
        self.assertIn("orders.updated_at", statements[1])


if __name__ == "__main__":
    unittest.main()
