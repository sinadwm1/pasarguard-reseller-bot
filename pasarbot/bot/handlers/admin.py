from __future__ import annotations

import html

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from pasarbot.bot.admin_schema import ADMIN_REPLY_SECTIONS, ADMIN_SECTION_ACTIONS
from pasarbot.bot.copy import bot_copy
from pasarbot.bot.handlers.customer import money
from pasarbot.bot.keyboards import admin_menu, admin_reply_menu, inline_keyboard
from pasarbot.bot.presentation import identity
from pasarbot.bot.theme import load_button_theme
from pasarbot.config import Settings
from pasarbot.db.models import (
    AccountingEntry,
    BotAdmin,
    ChannelLock,
    Customer,
    Order,
    PasarGuardInstance,
    PlanModel,
    Receipt,
    ResellerModel,
)
from pasarbot.db.repositories import AdminRepository, CustomerRepository, ReceiptRepository
from pasarbot.domain.enums import OrderStatus, Permission, ReceiptStatus
from pasarbot.domain.telegram import ButtonSpec
from pasarbot.security import CallbackSigner, InvalidCallback

router = Router(name="admin")


SECTION_PERMISSIONS: dict[str, Permission] = {
    "dashboard": Permission.DASHBOARD_VIEW,
    "plans": Permission.PLAN_MANAGE,
    "locations": Permission.PLAN_MANAGE,
    "customers": Permission.CUSTOMER_VIEW,
    "resellers": Permission.RESELLER_VIEW,
    "support": Permission.SUPPORT_MANAGE,
    "orders": Permission.DASHBOARD_VIEW,
    "receipts": Permission.RECEIPT_APPROVE,
    "payments": Permission.PAYMENT_MANAGE,
    "accounting": Permission.ACCOUNTING_VIEW,
    "reports": Permission.DASHBOARD_VIEW,
    "receipt_groups": Permission.PAYMENT_MANAGE,
    "channel_locks": Permission.CHANNEL_LOCK_MANAGE,
    "admins": Permission.ADMIN_MANAGE,
    "texts": Permission.TEXT_MANAGE,
    "buttons": Permission.TEXT_MANAGE,
    "broadcast": Permission.BROADCAST_SEND,
    "pasarguard": Permission.PASARGUARD_MANAGE,
    "settings": Permission.ADMIN_MANAGE,
    "help": Permission.DASHBOARD_VIEW,
}


async def admin_permissions(
    session: AsyncSession, settings: Settings, telegram_id: int
) -> frozenset[Permission]:
    return await AdminRepository(session, settings.owner_telegram_ids).permissions_for(telegram_id)


async def admin_row(session: AsyncSession, telegram_id: int) -> BotAdmin | None:
    return await session.scalar(select(BotAdmin).where(BotAdmin.telegram_id == telegram_id))


@router.message(Command(commands=["owner", "admin"]))
async def admin_entry(message: Message, session: AsyncSession, settings: Settings,
                      state: FSMContext | None = None) -> None:
    if not message.from_user:
        return
    permissions = await admin_permissions(session, settings, message.from_user.id)
    if Permission.DASHBOARD_VIEW not in permissions:
        await message.answer("⛔ دسترسی مدیریت ندارید.")
        return
    await CustomerRepository(session).upsert_telegram_user(
        message.from_user.id, message.from_user.username,
        message.from_user.first_name or "", message.from_user.last_name or "",
    )
    if state is not None:
        await state.clear()
    await message.answer(
        "🛡 <b>حالت مدیریت فعال شد</b>\n"
        "منوی دائمی مدیریت پایین صفحه قرار گرفت. برای خروج، دکمه «خروج از مدیریت» را بزنید.",
        parse_mode="HTML",
        reply_markup=admin_reply_menu(modern_features=settings.telegram_button_features,
                                      theme=await load_button_theme(session)),
    )
    text, rows = await render_section(session, "dashboard")
    await message.answer(
        text,
        parse_mode="HTML",
        reply_markup=inline_keyboard(
            action_rows("dashboard") + rows,
            modern_features=settings.telegram_button_features,
        ),
    )


@router.callback_query(F.data.in_({f"admin:{key}" for key in SECTION_PERMISSIONS}))
async def admin_section(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    if not callback.from_user or not callback.message:
        return
    section = callback.data.split(":", 1)[1]
    required = SECTION_PERMISSIONS.get(section)
    permissions = await admin_permissions(session, settings, callback.from_user.id)
    if required is None or required not in permissions:
        await callback.answer("مجوز این بخش را ندارید.", show_alert=True)
        return
    text, rows = await render_section(session, section)
    rows = action_rows(section) + rows
    rows.append([ButtonSpec("↩️ همه بخش‌ها", callback_data="admin:menu")])
    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=inline_keyboard(rows, modern_features=settings.telegram_button_features),
    )
    await callback.answer()


@router.callback_query(F.data == "admin:menu")
async def admin_home(callback: CallbackQuery, settings: Settings, session: AsyncSession) -> None:
    if callback.message:
        await callback.message.edit_text(
            "🛡 مدیریت ربات",
            reply_markup=admin_menu(
                modern_features=settings.telegram_button_features,
                theme=await load_button_theme(session),
            ),
        )
    await callback.answer()


def action_rows(section: str) -> list[list[ButtonSpec]]:
    """Return compact inline action rows for one management section."""
    actions = ADMIN_SECTION_ACTIONS.get(section, ())
    rows: list[list[ButtonSpec]] = []
    for index in range(0, len(actions), 2):
        rows.append(
            [
                ButtonSpec(item.label, callback_data=item.callback, style=item.style)
                for item in actions[index : index + 2]
            ]
        )
    return rows


async def render_section(
    session: AsyncSession, section: str
) -> tuple[str, list[list[ButtonSpec]]]:
    rows: list[list[ButtonSpec]] = []
    if section == "dashboard":
        today_sales = await session.scalar(
            select(func.coalesce(func.sum(Order.payable_irt), 0)).where(
                Order.status == OrderStatus.COMPLETED.value,
                func.date(Order.updated_at) == func.current_date(),
            )
        )
        active = await session.scalar(
            select(func.count()).select_from(ResellerModel).where(ResellerModel.status == "active")
        )
        pending = await session.scalar(
            select(func.count()).select_from(Receipt).where(Receipt.status == "pending")
        )
        failed = await session.scalar(
            select(func.count()).select_from(Order).where(Order.status == "failed")
        )
        return (
            f"📊 <b>داشبورد</b>\n\n💰 فروش امروز: {money(int(today_sales or 0))}\n"
            f"🖥 نمایندگی فعال: {active or 0}\n🧾 رسید منتظر: {pending or 0}\n"
            f"⚠️ سفارش خطادار: {failed or 0}",
            rows,
        )
    if section == "plans":
        items = (await session.scalars(select(PlanModel).order_by(PlanModel.id))).all()
        body = "\n".join(
            f"#{x.id} — {x.name} — {money(x.price_irt)} — {'✅' if x.is_active else '⛔'}"
            for x in items
        ) or "پلنی ثبت نشده است."
        return f"📦 <b>پلن‌ها</b>\n\n{body}", rows
    if section == "customers":
        count = await session.scalar(select(func.count()).select_from(Customer))
        blocked = await session.scalar(
            select(func.count()).select_from(Customer).where(Customer.is_blocked.is_(True))
        )
        return f"👤 <b>مشتریان</b>\n\nکل: {count or 0}\nمسدود: {blocked or 0}", rows
    if section == "resellers":
        counts = (
            await session.execute(
                select(ResellerModel.status, func.count()).group_by(ResellerModel.status)
            )
        ).all()
        body = "\n".join(f"{status}: {count}" for status, count in counts) or "موردی وجود ندارد."
        return f"🖥 <b>نمایندگی‌ها</b>\n\n{body}", rows
    if section == "orders":
        items = (await session.scalars(select(Order).order_by(Order.id.desc()).limit(20))).all()
        body = "\n".join(f"#{x.id} — {money(x.payable_irt)} — {x.status}" for x in items) or "سفارشی نیست."
        return f"🛒 <b>آخرین سفارش‌ها</b>\n\n{body}", rows
    if section == "receipts":
        items = (
            await session.scalars(
                select(Receipt).where(Receipt.status == "pending").order_by(Receipt.id).limit(20)
            )
        ).all()
        for item in items:
            rows.append([ButtonSpec(f"🧾 رسید #{item.id}", callback_data=f"admin:receipt:{item.id}")])
        return "🧾 <b>رسیدهای منتظر</b>" if items else "🧾 رسید منتظری وجود ندارد.", rows
    if section == "accounting":
        total = await session.scalar(
            select(func.coalesce(func.sum(AccountingEntry.amount_irt), 0)).where(
                AccountingEntry.status == "completed"
            )
        )
        grouped = (await session.execute(select(AccountingEntry.transaction_type,
                        func.count(), func.coalesce(func.sum(AccountingEntry.amount_irt), 0))
                        .where(AccountingEntry.status == "completed")
                        .group_by(AccountingEntry.transaction_type))).all()
        today = await session.scalar(select(func.coalesce(func.sum(AccountingEntry.amount_irt), 0))
                                     .where(AccountingEntry.status == "completed",
                                            func.date(AccountingEntry.created_at) == func.current_date()))
        lines = "\n".join(f"• {html.escape(kind)}: {count} مورد · {money(int(amount))}"
                          for kind, count, amount in grouped)
        return (f"💰 <b>حسابداری</b>\n\nامروز: {money(int(today or 0))}\n"
                f"جمع ثبت‌شده: {money(int(total or 0))}\n\n"
                f"<b>تفکیک تراکنش‌ها</b>\n{lines or 'تراکنشی ثبت نشده است.'}\n\n"
                "برای جزئیات هر تراکنش «آخرین تراکنش‌ها» را انتخاب کنید."), rows
    if section == "pasarguard":
        instances = (await session.scalars(select(PasarGuardInstance))).all()
        body = "\n".join(
            f"#{x.id} — {x.name} — {'🟢' if x.is_active else '🔴'}" for x in instances
        ) or "پنلی ثبت نشده است."
        return f"🔌 <b>PasarGuard</b>\n\n{body}", rows
    if section == "channel_locks":
        locks = (await session.scalars(select(ChannelLock).order_by(ChannelLock.display_order))).all()
        body = "\n".join(
            f"#{x.id} — {x.title} — {'✅' if x.is_active else '⛔'}" for x in locks
        ) or "قفل کانالی ثبت نشده است."
        return f"📣 <b>قفل کانال</b>\n\n{body}", rows
    if section == "admins":
        admins = (await session.scalars(select(BotAdmin))).all()
        customers = (await session.scalars(select(Customer).where(
            Customer.telegram_id.in_([x.telegram_id for x in admins])
        ))).all() if admins else []
        usernames = {customer.telegram_id: customer.username for customer in customers}
        body = "\n\n".join(f"{identity(x.telegram_id, usernames.get(x.telegram_id), x.display_name)}\n"
                           f"نقش: {html.escape(x.role_name)}" for x in admins)
        return f"🛡 <b>ادمین‌ها</b>\n\n{body}", rows
    if section == "reports":
        sold = await session.scalar(select(func.coalesce(func.sum(ResellerModel.total_quota_bytes), 0)))
        allocated = await session.scalar(select(func.coalesce(func.sum(ResellerModel.allocated_bytes), 0)))
        used = await session.scalar(select(func.coalesce(func.sum(ResellerModel.actual_used_bytes), 0)))
        from pasarbot.domain.quota import format_bytes

        return (
            "📈 <b>گزارش کل</b>\n\n"
            f"حجم فروخته‌شده: {format_bytes(int(sold or 0))}\n"
            f"حجم تخصیص‌یافته: {format_bytes(int(allocated or 0))}\n"
            f"مصرف واقعی: {format_bytes(int(used or 0))}",
            rows,
        )
    if section == "help":
        rows = [[ButtonSpec(label, callback_data=f"admin:help:{key}")]
                for label, key in ADMIN_REPLY_SECTIONS.items() if key != "help"]
        return "📚 <b>راهنمای همه بخش‌ها</b>\nهر موضوع را بزنید. متن هر صفحه وظیفه، تفاوت‌ها و روش کار را توضیح می‌دهد.", rows
    titles = {
        "locations": "🌍 مدیریت لوکیشن‌ها و Groupهای مجاز",
        "payments": "💳 پرداخت فعال: کارت‌به‌کارت و کیف پول",
        "receipt_groups": "👥 گروه بررسی رسیدها",
        "texts": "📝 متن‌های قابل تنظیم ربات",
        "buttons": "🎨 عنوان، رنگ و Custom Emoji دکمه‌ها",
        "broadcast": "📢 ارسال پیام به مشتریان فعال",
        "settings": "⚙️ تنظیمات عملیاتی امن",
        "help": "📚 راهنمای دکمه‌ای مدیریت",
        "support": "🎫 مدیریت تیکت‌های پشتیبانی",
    }
    return f"<b>{titles.get(section, section)}</b>\n\nعملیات موردنظر را از دکمه‌های زیر انتخاب کنید.", rows


@router.callback_query(F.data.startswith("admin:receipt:"))
async def show_receipt(
    callback: CallbackQuery,
    session: AsyncSession,
    settings: Settings,
    signer: CallbackSigner,
) -> None:
    if not callback.from_user or not callback.message:
        return
    permissions = await admin_permissions(session, settings, callback.from_user.id)
    if not {Permission.RECEIPT_APPROVE, Permission.RECEIPT_REJECT}.intersection(permissions):
        await callback.answer("مجوز ندارید.", show_alert=True)
        return
    receipt_id = int(callback.data.rsplit(":", 1)[1])
    receipt = await session.get(Receipt, receipt_id)
    if receipt is None:
        await callback.answer("رسید پیدا نشد.", show_alert=True)
        return
    markup = None
    if receipt.status == ReceiptStatus.PENDING.value:
        actions = []
        if Permission.RECEIPT_APPROVE in permissions:
            actions.append(ButtonSpec(
                    "✅ تأیید",
                    callback_data=signer.encode("receipt.approve", receipt.id, receipt.version),
                    style="success",
                ))
        if Permission.RECEIPT_REJECT in permissions:
            actions.append(ButtonSpec(
                    "❌ رد",
                    callback_data=signer.encode("receipt.reject", receipt.id, receipt.version),
                    style="danger",
                ))
        markup = inline_keyboard([actions], modern_features=settings.telegram_button_features)
    order = await session.get(Order, receipt.order_id)
    customer = await session.get(Customer, receipt.customer_id)
    plan = await session.get(PlanModel, order.plan_id) if order and order.plan_id else None
    created = f"{order.created_at:%Y-%m-%d %H:%M} UTC" if order and order.created_at else "—"
    caption = (
        f"🧾 <b>رسید #{receipt.id}</b> · {receipt.status}\n"
        f"🛒 سفارش #{order.id if order else '-'} · {html.escape(plan.name if plan else (order.order_type if order else '-'))}\n"
        f"💰 {money(order.payable_irt) if order else '-'}\n"
        f"{identity(customer.telegram_id, customer.username, customer.first_name) if customer else 'مشتری حذف شده'}\n"
        f"🕒 {created}"
    )
    await callback.message.answer_photo(receipt.telegram_file_id, caption=caption, reply_markup=markup)
    await callback.answer()


@router.callback_query(F.data.startswith("receipt."))
async def decide_receipt(
    callback: CallbackQuery,
    session: AsyncSession,
    settings: Settings,
    signer: CallbackSigner,
    bot: Bot,
) -> None:
    if not callback.from_user:
        return
    try:
        action, receipt_id, version = signer.decode(callback.data)
    except InvalidCallback:
        await callback.answer("درخواست نامعتبر است.", show_alert=True)
        return
    decision = ReceiptStatus.APPROVED if action == "receipt.approve" else ReceiptStatus.REJECTED
    required = Permission.RECEIPT_APPROVE if decision == ReceiptStatus.APPROVED else Permission.RECEIPT_REJECT
    permissions = await admin_permissions(session, settings, callback.from_user.id)
    if required not in permissions:
        await callback.answer("مجوز این عملیات را ندارید.", show_alert=True)
        return
    admin = await admin_row(session, callback.from_user.id)
    if admin is None:
        await callback.answer("پروفایل ادمین پیدا نشد.", show_alert=True)
        return
    receipt = await session.get(Receipt, receipt_id)
    if receipt is None or receipt.version != version:
        await callback.answer("این رسید قبلاً پردازش یا تغییر کرده است.", show_alert=True)
        return
    updated = await ReceiptRepository(session).decide_once(
        receipt_id, decision, admin.id, None
    )
    if updated is None:
        await callback.answer("این رسید قبلاً پردازش شده است.", show_alert=True)
        return
    customer = await session.get(Customer, updated.customer_id)
    order = await session.get(Order, updated.order_id)
    if decision == ReceiptStatus.APPROVED:
        if order is not None:
            session.add(
                AccountingEntry(
                    order_id=order.id,
                    customer_id=order.customer_id,
                    admin_id=admin.id,
                    transaction_type=order.order_type,
                    amount_irt=order.payable_irt,
                    status="completed",
                    payment_method="manual_receipt",
                    metadata_json={"receipt_id": updated.id},
                )
            )
    if customer:
        is_topup = bool(order and order.order_type == "wallet_topup")
        approved_default = ("✅ پرداخت تأیید شد؛ شارژ کیف پول در حال اعمال است."
                            if is_topup else "✅ پرداخت تأیید شد و ساخت/به‌روزرسانی نمایندگی آغاز شد.")
        message_text = await bot_copy(session,
            "receipt_approved" if decision == ReceiptStatus.APPROVED else "receipt_rejected",
            approved_default if decision == ReceiptStatus.APPROVED else
            "❌ رسید شما رد شد. برای بررسی بیشتر با پشتیبانی تماس بگیرید.")
        await bot.send_message(
            customer.telegram_id, message_text,
        )
    status_text = "✅ تأیید شد" if decision == ReceiptStatus.APPROVED else "❌ رد شد"
    if callback.message:
        caption = (callback.message.caption or f"رسید #{receipt_id}") + (
            f"\n\n{status_text}\nتوسط:\n"
            f"{identity(callback.from_user.id, callback.from_user.username, callback.from_user.full_name)}"
        )
        await callback.message.edit_caption(caption=caption, reply_markup=None)
    await callback.answer(status_text)
