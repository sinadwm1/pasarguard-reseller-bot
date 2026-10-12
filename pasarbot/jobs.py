from __future__ import annotations

import asyncio
import logging

from aiogram import Bot

from pasarbot.bot.copy import bot_copy
from pasarbot.config import Settings
from pasarbot.services.runtime_provisioning import RuntimeProvisioner
from pasarbot.services.sync import ResellerSyncService

logger = logging.getLogger(__name__)


class BackgroundJobs:
    def __init__(
        self,
        bot: Bot,
        settings: Settings,
        provisioner: RuntimeProvisioner,
        sync_service: ResellerSyncService,
    ) -> None:
        self.bot = bot
        self.settings = settings
        self.provisioner = provisioner
        self.sync_service = sync_service
        self._tasks: list[asyncio.Task[None]] = []
        self._stopping = asyncio.Event()

    def start(self) -> None:
        self._tasks = [
            asyncio.create_task(self._provision_loop(), name="provision-loop"),
            asyncio.create_task(self._sync_loop(), name="sync-loop"),
        ]

    async def stop(self) -> None:
        self._stopping.set()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def _provision_loop(self) -> None:
        while not self._stopping.is_set():
            try:
                delivery = await self.provisioner.process_next()
                if delivery is None:
                    await asyncio.sleep(3)
                    continue
                if delivery.reseller_id == 0:
                    await self.bot.send_message(
                        delivery.telegram_id,
                        f"✅ شارژ کیف پول سفارش #{delivery.order_id} ثبت و اعمال شد.",
                    )
                    continue
                async with self.provisioner.database.session() as session:
                    delivery_text = await bot_copy(session, "panel_delivery",
                        "✅ نمایندگی سفارش #{order_id} آماده شد.\n\n"
                        "آدرس: {panel_url}\nUsername: <code>{panel_username}</code>\n"
                        "Password: <code>{panel_password}</code>\n\nرمز را در محل امن نگه دارید.",
                        order_id=delivery.order_id, panel_url=delivery.panel_url,
                        panel_username=delivery.username, panel_password=delivery.password or "—")
                await self.bot.send_message(
                    delivery.telegram_id,
                    delivery_text,
                    parse_mode="HTML",
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("provision loop failed")
                await asyncio.sleep(10)

    async def _sync_loop(self) -> None:
        while not self._stopping.is_set():
            try:
                await self.sync_service.sync_all()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("sync loop failed")
            await asyncio.sleep(self.settings.sync_interval_seconds)
