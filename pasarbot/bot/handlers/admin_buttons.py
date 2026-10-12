from __future__ import annotations

import html
import logging
import os
import shutil
from datetime import UTC, datetime, timedelta

from aiogram import Bot, F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from pasarbot.bot.admin_schema import ADMIN_REPLY_SECTIONS
from pasarbot.bot.copy import bot_copy
from pasarbot.bot.handlers.customer import money
from pasarbot.bot.help_texts import HELP_TEXTS
from pasarbot.bot.keyboards import CUSTOMER_MENU, customer_menu, inline_keyboard
from pasarbot.bot.presentation import identity, when
from pasarbot.bot.theme import load_button_theme
from pasarbot.config import Settings
from pasarbot.crypto import SecretBox
from pasarbot.db.models import (
    AccountingEntry,
    AddonPackage,
    BotAdmin,
    BotText,
    ButtonConfig,
    ChannelLock,
    Customer,
    Location,
    Order,
    PasarGuardInstance,
    PaymentAccount,
    PaymentMethod,
    PlanModel,
    Receipt,
    ResellerModel,
    RuntimeSetting,
    Ticket,
    TicketMessage,
)
from pasarbot.domain.enums import ROLE_PRESETS, OrderStatus, Permission, ResellerStatus
from pasarbot.domain.quota import format_bytes
from pasarbot.domain.telegram import ButtonSpec
from pasarbot.integrations.pasarguard import (
    HttpxTransport,
    PasarGuardClient,
    PasarGuardError,
    PasarGuardRoutes,
)
from pasarbot.services.runtime_provisioning import RuntimeProvisioner

from .admin import (
    SECTION_PERMISSIONS,
    action_rows,
    admin_permissions,
    render_section,
)

router = Router(name="admin_buttons")
logger = logging.getLogger(__name__)

PERMISSION_TITLES = {
    Permission.DASHBOARD_VIEW: "داشبورد", Permission.CUSTOMER_VIEW: "مشاهده مشتری",
    Permission.CUSTOMER_EDIT: "ویرایش مشتری", Permission.RESELLER_VIEW: "مشاهده نمایندگی",
    Permission.RESELLER_EDIT: "مدیریت نمایندگی", Permission.PLAN_MANAGE: "پلن و لوکیشن",
    Permission.RECEIPT_APPROVE: "تأیید رسید", Permission.RECEIPT_REJECT: "رد رسید",
    Permission.WALLET_MANAGE: "اصلاح کیف پول", Permission.ACCOUNTING_VIEW: "حسابداری",
    Permission.PAYMENT_MANAGE: "تنظیم پرداخت", Permission.BROADCAST_SEND: "ارسال همگانی",
    Permission.TEXT_MANAGE: "متن و دکمه", Permission.CHANNEL_LOCK_MANAGE: "قفل کانال",
    Permission.PASARGUARD_MANAGE: "اتصال PasarGuard", Permission.ADMIN_MANAGE: "مدیریت ادمین",
    Permission.SUPPORT_MANAGE: "پشتیبانی",
}


def permission_rows(admin: BotAdmin) -> list[list[ButtonSpec]]:
    custom = set(admin.permissions or [])
    inherited = ROLE_PRESETS.get(admin.role_name, frozenset())
    return [[ButtonSpec(
        f"{'🔒' if permission in inherited else '✅' if permission.value in custom else '▫️'} "
        f"{PERMISSION_TITLES[permission]}",
        callback_data=f"admin:permission_toggle:{admin.id}:{index}")]
        for index, permission in enumerate(Permission)]


ENTITY_PERMISSIONS: dict[str, Permission] = {
    "plan": Permission.PLAN_MANAGE,
    "addon": Permission.PLAN_MANAGE,
    "location": Permission.PLAN_MANAGE,
    "customer": Permission.CUSTOMER_VIEW,
    "reseller": Permission.RESELLER_VIEW,
    "ticket": Permission.SUPPORT_MANAGE,
    "all_ticket": Permission.SUPPORT_MANAGE,
    "order": Permission.DASHBOARD_VIEW,
    "failed_order": Permission.DASHBOARD_VIEW,
    "receipt": Permission.RECEIPT_APPROVE,
    "reviewed_receipt": Permission.RECEIPT_APPROVE,
    "card_account": Permission.PAYMENT_MANAGE,
    "accounting": Permission.ACCOUNTING_VIEW,
    "expiring_reseller": Permission.RESELLER_VIEW,
    "channel": Permission.CHANNEL_LOCK_MANAGE,
    "admin": Permission.ADMIN_MANAGE,
    "bot_text": Permission.TEXT_MANAGE,
    "button": Permission.TEXT_MANAGE,
    "pasarguard": Permission.PASARGUARD_MANAGE,
}


async def allowed(
    session: AsyncSession,
    settings: Settings,
    telegram_id: int,
    permission: Permission,
) -> bool:
    return permission in await admin_permissions(session, settings, telegram_id)


def management_rows(section: str, extra: list[list[ButtonSpec]]) -> list[list[ButtonSpec]]:
    return action_rows(section) + extra + [
        [ButtonSpec("↩️ بازگشت به بخش", callback_data=f"admin:{section}")]
    ]


@router.message(F.text)
async def reply_section(message: Message, session: AsyncSession, settings: Settings,
                        state: FSMContext) -> None:
    if not message.from_user or not message.text:
        return
    theme = await load_button_theme(session)
    matching = [section for label, section in ADMIN_REPLY_SECTIONS.items()
                if message.text in {label, theme[f"admin.{section}"].text
                                    if f"admin.{section}" in theme else label}]
    if not matching:
        raise SkipHandler()
    section = matching[0]
    permission = SECTION_PERMISSIONS[section]
    customer_labels = {theme[key].text if key in theme else label for key, label, _ in CUSTOMER_MENU}
    if message.text in customer_labels:
        raise SkipHandler()
    if not await allowed(session, settings, message.from_user.id, permission):
        await message.answer("⛔ مجوز این بخش را ندارید.")
        return
    await state.clear()
    text, detail_rows = await render_section(session, section)
    await message.answer(
        text,
        reply_markup=inline_keyboard(
            action_rows(section) + detail_rows,
            modern_features=settings.telegram_button_features,
        ),
    )


@router.message(F.text == "🚪 خروج از مدیریت")
async def exit_admin(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
    settings: Settings,
) -> None:
    await state.clear()
    await message.answer(
        "حالت مدیریت بسته شد. منوی مشتری:",
        reply_markup=customer_menu(
            modern_features=settings.telegram_button_features,
            theme=await load_button_theme(session),
        ),
    )


@router.callback_query(F.data.startswith("admin:list:"))
async def list_entities(
    callback: CallbackQuery,
    session: AsyncSession,
    settings: Settings,
) -> None:
    if not callback.from_user or not callback.message:
        return
    entity = callback.data.split(":", 2)[2]
    permission = ENTITY_PERMISSIONS.get(entity)
    if permission is None or not await allowed(session, settings, callback.from_user.id, permission):
        await callback.answer("مجوز ندارید.", show_alert=True)
        return
    text, rows, section = await entity_list(session, entity)
    await callback.message.edit_text(
        text,
        reply_markup=inline_keyboard(
            management_rows(section, rows),
            modern_features=settings.telegram_button_features,
        ),
    )
    await callback.answer()


async def entity_list(
    session: AsyncSession, entity: str
) -> tuple[str, list[list[ButtonSpec]], str]:
    rows: list[list[ButtonSpec]] = []
    if entity == "plan":
        items = (await session.scalars(select(PlanModel).order_by(PlanModel.id.desc()).limit(50))).all()
        rows = [[ButtonSpec(f"📦 #{x.id} {x.name}", callback_data=f"admin:detail:plan:{x.id}")] for x in items]
        return "📦 <b>فهرست پلن‌ها</b>", rows, "plans"
    if entity == "addon":
        items = (await session.scalars(select(AddonPackage).order_by(AddonPackage.id.desc()).limit(50))).all()
        rows = [[ButtonSpec(f"🧩 #{x.id} {x.name}", callback_data=f"admin:detail:addon:{x.id}")] for x in items]
        return "🧩 <b>بسته‌های حجم، تمدید و ارتقا</b>", rows, "plans"
    if entity == "location":
        items = (await session.scalars(select(Location).order_by(Location.id.desc()).limit(50))).all()
        rows = [[ButtonSpec(f"{x.flag} #{x.id} {x.name}", callback_data=f"admin:detail:location:{x.id}")] for x in items]
        return "🌍 <b>لوکیشن‌ها</b>", rows, "locations"
    if entity == "customer":
        items = (await session.scalars(select(Customer).order_by(Customer.id.desc()).limit(30))).all()
        rows = [[ButtonSpec(f"👤 {x.telegram_id} @{x.username or '-'}", callback_data=f"admin:detail:customer:{x.id}")] for x in items]
        return "👤 <b>آخرین مشتریان</b>", rows, "customers"
    if entity in {"reseller", "expiring_reseller"}:
        query = select(ResellerModel).order_by(ResellerModel.id.desc()).limit(40)
        if entity == "expiring_reseller":
            query = query.where(
                ResellerModel.expires_at.is_not(None),
                ResellerModel.expires_at <= datetime.now(UTC) + timedelta(days=7),
            )
        items = (await session.scalars(query)).all()
        rows = [[ButtonSpec(f"🖥 #{x.id} {x.panel_username or 'در انتظار'}", callback_data=f"admin:detail:reseller:{x.id}")] for x in items]
        return "🖥 <b>نمایندگی‌ها</b>", rows, "resellers"
    if entity in {"ticket", "all_ticket"}:
        query = select(Ticket).order_by(Ticket.id.desc()).limit(50)
        if entity == "ticket":
            query = query.where(Ticket.status.in_(["open", "answered"]))
        items = (await session.scalars(query)).all()
        rows = [[ButtonSpec(f"🎫 #{x.id} {x.subject[:35]} — {x.status}", callback_data=f"admin:detail:ticket:{x.id}")] for x in items]
        return "🎫 <b>تیکت‌های پشتیبانی</b>", rows, "support"
    if entity in {"order", "failed_order"}:
        query = select(Order).order_by(Order.id.desc()).limit(40)
        if entity == "failed_order":
            query = query.where(Order.status == OrderStatus.FAILED.value)
        items = (await session.scalars(query)).all()
        rows = [[ButtonSpec(f"🛒 #{x.id} {x.status} — {money(x.payable_irt)}", callback_data=f"admin:detail:order:{x.id}")] for x in items]
        return "🛒 <b>سفارش‌ها</b>", rows, "orders"
    if entity in {"receipt", "reviewed_receipt"}:
        query = select(Receipt).order_by(Receipt.id.desc()).limit(40)
        query = query.where(
            Receipt.status == "pending" if entity == "receipt" else Receipt.status != "pending"
        )
        items = (await session.scalars(query)).all()
        rows = [[ButtonSpec(f"🧾 #{x.id} {x.status}", callback_data=f"admin:receipt:{x.id}")] for x in items]
        return "🧾 <b>رسیدها</b>", rows, "receipts"
    if entity == "card_account":
        items = (
            await session.scalars(
                select(PaymentAccount)
                .join(PaymentMethod)
                .where(PaymentMethod.kind == "manual")
                .order_by(PaymentAccount.id.desc())
            )
        ).all()
        rows = [[ButtonSpec(f"💳 #{x.id} {x.title}", callback_data=f"admin:detail:card_account:{x.id}")] for x in items]
        return "💳 <b>کارت‌های کارت‌به‌کارت</b>", rows, "payments"
    if entity == "accounting":
        items = (await session.scalars(select(AccountingEntry).order_by(AccountingEntry.id.desc()).limit(40))).all()
        rows = [[ButtonSpec(f"💰 #{x.id} {x.transaction_type} · {money(x.amount_irt)}",
                            callback_data=f"admin:detail:accounting:{x.id}")] for x in items]
        body = f"{len(items)} تراکنش آخر؛ برای جزئیات هر ردیف را انتخاب کنید."
        return f"💰 <b>آخرین تراکنش‌ها</b>\n\n{body or 'موردی ثبت نشده است.'}", rows, "accounting"
    if entity == "channel":
        items = (await session.scalars(select(ChannelLock).order_by(ChannelLock.display_order))).all()
        rows = [[ButtonSpec(f"📣 #{x.id} {x.title}", callback_data=f"admin:detail:channel:{x.id}")] for x in items]
        return "📣 <b>قفل‌های کانال</b>", rows, "channel_locks"
    if entity == "admin":
        items = (await session.scalars(select(BotAdmin).order_by(BotAdmin.id))).all()
        rows = [[ButtonSpec(f"🛡 #{x.id} {x.display_name} ({x.role_name})", callback_data=f"admin:detail:admin:{x.id}")] for x in items]
        return "🛡 <b>ادمین‌های ربات</b>", rows, "admins"
    if entity == "bot_text":
        items = (await session.scalars(select(BotText).order_by(BotText.key))).all()
        body = "\n".join(f"• <code>{html.escape(x.key)}</code>: {html.escape(x.value[:80])}" for x in items)
        rows = [[ButtonSpec(f"✏️ {x.key}", callback_data=f"admin:appearance:edit_text:{x.key}"),
                 ButtonSpec("↩️ پیش‌فرض", callback_data=f"admin:appearance:reset_text:{x.key}")]
                for x in items]
        return f"📝 <b>متن‌های سفارشی</b>\n\n{body or 'هنوز متنی سفارشی نشده است.'}", rows, "texts"
    if entity == "button":
        items = (await session.scalars(select(ButtonConfig).order_by(ButtonConfig.display_order))).all()
        body = "\n".join(f"• <code>{html.escape(x.key)}</code>: {html.escape(x.text)} ({x.style or 'default'})" for x in items)
        rows = [[ButtonSpec(f"✏️ {x.key}", callback_data=f"admin:appearance:edit_button:{x.key}"),
                 ButtonSpec("↩️ پیش‌فرض", callback_data=f"admin:appearance:reset_button:{x.key}")]
                for x in items]
        return f"🎨 <b>دکمه‌های سفارشی</b>\n\n{body or 'هنوز دکمه‌ای سفارشی نشده است.'}", rows, "buttons"
    if entity == "pasarguard":
        items = (await session.scalars(select(PasarGuardInstance).order_by(PasarGuardInstance.id))).all()
        rows = [[ButtonSpec(f"🔌 #{x.id} {x.name}", callback_data=f"admin:detail:pasarguard:{x.id}")] for x in items]
        return "🔌 <b>پنل‌های PasarGuard</b>", rows, "pasarguard"
    return "موردی وجود ندارد.", rows, "dashboard"


@router.callback_query(F.data.startswith("admin:detail:"))
async def entity_detail(
    callback: CallbackQuery,
    session: AsyncSession,
    settings: Settings,
) -> None:
    if not callback.from_user or not callback.message:
        return
    _, _, entity, raw_id = callback.data.split(":", 3)
    permission = ENTITY_PERMISSIONS.get(entity)
    if permission is None or not await allowed(session, settings, callback.from_user.id, permission):
        await callback.answer("مجوز ندارید.", show_alert=True)
        return
    text, rows, section = await detail_view(session, entity, int(raw_id), settings)
    await callback.message.edit_text(
        text,
        reply_markup=inline_keyboard(
            rows + [[ButtonSpec("↩️ بازگشت", callback_data=f"admin:list:{entity}")]],
            modern_features=settings.telegram_button_features,
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:related:"))
async def related_customer_items(callback: CallbackQuery, session: AsyncSession,
                                 settings: Settings) -> None:
    if not callback.from_user or not callback.message:
        return
    _, _, category, raw_id = callback.data.split(":", 3)
    if category not in {"panels", "orders", "tickets"} or not await allowed(
        session, settings, callback.from_user.id, Permission.CUSTOMER_VIEW
    ):
        await callback.answer("مجوز ندارید.", show_alert=True)
        return
    customer_id = int(raw_id)
    model, detail, label, permission = {
        "panels": (ResellerModel, "reseller", "🖥", Permission.RESELLER_VIEW),
        "orders": (Order, "order", "🛒", Permission.DASHBOARD_VIEW),
        "tickets": (Ticket, "ticket", "🎫", Permission.SUPPORT_MANAGE),
    }[category]
    if not await allowed(session, settings, callback.from_user.id, permission):
        await callback.answer("مجوز این فهرست را ندارید.", show_alert=True)
        return
    items = (await session.scalars(select(model).where(model.customer_id == customer_id)
             .order_by(model.id.desc()).limit(30))).all()
    rows = [[ButtonSpec(f"{label} #{item.id} · {getattr(item, 'status', '')}",
                        callback_data=f"admin:detail:{detail}:{item.id}")] for item in items]
    rows.append([ButtonSpec("↩️ مشتری", callback_data=f"admin:detail:customer:{customer_id}")])
    await callback.message.edit_text(f"{label} <b>موارد مشتری #{customer_id}</b>\n"
                                     f"{len(items)} مورد آخر", reply_markup=inline_keyboard(
                                         rows, modern_features=settings.telegram_button_features))
    await callback.answer()


async def detail_view(
    session: AsyncSession,
    entity: str,
    item_id: int,
    settings: Settings,
) -> tuple[str, list[list[ButtonSpec]], str]:
    rows: list[list[ButtonSpec]] = []
    if entity == "plan":
        item = await session.get(PlanModel, item_id)
        if item is None:
            return "پلن پیدا نشد.", rows, "plans"
        rows = [
            [ButtonSpec("✏️ ویرایش همه تنظیمات", callback_data=f"admin:wizard:plan:{item.id}", style="primary")],
            [
                ButtonSpec("⛔ غیرفعال" if item.is_active else "✅ فعال", callback_data=f"admin:toggle:plan_active:{item.id}"),
                ButtonSpec("🙈 مخفی" if item.is_visible else "👁 نمایش", callback_data=f"admin:toggle:plan_visible:{item.id}"),
            ],
        ]
        return (
            f"📦 <b>{html.escape(item.name)}</b>\n\nقیمت: {money(item.price_irt)}\n"
            f"تمدید: {money(item.renewal_price_irt)}\nمدت: {item.duration_days} روز\n"
            f"حالت: {item.limit_mode}\nحجم: {format_bytes(item.total_quota_bytes)}\n"
            f"سقف User: {item.max_users or 'نامحدود'}\nGroupها: {item.group_ids}\n"
            f"فعال: {'بله' if item.is_active else 'خیر'} | نمایش: {'بله' if item.is_visible else 'خیر'}",
            rows,
            "plans",
        )
    if entity == "addon":
        item = await session.get(AddonPackage, item_id)
        if item is None:
            return "بسته پیدا نشد.", rows, "plans"
        rows = [[ButtonSpec("⛔ غیرفعال" if item.is_active else "✅ فعال", callback_data=f"admin:toggle:addon:{item.id}")]]
        return f"🧩 <b>{html.escape(item.name)}</b>\nنوع: {item.kind}\nقیمت: {money(item.amount_irt)}\nفعال: {'بله' if item.is_active else 'خیر'}", rows, "plans"
    if entity == "location":
        item = await session.get(Location, item_id)
        if item is None:
            return "لوکیشن پیدا نشد.", rows, "locations"
        rows = [
            [ButtonSpec("✏️ ویرایش", callback_data=f"admin:wizard:location:{item.id}", style="primary")],
            [ButtonSpec("⛔ غیرفعال" if item.is_active else "✅ فعال", callback_data=f"admin:toggle:location:{item.id}")],
        ]
        return f"{item.flag} <b>{html.escape(item.name)}</b>\nپنل: #{item.pasarguard_instance_id}\nGroupها: {item.group_ids}\nفعال: {'بله' if item.is_active else 'خیر'}", rows, "locations"
    if entity == "customer":
        item = await session.get(Customer, item_id)
        if item is None:
            return "مشتری پیدا نشد.", rows, "customers"
        panels = await session.scalar(select(func.count()).select_from(ResellerModel).where(ResellerModel.customer_id == item.id))
        rows = [
            [
                ButtonSpec("✉️ ارسال پیام", callback_data=f"admin:wizard:customer_message:{item.id}", style="primary"),
                ButtonSpec("💰 اصلاح کیف پول", callback_data=f"admin:wizard:wallet_adjust:{item.id}"),
            ],
            [ButtonSpec("🖥 نمایندگی‌ها", callback_data=f"admin:related:panels:{item.id}"),
             ButtonSpec("🛒 سفارش‌ها", callback_data=f"admin:related:orders:{item.id}")],
            [ButtonSpec("🎫 تیکت‌ها", callback_data=f"admin:related:tickets:{item.id}")],
            [ButtonSpec("✅ رفع مسدودی" if item.is_blocked else "🚫 مسدودکردن", callback_data=f"admin:customer:block:{item.id}", style="danger" if not item.is_blocked else "success")],
        ]
        orders = await session.scalar(select(func.count()).select_from(Order).where(Order.customer_id == item.id))
        return (
            f"👤 <b>مشتری #{item.id}</b>\n{identity(item.telegram_id, item.username, (item.first_name + ' ' + item.last_name).strip())}\n\n"
            f"💰 کیف پول: {money(item.wallet_balance_irt)}\n"
            f"🖥 نمایندگی‌ها: {panels or 0} · 🛒 سفارش‌ها: {orders or 0}\n"
            f"🚫 مسدود: {'بله' if item.is_blocked else 'خیر'}\n"
            f"🕒 عضویت: {when(item.created_at)} · آخرین تغییر: {when(item.updated_at)}",
            rows,
            "customers",
        )
    if entity == "reseller":
        item = await session.get(ResellerModel, item_id)
        if item is None:
            return "نمایندگی پیدا نشد.", rows, "resellers"
        rows = [
            [ButtonSpec("🔄 Sync فوری", callback_data=f"admin:reseller:sync:{item.id}", style="primary")],
            [ButtonSpec("👥 کاربران نمایندگی", callback_data=f"admin:reseller:users:{item.id}"),
             ButtonSpec("🔎 جستجوی User", callback_data=f"admin:wizard:user_search:{item.id}")],
            [ButtonSpec("🛠 بازاعمال Role و سقف‌ها", callback_data=f"admin:reseller:role_sync:{item.id}")],
            [ButtonSpec("🔑 بازنشانی رمز", callback_data=f"admin:reseller:reset_password:{item.id}")],
            [
                ButtonSpec("▶️ فعال‌سازی", callback_data=f"admin:reseller:activate:{item.id}", style="success"),
                ButtonSpec("⏸ تعلیق", callback_data=f"admin:reseller:suspend:{item.id}", style="danger"),
            ],
        ]
        owner = await session.get(Customer, item.customer_id)
        plan = await session.get(PlanModel, item.plan_id)
        instance = await session.get(PasarGuardInstance, item.pasarguard_instance_id)
        owner_text = identity(owner.telegram_id, owner.username, owner.first_name) if owner else "مالک پیدا نشد"
        remaining = (max(item.total_quota_bytes - item.allocated_bytes, 0)
                     if item.total_quota_bytes is not None else None)
        return (
            f"🖥 <b>نمایندگی #{item.id}</b>\n{owner_text}\n\n"
            f"📦 پلن: {html.escape(plan.name if plan else '—')} · PasarGuard: {html.escape(instance.name if instance else '—')}\n"
            f"🌐 Username: <code>{html.escape(item.panel_username or '-')}</code> · Admin ID: <code>{html.escape(str(item.pasarguard_admin_id or '-'))}</code>\n"
            f"🟢 وضعیت: {item.status} · User: {item.users_count}/{item.max_users or '∞'}\n"
            f"📊 ظرفیت: {format_bytes(item.total_quota_bytes)} · تخصیص‌یافته: {format_bytes(item.allocated_bytes)}\n"
            f"📥 آزاد: {format_bytes(remaining)} · مصرف: {format_bytes(item.actual_used_bytes)}\n"
            f"🛡 سقف ترافیک native Admin: {format_bytes(item.total_quota_bytes)}\n"
            f"🌍 Groupها: {html.escape(str(item.group_ids))}\n"
            f"🕒 خرید: {when(item.created_at)} · انقضا: {when(item.expires_at)}\n"
            f"🔄 آخرین Sync: {when(item.last_synced_at)} · خطا: {html.escape(item.last_sync_error or 'ندارد')}\n"
            "ℹ️ Role سقف User و حجم هر User را enforce می‌کند؛ Admin سقف مصرف واقعی را دارد؛ "
            "جمع حجم تخصیص‌یافته در Sync بررسی می‌شود.",
            rows,
            "resellers",
        )
    if entity == "ticket":
        item = await session.get(Ticket, item_id)
        if item is None:
            return "تیکت پیدا نشد.", rows, "support"
        customer = await session.get(Customer, item.customer_id)
        messages = (
            await session.scalars(
                select(TicketMessage)
                .where(TicketMessage.ticket_id == item.id)
                .order_by(TicketMessage.id.desc())
                .limit(5)
            )
        ).all()
        history = "\n".join(
            f"• {html.escape(message.sender_type)}: {html.escape(message.body[:180])}"
            for message in reversed(messages)
        )
        rows = [
            [ButtonSpec("✉️ پاسخ به مشتری", callback_data=f"admin:wizard:ticket_reply:{item.id}", style="primary")],
            [ButtonSpec("✅ بستن تیکت" if item.status != "closed" else "↩️ بازکردن", callback_data=f"admin:ticket:toggle:{item.id}", style="success")],
        ]
        return (
            f"🎫 <b>تیکت #{item.id}</b>\n{identity(customer.telegram_id, customer.username, customer.first_name) if customer else 'مشتری حذف شده'}\n"
            f"موضوع: {html.escape(item.subject)}\nوضعیت: {item.status}\n\n{history or 'هنوز پیامی ثبت نشده است.'}",
            rows,
            "support",
        )
    if entity == "order":
        item = await session.get(Order, item_id)
        if item is None:
            return "سفارش پیدا نشد.", rows, "orders"
        if item.status == OrderStatus.FAILED.value:
            rows.append([ButtonSpec("🔄 تلاش مجدد ساخت", callback_data=f"admin:order:retry:{item.id}", style="success")])
        owner = await session.get(Customer, item.customer_id)
        plan = await session.get(PlanModel, item.plan_id) if item.plan_id else None
        return (f"🛒 <b>سفارش #{item.id}</b>\n{identity(owner.telegram_id, owner.username, owner.first_name) if owner else 'مشتری حذف شده'}\n"
                f"📦 پلن: {html.escape(plan.name if plan else item.order_type)}\n"
                f"🖥 نمایندگی: #{item.reseller_id or '—'} · نوع: {item.order_type}\n"
                f"💰 مبلغ: {money(item.payable_irt)} · وضعیت: {item.status}\n"
                f"🕒 ثبت: {when(item.created_at)} · تغییر: {when(item.updated_at)}\n"
                f"⚠️ خطا: {html.escape(item.error_message or 'ندارد')}"), rows, "orders"
    if entity == "accounting":
        item = await session.get(AccountingEntry, item_id)
        if item is None:
            return "تراکنش پیدا نشد.", rows, "accounting"
        customer = await session.get(Customer, item.customer_id)
        admin = await session.get(BotAdmin, item.admin_id) if item.admin_id else None
        admin_identity = identity(admin.telegram_id, name=admin.display_name) if admin else "سیستم"
        return (f"💰 <b>تراکنش #{item.id}</b>\n{identity(customer.telegram_id, customer.username, customer.first_name) if customer else 'مشتری حذف شده'}\n"
                f"🛒 سفارش: #{item.order_id or '—'} · نوع: {item.transaction_type}\n"
                f"💵 مبلغ: {money(item.amount_irt)} · وضعیت: {item.status}\n"
                f"💳 روش: {item.payment_method}\nادمین: {admin_identity}\n"
                f"🕒 ثبت: {when(item.created_at)}"), rows, "accounting"
    if entity == "card_account":
        item = await session.get(PaymentAccount, item_id)
        if item is None:
            return "کارت پیدا نشد.", rows, "payments"
        rows = [
            [ButtonSpec("✏️ ویرایش کارت", callback_data=f"admin:wizard:card_account:{item.id}", style="primary")],
            [ButtonSpec("⛔ غیرفعال" if item.is_active else "✅ فعال", callback_data=f"admin:toggle:card_account:{item.id}")],
        ]
        return f"💳 <b>{html.escape(item.title)}</b>\n<code>{html.escape(item.account_value)}</code>\nبه نام: {html.escape(item.holder_name)}\nفعال: {'بله' if item.is_active else 'خیر'}", rows, "payments"
    if entity == "channel":
        item = await session.get(ChannelLock, item_id)
        if item is None:
            return "کانال پیدا نشد.", rows, "channel_locks"
        rows = [
            [ButtonSpec("✏️ ویرایش", callback_data=f"admin:wizard:channel:{item.id}", style="primary")],
            [ButtonSpec("⛔ غیرفعال" if item.is_active else "✅ فعال", callback_data=f"admin:toggle:channel:{item.id}")],
        ]
        return f"📣 <b>{html.escape(item.title)}</b>\nChat: <code>{html.escape(item.chat_id)}</code>\nScope: {item.scopes}\nفعال: {'بله' if item.is_active else 'خیر'}", rows, "channel_locks"
    if entity == "admin":
        item = await session.get(BotAdmin, item_id)
        if item is None:
            return "ادمین پیدا نشد.", rows, "admins"
        rows = [
            [ButtonSpec("✏️ ویرایش نقش", callback_data=f"admin:wizard:admin:{item.id}", style="primary")],
            [ButtonSpec("🔐 دسترسی‌های سفارشی", callback_data=f"admin:permissions:{item.id}")],
            [ButtonSpec("⛔ غیرفعال" if item.is_active else "✅ فعال", callback_data=f"admin:toggle:admin:{item.id}")],
        ]
        from pasarbot.services.authorization import effective_permissions
        granted = effective_permissions(item.telegram_id, settings.owner_telegram_ids,
                                        item.role_name, item.permissions, active=item.is_active)
        person = await session.scalar(select(Customer).where(Customer.telegram_id == item.telegram_id))
        return (f"🛡 <b>ادمین #{item.id}</b>\n{identity(item.telegram_id, person.username if person else None, item.display_name)}\n"
                f"🔐 نقش: {item.role_name} · فعال: {'بله' if item.is_active else 'خیر'}\n"
                f"✅ دسترسی‌ها ({len(granted)}): {html.escape(', '.join(sorted(x.value for x in granted)))}\n"
                f"🕒 افزوده‌شده: {when(item.created_at)} · ویرایش: {when(item.updated_at)}"), rows, "admins"
    if entity == "pasarguard":
        item = await session.get(PasarGuardInstance, item_id)
        if item is None:
            return "پنل پیدا نشد.", rows, "pasarguard"
        rows = [
            [ButtonSpec("🧪 تست اتصال", callback_data=f"admin:pasarguard:test:{item.id}", style="primary")],
            [ButtonSpec("✏️ ویرایش اتصال / تعویض کلید", callback_data=f"admin:wizard:pasarguard:{item.id}")],
            [ButtonSpec("⛔ غیرفعال" if item.is_active else "✅ فعال", callback_data=f"admin:toggle:pasarguard:{item.id}")],
        ]
        return f"🔌 <b>{html.escape(item.name)}</b>\nAPI: {html.escape(item.base_url)}\nDashboard: {html.escape(item.dashboard_url)}\nفعال: {'بله' if item.is_active else 'خیر'}\nآخرین خطا: {html.escape(item.last_health_error or 'ندارد')}", rows, "pasarguard"
    return "جزئیات پیدا نشد.", rows, "dashboard"


TOGGLE_MODELS: dict[str, tuple[type, Permission]] = {
    "plan_active": (PlanModel, Permission.PLAN_MANAGE),
    "plan_visible": (PlanModel, Permission.PLAN_MANAGE),
    "addon": (AddonPackage, Permission.PLAN_MANAGE),
    "location": (Location, Permission.PLAN_MANAGE),
    "card_account": (PaymentAccount, Permission.PAYMENT_MANAGE),
    "channel": (ChannelLock, Permission.CHANNEL_LOCK_MANAGE),
    "admin": (BotAdmin, Permission.ADMIN_MANAGE),
    "pasarguard": (PasarGuardInstance, Permission.PASARGUARD_MANAGE),
}


@router.callback_query(F.data.startswith("admin:toggle:"))
async def toggle_entity(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    if not callback.from_user:
        return
    _, _, entity, raw_id = callback.data.split(":", 3)
    config = TOGGLE_MODELS.get(entity)
    if config is None or not await allowed(session, settings, callback.from_user.id, config[1]):
        await callback.answer("مجوز ندارید.", show_alert=True)
        return
    item = await session.get(config[0], int(raw_id))
    if item is None:
        await callback.answer("مورد پیدا نشد.", show_alert=True)
        return
    if entity == "admin" and item.telegram_id in settings.owner_telegram_ids:
        await callback.answer("مالک اصلی را نمی‌توان غیرفعال کرد.", show_alert=True)
        return
    attribute = "is_visible" if entity == "plan_visible" else "is_active"
    setattr(item, attribute, not bool(getattr(item, attribute)))
    await session.flush()
    await callback.answer("وضعیت تغییر کرد.", show_alert=True)
    if callback.message:
        detail_entity = "plan" if entity.startswith("plan_") else entity
        text, rows, _ = await detail_view(session, detail_entity, int(raw_id), settings)
        await callback.message.edit_text(
            text,
            reply_markup=inline_keyboard(
                rows + [[ButtonSpec("↩️ بازگشت", callback_data=f"admin:list:{detail_entity}")]],
                modern_features=settings.telegram_button_features,
            ),
        )


@router.callback_query(F.data.startswith("admin:customer:block:"))
async def toggle_customer_block(
    callback: CallbackQuery, session: AsyncSession, settings: Settings,
    secret_box: SecretBox,
) -> None:
    if not callback.from_user:
        return
    if not await allowed(session, settings, callback.from_user.id, Permission.CUSTOMER_EDIT):
        await callback.answer("مجوز ویرایش مشتری ندارید.", show_alert=True)
        return
    customer = await session.get(Customer, int(callback.data.rsplit(":", 1)[1]))
    if customer is None:
        await callback.answer("مشتری پیدا نشد.", show_alert=True)
        return
    customer.is_blocked = not customer.is_blocked
    panels = (await session.scalars(select(ResellerModel).where(
        ResellerModel.customer_id == customer.id,
        ResellerModel.status.in_([ResellerStatus.ACTIVE.value, ResellerStatus.SUSPENDED.value]),
    ))).all()
    restored = suspended = skipped = 0
    for panel in panels:
        if not panel.pasarguard_admin_id:
            continue
        instance = await session.get(PasarGuardInstance, panel.pasarguard_instance_id)
        if not instance:
            skipped += 1
            continue
        client = pg_client(instance, secret_box)
        try:
            if customer.is_blocked and panel.status == ResellerStatus.ACTIVE.value:
                await client.suspend_admin(str(panel.pasarguard_admin_id))
                panel.status = ResellerStatus.SUSPENDED.value
                panel.manual_overrides = {**(panel.manual_overrides or {}), "suspended_by_customer_block": True}
                suspended += 1
            elif not customer.is_blocked:
                auto_suspended = bool((panel.manual_overrides or {}).get("suspended_by_customer_block"))
                if panel.status == ResellerStatus.SUSPENDED.value and not auto_suspended:
                    skipped += 1
                    continue
                if panel.status == ResellerStatus.ACTIVE.value:
                    remote = await client.find_admin_by_username(panel.panel_username or "")
                    if remote is None or str(remote.get("status", "active")).casefold() not in {"disabled", "inactive"}:
                        continue
                snapshot = await client.reseller_snapshot(str(panel.pasarguard_admin_id))
                if (panel.expires_at and panel.expires_at <= datetime.now(UTC)) or (
                    panel.total_quota_bytes is not None and
                    (snapshot.allocated_bytes > panel.total_quota_bytes or snapshot.unlimited_users)
                ) or (panel.max_users is not None and snapshot.users_count > panel.max_users):
                    skipped += 1
                    continue
                await client.activate_admin(str(panel.pasarguard_admin_id))
                panel.status = ResellerStatus.ACTIVE.value
                panel.manual_overrides = {key: value for key, value in (panel.manual_overrides or {}).items()
                                          if key != "suspended_by_customer_block"}
                restored += 1
        except Exception as exc:
            panel.last_sync_error = f"reactivation: {type(exc).__name__}"
            skipped += 1
    await session.flush()
    await callback.answer(f"وضعیت مشتری تغییر کرد؛ فعال‌شده: {restored}، تعلیق‌شده: {suspended}، نیازمند بررسی: {skipped}.", show_alert=True)


@router.callback_query(F.data.startswith("admin:ticket:toggle:"))
async def toggle_ticket(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    if not callback.from_user:
        return
    if not await allowed(session, settings, callback.from_user.id, Permission.SUPPORT_MANAGE):
        await callback.answer("مجوز ندارید.", show_alert=True)
        return
    ticket = await session.get(Ticket, int(callback.data.rsplit(":", 1)[1]))
    if ticket is None:
        await callback.answer("تیکت پیدا نشد.", show_alert=True)
        return
    ticket.status = "open" if ticket.status == "closed" else "closed"
    await session.flush()
    await callback.answer("وضعیت تیکت تغییر کرد.", show_alert=True)


@router.callback_query(F.data.startswith("ticket:review:") | F.data.startswith("ticket:reply:"))
async def ticket_group_action(callback: CallbackQuery, session: AsyncSession,
                              settings: Settings, bot: Bot, state: FSMContext) -> None:
    if not callback.from_user:
        return
    if not await allowed(session, settings, callback.from_user.id, Permission.SUPPORT_MANAGE):
        await callback.answer("مجوز پاسخ به تیکت ندارید.", show_alert=True)
        return
    _, action, raw_id = callback.data.split(":")
    ticket = await session.scalar(select(Ticket).where(Ticket.id == int(raw_id)).with_for_update())
    if ticket is None or ticket.status not in {"open", "reviewed"}:
        await callback.answer("این تیکت قبلاً بررسی شده است.", show_alert=True)
        return
    if action == "reply":
        from .admin_wizard import AdminWizardState
        await state.set_state(AdminWizardState.collecting)
        await state.set_data({"flow": "ticket_reply", "values": {}, "context": {"target_id": ticket.id}})
        await callback.answer("متن پاسخ را در همین گفتگو ارسال کنید.")
        if callback.message:
            await callback.message.answer(f"✍️ پاسخ تیکت #{ticket.id} را بنویسید:")
        return
    ticket.status = "reviewed"
    customer = await session.get(Customer, ticket.customer_id)
    if callback.message:
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(f"✅ تیکت #{ticket.id} بررسی شد. در صورت نیاز از پنل پشتیبانی پاسخ دهید.")
    if customer:
        try:
            await bot.send_message(customer.telegram_id, await bot_copy(session, "ticket_reviewed",
                "✅ تیکت #{ticket_id} بررسی شد. پاسخ احتمالی را در بخش پشتیبانی ببینید.",
                ticket_id=ticket.id))
        except Exception as exc:
            logger.warning("ticket notification failed: %s", type(exc).__name__)
    await callback.answer("بررسی شد.")


@router.callback_query(F.data.startswith("admin:permissions:"))
async def show_admin_permissions(
    callback: CallbackQuery, session: AsyncSession, settings: Settings
) -> None:
    if not callback.from_user or not callback.message:
        return
    if not await allowed(session, settings, callback.from_user.id, Permission.ADMIN_MANAGE):
        await callback.answer("مجوز ندارید.", show_alert=True)
        return
    admin_id = int(callback.data.rsplit(":", 1)[1])
    admin = await session.get(BotAdmin, admin_id)
    if admin is None:
        await callback.answer("ادمین پیدا نشد.", show_alert=True)
        return
    rows = permission_rows(admin)
    rows.append([ButtonSpec("↩️ بازگشت به ادمین", callback_data=f"admin:detail:admin:{admin.id}")])
    await callback.message.edit_text(
        f"🔐 <b>دسترسی‌های سفارشی {html.escape(admin.display_name)}</b>\n"
        "🔒 دسترسی نقش آماده است و اینجا خاموش نمی‌شود. برای تنظیم دقیق، نقش «سفارشی» را انتخاب کنید.\n"
        "✅ مجوز اضافی فعال · ▫️ غیرفعال",
        reply_markup=inline_keyboard(rows, modern_features=settings.telegram_button_features),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:permission_toggle:"))
async def toggle_admin_permission(
    callback: CallbackQuery, session: AsyncSession, settings: Settings
) -> None:
    if not callback.from_user:
        return
    if not await allowed(session, settings, callback.from_user.id, Permission.ADMIN_MANAGE):
        await callback.answer("مجوز ندارید.", show_alert=True)
        return
    _, _, raw_id, raw_permission = callback.data.split(":", 3)
    admin = await session.get(BotAdmin, int(raw_id))
    try:
        permission = list(Permission)[int(raw_permission)]
    except (ValueError, IndexError):
        await callback.answer("دسترسی نامعتبر است.", show_alert=True)
        return
    if admin is None or admin.telegram_id in settings.owner_telegram_ids:
        await callback.answer("دسترسی Owner از این بخش تغییر نمی‌کند.", show_alert=True)
        return
    custom = set(admin.permissions or [])
    if permission in ROLE_PRESETS.get(admin.role_name, frozenset()):
        await callback.answer("این دسترسی از نقش آماده می‌آید. ابتدا نقش را «سفارشی» کنید.", show_alert=True)
        return
    if permission.value in custom:
        custom.remove(permission.value)
    else:
        custom.add(permission.value)
    admin.permissions = sorted(custom)
    await session.flush()
    await callback.answer("دسترسی تغییر کرد.", show_alert=True)
    if callback.message:
        rows = permission_rows(admin)
        rows.append([ButtonSpec("↩️ بازگشت به ادمین", callback_data=f"admin:detail:admin:{admin.id}")])
        await callback.message.edit_reply_markup(
            reply_markup=inline_keyboard(
                rows, modern_features=settings.telegram_button_features
            )
        )


def pg_client(instance: PasarGuardInstance, secret_box: SecretBox) -> PasarGuardClient:
    return PasarGuardClient(
        base_url=instance.base_url,
        access_token=secret_box.decrypt(instance.encrypted_access_token),
        transport=HttpxTransport(verify_tls=instance.verify_tls),
        routes=PasarGuardRoutes.from_overrides(instance.route_overrides),
    )


@router.callback_query(F.data.startswith("admin:appearance:"))
async def appearance_action(callback: CallbackQuery, session: AsyncSession,
                            settings: Settings, state: FSMContext,
                            secret_box: SecretBox) -> None:
    if not callback.from_user or not callback.message:
        return
    if not await allowed(session, settings, callback.from_user.id, Permission.TEXT_MANAGE):
        await callback.answer("مجوز ندارید.", show_alert=True)
        return
    _, _, action, key = callback.data.split(":", 3)
    from pasarbot.bot.admin_schema import BUTTON_CHOICES, TEXT_CHOICES
    is_button = action.endswith("button")
    choices = BUTTON_CHOICES if is_button else TEXT_CHOICES
    if action not in {"edit_button", "reset_button", "edit_text", "reset_text"} or key not in {x[1] for x in choices}:
        await callback.answer("گزینه معتبر نیست.", show_alert=True)
        return
    model = ButtonConfig if is_button else BotText
    record = await session.get(model, key)
    if action.startswith("reset"):
        if record:
            await session.delete(record)
        await callback.answer("به حالت پیش‌فرض برگشت.", show_alert=True)
        text, rows, section = await entity_list(session, "button" if is_button else "bot_text")
        await callback.message.edit_text(text, reply_markup=inline_keyboard(
            management_rows(section, rows), modern_features=settings.telegram_button_features))
        return
    from .admin_wizard import AdminWizardState, prompt_next
    values = {"key": key}
    existing = ({"text": record.text, "style": record.style or "default",
                 "custom_emoji_id": record.custom_emoji_id}
                if is_button else {"value": record.value}) if record else {}
    await state.set_state(AdminWizardState.collecting)
    await state.set_data({"flow": "button" if is_button else "bot_text",
                          "values": values, "context": {"existing_values": existing}})
    await callback.answer()
    await prompt_next(callback.message, state, settings, session, secret_box)


@router.callback_query(F.data.regexp(r"^admin:reseller:(sync|role_sync|reset_password|suspend|activate):[0-9]+$"))
async def reseller_action(
    callback: CallbackQuery,
    session: AsyncSession,
    settings: Settings,
    secret_box: SecretBox,
    bot: Bot,
) -> None:
    if not callback.from_user:
        return
    _, _, action, raw_id = callback.data.split(":", 3)
    if not await allowed(session, settings, callback.from_user.id, Permission.RESELLER_EDIT):
        await callback.answer("مجوز ویرایش نمایندگی ندارید.", show_alert=True)
        return
    reseller = await session.get(ResellerModel, int(raw_id))
    if reseller is None or not reseller.pasarguard_admin_id:
        await callback.answer("نمایندگی کامل ساخته نشده است.", show_alert=True)
        return
    instance = await session.get(PasarGuardInstance, reseller.pasarguard_instance_id)
    if instance is None:
        await callback.answer("پنل PasarGuard پیدا نشد.", show_alert=True)
        return
    client = pg_client(instance, secret_box)
    if action == "sync":
        snapshot = await client.reseller_snapshot(str(reseller.pasarguard_admin_id))
        reseller.users_count = snapshot.users_count
        reseller.allocated_bytes = snapshot.allocated_bytes
        reseller.actual_used_bytes = snapshot.actual_used_bytes
        reseller.last_synced_at = datetime.now(UTC)
        reseller.last_sync_error = None
    elif action == "role_sync":
        plan = await session.get(PlanModel, reseller.plan_id)
        if plan is None:
            await callback.answer("پلن پیدا نشد.", show_alert=True)
            return
        was_active = reseller.status == ResellerStatus.ACTIVE.value
        try:
            await client.suspend_admin(str(reseller.pasarguard_admin_id))
            role_id = await RuntimeProvisioner._reseller_role_id(client, reseller)
            spec = RuntimeProvisioner._role_spec(plan, role_name=RuntimeProvisioner._role_name(reseller.id),
                         groups=reseller.group_ids, templates=reseller.template_ids,
                         quota=reseller.total_quota_bytes, max_users=reseller.max_users)
            await client.apply_role(role_id, spec)
            await client.update_admin(str(reseller.pasarguard_admin_id), {"data_limit": reseller.total_quota_bytes})
            await client.verify_admin_limits(str(reseller.pasarguard_admin_id), role_id, reseller.total_quota_bytes)
            if was_active:
                await client.activate_admin(str(reseller.pasarguard_admin_id))
            reseller.last_sync_error = None
        except PasarGuardError as exc:
            reseller.status = ResellerStatus.SUSPENDED.value
            reseller.last_sync_error = str(exc)[:300]
            await callback.answer(f"بازاعمال ناموفق: {str(exc)[:140]}", show_alert=True)
            return
    elif action == "reset_password":
        password = RuntimeProvisioner._password()
        await client.update_admin(str(reseller.pasarguard_admin_id), {"password": password})
        reseller.encrypted_panel_password = secret_box.encrypt(password)
        customer = await session.get(Customer, reseller.customer_id)
        if customer:
            try:
                await bot.send_message(customer.telegram_id,
                    f"🔑 رمز نمایندگی #{reseller.id} بازنشانی شد:\n<code>{password}</code>")
            except Exception as exc:
                logger.warning("password delivery failed for reseller %s: %s", reseller.id, type(exc).__name__)
                reseller.last_sync_error = "password delivery failed; ask customer to open bot chat"
                await session.flush()
                await callback.answer("رمز تغییر کرد ولی تلگرام نتوانست آن را به مشتری برساند؛ از مشتری بخواهید بات را Start کند، سپس دوباره رمز را بازنشانی کنید.", show_alert=True)
                return
    elif action == "suspend":
        await client.suspend_admin(str(reseller.pasarguard_admin_id))
        reseller.status = ResellerStatus.SUSPENDED.value
        reseller.manual_overrides = {key: value for key, value in (reseller.manual_overrides or {}).items()
                                     if key != "suspended_by_customer_block"}
    elif action == "activate":
        customer = await session.get(Customer, reseller.customer_id)
        if customer is None or customer.is_blocked:
            await callback.answer("مشتری مسدود است؛ ابتدا رفع مسدودی کنید.", show_alert=True)
            return
        snapshot = await client.reseller_snapshot(str(reseller.pasarguard_admin_id))
        if (reseller.expires_at and reseller.expires_at <= datetime.now(UTC)) or (
            reseller.total_quota_bytes is not None and
            (snapshot.allocated_bytes > reseller.total_quota_bytes or snapshot.unlimited_users)
        ) or (reseller.max_users is not None and snapshot.users_count > reseller.max_users):
            await callback.answer("اعتبار یا سهمیهٔ نمایندگی نامعتبر است؛ ابتدا محدودیت‌ها را اصلاح کنید.", show_alert=True)
            return
        await client.activate_admin(str(reseller.pasarguard_admin_id))
        reseller.status = ResellerStatus.ACTIVE.value
    else:
        await callback.answer("عملیات ناشناخته است.", show_alert=True)
        return
    reseller.version += 1
    await session.flush()
    await callback.answer("عملیات انجام شد.", show_alert=True)


@router.callback_query(F.data.startswith("admin:reseller:users:"))
async def reseller_users_admin(callback: CallbackQuery, session: AsyncSession,
                               settings: Settings, secret_box: SecretBox) -> None:
    if not callback.from_user or not callback.message:
        return
    if not await allowed(session, settings, callback.from_user.id, Permission.RESELLER_VIEW):
        await callback.answer("مجوز ندارید.", show_alert=True)
        return
    reseller = await session.get(ResellerModel, int(callback.data.rsplit(":", 1)[1]))
    if not reseller or not reseller.pasarguard_admin_id:
        await callback.answer("نمایندگی آماده نیست.", show_alert=True)
        return
    instance = await session.get(PasarGuardInstance, reseller.pasarguard_instance_id)
    if not instance:
        await callback.answer("پنل مقصد پیدا نشد.", show_alert=True)
        return
    try:
        snapshot = await pg_client(instance, secret_box).reseller_snapshot(str(reseller.pasarguard_admin_id))
    except PasarGuardError as exc:
        await callback.answer(str(exc)[:140], show_alert=True)
        return
    lines = [f"👥 <b>Userهای نمایندگی #{reseller.id}</b> · {snapshot.users_count}\n"]
    lines.extend(f"\n• ID <code>{html.escape(str(x.id))}</code> · {html.escape(x.username)} · "
                 f"{'نامحدود ⚠️' if x.data_limit_bytes == 0 else format_bytes(x.data_limit_bytes)} / {format_bytes(x.used_bytes)}"
                 for x in snapshot.users[:30])
    if snapshot.users_count > 30:
        lines.append("\nبرای موردهای دیگر از جستجو استفاده کنید.")
    await callback.message.edit_text("".join(lines), reply_markup=inline_keyboard([
        [ButtonSpec("🔎 جستجو", callback_data=f"admin:wizard:user_search:{reseller.id}")],
        [ButtonSpec("↩️ نمایندگی", callback_data=f"admin:detail:reseller:{reseller.id}")],
    ], modern_features=settings.telegram_button_features))
    await callback.answer()


@router.callback_query(F.data.startswith("admin:order:retry:"))
async def retry_order(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    if not callback.from_user:
        return
    if not await allowed(session, settings, callback.from_user.id, Permission.RESELLER_EDIT):
        await callback.answer("مجوز تلاش مجدد ندارید.", show_alert=True)
        return
    order = await session.get(Order, int(callback.data.rsplit(":", 1)[1]))
    if order is None or order.status != OrderStatus.FAILED.value:
        await callback.answer("این سفارش در وضعیت خطادار نیست.", show_alert=True)
        return
    order.status = OrderStatus.PAID.value
    order.error_message = None
    await session.flush()
    await callback.answer("سفارش وارد صف ساخت شد.", show_alert=True)


@router.callback_query(F.data == "admin:payment:wallet")
async def wallet_status(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    if not callback.from_user or not callback.message:
        return
    if not await allowed(session, settings, callback.from_user.id, Permission.PAYMENT_MANAGE):
        await callback.answer("مجوز ندارید.", show_alert=True)
        return
    total = await session.scalar(select(func.coalesce(func.sum(Customer.wallet_balance_irt), 0)))
    await callback.message.edit_text(
        f"💰 <b>کیف پول فعال است</b>\n\nمجموع موجودی مشتریان: {money(int(total or 0))}\n"
        "افزایش یا کاهش موجودی فقط با ثبت تراکنش حسابداری انجام می‌شود.",
        reply_markup=inline_keyboard(
            management_rows("payments", [[ButtonSpec("➕/➖ اصلاح موجودی", callback_data="admin:wizard:wallet_adjust", style="primary")]]),
            modern_features=settings.telegram_button_features,
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:receipt_group:"))
async def receipt_group_action(
    callback: CallbackQuery,
    session: AsyncSession,
    settings: Settings,
    bot: Bot,
) -> None:
    if not callback.from_user:
        return
    if not await allowed(session, settings, callback.from_user.id, Permission.PAYMENT_MANAGE):
        await callback.answer("مجوز ندارید.", show_alert=True)
        return
    action = callback.data.rsplit(":", 1)[1]
    if action == "disable":
        record = await session.get(RuntimeSetting, "receipt_group_id")
        if record:
            record.value = {}
        settings.receipt_group_id = None
        await callback.answer("ارسال رسید به گروه غیرفعال شد.", show_alert=True)
        return
    if not settings.receipt_group_id:
        await callback.answer("ابتدا گروه را تنظیم کنید.", show_alert=True)
        return
    await bot.send_message(settings.receipt_group_id, "✅ پیام آزمایشی بات رسید؛ اتصال گروه درست است.")
    await callback.answer("پیام آزمایشی ارسال شد.", show_alert=True)


@router.callback_query(F.data.startswith("admin:pasarguard:test:"))
async def test_pasarguard(
    callback: CallbackQuery,
    session: AsyncSession,
    settings: Settings,
    secret_box: SecretBox,
) -> None:
    if not callback.from_user:
        return
    if not await allowed(session, settings, callback.from_user.id, Permission.PASARGUARD_MANAGE):
        await callback.answer("مجوز ندارید.", show_alert=True)
        return
    instance = await session.get(PasarGuardInstance, int(callback.data.rsplit(":", 1)[1]))
    if instance is None:
        await callback.answer("پنل پیدا نشد.", show_alert=True)
        return
    try:
        await pg_client(instance, secret_box).preflight()
        error = None
    except Exception as exc:
        error = str(exc)[:300]
    instance.last_health_at = datetime.now(UTC)
    instance.last_health_error = error
    await session.flush()
    await callback.answer("✅ مجوزها و اتصال تأیید شد." if error is None else f"❌ {error[:175]}", show_alert=True)


@router.callback_query(F.data == "admin:settings:status")
async def settings_status(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    if not callback.from_user or not callback.message:
        return
    if not await allowed(session, settings, callback.from_user.id, Permission.ADMIN_MANAGE):
        await callback.answer("مجوز ندارید.", show_alert=True)
        return
    panels = await session.scalar(select(func.count()).select_from(PasarGuardInstance).where(PasarGuardInstance.is_active.is_(True)))
    active = await session.scalar(select(func.count()).select_from(ResellerModel).where(ResellerModel.status == "active"))
    suspended = await session.scalar(select(func.count()).select_from(ResellerModel).where(ResellerModel.status == "suspended"))
    pending = await session.scalar(select(func.count()).select_from(Receipt).where(Receipt.status == "pending"))
    errors = await session.scalar(select(func.count()).select_from(ResellerModel).where(ResellerModel.last_sync_error.is_not(None)))
    records = (await session.scalars(select(PasarGuardInstance).order_by(PasarGuardInstance.id))).all()
    disk = shutil.disk_usage("/app")
    try:
        import redis.asyncio as redis
        cache = redis.from_url(settings.redis_url.get_secret_value())
        cache_ok = await cache.ping()
        await cache.aclose()
    except Exception:
        cache_ok = False
    pg_lines = "\n".join(f"• {html.escape(x.name)}: {'فعال' if x.is_active else 'غیرفعال'}"
                         f" · بررسی {when(x.last_health_at)}"
                         f" · {html.escape((x.last_health_error or 'بدون خطای ثبت‌شده')[:80])}"
                         for x in records)
    await callback.message.edit_text(
        "⚙️ <b>وضعیت سیستم</b>\n\n"
        f"محیط: {html.escape(settings.environment)}\n"
        f"پنل مقصد فعال: {panels or 0}\n"
        f"نمایندگی فعال: {active or 0} · معلق: {suspended or 0}\n"
        f"رسید منتظر: {pending or 0} · خطای Sync: {errors or 0}\n"
        f"PostgreSQL: ✅ · Redis: {'✅' if cache_ok else '❌'}\n"
        f"فضای دیسک کانتینر: {disk.free // (1024**3)} GB آزاد از {disk.total // (1024**3)} GB\n"
        f"Load سیستم: {os.getloadavg()[0]:.2f}\n"
        f"فاصله Sync: {settings.sync_interval_seconds // 60} دقیقه\n"
        f"عبور از سهمیه: {settings.quota_enforcement_mode}\n"
        f"پرداخت‌های نمایشی: کارت‌به‌کارت و کیف پول\n\n{pg_lines or 'پنلی متصل نیست.'}",
        reply_markup=inline_keyboard(
            management_rows("settings", []),
            modern_features=settings.telegram_button_features,
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:help:"))
async def contextual_help(callback: CallbackQuery, settings: Settings) -> None:
    if not callback.message:
        return
    key = callback.data.rsplit(":", 1)[1]
    await callback.message.edit_text(
        HELP_TEXTS.get(key, "راهنمای این بخش پیدا نشد."),
        reply_markup=inline_keyboard(
            [[ButtonSpec("↩️ راهنمای مدیریت", callback_data="admin:help")]],
            modern_features=settings.telegram_button_features,
        ),
    )
    await callback.answer()
