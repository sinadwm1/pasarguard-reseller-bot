from __future__ import annotations

import logging
from datetime import UTC, datetime

from aiogram import Bot
from sqlalchemy import select

from pasarbot.config import Settings
from pasarbot.crypto import SecretBox
from pasarbot.db.models import Customer, PasarGuardInstance, PlanModel, ResellerModel
from pasarbot.db.session import Database
from pasarbot.domain.enums import ResellerStatus
from pasarbot.domain.quota import format_bytes
from pasarbot.integrations.pasarguard import (
    HttpxTransport,
    PasarGuardClient,
    PasarGuardError,
    PasarGuardLimitMismatch,
    PasarGuardRoutes,
)
from pasarbot.services.runtime_provisioning import RuntimeProvisioner

logger = logging.getLogger(__name__)


class ResellerSyncService:
    def __init__(
        self,
        database: Database,
        secret_box: SecretBox,
        settings: Settings,
        bot: Bot,
    ) -> None:
        self.database = database
        self.secret_box = secret_box
        self.settings = settings
        self.bot = bot

    async def sync_all(self) -> None:
        async with self.database.session() as session:
            reseller_ids = (
                await session.scalars(
                    select(ResellerModel.id).where(
                        ResellerModel.status.in_([
                            ResellerStatus.ACTIVE.value,
                            ResellerStatus.SUSPENDED.value,
                        ])
                    )
                )
            ).all()
        for reseller_id in reseller_ids:
            await self.sync_one(reseller_id)

    async def sync_one(self, reseller_id: int) -> None:
        async with self.database.session() as session:
            reseller = await session.get(ResellerModel, reseller_id)
            if reseller is None or not reseller.pasarguard_admin_id:
                return
            instance = await session.get(PasarGuardInstance, reseller.pasarguard_instance_id)
            customer = await session.get(Customer, reseller.customer_id)
            if instance is None:
                reseller.last_sync_error = "instance not found"
                await session.commit()
                return
            client = PasarGuardClient(
                base_url=instance.base_url,
                access_token=self.secret_box.decrypt(instance.encrypted_access_token),
                transport=HttpxTransport(verify_tls=instance.verify_tls),
                routes=PasarGuardRoutes.from_overrides(instance.route_overrides),
            )
            try:
                repaired_limits = False
                if reseller.status == ResellerStatus.ACTIVE.value:
                    plan = await session.get(PlanModel, reseller.plan_id)
                    if plan is None:
                        raise PasarGuardError("Reseller plan is missing")
                    role_id = await RuntimeProvisioner._reseller_role_id(client, reseller)
                    spec = RuntimeProvisioner._role_spec(
                        plan, role_name=RuntimeProvisioner._role_name(reseller.id),
                        groups=reseller.group_ids, templates=reseller.template_ids,
                        quota=reseller.total_quota_bytes, max_users=reseller.max_users,
                    )
                    try:
                        await client.verify_role(role_id, spec)
                        await client.verify_admin_limits(str(reseller.pasarguard_admin_id), role_id,
                                                         reseller.total_quota_bytes)
                    except PasarGuardLimitMismatch:
                        # Fail closed while repairing Role scope/limits and the
                        # native Admin traffic cap. This also upgrades panels
                        # created before these restrictions were applied.
                        await client.suspend_admin(str(reseller.pasarguard_admin_id))
                        reseller.status = ResellerStatus.SUSPENDED.value
                        await client.apply_role(role_id, spec)
                        await client.update_admin(
                            str(reseller.pasarguard_admin_id),
                            {"data_limit": reseller.total_quota_bytes},
                        )
                        await client.verify_role(role_id, spec)
                        await client.verify_admin_limits(
                            str(reseller.pasarguard_admin_id), role_id,
                            reseller.total_quota_bytes,
                        )
                        repaired_limits = True
                snapshot = await client.reseller_snapshot(str(reseller.pasarguard_admin_id))
                reseller.users_count = snapshot.users_count
                reseller.allocated_bytes = snapshot.allocated_bytes
                reseller.actual_used_bytes = snapshot.actual_used_bytes
                reseller.last_synced_at = datetime.now(UTC)
                reseller.last_sync_error = None

                over_volume = (
                    reseller.total_quota_bytes is not None
                    and (reseller.allocated_bytes > reseller.total_quota_bytes
                         or snapshot.unlimited_users > 0)
                )
                over_users = reseller.max_users is not None and reseller.users_count > reseller.max_users
                expired = bool(reseller.expires_at and reseller.expires_at <= datetime.now(UTC))
                if (over_volume or over_users or expired) and self.settings.quota_enforcement_mode == "suspend":
                    await client.suspend_admin(str(reseller.pasarguard_admin_id))
                    reseller.status = (
                        ResellerStatus.EXPIRED.value if expired else ResellerStatus.SUSPENDED.value
                    )
                    if customer:
                        await self.bot.send_message(
                            customer.telegram_id,
                            "⛔ نمایندگی به دلیل پایان اعتبار یا عبور از سقف پلن تعلیق شد.\n"
                            f"اختصاص‌یافته: {format_bytes(reseller.allocated_bytes)}",
                        )
                    await self._alert_owners(
                        f"⚠️ نمایندگی #{reseller.id} به علت عبور از محدودیت تعلیق شد."
                    )
                elif repaired_limits:
                    await client.activate_admin(str(reseller.pasarguard_admin_id))
                    reseller.status = ResellerStatus.ACTIVE.value
                    await self._alert_owners(
                        f"✅ محدودیت‌های نمایندگی #{reseller.id} اصلاح و دوباره اعمال شد."
                    )
                elif reseller.total_quota_bytes:
                    ratio = reseller.allocated_bytes / reseller.total_quota_bytes
                    if ratio >= 0.8 and customer:
                        await self.bot.send_message(
                            customer.telegram_id,
                            f"⚠️ {ratio:.0%} ظرفیت نمایندگی شما تخصیص داده شده است.",
                        )
                await session.commit()
            except Exception as exc:
                reseller.last_sync_error = type(exc).__name__
                await session.commit()
                await self._alert_owners(f"🔴 Sync نمایندگی #{reseller.id} ناموفق بود: {type(exc).__name__}")

    async def _alert_owners(self, text: str) -> None:
        for telegram_id in self.settings.owner_telegram_ids:
            try:
                await self.bot.send_message(telegram_id, text)
            except Exception as exc:
                logger.warning(
                    "owner alert delivery failed for %s: %s",
                    telegram_id,
                    type(exc).__name__,
                )
