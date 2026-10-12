from __future__ import annotations

import html
import logging
import secrets
from datetime import UTC, datetime

from aiogram import Bot, F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from pasarbot.bot.admin_schema import ACTIVE_PAYMENT_KINDS
from pasarbot.bot.copy import bot_copy, manual_payment_copy
from pasarbot.bot.keyboards import CUSTOMER_MENU, customer_menu, inline_keyboard
from pasarbot.bot.presentation import identity
from pasarbot.bot.theme import load_button_theme
from pasarbot.config import Settings
from pasarbot.crypto import SecretBox
from pasarbot.db.models import (
    AccountingEntry,
    AddonPackage,
    BotAdmin,
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
    Ticket,
    TicketMessage,
    WalletEntry,
)
from pasarbot.db.repositories import CustomerRepository
from pasarbot.domain.enums import OrderStatus, Permission, TransactionType
from pasarbot.domain.quota import format_bytes
from pasarbot.domain.telegram import ButtonSpec, RequiredChannel, check_channel_access
from pasarbot.integrations.pasarguard import HttpxTransport, PasarGuardClient, PasarGuardError
from pasarbot.security import CallbackSigner, stable_idempotency_key

router = Router(name="customer")
logger = logging.getLogger(__name__)


class PaymentFlow(StatesGroup):
    waiting_receipt = State()
    waiting_support_subject = State()
    waiting_user_search = State()
    waiting_topup_amount = State()


def money(value: int) -> str:
    return f"{value:,} تومان"


async def current_customer(session: AsyncSession, telegram_id: int) -> Customer | None:
    return await session.scalar(select(Customer).where(Customer.telegram_id == telegram_id))


async def available_payment_methods(session: AsyncSession) -> list[PaymentMethod]:
    active_manual_accounts = select(PaymentAccount.payment_method_id).where(
        PaymentAccount.is_active.is_(True)
    )
    return list(
        (
            await session.scalars(
                select(PaymentMethod)
                .where(
                    PaymentMethod.is_active.is_(True),
                    PaymentMethod.kind.in_(ACTIVE_PAYMENT_KINDS),
                    or_(
                        PaymentMethod.kind == "wallet",
                        PaymentMethod.id.in_(active_manual_accounts),
                    ),
                )
                .order_by(PaymentMethod.id)
            )
        ).all()
    )


@router.message(CommandStart())
async def start(message: Message, session: AsyncSession, settings: Settings, bot: Bot,
                state: FSMContext | None = None) -> None:
    user = message.from_user
    if user is None:
        return
    if state is not None:
        await state.clear()
    customer = await CustomerRepository(session).upsert_telegram_user(
        user.id, user.username, user.first_name or "", user.last_name or ""
    )
    if customer.is_blocked:
        await message.answer("⛔ حساب شما مسدود است. از بخش پشتیبانی با مدیریت تماس بگیرید.")
        return

    locks = (
        await session.scalars(
            select(ChannelLock)
            .where(ChannelLock.is_active.is_(True))
            .order_by(ChannelLock.display_order)
        )
    ).all()
    channels = tuple(
        RequiredChannel(lock.chat_id, lock.title, lock.join_url)
        for lock in locks
        if "all" in lock.scopes
    )
    if channels:
        class Membership:
            async def member_status(self, chat_id: str, user_id: int) -> str:
                return str((await bot.get_chat_member(chat_id=chat_id, user_id=user_id)).status)

        access = await check_channel_access(Membership(), user.id, channels)
        if not access.allowed:
            rows = [[ButtonSpec(f"📣 {item.title}", url=item.join_url)] for item in access.missing]
            rows.append([ButtonSpec("✅ بررسی عضویت", callback_data="customer:check_membership", style="success")])
            await message.answer(
                await bot_copy(session, "channel_lock", "برای استفاده از بات ابتدا در کانال‌های زیر عضو شوید:"),
                reply_markup=inline_keyboard(rows, modern_features=settings.telegram_button_features),
            )
            return
    await message.answer(
        await bot_copy(session, "welcome", "سلام {name} 👋\nاز منوی زیر انتخاب کنید:",
                       name=user.first_name or "دوست عزیز"),
        reply_markup=customer_menu(
            modern_features=settings.telegram_button_features,
            theme=await load_button_theme(session),
        ),
    )


@router.callback_query(F.data == "customer:check_membership")
async def check_membership(callback: CallbackQuery, session: AsyncSession,
                           settings: Settings, bot: Bot) -> None:
    if not callback.from_user or not callback.message:
        return
    locks = (await session.scalars(select(ChannelLock).where(ChannelLock.is_active.is_(True)))).all()
    channels = tuple(RequiredChannel(x.chat_id, x.title, x.join_url)
                     for x in locks if "all" in x.scopes)

    class Membership:
        async def member_status(self, chat_id: str, user_id: int) -> str:
            return str((await bot.get_chat_member(chat_id=chat_id, user_id=user_id)).status)

    result = await check_channel_access(Membership(), callback.from_user.id, channels)
    if not result.allowed:
        await callback.answer("هنوز عضویت همه کانال‌ها تأیید نشده است.", show_alert=True)
        return
    await callback.answer("عضویت تأیید شد.")
    await callback.message.answer("✅ از منوی زیر انتخاب کنید:", reply_markup=customer_menu(
        modern_features=settings.telegram_button_features,
        theme=await load_button_theme(session),
    ))


@router.callback_query(F.data == "customer:buy")
async def buy(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    if not callback.message:
        return
    await show_buy(callback.message, session, settings, edit=True)
    await callback.answer()


async def show_buy(message: Message, session: AsyncSession, settings: Settings, *, edit: bool = False) -> None:
    plans = (
        await session.scalars(
            select(PlanModel)
            .where(PlanModel.is_active.is_(True), PlanModel.is_visible.is_(True))
            .order_by(PlanModel.price_irt)
        )
    ).all()
    if not plans:
        text = "🛒 فعلاً پلن فعال و قابل نمایش وجود ندارد. مدیر باید از بخش «پلن‌ها» یک پلن بسازد و نمایش آن را روشن کند."
        if edit:
            await message.edit_text(text)
        else:
            await message.answer(text)
        return
    rows = [
        [
            ButtonSpec(
                f"{plan.name} — {money(plan.price_irt)}",
                callback_data=f"buy:plan:{plan.id}",
                style="success",
            )
        ]
        for plan in plans
    ]
    rows.append([ButtonSpec("↩️ منوی اصلی", callback_data="customer:home")])
    markup = inline_keyboard(rows, modern_features=settings.telegram_button_features)
    intro = await bot_copy(session, "buy_intro", "🛒 پلن موردنظر را انتخاب کنید:")
    if edit:
        await message.edit_text(intro, reply_markup=markup)
    else:
        await message.answer(intro, reply_markup=markup)


@router.callback_query(F.data.startswith("buy:plan:"))
async def select_plan(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    if not callback.message:
        return
    plan_id = int(callback.data.rsplit(":", 1)[1])
    plan = await session.get(PlanModel, plan_id)
    if plan is None or not plan.is_active or not plan.is_visible:
        await callback.answer("این پلن دیگر قابل خرید نیست.", show_alert=True)
        return
    methods = await available_payment_methods(session)
    if not methods:
        await callback.message.edit_text(
            "برای پرداخت، مدیر باید در بخش «پرداخت» یک کارت فعال ثبت کند یا موجودی کیف پول را شارژ کند.",
            reply_markup=inline_keyboard([[ButtonSpec("↩️ بازگشت", callback_data="customer:buy")]],
                                         modern_features=settings.telegram_button_features),
        )
        await callback.answer()
        return
    quota = format_bytes(plan.total_quota_bytes)
    users = "نامحدود" if plan.max_users is None else str(plan.max_users)
    text = (
        f"📦 <b>{html.escape(plan.name)}</b>\n\n{html.escape(plan.description or '')}\n\n"
        f"💰 قیمت: {money(plan.price_irt)}\n"
        f"📊 حجم کل: {quota}\n👥 سقف کاربر: {users}\n"
        f"📅 اعتبار: {plan.duration_days} روز\n\nروش پرداخت را انتخاب کنید:"
    )
    rows = [
        [ButtonSpec(method.title, callback_data=f"buy:pay:{plan.id}:{method.id}", style="primary")]
        for method in methods
    ]
    rows.append([ButtonSpec("↩️ بازگشت", callback_data="customer:buy")])
    await callback.message.edit_text(
        text,
        reply_markup=inline_keyboard(rows, modern_features=settings.telegram_button_features),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("buy:pay:"))
async def choose_payment(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
    settings: Settings,
) -> None:
    if not callback.from_user or not callback.message:
        return
    _, _, plan_text, method_text = callback.data.split(":")
    plan = await session.get(PlanModel, int(plan_text))
    method = await session.get(PaymentMethod, int(method_text))
    customer = await current_customer(session, callback.from_user.id)
    if (
        plan is None
        or method is None
        or customer is None
        or not plan.is_active
        or not plan.is_visible
        or not method.is_active
        or method.kind not in ACTIVE_PAYMENT_KINDS
    ):
        await callback.answer("انتخاب معتبر نیست.", show_alert=True)
        return
    pending = await session.scalar(
        select(Order).where(
            Order.customer_id == customer.id,
            Order.plan_id == plan.id,
            Order.order_type == TransactionType.INITIAL_SALE.value,
            Order.status == OrderStatus.AWAITING_PAYMENT.value,
        )
    )
    if pending is None:
        under_review = await session.scalar(select(Order).where(
            Order.customer_id == customer.id,
            Order.plan_id == plan.id,
            Order.order_type == TransactionType.INITIAL_SALE.value,
            Order.status == OrderStatus.PAYMENT_REVIEW.value,
        ))
        if under_review is not None:
            await callback.answer("رسید این پلن در حال بررسی است؛ نتیجه را منتظر بمانید.", show_alert=True)
            return
    order = pending or Order(
        customer_id=customer.id,
        plan_id=plan.id,
        order_type=TransactionType.INITIAL_SALE.value,
        status=OrderStatus.AWAITING_PAYMENT.value,
        amount_irt=plan.price_irt,
        discount_irt=0,
        payable_irt=plan.price_irt,
        idempotency_key=secrets.token_hex(32),
        metadata_json={"payment_method_id": method.id},
    )
    if pending is None:
        session.add(order)
        await session.flush()

    if method.kind == "wallet":
        customer = await session.scalar(
            select(Customer).where(Customer.id == customer.id).with_for_update()
        )
        if customer is None or customer.wallet_balance_irt < order.payable_irt:
            await callback.answer("موجودی کیف پول کافی نیست.", show_alert=True)
            return
        customer.wallet_balance_irt -= order.payable_irt
        order.status = OrderStatus.PAID.value
        session.add(
            WalletEntry(
                customer_id=customer.id,
                amount_irt=-order.payable_irt,
                balance_after_irt=customer.wallet_balance_irt,
                transaction_type=order.order_type,
                order_id=order.id,
                description=f"پرداخت سفارش #{order.id}",
                idempotency_key=stable_idempotency_key("wallet-order", order.id),
            )
        )
        session.add(
            AccountingEntry(
                order_id=order.id,
                customer_id=customer.id,
                transaction_type=order.order_type,
                amount_irt=order.payable_irt,
                status="completed",
                payment_method="wallet",
            )
        )
        await callback.message.edit_text(
            f"✅ پرداخت سفارش #{order.id} از کیف پول انجام شد. ساخت نمایندگی آغاز شد."
        )
        await callback.answer()
        return

    accounts = (
        await session.scalars(
            select(PaymentAccount).where(
                PaymentAccount.payment_method_id == method.id,
                PaymentAccount.is_active.is_(True),
            )
        )
    ).all()
    if not accounts:
        await callback.answer("هنوز کارت بانکی فعالی ثبت نشده است.", show_alert=True)
        return
    account_text = "\n\n".join([await manual_payment_copy(session, item, order) for item in accounts])
    await state.set_state(PaymentFlow.waiting_receipt)
    await state.update_data(order_id=order.id, payment_method_id=method.id)
    await callback.message.edit_text(
        f"🧾 سفارش #{order.id}\n💰 مبلغ: {money(order.payable_irt)}\n\n"
        f"{method.instructions}\n\n{account_text}\n\n"
        "پس از پرداخت، تصویر رسید را همین‌جا ارسال کنید.",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(PaymentFlow.waiting_receipt, F.photo)
async def receive_receipt(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
    bot: Bot,
    signer: CallbackSigner,
    settings: Settings,
) -> None:
    if not message.from_user or not message.photo:
        return
    data = await state.get_data()
    order_id = int(data["order_id"])
    order = await session.scalar(
        select(Order).where(Order.id == order_id).with_for_update()
    )
    customer = await current_customer(session, message.from_user.id)
    if order is None or customer is None or order.customer_id != customer.id or (
        order.status != OrderStatus.AWAITING_PAYMENT.value
    ):
        await state.clear()
        await message.answer("سفارش معتبر نیست.")
        return
    existing = await session.scalar(select(Receipt).where(Receipt.order_id == order.id))
    if existing:
        await state.clear()
        await message.answer("برای این سفارش قبلاً رسید ثبت شده است.")
        return
    receipt = Receipt(
        order_id=order.id,
        customer_id=customer.id,
        payment_method_id=int(data["payment_method_id"]),
        telegram_file_id=message.photo[-1].file_id,
    )
    session.add(receipt)
    order.status = OrderStatus.PAYMENT_REVIEW.value
    await session.flush()
    markup = inline_keyboard(
            [[
                ButtonSpec(
                    "✅ تأیید",
                    callback_data=signer.encode("receipt.approve", receipt.id, receipt.version),
                    style="success",
                ),
                ButtonSpec(
                    "❌ رد",
                    callback_data=signer.encode("receipt.reject", receipt.id, receipt.version),
                    style="danger",
                ),
            ]],
            modern_features=settings.telegram_button_features,
    )
    plan = await session.get(PlanModel, order.plan_id) if order.plan_id else None
    caption = (
        f"🧾 <b>رسید جدید #{receipt.id}</b>\n"
        f"{identity(customer.telegram_id, customer.username, customer.first_name)}\n"
        f"📦 {html.escape(plan.name if plan else order.order_type)} · سفارش #{order.id}\n"
        f"💰 {money(order.payable_irt)}\n🕒 {datetime.now(UTC):%Y-%m-%d %H:%M} UTC"
    )
    recipients = set(settings.owner_telegram_ids)
    admins = (await session.scalars(select(BotAdmin).where(BotAdmin.is_active.is_(True)))).all()
    from pasarbot.services.authorization import effective_permissions
    recipients.update(admin.telegram_id for admin in admins if Permission.RECEIPT_APPROVE in
                      effective_permissions(admin.telegram_id, settings.owner_telegram_ids,
                                            admin.role_name, admin.permissions))
    for recipient in recipients:
        try:
            await bot.send_photo(recipient, message.photo[-1].file_id, caption=caption,
                                 reply_markup=markup)
        except Exception as exc:
            logger.warning("receipt admin notification failed for %s: %s", recipient, type(exc).__name__)
    if settings.receipt_group_id:
        try:
            sent = await bot.send_photo(settings.receipt_group_id, receipt.telegram_file_id,
                                        caption=caption, reply_markup=markup)
            receipt.group_chat_id = sent.chat.id
            receipt.group_message_id = sent.message_id
        except Exception as exc:
            logger.warning("receipt group notification failed: %s", type(exc).__name__)
    await state.clear()
    await message.answer(await bot_copy(session, "receipt_registered",
        "✅ رسید #{receipt_id} ثبت شد و پس از بررسی نتیجه برای شما ارسال می‌شود.", receipt_id=receipt.id))


@router.message(PaymentFlow.waiting_receipt)
async def receipt_must_be_photo(message: Message) -> None:
    await message.answer("لطفاً رسید را به‌صورت عکس ارسال کنید.")


@router.callback_query(F.data == "customer:panels")
async def panels(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    if not callback.from_user or not callback.message:
        return
    await show_panels(callback.message, session, settings, callback.from_user.id, edit=True)
    await callback.answer()


async def show_panels(message: Message, session: AsyncSession, settings: Settings, telegram_id: int, *, edit: bool = False) -> None:
    customer = await current_customer(session, telegram_id)
    if customer is None:
        return
    items = (
        await session.scalars(
            select(ResellerModel)
            .where(ResellerModel.customer_id == customer.id, ResellerModel.status != "deleted")
            .order_by(ResellerModel.id.desc())
        )
    ).all()
    if not items:
        if edit:
            await message.edit_text("هنوز نمایندگی ندارید. از «خرید نمایندگی» در منوی پایین شروع کنید.")
        else:
            await message.answer("هنوز نمایندگی ندارید. از «خرید نمایندگی» در منوی پایین شروع کنید.")
        return
    rows = [
        [ButtonSpec(f"🖥 نمایندگی #{item.id}", callback_data=f"panel:view:{item.id}", style="primary")]
        for item in items
    ]
    rows.append([ButtonSpec("↩️ منوی اصلی", callback_data="customer:home")])
    markup = inline_keyboard(rows, modern_features=settings.telegram_button_features)
    intro = await bot_copy(session, "panels_intro", "نمایندگی‌های شما:")
    if edit:
        await message.edit_text(intro, reply_markup=markup)
    else:
        await message.answer(intro, reply_markup=markup)


@router.callback_query(F.data.startswith("panel:view:"))
async def panel_detail(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    if not callback.from_user or not callback.message:
        return
    panel_id = int(callback.data.rsplit(":", 1)[1])
    customer = await current_customer(session, callback.from_user.id)
    panel = await session.scalar(
        select(ResellerModel).where(
            ResellerModel.id == panel_id,
            ResellerModel.customer_id == (customer.id if customer else -1),
        )
    )
    if panel is None:
        await callback.answer("پنل پیدا نشد.", show_alert=True)
        return
    remaining = (
        None
        if panel.total_quota_bytes is None
        else max(panel.total_quota_bytes - panel.allocated_bytes, 0)
    )
    expiry = panel.expires_at.astimezone(UTC).strftime("%Y-%m-%d") if panel.expires_at else "نامحدود"
    text = (
        f"🖥 <b>نمایندگی #{panel.id}</b>\n\n"
        f"وضعیت: {panel.status}\n"
        f"ظرفیت: {format_bytes(panel.total_quota_bytes)}\n"
        f"اختصاص داده شده: {format_bytes(panel.allocated_bytes)}\n"
        f"باقی‌مانده: {format_bytes(remaining)}\n"
        f"مصرف واقعی: {format_bytes(panel.actual_used_bytes)}\n"
        f"تعداد User: {panel.users_count}\n"
        f"انقضا: {expiry}\n"
        f"آخرین Sync: {panel.last_synced_at or 'هنوز انجام نشده'}"
    )
    instance = await session.get(PasarGuardInstance, panel.pasarguard_instance_id)
    rows = [
        [ButtonSpec("📊 گزارش کامل", callback_data=f"panel:report:{panel.id}"),
         ButtonSpec("👥 کاربران من", callback_data=f"panel:users:{panel.id}")],
        [
            ButtonSpec("➕ افزایش حجم", callback_data=f"panel:add_volume:{panel.id}", style="success"),
            ButtonSpec("📅 تمدید", callback_data=f"panel:renew:{panel.id}", style="primary"),
        ],
        [
            ButtonSpec("⬆️ ارتقای پلن", callback_data=f"panel:upgrade:{panel.id}"),
            ButtonSpec("🌍 ارتقای لوکیشن", callback_data=f"panel:location:{panel.id}"),
        ],
        [ButtonSpec("↩️ پنل‌های من", callback_data="customer:panels")],
    ]
    if instance and instance.dashboard_url.startswith("https://"):
        rows.insert(-1, [ButtonSpec("🌐 ورود به PasarGuard", url=instance.dashboard_url)])
    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=inline_keyboard(rows, modern_features=settings.telegram_button_features),
    )
    await callback.answer()


async def owned_panel(session: AsyncSession, telegram_id: int, panel_id: int) -> ResellerModel | None:
    customer = await current_customer(session, telegram_id)
    return await session.scalar(select(ResellerModel).where(
        ResellerModel.id == panel_id,
        ResellerModel.customer_id == (customer.id if customer else -1),
        ResellerModel.status != "deleted",
    ))


@router.callback_query(F.data.startswith("panel:report:"))
async def panel_report(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    if not callback.from_user or not callback.message:
        return
    panel_id = int(callback.data.rsplit(":", 1)[1])
    panel = await owned_panel(session, callback.from_user.id, panel_id)
    if panel is None:
        await callback.answer("پنل پیدا نشد.", show_alert=True)
        return
    remaining = max(panel.total_quota_bytes - panel.allocated_bytes, 0) if panel.total_quota_bytes is not None else None
    percent = round(panel.allocated_bytes * 100 / panel.total_quota_bytes) if panel.total_quota_bytes else 0
    await callback.message.edit_text(
        f"📊 گزارش نمایندگی #{panel.id}\n\n👥 کاربران: {panel.users_count}"
        f"\n📦 ظرفیت: {format_bytes(panel.total_quota_bytes)}"
        f"\n📤 تخصیص‌یافته: {format_bytes(panel.allocated_bytes)}"
        f"\n📥 آزاد: {format_bytes(remaining)}"
        f"\n📊 نسبت تخصیص: {percent}%"
        f"\n🌐 مصرف واقعی: {format_bytes(panel.actual_used_bytes)}"
        f"\n🕒 آخرین همگام‌سازی: {panel.last_synced_at or 'هنوز انجام نشده'}",
        reply_markup=inline_keyboard([[ButtonSpec("↩️ بازگشت به نمایندگی", callback_data=f"panel:view:{panel.id}")]],
                                     modern_features=settings.telegram_button_features),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("panel:users:"))
async def panel_users(callback: CallbackQuery, session: AsyncSession, settings: Settings,
                      secret_box: SecretBox) -> None:
    if not callback.from_user or not callback.message:
        return
    panel_id = int(callback.data.rsplit(":", 1)[1])
    panel = await owned_panel(session, callback.from_user.id, panel_id)
    if panel is None or not panel.pasarguard_admin_id:
        await callback.answer("نمایندگی فعال پیدا نشد.", show_alert=True)
        return
    instance = await session.get(PasarGuardInstance, panel.pasarguard_instance_id)
    if instance is None:
        await callback.answer("پنل PasarGuard پیدا نشد.", show_alert=True)
        return
    client = PasarGuardClient(instance.base_url, secret_box.decrypt(instance.encrypted_access_token),
                             HttpxTransport(verify_tls=instance.verify_tls))
    try:
        snapshot = await client.reseller_snapshot(panel.pasarguard_admin_id)
    except PasarGuardError:
        await callback.answer("دریافت کاربران ناموفق بود؛ بعداً دوباره تلاش کنید.", show_alert=True)
        return
    lines = [f"👥 کاربران نمایندگی #{panel.id} ({snapshot.users_count})"]
    for user in snapshot.users[:30]:
        quota = "نامحدود ⚠️" if user.data_limit_bytes == 0 else format_bytes(user.data_limit_bytes)
        lines.append(f"\n• ID <code>{html.escape(str(user.id))}</code> · {html.escape(user.username)} — {quota}"
                     f" / مصرف {format_bytes(user.used_bytes)} — {html.escape(user.status)}")
    if snapshot.users_count > 30:
        lines.append("\nفقط ۳۰ مورد نخست نمایش داده شده است.")
    if not snapshot.users:
        lines.append("\nهنوز کاربری ندارد.")
    rows = [[ButtonSpec("🔎 جستجو با ID یا username", callback_data=f"panel:user_search:{panel.id}", style="primary")],
            [ButtonSpec("↩️ بازگشت", callback_data=f"panel:view:{panel.id}")]]
    await callback.message.edit_text("".join(lines), parse_mode="HTML",
        reply_markup=inline_keyboard(rows, modern_features=settings.telegram_button_features))
    await callback.answer()


@router.callback_query(F.data.startswith("panel:user_search:"))
async def panel_user_search_start(callback: CallbackQuery, state: FSMContext,
                                  session: AsyncSession) -> None:
    if not callback.from_user:
        return
    panel_id = int(callback.data.rsplit(":", 1)[1])
    if not await owned_panel(session, callback.from_user.id, panel_id):
        await callback.answer("پنل پیدا نشد.", show_alert=True)
        return
    await state.set_state(PaymentFlow.waiting_user_search)
    await state.update_data(panel_id=panel_id)
    if callback.message:
        await callback.message.answer("ID عددی یا username کاربرِ همین نمایندگی را بفرستید.")
    await callback.answer()


@router.message(PaymentFlow.waiting_user_search, F.text)
async def panel_user_search_result(message: Message, state: FSMContext,
                                   session: AsyncSession, secret_box: SecretBox,
                                   settings: Settings) -> None:
    if not message.from_user:
        return
    data = await state.get_data()
    panel = await owned_panel(session, message.from_user.id, int(data["panel_id"]))
    if not panel or not panel.pasarguard_admin_id:
        await state.clear()
        await message.answer("پنل در دسترس نیست.")
        return
    instance = await session.get(PasarGuardInstance, panel.pasarguard_instance_id)
    if instance is None:
        await message.answer("اتصال PasarGuard در دسترس نیست.")
        return
    client = PasarGuardClient(instance.base_url, secret_box.decrypt(instance.encrypted_access_token),
                             HttpxTransport(verify_tls=instance.verify_tls))
    try:
        snapshot = await client.reseller_snapshot(panel.pasarguard_admin_id)
    except PasarGuardError:
        await message.answer("دریافت کاربران ناموفق بود؛ دوباره تلاش کنید.")
        return
    query = (message.text or "").strip().lstrip("@").casefold()
    matches = [x for x in snapshot.users if str(x.id) == query or x.username.casefold() == query]
    await state.clear()
    if not matches:
        await message.answer("کاربری با این شناسه در نمایندگی شما پیدا نشد.")
        return
    user = matches[0]
    await message.answer(
        f"👤 <b>{html.escape(user.username)}</b> · ID <code>{html.escape(str(user.id))}</code>\n"
        f"وضعیت: {html.escape(user.status)}\n"
        f"حجم: {'نامحدود ⚠️' if user.data_limit_bytes == 0 else format_bytes(user.data_limit_bytes)}\n"
        f"مصرف: {format_bytes(user.used_bytes)}\n"
        f"مانده: {format_bytes(max(0, user.data_limit_bytes - user.used_bytes))}\n"
        f"انقضا: {html.escape(str(user.expire_at or '—'))}",
        reply_markup=inline_keyboard([[ButtonSpec("↩️ کاربران من", callback_data=f"panel:users:{panel.id}")]],
                                     modern_features=settings.telegram_button_features),
    )


@router.callback_query(F.data == "customer:wallet")
async def wallet(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    if not callback.from_user or not callback.message:
        return
    customer = await current_customer(session, callback.from_user.id)
    await callback.message.edit_text(
        await bot_copy(session, "wallet_intro",
            "💰 موجودی کیف پول: {balance}\nبرای شارژ مبلغ را انتخاب کنید و رسید کارت‌به‌کارت بفرستید.",
            balance=money(customer.wallet_balance_irt if customer else 0)),
        reply_markup=inline_keyboard([[ButtonSpec("➕ افزایش موجودی", callback_data="wallet:topup", style="success")]],
                                     modern_features=settings.telegram_button_features),
    )
    await callback.answer()


@router.callback_query(F.data == "wallet:topup")
async def wallet_topup(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(PaymentFlow.waiting_topup_amount)
    if callback.message:
        await callback.message.answer("مبلغ شارژ کیف پول را به تومان، فقط عدد و حداقل ۱۰٬۰۰۰ بفرستید.")
    await callback.answer()


@router.message(PaymentFlow.waiting_topup_amount, F.text)
async def wallet_amount(message: Message, state: FSMContext, session: AsyncSession,
                        settings: Settings) -> None:
    if not message.from_user:
        return
    try:
        amount = int((message.text or "").replace(",", ""))
    except ValueError:
        amount = 0
    if not 10_000 <= amount <= 1_000_000_000:
        await message.answer("مبلغ باید عددی بین ۱۰٬۰۰۰ و یک میلیارد تومان باشد.")
        return
    customer = await current_customer(session, message.from_user.id)
    if customer is None or customer.is_blocked:
        await message.answer("حساب شما فعال نیست.")
        await state.clear()
        return
    methods = [method for method in await available_payment_methods(session)
               if method.kind == "manual"]
    if not methods:
        await message.answer("کارت‌به‌کارت هنوز تنظیم نشده است.")
        await state.clear()
        return
    order = Order(customer_id=customer.id, order_type=TransactionType.WALLET_TOPUP.value,
                  status=OrderStatus.AWAITING_PAYMENT.value, amount_irt=amount,
                  discount_irt=0, payable_irt=amount, idempotency_key=secrets.token_hex(32),
                  metadata_json={})
    session.add(order)
    await session.flush()
    await state.clear()
    await message.answer(
        f"💰 شارژ {money(amount)} · سفارش #{order.id}\nروش پرداخت را انتخاب کنید:",
        reply_markup=inline_keyboard([
            [ButtonSpec(x.title, callback_data=f"order:pay:{order.id}:{x.id}", style="primary")]
            for x in methods
        ], modern_features=settings.telegram_button_features),
    )


ADDON_ACTIONS = {
    "add_volume": ("volume", TransactionType.EXTRA_VOLUME),
    "renew": ("renewal", TransactionType.RENEWAL),
    "upgrade": ("plan_upgrade", TransactionType.PLAN_UPGRADE),
    "location": ("location_upgrade", TransactionType.LOCATION_UPGRADE),
}


async def addon_eligible(
    session: AsyncSession, panel: ResellerModel, plan: PlanModel, package: AddonPackage
) -> bool:
    if not package.is_active or panel.status in {"deleted", "pending_creation", "error"}:
        return False
    if package.kind == "volume":
        return bool(plan.can_buy_volume and panel.total_quota_bytes is not None and
                    package.quota_bytes and package.quota_bytes > 0)
    if package.kind == "renewal":
        return bool(plan.can_renew and package.duration_days and package.duration_days > 0)
    if package.kind == "plan_upgrade":
        target = await session.get(PlanModel, package.to_plan_id) if package.to_plan_id else None
        return bool(plan.can_upgrade and package.from_plan_id in (None, panel.plan_id) and
                    target and target.is_active and target.id != panel.plan_id and
                    target.pasarguard_instance_id == panel.pasarguard_instance_id)
    if package.kind == "location_upgrade":
        location = await session.get(Location, package.location_id) if package.location_id else None
        return bool(plan.can_upgrade_location and location and location.is_active and
                    location.pasarguard_instance_id == panel.pasarguard_instance_id and
                    not set(location.group_ids).issubset(panel.group_ids))
    return False


@router.callback_query(F.data.startswith("panel:add_volume:") | F.data.startswith("panel:renew:") | F.data.startswith("panel:upgrade:") | F.data.startswith("panel:location:"))
async def panel_addons(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    if not callback.from_user or not callback.message:
        return
    _, action, panel_text = callback.data.split(":")
    kind, _ = ADDON_ACTIONS[action]
    customer = await current_customer(session, callback.from_user.id)
    panel = await session.scalar(
        select(ResellerModel).where(
            ResellerModel.id == int(panel_text),
            ResellerModel.customer_id == (customer.id if customer else -1),
        )
    )
    if panel is None:
        await callback.answer("نمایندگی پیدا نشد.", show_alert=True)
        return
    plan = await session.get(PlanModel, panel.plan_id)
    if plan is None:
        await callback.answer("پلن نمایندگی پیدا نشد.", show_alert=True)
        return
    packages = (
        await session.scalars(
            select(AddonPackage).where(
                AddonPackage.kind == kind,
                AddonPackage.is_active.is_(True),
            ).order_by(AddonPackage.amount_irt)
        )
    ).all()
    packages = [item for item in packages if await addon_eligible(session, panel, plan, item)]
    if not packages:
        await callback.answer("برای این عملیات بسته فعالی تعریف نشده است.", show_alert=True)
        return
    rows = [
        [ButtonSpec(
            f"{item.name} — {money(item.amount_irt)}",
            callback_data=f"addon:select:{panel.id}:{item.id}",
            style="success",
        )]
        for item in packages
    ]
    rows.append([ButtonSpec("↩️ بازگشت", callback_data=f"panel:view:{panel.id}")])
    await callback.message.edit_text(
        "بسته موردنظر را انتخاب کنید:",
        reply_markup=inline_keyboard(rows, modern_features=settings.telegram_button_features),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("addon:select:"))
async def select_addon(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    if not callback.from_user or not callback.message:
        return
    _, _, panel_text, package_text = callback.data.split(":")
    customer = await current_customer(session, callback.from_user.id)
    panel = await session.scalar(
        select(ResellerModel).where(
            ResellerModel.id == int(panel_text),
            ResellerModel.customer_id == (customer.id if customer else -1),
        )
    )
    package = await session.get(AddonPackage, int(package_text))
    plan = await session.get(PlanModel, panel.plan_id) if panel else None
    if panel is None or package is None or plan is None or not await addon_eligible(session, panel, plan, package):
        await callback.answer("بسته معتبر نیست.", show_alert=True)
        return
    type_by_kind = {
        "volume": TransactionType.EXTRA_VOLUME,
        "renewal": TransactionType.RENEWAL,
        "plan_upgrade": TransactionType.PLAN_UPGRADE,
        "location_upgrade": TransactionType.LOCATION_UPGRADE,
    }
    transaction_type = type_by_kind.get(package.kind)
    if transaction_type is None:
        await callback.answer("نوع بسته معتبر نیست.", show_alert=True)
        return
    metadata = {
        "addon_package_id": package.id,
        "quota_bytes": package.quota_bytes,
        "duration_days": package.duration_days,
        "location_id": package.location_id,
        "to_plan_id": package.to_plan_id,
    }
    order = Order(
        customer_id=customer.id,
        reseller_id=panel.id,
        plan_id=panel.plan_id,
        order_type=transaction_type.value,
        status=OrderStatus.AWAITING_PAYMENT.value,
        amount_irt=package.amount_irt,
        discount_irt=0,
        payable_irt=package.amount_irt,
        idempotency_key=secrets.token_hex(32),
        metadata_json=metadata,
    )
    session.add(order)
    await session.flush()
    methods = await available_payment_methods(session)
    rows = [[ButtonSpec(x.title, callback_data=f"order:pay:{order.id}:{x.id}", style="primary")] for x in methods]
    await callback.message.edit_text(
        f"{package.name}\nمبلغ: {money(package.amount_irt)}\nروش پرداخت:",
        reply_markup=inline_keyboard(rows, modern_features=settings.telegram_button_features),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("order:pay:"))
async def pay_existing_order(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    if not callback.from_user or not callback.message:
        return
    _, _, order_text, method_text = callback.data.split(":")
    customer = await current_customer(session, callback.from_user.id)
    order = await session.scalar(
        select(Order).where(
            Order.id == int(order_text),
            Order.customer_id == (customer.id if customer else -1),
        ).with_for_update()
    )
    method = await session.get(PaymentMethod, int(method_text))
    if (
        order is None
        or method is None
        or method.kind not in ACTIVE_PAYMENT_KINDS
        or not method.is_active
        or order.status != OrderStatus.AWAITING_PAYMENT.value
        or (order.order_type == TransactionType.WALLET_TOPUP.value and method.kind != "manual")
    ):
        await callback.answer("سفارش قابل پرداخت نیست.", show_alert=True)
        return
    if method.kind == "wallet":
        locked_customer = await session.scalar(
            select(Customer).where(Customer.id == customer.id).with_for_update()
        )
        if locked_customer is None or locked_customer.wallet_balance_irt < order.payable_irt:
            await callback.answer("موجودی کیف پول کافی نیست.", show_alert=True)
            return
        locked_customer.wallet_balance_irt -= order.payable_irt
        order.status = OrderStatus.PAID.value
        session.add(
            WalletEntry(
                customer_id=locked_customer.id,
                amount_irt=-order.payable_irt,
                balance_after_irt=locked_customer.wallet_balance_irt,
                transaction_type=order.order_type,
                order_id=order.id,
                description=f"پرداخت سفارش #{order.id}",
                idempotency_key=stable_idempotency_key("wallet-order", order.id),
            )
        )
        session.add(
            AccountingEntry(
                order_id=order.id,
                customer_id=locked_customer.id,
                transaction_type=order.order_type,
                amount_irt=order.payable_irt,
                status="completed",
                payment_method="wallet",
            )
        )
        await callback.message.edit_text("✅ پرداخت انجام شد؛ عملیات پس از پردازش اعمال می‌شود.")
        await callback.answer()
        return
    accounts = (
        await session.scalars(
            select(PaymentAccount).where(
                PaymentAccount.payment_method_id == method.id,
                PaymentAccount.is_active.is_(True),
            )
        )
    ).all()
    if not accounts:
        await callback.answer("هنوز کارت بانکی فعالی ثبت نشده است.", show_alert=True)
        return
    details = "\n\n".join([await manual_payment_copy(session, x, order) for x in accounts])
    await state.set_state(PaymentFlow.waiting_receipt)
    await state.update_data(order_id=order.id, payment_method_id=method.id)
    await callback.message.edit_text(
        f"سفارش #{order.id}\nمبلغ: {money(order.payable_irt)}\n\n{method.instructions}\n{details}\n\nتصویر رسید را ارسال کنید."
    )
    await callback.answer()


@router.callback_query(F.data == "customer:support")
async def support_start(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    if callback.message and callback.from_user:
        await show_support(callback.message, session, settings, callback.from_user.id, edit=True)
    await callback.answer()


async def show_support(message: Message, session: AsyncSession, settings: Settings,
                       telegram_id: int, *, edit: bool = False) -> None:
    customer = await current_customer(session, telegram_id)
    if customer is None:
        return
    tickets = (await session.scalars(select(Ticket).where(Ticket.customer_id == customer.id)
               .order_by(Ticket.id.desc()).limit(10))).all()
    rows = [[ButtonSpec("➕ ثبت تیکت جدید", callback_data="support:new", style="success")]]
    rows.extend([[ButtonSpec(f"🎫 #{ticket.id} {ticket.subject[:30]} — {ticket.status}",
                              callback_data=f"support:view:{ticket.id}")]
                 for ticket in tickets])
    rows.append([ButtonSpec("↩️ منوی اصلی", callback_data="customer:home")])
    body = await bot_copy(session, "support_intro",
        "🎫 <b>پشتیبانی</b>\n\nتیکت جدید بسازید یا وضعیت تیکت‌های قبلی را باز کنید.")
    if edit:
        await message.edit_text(body, reply_markup=inline_keyboard(rows,
                                modern_features=settings.telegram_button_features))
    else:
        await message.answer(body, reply_markup=inline_keyboard(rows,
                             modern_features=settings.telegram_button_features))


@router.callback_query(F.data == "support:new")
async def support_new(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(PaymentFlow.waiting_support_subject)
    if callback.message:
        await callback.message.answer("موضوع و شرح درخواست را در یک پیام بفرستید. برای لغو /start را بزنید.")
    await callback.answer()


@router.callback_query(F.data.startswith("support:view:"))
async def support_view(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    if not callback.from_user or not callback.message:
        return
    customer = await current_customer(session, callback.from_user.id)
    ticket = await session.get(Ticket, int(callback.data.rsplit(":", 1)[1]))
    if not customer or not ticket or ticket.customer_id != customer.id:
        await callback.answer("تیکت پیدا نشد.", show_alert=True)
        return
    entries = (await session.scalars(select(TicketMessage).where(TicketMessage.ticket_id == ticket.id)
               .order_by(TicketMessage.id.desc()).limit(10))).all()
    history = "\n\n".join(f"{'🛡 پشتیبانی' if x.sender_type == 'admin' else '👤 شما'}: "
                          f"{html.escape(x.body[:700])}" for x in reversed(entries))
    await callback.message.edit_text(
        f"🎫 <b>تیکت #{ticket.id}</b>\nموضوع: {html.escape(ticket.subject)}\n"
        f"وضعیت: {ticket.status}\n\n{history}",
        reply_markup=inline_keyboard([[ButtonSpec("↩️ تیکت‌های من", callback_data="customer:support")]],
                                     modern_features=settings.telegram_button_features),
    )
    await callback.answer()


@router.message(PaymentFlow.waiting_support_subject, F.text)
async def support_create(message: Message, session: AsyncSession, state: FSMContext,
                         bot: Bot, settings: Settings) -> None:
    if not message.from_user or not message.text:
        return
    customer = await current_customer(session, message.from_user.id)
    if customer is None:
        await state.clear()
        return
    if customer.is_blocked:
        await message.answer("حساب شما مسدود است.")
        await state.clear()
        return
    body = message.text.strip()
    if len(body) < 5:
        await message.answer("شرح درخواست را در دست‌کم ۵ نویسه بفرستید.")
        return
    ticket = Ticket(customer_id=customer.id, subject=body[:200])
    session.add(ticket)
    await session.flush()
    session.add(
        TicketMessage(
            ticket_id=ticket.id,
            sender_type="customer",
            sender_id=message.from_user.id,
            body=body[:4000],
        )
    )
    await state.clear()
    if settings.receipt_group_id:
        try:
            sent = await bot.send_message(
                settings.receipt_group_id,
                f"🎫 <b>تیکت جدید #{ticket.id}</b>\n"
                f"{identity(customer.telegram_id, customer.username, customer.first_name)}\n"
                f"📝 {html.escape(body[:1500])}",
                reply_markup=inline_keyboard([[
                    ButtonSpec("✅ بررسی شد", callback_data=f"ticket:review:{ticket.id}", style="success"),
                    ButtonSpec("✍️ بررسی و پاسخ", callback_data=f"ticket:reply:{ticket.id}", style="primary"),
                ]], modern_features=settings.telegram_button_features),
            )
            ticket.group_chat_id, ticket.group_message_id = sent.chat.id, sent.message_id
        except Exception as exc:
            logger.warning("ticket group notification failed: %s", type(exc).__name__)
    await message.answer(await bot_copy(session, "ticket_created",
        "✅ تیکت #{ticket_id} ثبت شد. از دکمه پشتیبانی می‌توانید وضعیت و پاسخ آن را ببینید.",
        ticket_id=ticket.id))


@router.callback_query(F.data == "customer:orders")
async def orders(callback: CallbackQuery, session: AsyncSession) -> None:
    if not callback.from_user or not callback.message:
        return
    customer = await current_customer(session, callback.from_user.id)
    await callback.message.edit_text(await order_summary(session, customer.id if customer else -1))
    await callback.answer()


async def order_summary(session: AsyncSession, customer_id: int) -> str:
    items = (
        await session.scalars(
            select(Order).where(Order.customer_id == customer_id).order_by(Order.id.desc()).limit(20)
        )
    ).all()
    text = await bot_copy(session, "orders_intro", "🧾 آخرین سفارش‌ها") + "\n\n" + ("\n".join(
        f"#{item.id} — {money(item.payable_irt)} — {item.status}" for item in items
    ) or "سفارشی وجود ندارد.")
    return text


CUSTOMER_HELP_TEXT = (
    "📚 راهنمای سریع\n\n"
    "۱) خرید نمایندگی را بزنید.\n۲) پلن و روش پرداخت را انتخاب کنید.\n"
    "۳) در پرداخت دستی تصویر رسید را بفرستید.\n"
    "۴) پس از تأیید، اطلاعات ورود خودکار تحویل می‌شود.\n"
    "۵) مصرف و ظرفیت را از «پنل‌های من» ببینید."
)


@router.callback_query(F.data == "customer:help")
async def customer_help(callback: CallbackQuery, session: AsyncSession) -> None:
    if callback.message:
        await callback.message.edit_text(await bot_copy(session, "customer_help", CUSTOMER_HELP_TEXT))
    await callback.answer()


@router.callback_query(F.data == "customer:home")
async def home(callback: CallbackQuery, settings: Settings, session: AsyncSession) -> None:
    if callback.message:
        await callback.message.answer("منوی اصلی:", reply_markup=customer_menu(
            modern_features=settings.telegram_button_features,
            theme=await load_button_theme(session),
        ))
    await callback.answer()


@router.message(F.text)
async def customer_reply_action(
    message: Message, session: AsyncSession, settings: Settings, state: FSMContext,
) -> None:
    theme = await load_button_theme(session)
    matching = [key for key, label, _ in CUSTOMER_MENU
                if message.text == (theme[key].text if key in theme else label)]
    if not matching:
        raise SkipHandler()
    if not message.from_user:
        return
    customer = await current_customer(session, message.from_user.id)
    if customer is None or customer.is_blocked:
        await message.answer("برای شروع /start را بزنید.")
        return
    await state.clear()
    action = matching[0]
    if action == "customer.buy":
        await show_buy(message, session, settings)
    elif action == "customer.panels":
        await show_panels(message, session, settings, message.from_user.id)
    elif action == "customer.wallet":
        await message.answer(
            await bot_copy(session, "wallet_intro",
                "💰 موجودی کیف پول: {balance}\nبرای شارژ، دکمه زیر را بزنید.",
                balance=money(customer.wallet_balance_irt)),
            reply_markup=inline_keyboard([
                [ButtonSpec("➕ افزایش موجودی", callback_data="wallet:topup", style="success")]
            ], modern_features=settings.telegram_button_features),
        )
    elif action == "customer.orders":
        await message.answer(await order_summary(session, customer.id))
    elif action == "customer.support":
        await show_support(message, session, settings, message.from_user.id)
    elif action == "customer.help":
        await message.answer(await bot_copy(session, "customer_help", CUSTOMER_HELP_TEXT))
