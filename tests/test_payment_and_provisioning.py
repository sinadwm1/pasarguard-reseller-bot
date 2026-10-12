from __future__ import annotations

import asyncio
import unittest

from pasarbot.domain.enums import ReceiptStatus
from pasarbot.domain.payments import (
    InMemoryAtomicReceiptStore,
    ReceiptAlreadyProcessed,
    ReceiptDecisionService,
    ReceiptRecord,
)
from pasarbot.integrations.pasarguard import AdminSpec, PasarGuardError
from pasarbot.services.provisioning import (
    ProvisioningService,
    ProvisionRequest,
    ProvisionResult,
    ProvisionState,
)


class FakeProvisionStore:
    def __init__(self) -> None:
        self.claimed = False
        self.completed: ProvisionResult | None = None
        self.retry_error: str | None = None
        self.failed_error: str | None = None

    async def completed_result(self, order_id: int) -> ProvisionResult | None:
        return self.completed

    async def claim(self, order_id: int) -> bool:
        if self.claimed:
            return False
        self.claimed = True
        return True

    async def complete(
        self, request: ProvisionRequest, admin_id: str, username: str, password: str
    ) -> None:
        self.completed = ProvisionResult(
            ProvisionState.COMPLETED,
            admin_id=admin_id,
            username=username,
            password=password,
            dashboard_url=request.dashboard_url,
        )

    async def mark_retryable(self, order_id: int, error: str) -> None:
        self.retry_error = error

    async def mark_failed(self, order_id: int, error: str) -> None:
        self.failed_error = error


class FakeAdminCreator:
    def __init__(self, error: PasarGuardError | None = None) -> None:
        self.calls = 0
        self.error = error

    async def create_admin(self, spec: AdminSpec) -> str:
        self.calls += 1
        if self.error:
            raise self.error
        return "admin-42"


class PaymentAndProvisioningTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_one_concurrent_receipt_decision_wins(self) -> None:
        store = InMemoryAtomicReceiptStore(ReceiptRecord(id=1, order_id=10))
        service = ReceiptDecisionService(store)

        async def attempt(admin_id: int) -> str:
            try:
                result = await service.decide(1, ReceiptStatus.APPROVED, admin_id)
                return f"won:{result.reviewed_by}"
            except ReceiptAlreadyProcessed:
                return "lost"

        results = await asyncio.gather(*(attempt(i) for i in range(20)))
        self.assertEqual(sum(item.startswith("won:") for item in results), 1)
        self.assertEqual(results.count("lost"), 19)
        self.assertEqual(store.records[1].status, ReceiptStatus.APPROVED)

    async def test_provision_is_idempotent(self) -> None:
        store = FakeProvisionStore()
        creator = FakeAdminCreator()
        service = ProvisioningService(store, creator)
        request = ProvisionRequest(1, 2, "reseller_1", "role-1", (4,), (7,), "https://panel")
        first = await service.provision(request)
        second = await service.provision(request)
        self.assertEqual(first.state, ProvisionState.COMPLETED)
        self.assertEqual(second.state, ProvisionState.ALREADY_COMPLETED)
        self.assertEqual(creator.calls, 1)
        self.assertNotEqual(first.password, "")

    async def test_retryable_failure_remains_recoverable(self) -> None:
        store = FakeProvisionStore()
        creator = FakeAdminCreator(PasarGuardError("offline", status_code=503, retryable=True))
        result = await ProvisioningService(store, creator).provision(
            ProvisionRequest(1, 2, "reseller_1", "role-1", (), (), "https://panel")
        )
        self.assertEqual(result.state, ProvisionState.PENDING_RETRY)
        self.assertIsNotNone(store.retry_error)
        self.assertIsNone(store.failed_error)


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(PaymentAndProvisioningTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if result.wasSuccessful():
        print("payment-and-provisioning-tests-passed")
    raise SystemExit(not result.wasSuccessful())
