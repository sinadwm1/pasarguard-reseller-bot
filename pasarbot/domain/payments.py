from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Protocol

from .enums import ReceiptStatus


class ReceiptAlreadyProcessed(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ReceiptRecord:
    id: int
    order_id: int
    status: ReceiptStatus = ReceiptStatus.PENDING
    reviewed_by: int | None = None
    reviewed_at: datetime | None = None
    reason: str | None = None
    version: int = 1


class AtomicReceiptStore(Protocol):
    async def decide_if_pending(
        self, receipt_id: int, decision: ReceiptStatus, admin_id: int, reason: str | None
    ) -> ReceiptRecord | None: ...


class ReceiptDecisionService:
    def __init__(self, store: AtomicReceiptStore) -> None:
        self.store = store

    async def decide(
        self,
        receipt_id: int,
        decision: ReceiptStatus,
        admin_id: int,
        reason: str | None = None,
    ) -> ReceiptRecord:
        if decision not in {ReceiptStatus.APPROVED, ReceiptStatus.REJECTED}:
            raise ValueError("decision must be approved or rejected")
        result = await self.store.decide_if_pending(receipt_id, decision, admin_id, reason)
        if result is None:
            raise ReceiptAlreadyProcessed("receipt already processed")
        return result


class InMemoryAtomicReceiptStore:
    """Deterministic concurrency test double with database-like compare-and-set semantics."""

    def __init__(self, *receipts: ReceiptRecord) -> None:
        self.records = {record.id: record for record in receipts}
        self._lock = asyncio.Lock()

    async def decide_if_pending(
        self, receipt_id: int, decision: ReceiptStatus, admin_id: int, reason: str | None
    ) -> ReceiptRecord | None:
        async with self._lock:
            current = self.records.get(receipt_id)
            if current is None or current.status != ReceiptStatus.PENDING:
                return None
            updated = replace(
                current,
                status=decision,
                reviewed_by=admin_id,
                reviewed_at=datetime.now(UTC),
                reason=reason,
                version=current.version + 1,
            )
            self.records[receipt_id] = updated
            return updated
