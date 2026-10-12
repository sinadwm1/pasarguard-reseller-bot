from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from pasarbot.domain.enums import ROLE_PRESETS, OrderStatus, Permission, ReceiptStatus

from .models import BotAdmin, Customer, Order, Receipt, ResellerModel


class CustomerRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert_telegram_user(
        self, telegram_id: int, username: str | None, first_name: str, last_name: str
    ) -> Customer:
        customer = await self.session.scalar(
            select(Customer).where(Customer.telegram_id == telegram_id)
        )
        if customer is None:
            customer = Customer(
                telegram_id=telegram_id,
                username=username,
                first_name=first_name,
                last_name=last_name,
            )
            self.session.add(customer)
        else:
            customer.username = username
            customer.first_name = first_name
            customer.last_name = last_name
        await self.session.flush()
        return customer


class AdminRepository:
    def __init__(self, session: AsyncSession, owner_ids: list[int]) -> None:
        self.session = session
        self.owner_ids = set(owner_ids)

    async def permissions_for(self, telegram_id: int) -> frozenset[Permission]:
        if telegram_id in self.owner_ids:
            return ROLE_PRESETS["owner"]
        admin = await self.session.scalar(
            select(BotAdmin).where(
                BotAdmin.telegram_id == telegram_id,
                BotAdmin.is_active.is_(True),
            )
        )
        if admin is None:
            return frozenset()
        preset = ROLE_PRESETS.get(admin.role_name, frozenset())
        custom = {Permission(item) for item in (admin.permissions or []) if item in Permission._value2member_map_}
        return frozenset(preset | custom)


class ReceiptRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def decide_once(
        self,
        receipt_id: int,
        decision: ReceiptStatus,
        admin_id: int,
        reason: str | None = None,
    ) -> Receipt | None:
        if decision not in {ReceiptStatus.APPROVED, ReceiptStatus.REJECTED}:
            raise ValueError("receipt decision must be terminal")
        result = await self.session.execute(
            update(Receipt)
            .where(Receipt.id == receipt_id, Receipt.status == ReceiptStatus.PENDING.value)
            .values(
                status=decision.value,
                reviewed_by_admin_id=admin_id,
                reviewed_at=datetime.now(UTC),
                review_reason=reason,
                version=Receipt.version + 1,
            )
            .returning(Receipt)
        )
        receipt = result.scalar_one_or_none()
        if receipt is not None:
            order_status = (
                OrderStatus.PAID.value
                if decision == ReceiptStatus.APPROVED
                else OrderStatus.REJECTED.value
            )
            await self.session.execute(
                update(Order).where(Order.id == receipt.order_id).values(status=order_status)
            )
        return receipt


class ProvisioningRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def claim_order(self, order_id: int) -> Order | None:
        result = await self.session.execute(
            update(Order)
            .where(Order.id == order_id, Order.status == OrderStatus.PAID.value)
            .values(
                status=OrderStatus.PROVISIONING.value,
                provision_attempts=Order.provision_attempts + 1,
            )
            .returning(Order)
        )
        return result.scalar_one_or_none()

    async def lock_reseller(self, reseller_id: int) -> ResellerModel:
        reseller = await self.session.scalar(
            select(ResellerModel).where(ResellerModel.id == reseller_id).with_for_update()
        )
        if reseller is None:
            raise LookupError("reseller not found")
        return reseller
