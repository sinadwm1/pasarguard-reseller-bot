from __future__ import annotations

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.types import BotCommand, Message
from sqlalchemy import select

from pasarbot.bot.button_fallback import ButtonFallback
from pasarbot.bot.handlers import (
    admin_buttons_router,
    admin_router,
    admin_wizard_router,
    customer_router,
    receipt_router,
)
from pasarbot.bot.handlers.admin import admin_entry
from pasarbot.bot.middleware import DatabaseMiddleware
from pasarbot.config import Settings
from pasarbot.crypto import SecretBox
from pasarbot.db.models import BotAdmin, PaymentMethod, RuntimeSetting
from pasarbot.db.session import Database
from pasarbot.jobs import BackgroundJobs
from pasarbot.security import CallbackSigner
from pasarbot.services.runtime_provisioning import RuntimeProvisioner
from pasarbot.services.sync import ResellerSyncService


async def build_app(settings: Settings) -> tuple[Bot, Dispatcher, Database, BackgroundJobs]:
    bot = Bot(
        token=settings.bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode="HTML"),
    )
    bot.session.middleware(ButtonFallback())
    storage = RedisStorage.from_url(settings.redis_url.get_secret_value())
    dispatcher = Dispatcher(storage=storage)
    database = Database(settings)
    secret_box = SecretBox(settings.encryption_key.get_secret_value())
    signer = CallbackSigner(settings.callback_signing_key.get_secret_value().encode())

    await bootstrap(database, settings)
    dispatcher.update.middleware(DatabaseMiddleware(database))
    dispatcher["settings"] = settings
    dispatcher["signer"] = signer
    dispatcher["secret_box"] = secret_box

    dynamic_router = Router(name="dynamic_admin_command")
    if settings.admin_command not in {"/owner", "/admin"}:
        @dynamic_router.message(F.text == settings.admin_command)
        async def dynamic_admin(message: Message, **data: object) -> None:
            await admin_entry(message, data["session"], settings, data["state"])  # type: ignore[arg-type]

    dispatcher.include_router(dynamic_router)
    dispatcher.include_router(admin_router)
    dispatcher.include_router(admin_buttons_router)
    dispatcher.include_router(admin_wizard_router)
    dispatcher.include_router(receipt_router)
    dispatcher.include_router(customer_router)

    provisioner = RuntimeProvisioner(database, secret_box)
    await provisioner.recover_incomplete_orders()
    sync_service = ResellerSyncService(database, secret_box, settings, bot)
    jobs = BackgroundJobs(bot, settings, provisioner, sync_service)
    return bot, dispatcher, database, jobs


async def bootstrap(database: Database, settings: Settings) -> None:
    async with database.session() as session, session.begin():
        for telegram_id in settings.owner_telegram_ids:
            owner = await session.scalar(
                select(BotAdmin).where(BotAdmin.telegram_id == telegram_id)
            )
            if owner is None:
                session.add(
                    BotAdmin(
                        telegram_id=telegram_id,
                        display_name="Owner",
                        role_name="owner",
                        permissions=[],
                    )
                )
            else:
                owner.role_name = "owner"
                owner.is_active = True
        receipt_group = await session.get(RuntimeSetting, "receipt_group_id")
        if receipt_group and receipt_group.value.get("chat_id"):
            settings.receipt_group_id = int(receipt_group.value["chat_id"])
        sync_interval = await session.get(RuntimeSetting, "sync_interval_seconds")
        if sync_interval and sync_interval.value.get("seconds"):
            settings.sync_interval_seconds = int(sync_interval.value["seconds"])
        quota_mode = await session.get(RuntimeSetting, "quota_enforcement_mode")
        if quota_mode and quota_mode.value.get("mode") in {"alert", "suspend"}:
            settings.quota_enforcement_mode = str(quota_mode.value["mode"])

        manual = await session.scalar(select(PaymentMethod).where(PaymentMethod.kind == "manual"))
        if manual is None:
            session.add(
                PaymentMethod(
                    kind="manual",
                    title="💳 کارت‌به‌کارت",
                    description="واریز بانکی و ارسال تصویر رسید",
                    instructions="مبلغ دقیق را به یکی از کارت‌های نمایش‌داده‌شده واریز و تصویر رسید را ارسال کنید.",
                    is_active=True,
                )
            )
        else:
            manual.is_active = True
        wallet = await session.scalar(select(PaymentMethod).where(PaymentMethod.kind == "wallet"))
        if wallet is None:
            session.add(
                PaymentMethod(
                    kind="wallet",
                    title="💰 کیف پول",
                    description="پرداخت فوری از موجودی کیف پول",
                    instructions="مبلغ سفارش از موجودی کیف پول شما کسر می‌شود.",
                    is_active=True,
                )
            )
        else:
            wallet.is_active = True
        unsupported = (
            await session.scalars(
                select(PaymentMethod).where(PaymentMethod.kind.not_in(["manual", "wallet"]))
            )
        ).all()
        for method in unsupported:
            method.is_active = False


async def set_commands(bot: Bot, settings: Settings) -> None:
    admin_command = settings.admin_command.removeprefix("/")
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="منوی اصلی"),
            BotCommand(command=admin_command, description="پنل مدیریت"),
        ]
    )
