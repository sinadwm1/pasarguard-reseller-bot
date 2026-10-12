from __future__ import annotations

import logging
import secrets
import string
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update

from pasarbot.crypto import SecretBox
from pasarbot.db.models import (
    Customer,
    Location,
    Order,
    PasarGuardInstance,
    PlanModel,
    ResellerModel,
    WalletEntry,
)
from pasarbot.db.session import Database
from pasarbot.domain.enums import OrderStatus, ResellerStatus, TransactionType
from pasarbot.integrations.pasarguard import (
    AdminSpec,
    HttpxTransport,
    PasarGuardClient,
    PasarGuardError,
    PasarGuardLimitMismatch,
    PasarGuardRoutes,
    RestrictedRoleSpec,
)
from pasarbot.security import stable_idempotency_key

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Delivery:
    telegram_id: int
    order_id: int
    panel_url: str
    username: str
    password: str | None
    reseller_id: int


class RuntimeProvisioner:
    """Claims paid orders atomically, then applies their side effect exactly once in our database."""

    def __init__(self, database: Database, secret_box: SecretBox) -> None:
        self.database = database
        self.secret_box = secret_box

    async def recover_incomplete_orders(self) -> tuple[int, int]:
        """Recover the v1.5 GET-route regression and abandoned provisioning claims.

        All affected operations are idempotent: initial sales reuse the stable
        reseller username and add-ons write absolute Role/Admin limits.
        """
        stale_before = datetime.now(UTC) - timedelta(minutes=15)
        async with self.database.session() as session, session.begin():
            compatibility = await session.execute(
                update(Order)
                .where(
                    Order.status == OrderStatus.FAILED.value,
                    Order.error_message.ilike("%returned 405%"),
                )
                .values(
                    status=OrderStatus.PAID.value,
                    error_message="Recovered automatically after PasarGuard v5.4 route fix",
                )
            )
            abandoned = await session.execute(
                update(Order)
                .where(
                    Order.status == OrderStatus.PROVISIONING.value,
                    Order.updated_at < stale_before,
                )
                .values(
                    status=OrderStatus.PAID.value,
                    error_message="Recovered abandoned provisioning claim",
                )
            )
            recovered_compatibility = int(compatibility.rowcount or 0)
            recovered_abandoned = int(abandoned.rowcount or 0)
            if recovered_compatibility or recovered_abandoned:
                logger.warning(
                    "requeued provisioning orders: v1.5_compatibility=%s abandoned=%s",
                    recovered_compatibility,
                    recovered_abandoned,
                )
            return recovered_compatibility, recovered_abandoned

    async def process_next(self) -> Delivery | None:
        order_id = await self._claim_next()
        if order_id is None:
            return None
        try:
            return await self._process(order_id)
        except PasarGuardError as exc:
            await self._record_failure(order_id, str(exc), retryable=exc.retryable)
            return None
        except Exception as exc:
            await self._record_failure(order_id, type(exc).__name__, retryable=False)
            raise

    async def _claim_next(self) -> int | None:
        async with self.database.session() as session, session.begin():
            candidate = await session.scalar(
                select(Order.id)
                .where(Order.status == OrderStatus.PAID.value)
                .order_by(Order.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if candidate is None:
                return None
            result = await session.execute(
                update(Order)
                .where(Order.id == candidate, Order.status == OrderStatus.PAID.value)
                .values(
                    status=OrderStatus.PROVISIONING.value,
                    provision_attempts=Order.provision_attempts + 1,
                    error_message=None,
                )
            )
            return candidate if result.rowcount == 1 else None

    async def _process(self, order_id: int) -> Delivery:
        async with self.database.session() as session:
            order = await session.get(Order, order_id)
            if order is None or order.status != OrderStatus.PROVISIONING.value:
                raise LookupError("claimed order disappeared")
            customer = await session.get(Customer, order.customer_id)
            if customer is None:
                raise LookupError("order customer not found")

            if order.order_type == TransactionType.INITIAL_SALE.value:
                return await self._initial_sale(session, order, customer)
            if order.order_type == TransactionType.WALLET_TOPUP.value:
                customer = await session.scalar(select(Customer).where(Customer.id == customer.id).with_for_update())
                if customer is None:
                    raise LookupError("wallet top-up customer not found")
                customer.wallet_balance_irt += order.payable_irt
                session.add(WalletEntry(
                    customer_id=customer.id, amount_irt=order.payable_irt,
                    balance_after_irt=customer.wallet_balance_irt,
                    transaction_type=TransactionType.WALLET_TOPUP.value, order_id=order.id,
                    description=f"شارژ سفارش #{order.id}",
                    idempotency_key=stable_idempotency_key("wallet-topup", order.id),
                ))
                order.status = OrderStatus.COMPLETED.value
                await session.commit()
                return Delivery(customer.telegram_id, order.id, "", "", None, 0)
            if order.reseller_id is None:
                raise ValueError("add-on order has no reseller")
            reseller = await session.get(ResellerModel, order.reseller_id)
            if reseller is None:
                raise LookupError("reseller not found")
            if order.order_type == TransactionType.RENEWAL.value:
                days = int(order.metadata_json.get("duration_days", 0))
                base = max(reseller.expires_at or datetime.now(UTC), datetime.now(UTC))
                reseller.expires_at = base + timedelta(days=days)
                if reseller.status == ResellerStatus.EXPIRED.value:
                    client = await self._client(session, reseller.pasarguard_instance_id)
                    snapshot = await client.reseller_snapshot(str(reseller.pasarguard_admin_id))
                    if (reseller.total_quota_bytes is None or
                        (snapshot.allocated_bytes <= reseller.total_quota_bytes and not snapshot.unlimited_users)) and (
                        reseller.max_users is None or snapshot.users_count <= reseller.max_users
                    ):
                        await client.activate_admin(str(reseller.pasarguard_admin_id))
                        reseller.status = ResellerStatus.ACTIVE.value
            elif order.order_type == TransactionType.EXTRA_VOLUME.value:
                if reseller.total_quota_bytes is None:
                    raise ValueError("volume add-on is not allowed on a user-only plan")
                quota = int(order.metadata_json.get("quota_bytes", 0))
                reseller.total_quota_bytes = (reseller.total_quota_bytes or 0) + quota
                plan = await session.get(PlanModel, reseller.plan_id)
                if plan is None:
                    raise LookupError("reseller plan not found")
                client = await self._client(session, reseller.pasarguard_instance_id)
                role_id = await self._reseller_role_id(client, reseller)
                await client.apply_role(role_id, self._role_spec(plan, role_name=self._role_name(reseller.id),
                    groups=reseller.group_ids, templates=reseller.template_ids,
                    quota=reseller.total_quota_bytes, max_users=reseller.max_users))
                await client.update_admin(str(reseller.pasarguard_admin_id), {"data_limit": reseller.total_quota_bytes})
                await client.verify_admin_limits(str(reseller.pasarguard_admin_id), role_id, reseller.total_quota_bytes)
            elif order.order_type == TransactionType.LOCATION_UPGRADE.value:
                location_id = int(order.metadata_json["location_id"])
                location = await session.get(Location, location_id)
                if location is None or location.pasarguard_instance_id != reseller.pasarguard_instance_id:
                    raise LookupError("location does not belong to this PasarGuard instance")
                reseller.group_ids = sorted(set(reseller.group_ids) | set(location.group_ids))
                client = await self._client(session, reseller.pasarguard_instance_id)
                plan = await session.get(PlanModel, reseller.plan_id)
                if plan is None:
                    raise LookupError("reseller plan not found")
                role_id = await self._reseller_role_id(client, reseller)
                await client.apply_role(role_id, self._role_spec(plan, role_name=self._role_name(reseller.id),
                    groups=reseller.group_ids, templates=reseller.template_ids,
                    quota=reseller.total_quota_bytes, max_users=reseller.max_users))
                await client.update_admin(str(reseller.pasarguard_admin_id), {"data_limit": reseller.total_quota_bytes})
                await client.verify_admin_limits(str(reseller.pasarguard_admin_id), role_id, reseller.total_quota_bytes)
            elif order.order_type == TransactionType.PLAN_UPGRADE.value:
                await self._upgrade_plan(session, reseller, order)
            else:
                raise ValueError(f"unsupported order type: {order.order_type}")

            order.status = OrderStatus.COMPLETED.value
            reseller.version += 1
            await session.commit()
            instance = await session.get(PasarGuardInstance, reseller.pasarguard_instance_id)
            return Delivery(
                telegram_id=customer.telegram_id,
                order_id=order.id,
                panel_url=instance.dashboard_url if instance else "",
                username=reseller.panel_username or "",
                password=None,
                reseller_id=reseller.id,
            )

    async def _initial_sale(self, session: Any, order: Order, customer: Customer) -> Delivery:
        if order.plan_id is None:
            raise ValueError("initial order has no plan")
        plan = await session.scalar(
            select(PlanModel).where(PlanModel.id == order.plan_id).with_for_update()
        )
        if plan is None:
            raise LookupError("plan not found")
        instance = await session.get(PasarGuardInstance, plan.pasarguard_instance_id)
        if instance is None or not instance.is_active:
            raise PasarGuardError("PasarGuard instance is inactive", retryable=True)
        client = self._make_client(instance)

        existing = await session.scalar(
            select(ResellerModel).where(ResellerModel.id == order.reseller_id)
        ) if order.reseller_id else None
        reseller = existing or ResellerModel(
            customer_id=customer.id,
            plan_id=plan.id,
            pasarguard_instance_id=instance.id,
            status=ResellerStatus.PENDING_CREATION.value,
            total_quota_bytes=plan.total_quota_bytes,
            max_users=plan.max_users,
            group_ids=list(plan.group_ids),
            template_ids=list(plan.template_ids),
            expires_at=datetime.now(UTC) + timedelta(days=plan.duration_days),
        )
        if existing is None:
            session.add(reseller)
            await session.flush()
            order.reseller_id = reseller.id
            # Persist the local identity before calling the remote API. If the
            # network or verification step fails, retrying now reuses the same
            # reseller username/Role instead of creating an orphan Admin.
            await session.commit()

        username = reseller.panel_username or f"reseller-{reseller.id}-tg{customer.telegram_id}"[-64:]

        password = self._password()
        admin = await client.find_admin_by_username(username)
        role_name = self._role_name(reseller.id)
        spec = self._role_spec(plan, role_name=role_name, groups=reseller.group_ids,
                               templates=reseller.template_ids, quota=reseller.total_quota_bytes,
                               max_users=reseller.max_users)
        if admin is None:
            role = await client.find_role_by_name(role_name)
            if role:
                role_id = PasarGuardClient._required_id(role, "role")
                await client.apply_role(role_id, spec)
            else:
                role_id = await client.create_role(spec)
                await client.verify_role(role_id, spec)
            admin_id = await client.create_admin(
                AdminSpec(
                    username=username,
                    password=password,
                    role_id=role_id,
                    group_ids=tuple(plan.group_ids),
                    template_ids=tuple(plan.template_ids),
                    enabled=False,
                    data_limit_bytes=reseller.total_quota_bytes,
                )
            )
            await client.verify_admin_limits(admin_id, role_id, reseller.total_quota_bytes)
        else:
            admin_id = PasarGuardClient._required_id(admin, "admin")
            role = admin.get("role")
            if not isinstance(role, dict) or role.get("name") != self._role_name(reseller.id):
                raise PasarGuardError("Existing PasarGuard username belongs to another role")
            role_id = PasarGuardClient._required_id(role, "role")
            await client.suspend_admin(admin_id)
            await client.apply_role(role_id, spec)
            await client.update_admin(admin_id, {"password": password,
                                                 "data_limit": reseller.total_quota_bytes})
            await client.verify_admin_limits(admin_id, role_id, reseller.total_quota_bytes)

        await client.activate_admin(admin_id)
        try:
            await client.verify_role(role_id, spec)
            await client.verify_admin_limits(admin_id, role_id, reseller.total_quota_bytes)
        except Exception:
            await client.suspend_admin(admin_id)
            raise

        reseller.pasarguard_admin_id = admin_id
        reseller.panel_username = username
        reseller.encrypted_panel_password = self.secret_box.encrypt(password)
        reseller.status = ResellerStatus.ACTIVE.value
        order.status = OrderStatus.COMPLETED.value
        await session.commit()
        return Delivery(
            telegram_id=customer.telegram_id,
            order_id=order.id,
            panel_url=instance.dashboard_url,
            username=username,
            password=password,
            reseller_id=reseller.id,
        )

    async def _upgrade_plan(self, session: Any, reseller: ResellerModel, order: Order) -> None:
        target_id = int(order.metadata_json["to_plan_id"])
        plan = await session.get(PlanModel, target_id)
        if plan is None or plan.pasarguard_instance_id != reseller.pasarguard_instance_id:
            raise LookupError("upgrade target must belong to this PasarGuard instance")
        client = await self._client(session, reseller.pasarguard_instance_id)
        role_id = await self._reseller_role_id(client, reseller)
        await client.apply_role(role_id, self._role_spec(plan, role_name=self._role_name(reseller.id),
            groups=plan.group_ids, templates=plan.template_ids,
            quota=plan.total_quota_bytes, max_users=plan.max_users))
        await client.update_admin(str(reseller.pasarguard_admin_id), {"data_limit": plan.total_quota_bytes})
        await client.verify_admin_limits(str(reseller.pasarguard_admin_id), role_id, plan.total_quota_bytes)
        reseller.plan_id = plan.id
        reseller.total_quota_bytes = plan.total_quota_bytes
        reseller.max_users = plan.max_users
        reseller.group_ids = list(plan.group_ids)
        reseller.template_ids = list(plan.template_ids)

    async def _client(self, session: Any, instance_id: int) -> PasarGuardClient:
        instance = await session.get(PasarGuardInstance, instance_id)
        if instance is None:
            raise LookupError("PasarGuard instance not found")
        return self._make_client(instance)

    def _make_client(self, instance: PasarGuardInstance) -> PasarGuardClient:
        return PasarGuardClient(
            base_url=instance.base_url,
            access_token=self.secret_box.decrypt(instance.encrypted_access_token),
            routes=PasarGuardRoutes.from_overrides(instance.route_overrides),
            transport=HttpxTransport(verify_tls=instance.verify_tls),
        )

    @staticmethod
    def _role_name(reseller_id: int) -> str:
        return f"reseller-{reseller_id}"

    @staticmethod
    async def _reseller_role_id(client: PasarGuardClient, reseller: ResellerModel) -> str:
        if not reseller.panel_username:
            raise PasarGuardError("Reseller has no PasarGuard username")
        admin = await client.find_admin_by_username(reseller.panel_username)
        if admin is None or str(admin.get("id")) != str(reseller.pasarguard_admin_id):
            raise PasarGuardError("Reseller admin could not be verified")
        role = admin.get("role")
        if not isinstance(role, dict) or role.get("name") != RuntimeProvisioner._role_name(reseller.id):
            raise PasarGuardLimitMismatch("Reseller does not have an isolated role; manual review required")
        return PasarGuardClient._required_id(role, "role")

    @staticmethod
    def _role_spec(plan: PlanModel, *, role_name: str, groups: list[int],
                   templates: list[int], quota: int | None, max_users: int | None) -> RestrictedRoleSpec:
        return RestrictedRoleSpec(
            name=role_name,
            max_users=max_users,
            min_user_quota_bytes=plan.min_user_quota_bytes,
            max_user_quota_bytes=plan.max_user_quota_bytes,
            min_user_expire_days=plan.min_user_expire_days,
            max_user_expire_days=plan.max_user_expire_days,
            allowed_group_ids=tuple(groups),
            allowed_template_ids=tuple(templates),
            hwid_enabled=plan.hwid_enabled,
            max_devices=plan.max_devices,
            force_template=plan.force_template,
            reset_allowed=plan.reset_allowed,
            total_quota_bytes=quota,
        )

    async def _record_failure(self, order_id: int, error: str, *, retryable: bool) -> None:
        async with self.database.session() as session, session.begin():
            order = await session.get(Order, order_id, with_for_update=True)
            if order is None:
                return
            order.status = OrderStatus.PAID.value if retryable else OrderStatus.FAILED.value
            order.error_message = error[:1000]

    @staticmethod
    def _password(length: int = 24) -> str:
        alphabet = string.ascii_letters + string.digits + "-_@#"
        return "".join(secrets.choice(alphabet) for _ in range(length))
