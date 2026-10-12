from __future__ import annotations

import asyncio
import html
import logging
import re
import secrets
from decimal import Decimal, InvalidOperation

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from pasarbot.bot.admin_schema import (
    ADMIN_REPLY_SECTIONS,
    BUTTON_CHOICES,
    TEXT_CHOICES,
    WIZARD_DEFINITIONS,
    WizardDefinition,
    WizardField,
)
from pasarbot.bot.keyboards import inline_keyboard
from pasarbot.bot.presentation import identity
from pasarbot.config import Settings
from pasarbot.crypto import SecretBox
from pasarbot.db.models import (
    AddonPackage,
    BotAdmin,
    BotText,
    ButtonConfig,
    ChannelLock,
    Customer,
    Location,
    PasarGuardInstance,
    PaymentAccount,
    PaymentMethod,
    PlanModel,
    ResellerModel,
    RuntimeSetting,
    Ticket,
    TicketMessage,
    WalletEntry,
)
from pasarbot.domain.enums import LimitMode, Permission
from pasarbot.domain.quota import format_bytes
from pasarbot.domain.telegram import ButtonSpec, SafeTemplate
from pasarbot.integrations.pasarguard import HttpxTransport, PasarGuardClient, PasarGuardError
from pasarbot.security import stable_idempotency_key

from .admin import admin_permissions

router = Router(name="admin_wizard")
logger = logging.getLogger(__name__)
SELECTOR_FIELDS = frozenset({"instance_id", "group_ids", "location_ids", "template_ids",
                             "location_id", "from_plan_id", "to_plan_id"})
MULTI_SELECT_FIELDS = frozenset({"group_ids", "location_ids", "template_ids"})
SELECTOR_PAGE_SIZE = 8


class AdminWizardState(StatesGroup):
    collecting = State()
    confirming = State()


def field_is_active(field: WizardField, values: dict[str, object]) -> bool:
    if field.when_key is None:
        return True
    return str(values.get(field.when_key)) in field.when_values


def active_fields(definition: WizardDefinition, values: dict[str, object]) -> list[WizardField]:
    return [field for field in definition.fields if field_is_active(field, values)]


def next_field(
    definition: WizardDefinition,
    values: dict[str, object],
) -> WizardField | None:
    return next((field for field in definition.fields if field_is_active(field, values) and field.key not in values), None)


def parse_value(field: WizardField, raw: str) -> object:
    value = raw.strip()
    if not value and not field.optional:
        raise ValueError("این مقدار اجباری است.")
    if field.optional and value in {"", "-"}:
        return None
    if field.kind == "text" or field.kind == "choice":
        if len(value) > 4000:
            raise ValueError("متن بیش از حد طولانی است.")
        return value
    if field.kind in {"int", "positive_int", "optional_int"}:
        number = int(value.replace(",", ""))
        if field.kind == "positive_int" and number <= 0:
            raise ValueError("عدد باید بزرگ‌تر از صفر باشد.")
        return number
    if field.kind in {"gb", "optional_gb"}:
        amount = Decimal(value.replace(",", ""))
        if amount <= 0:
            raise ValueError("حجم باید بزرگ‌تر از صفر باشد.")
        return int(amount * (1024**3))
    if field.kind == "ids":
        if field.optional and value in {"", "-"}:
            return []
        result = [int(item.strip()) for item in value.split(",") if item.strip()]
        if not result and not field.optional:
            raise ValueError("حداقل یک شناسه لازم است.")
        if any(item <= 0 for item in result):
            raise ValueError("شناسه‌ها باید عدد مثبت باشند.")
        return sorted(set(result))
    if field.kind == "url":
        if not value.startswith("https://"):
            raise ValueError("آدرس باید با https:// شروع شود.")
        return value.rstrip("/")
    if field.kind == "card":
        digits = re.sub(r"\D", "", value)
        if len(digits) != 16:
            raise ValueError("شماره کارت باید ۱۶ رقم باشد.")
        return "-".join(digits[index : index + 4] for index in range(0, 16, 4))
    raise ValueError("نوع این فیلد پشتیبانی نمی‌شود.")


def display_value(field: WizardField, value: object) -> str:
    if field.secret and value:
        return "••••••••"
    if value is None or value == []:
        return "—"
    if field.kind in {"gb", "optional_gb"}:
        return f"{int(value) / (1024**3):g} GB"
    if field.kind == "choice":
        labels = {item_value: label for label, item_value in field.choices}
        return labels.get(str(value), str(value))
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value)


def bool_value(value: object) -> bool:
    return str(value).lower() == "true"


async def ensure_permission(
    session: AsyncSession,
    settings: Settings,
    telegram_id: int,
    permission: Permission,
) -> bool:
    return permission in await admin_permissions(session, settings, telegram_id)


async def selector_options(
    field_key: str, values: dict[str, object], session: AsyncSession, secret_box: SecretBox,
) -> list[tuple[int, str]]:
    if field_key == "instance_id":
        items = (await session.scalars(select(PasarGuardInstance).where(PasarGuardInstance.is_active.is_(True)).order_by(PasarGuardInstance.name))).all()
        return [(item.id, item.name) for item in items]
    if field_key in {"group_ids", "template_ids"}:
        instance_id = values.get("instance_id")
        instance = await session.get(PasarGuardInstance, int(instance_id)) if instance_id else None
        if instance is None or not instance.is_active:
            return []
        client = PasarGuardClient(instance.base_url, secret_box.decrypt(instance.encrypted_access_token),
                                 HttpxTransport(verify_tls=instance.verify_tls))
        items = await (client.list_groups() if field_key == "group_ids" else client.list_templates())
        return sorted([(int(item["id"]), str(item.get("name") or item.get("username") or f"#{item['id']}"))
                       for item in items if str(item.get("id", "")).isdigit()], key=lambda item: item[1])
    if field_key in {"location_ids", "location_id"}:
        query = select(Location).where(Location.is_active.is_(True))
        if values.get("instance_id"):
            query = query.where(Location.pasarguard_instance_id == int(values["instance_id"]))
        items = (await session.scalars(query.order_by(Location.name))).all()
        return [(item.id, f"{item.flag} {item.name}") for item in items]
    if field_key in {"from_plan_id", "to_plan_id"}:
        items = (await session.scalars(select(PlanModel).where(PlanModel.is_active.is_(True)).order_by(PlanModel.name))).all()
        if field_key == "to_plan_id" and values.get("from_plan_id"):
            source = await session.get(PlanModel, int(values["from_plan_id"]))
            items = [item for item in items if source and item.id != source.id and item.pasarguard_instance_id == source.pasarguard_instance_id]
        return [(item.id, item.name) for item in items]
    return []


async def show_selector(
    message: Message, state: FSMContext, settings: Settings, session: AsyncSession,
    secret_box: SecretBox, field: WizardField, text: str, *, page: int = 0, edit: bool = False,
) -> None:
    data = await state.get_data()
    values = dict(data.get("values", {}))
    try:
        options = await selector_options(field.key, values, session, secret_box)
    except PasarGuardError:
        await message.answer("⚠️ دریافت گزینه‌ها از PasarGuard ناموفق بود. اتصال پنل را بررسی و دوباره تلاش کنید.",
                             reply_markup=inline_keyboard([
                                 [ButtonSpec("🔄 تلاش مجدد", callback_data="admin:wizard_reload")],
                                 [ButtonSpec("❌ لغو", callback_data="admin:wizard_cancel")],
                             ], modern_features=settings.telegram_button_features))
        return
    selected = list(data.get("selector_selected", [])) if data.get("selector_field") == field.key else []
    if data.get("selector_field") != field.key:
        await state.update_data(selector_field=field.key, selector_selected=[])
    page = max(0, min(page, max(0, (len(options) - 1) // SELECTOR_PAGE_SIZE)))
    visible = options[page * SELECTOR_PAGE_SIZE:(page + 1) * SELECTOR_PAGE_SIZE]
    rows = [[ButtonSpec(("✅ " if option_id in selected else "▫️ ") + label[:48],
                        callback_data=f"admin:wizard_pick:{option_id}", style="primary")]
            for option_id, label in visible]
    if len(options) > SELECTOR_PAGE_SIZE:
        navigation = []
        if page > 0:
            navigation.append(ButtonSpec("⬅️ قبلی", callback_data=f"admin:wizard_page:{page - 1}"))
        if (page + 1) * SELECTOR_PAGE_SIZE < len(options):
            navigation.append(ButtonSpec("بعدی ➡️", callback_data=f"admin:wizard_page:{page + 1}"))
        rows.append(navigation)
    if field.key in MULTI_SELECT_FIELDS:
        rows.append([ButtonSpec("✅ ثبت انتخاب‌ها", callback_data="admin:wizard_done", style="success")])
    if field.optional:
        rows.append([ButtonSpec("⏭ رد کردن این مرحله", callback_data="admin:wizard_skip")])
    if field.key in dict(data.get("context", {})).get("existing_values", {}):
        rows.append([ButtonSpec("↪️ نگه‌داشتن مقدار فعلی", callback_data="admin:wizard_keep")])
    rows.append([ButtonSpec("❌ لغو", callback_data="admin:wizard_cancel", style="danger")])
    rendered = text + ("\n\nاز گزینه‌های زیر انتخاب کنید." if options else "\n\nموردی یافت نشد؛ ابتدا آن را در PasarGuard یا بات بسازید.")
    markup = inline_keyboard(rows, modern_features=settings.telegram_button_features)
    if edit:
        await message.edit_text(rendered, reply_markup=markup, parse_mode="HTML")
    else:
        await message.answer(rendered, reply_markup=markup, parse_mode="HTML")


@router.callback_query(F.data.startswith("admin:wizard:"))
async def start_wizard(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
    secret_box: SecretBox,
) -> None:
    if not callback.from_user:
        return
    parts = callback.data.split(":")
    flow_key = parts[2] if len(parts) > 2 else ""
    definition = WIZARD_DEFINITIONS.get(flow_key)
    if definition is None:
        await callback.answer("فرم پیدا نشد.", show_alert=True)
        return
    if not await ensure_permission(session, settings, callback.from_user.id, definition.permission):
        await callback.answer("مجوز این عملیات را ندارید.", show_alert=True)
        return
    context: dict[str, object] = {}
    values: dict[str, object] = {}
    if len(parts) > 3:
        target_id = int(parts[3])
        context["target_id"] = target_id
        context["existing_values"] = await load_existing_values(
            flow_key, target_id, session
        )
        if flow_key == "wallet_adjust":
            customer = await session.get(Customer, target_id)
            if customer is None:
                await callback.answer("مشتری پیدا نشد.", show_alert=True)
                return
            values["telegram_id"] = customer.telegram_id
    await state.set_state(AdminWizardState.collecting)
    await state.set_data({"flow": flow_key, "values": values, "context": context})
    await callback.answer()
    await prompt_next(callback.message, state, settings, session, secret_box)


async def prompt_next(message: Message | None, state: FSMContext, settings: Settings,
                      session: AsyncSession, secret_box: SecretBox) -> None:
    if message is None:
        return
    data = await state.get_data()
    definition = WIZARD_DEFINITIONS[data["flow"]]
    values = dict(data.get("values", {}))
    field = next_field(definition, values)
    if field is None:
        await show_confirmation(message, state, settings)
        return
    fields = active_fields(definition, values)
    completed = sum(item.key in values for item in fields)
    text = (
        f"🧩 <b>{definition.title}</b>\n"
        f"مرحله {completed + 1} از {len(fields)}\n\n"
        f"<b>{field.title}</b>\n{field.prompt}"
    )
    context = dict(data.get("context", {}))
    existing = dict(context.get("existing_values", {}))
    if field.key in existing:
        text += f"\n\nمقدار فعلی: <code>{html.escape(display_value(field, existing[field.key]))}</code>"
    if field.key in SELECTOR_FIELDS:
        await show_selector(message, state, settings, session, secret_box, field, text)
        return
    rows: list[list[ButtonSpec]] = []
    if field.choices:
        rows.extend(
            [ButtonSpec(label, callback_data=f"admin:wizard_choice:{value}", style="primary")]
            for label, value in field.choices
        )
    if field.optional:
        rows.append([ButtonSpec("⏭ رد کردن این مرحله", callback_data="admin:wizard_skip")])
    if field.key in existing:
        rows.append([ButtonSpec("↪️ نگه‌داشتن مقدار فعلی", callback_data="admin:wizard_keep")])
    rows.append([ButtonSpec("❌ لغو", callback_data="admin:wizard_cancel", style="danger")])
    await message.answer(
        text,
        reply_markup=inline_keyboard(rows, modern_features=settings.telegram_button_features),
    )


@router.callback_query(AdminWizardState.collecting, F.data.startswith("admin:wizard_choice:"))
async def wizard_choice(
    callback: CallbackQuery,
    state: FSMContext,
    settings: Settings,
    session: AsyncSession,
    secret_box: SecretBox,
) -> None:
    data = await state.get_data()
    definition = WIZARD_DEFINITIONS[data["flow"]]
    if not callback.from_user or not await ensure_permission(session, settings, callback.from_user.id, definition.permission):
        await callback.answer("مجوز ندارید.", show_alert=True)
        return
    values = dict(data.get("values", {}))
    field = next_field(definition, values)
    if field is None or field.kind != "choice":
        await callback.answer("این انتخاب دیگر معتبر نیست.", show_alert=True)
        return
    raw = callback.data.split(":", 2)[2]
    allowed = {value for _, value in field.choices}
    if raw not in allowed:
        await callback.answer("گزینه معتبر نیست.", show_alert=True)
        return
    values[field.key] = raw
    if field.key == "key" and definition.key in {"button", "bot_text"}:
        record = await session.get(ButtonConfig if definition.key == "button" else BotText, raw)
        if record:
            existing = ({"text": record.text, "style": record.style or "default",
                         "custom_emoji_id": record.custom_emoji_id}
                        if definition.key == "button" else {"value": record.value})
            context = dict(data.get("context", {}))
            context["existing_values"] = existing
            await state.update_data(context=context)
    await state.update_data(values=values)
    await callback.answer(f"{field.title} ثبت شد")
    await prompt_next(callback.message, state, settings, session, secret_box)


@router.callback_query(AdminWizardState.collecting, F.data.startswith("admin:wizard_pick:"))
async def wizard_pick(callback: CallbackQuery, state: FSMContext, settings: Settings,
                      session: AsyncSession, secret_box: SecretBox) -> None:
    data = await state.get_data()
    definition = WIZARD_DEFINITIONS.get(data.get("flow"))
    values = dict(data.get("values", {}))
    field = next_field(definition, values) if definition else None
    if not field or field.key not in SELECTOR_FIELDS or not callback.from_user or not await ensure_permission(session, settings, callback.from_user.id, definition.permission):
        await callback.answer("این انتخاب معتبر نیست.", show_alert=True)
        return
    option_id = int(callback.data.rsplit(":", 1)[1])
    try:
        options = await selector_options(field.key, values, session, secret_box)
    except PasarGuardError:
        await callback.answer("دریافت گزینه‌ها ناموفق بود.", show_alert=True)
        return
    if option_id not in {item_id for item_id, _ in options}:
        await callback.answer("این گزینه دیگر در دسترس نیست.", show_alert=True)
        return
    if field.key in MULTI_SELECT_FIELDS:
        selected = set(data.get("selector_selected", [])) if data.get("selector_field") == field.key else set()
        if option_id in selected:
            selected.remove(option_id)
        else:
            selected.add(option_id)
        await state.update_data(selector_field=field.key, selector_selected=sorted(selected))
        await callback.answer("انتخاب به‌روزرسانی شد")
        if callback.message:
            await show_selector(callback.message, state, settings, session, secret_box, field,
                                f"<b>{field.title}</b>", page=next((i // SELECTOR_PAGE_SIZE for i, x in enumerate(options) if x[0] == option_id), 0), edit=True)
        return
    values[field.key] = option_id
    await state.update_data(values=values, selector_field=None, selector_selected=[])
    await callback.answer("ثبت شد")
    await prompt_next(callback.message, state, settings, session, secret_box)


@router.callback_query(AdminWizardState.collecting, F.data == "admin:wizard_done")
async def wizard_done(callback: CallbackQuery, state: FSMContext, settings: Settings,
                      session: AsyncSession, secret_box: SecretBox) -> None:
    data = await state.get_data()
    definition = WIZARD_DEFINITIONS.get(data.get("flow"))
    values = dict(data.get("values", {}))
    field = next_field(definition, values) if definition else None
    if not field or field.key not in MULTI_SELECT_FIELDS or data.get("selector_field") != field.key or not callback.from_user or not await ensure_permission(session, settings, callback.from_user.id, definition.permission):
        await callback.answer("این انتخاب معتبر نیست.", show_alert=True)
        return
    selected = list(data.get("selector_selected", []))
    try:
        current = {item_id for item_id, _ in await selector_options(field.key, values, session, secret_box)}
    except PasarGuardError:
        await callback.answer("دریافت گزینه‌ها ناموفق بود.", show_alert=True)
        return
    if not set(selected).issubset(current) or (not selected and not field.optional):
        await callback.answer("حداقل یک گزینه معتبر انتخاب کنید.", show_alert=True)
        return
    values[field.key] = selected
    await state.update_data(values=values, selector_field=None, selector_selected=[])
    await callback.answer("انتخاب‌ها ثبت شد")
    await prompt_next(callback.message, state, settings, session, secret_box)


@router.callback_query(AdminWizardState.collecting, F.data.startswith("admin:wizard_page:"))
async def wizard_page(callback: CallbackQuery, state: FSMContext, settings: Settings,
                      session: AsyncSession, secret_box: SecretBox) -> None:
    data = await state.get_data()
    definition = WIZARD_DEFINITIONS.get(data.get("flow"))
    field = next_field(definition, dict(data.get("values", {}))) if definition else None
    if field and field.key in SELECTOR_FIELDS and callback.message and callback.from_user and await ensure_permission(session, settings, callback.from_user.id, definition.permission):
        await show_selector(callback.message, state, settings, session, secret_box, field,
                            f"<b>{field.title}</b>", page=int(callback.data.rsplit(":", 1)[1]), edit=True)
    await callback.answer()


@router.callback_query(AdminWizardState.collecting, F.data == "admin:wizard_reload")
async def wizard_reload(callback: CallbackQuery, state: FSMContext, settings: Settings,
                        session: AsyncSession, secret_box: SecretBox) -> None:
    data = await state.get_data()
    definition = WIZARD_DEFINITIONS.get(data.get("flow"))
    if definition is None or not callback.from_user or not await ensure_permission(session, settings, callback.from_user.id, definition.permission):
        await callback.answer("مجوز ندارید.", show_alert=True)
        return
    await callback.answer()
    await prompt_next(callback.message, state, settings, session, secret_box)


@router.callback_query(AdminWizardState.collecting, F.data == "admin:wizard_skip")
async def wizard_skip(callback: CallbackQuery, state: FSMContext, settings: Settings,
                      session: AsyncSession, secret_box: SecretBox) -> None:
    data = await state.get_data()
    definition = WIZARD_DEFINITIONS[data["flow"]]
    if not callback.from_user or not await ensure_permission(session, settings, callback.from_user.id, definition.permission):
        await callback.answer("مجوز ندارید.", show_alert=True)
        return
    values = dict(data.get("values", {}))
    field = next_field(definition, values)
    if field is None or not field.optional:
        await callback.answer("این مرحله قابل رد کردن نیست.", show_alert=True)
        return
    values[field.key] = None if field.kind != "ids" else []
    await state.update_data(values=values)
    await callback.answer("رد شد")
    await prompt_next(callback.message, state, settings, session, secret_box)


@router.callback_query(AdminWizardState.collecting, F.data == "admin:wizard_keep")
async def wizard_keep(callback: CallbackQuery, state: FSMContext, settings: Settings,
                      session: AsyncSession, secret_box: SecretBox) -> None:
    data = await state.get_data()
    definition = WIZARD_DEFINITIONS[data["flow"]]
    if not callback.from_user or not await ensure_permission(session, settings, callback.from_user.id, definition.permission):
        await callback.answer("مجوز ندارید.", show_alert=True)
        return
    values = dict(data.get("values", {}))
    context = dict(data.get("context", {}))
    existing = dict(context.get("existing_values", {}))
    field = next_field(definition, values)
    if field is None or field.key not in existing:
        await callback.answer("مقدار قبلی در دسترس نیست.", show_alert=True)
        return
    values[field.key] = existing[field.key]
    if definition.key == "button" and field.key == "text":
        values["custom_emoji_id"] = existing.get("custom_emoji_id")
    await state.update_data(values=values)
    await callback.answer("مقدار فعلی حفظ شد")
    await prompt_next(callback.message, state, settings, session, secret_box)


@router.message(AdminWizardState.collecting, F.text)
async def wizard_text(
    message: Message,
    state: FSMContext,
    settings: Settings,
    session: AsyncSession,
    secret_box: SecretBox,
) -> None:
    data = await state.get_data()
    definition = WIZARD_DEFINITIONS[data["flow"]]
    values = dict(data.get("values", {}))
    field = next_field(definition, values)
    if field is None:
        await show_confirmation(message, state, settings)
        return
    if field.choices or field.key in SELECTOR_FIELDS:
        await message.answer("برای این مرحله از دکمه‌های گزینه‌ها استفاده کنید. اگر دکمه‌ها بالاتر نیستند، از نو وارد فرم شوید.")
        return
    if message.text in ADMIN_REPLY_SECTIONS:
        await message.answer("فرم فعلی بسته شد. برای ساخت دوباره، از دکمهٔ بخش موردنظر استفاده کنید.")
        await state.clear()
        return
    try:
        entered = message.text or ""
        if definition.key == "button" and field.key == "text":
            custom = next((entity for entity in (message.entities or [])
                           if entity.type == "custom_emoji" and entity.custom_emoji_id), None)
            if custom:
                encoded = entered.encode("utf-16-le")
                entered = (encoded[:custom.offset * 2] + encoded[(custom.offset + custom.length) * 2:]).decode("utf-16-le").strip()
                values["custom_emoji_id"] = custom.custom_emoji_id
            else:
                values["custom_emoji_id"] = None
        values[field.key] = parse_value(field, entered)
    except (ValueError, InvalidOperation) as exc:
        await message.answer(f"⚠️ {exc}\nدوباره مقدار درست را بفرستید.")
        return
    if field.secret:
        try:
            await message.delete()
        except Exception as exc:
            logger.debug("could not delete secret wizard message: %s", type(exc).__name__)
    await state.update_data(values=values)
    await prompt_next(message, state, settings, session, secret_box)


async def show_confirmation(message: Message, state: FSMContext, settings: Settings) -> None:
    data = await state.get_data()
    definition = WIZARD_DEFINITIONS[data["flow"]]
    values = dict(data.get("values", {}))
    lines = [f"✅ <b>تأیید نهایی: {definition.title}</b>"]
    for field in definition.fields:
        if field.key in values:
            lines.append(f"• {field.title}: <code>{html.escape(display_value(field, values[field.key]))}</code>")
    if definition.key == "button" and values.get("custom_emoji_id"):
        lines.append("• ایموجی Custom از پیام شما شناسایی شد.")
    rows = [[
        ButtonSpec("✅ ارسال" if definition.key == "broadcast" else "✅ ذخیره",
                   callback_data="admin:wizard_save", style="success"),
        ButtonSpec("❌ لغو", callback_data="admin:wizard_cancel", style="danger"),
    ]]
    if definition.key == "broadcast":
        rows.insert(0, [ButtonSpec("📨 ارسال آزمایشی به خودم", callback_data="admin:broadcast_test")])
    await state.set_state(AdminWizardState.confirming)
    await message.answer(
        "\n".join(lines),
        reply_markup=inline_keyboard(rows, modern_features=settings.telegram_button_features),
    )


@router.callback_query(F.data == "admin:wizard_cancel")
async def wizard_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if callback.message:
        await callback.message.answer("❌ عملیات لغو شد؛ هیچ تغییری ذخیره نشد.")
    await callback.answer()


@router.callback_query(AdminWizardState.confirming, F.data == "admin:broadcast_test")
async def broadcast_test(callback: CallbackQuery, state: FSMContext, session: AsyncSession,
                         settings: Settings, bot: Bot) -> None:
    if not callback.from_user:
        return
    data = await state.get_data()
    if data.get("flow") != "broadcast" or not await ensure_permission(
        session, settings, callback.from_user.id, Permission.BROADCAST_SEND
    ):
        await callback.answer("مجوز ندارید.", show_alert=True)
        return
    try:
        await bot.send_message(callback.from_user.id, str(data["values"]["text"]), parse_mode=None)
    except Exception as exc:
        logger.warning("broadcast preview failed for %s: %s", callback.from_user.id, type(exc).__name__)
        await callback.answer("پیام آزمایشی نرسید؛ گفتگوی خصوصی ربات را با /start فعال کنید.", show_alert=True)
        return
    await callback.answer("پیام آزمایشی به گفتگوی خصوصی شما ارسال شد.", show_alert=True)


@router.callback_query(AdminWizardState.confirming, F.data == "admin:wizard_save")
async def wizard_save(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
    secret_box: SecretBox,
    bot: Bot,
) -> None:
    if not callback.from_user:
        return
    data = await state.get_data()
    definition = WIZARD_DEFINITIONS[data["flow"]]
    if not await ensure_permission(session, settings, callback.from_user.id, definition.permission):
        await callback.answer("مجوز شما تغییر کرده است.", show_alert=True)
        return
    try:
        result = await execute_wizard(
            definition.key,
            dict(data.get("values", {})),
            dict(data.get("context", {})),
            session,
            settings,
            secret_box,
            bot,
            callback.from_user.id,
        )
    except (ValueError, LookupError, PasarGuardError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await state.clear()
    await callback.answer("ذخیره شد")
    if callback.message:
        await callback.message.answer(f"✅ {result}")


async def execute_wizard(
    flow: str,
    values: dict[str, object],
    context: dict[str, object],
    session: AsyncSession,
    settings: Settings,
    secret_box: SecretBox,
    bot: Bot,
    actor_telegram_id: int,
) -> str:
    if flow == "quick_plan":
        chosen = list(values["location_ids"])
        locations = (await session.scalars(select(Location).where(Location.id.in_(chosen),
                              Location.is_active.is_(True),
                              Location.pasarguard_instance_id == int(values["instance_id"])))).all()
        if not chosen or len(locations) != len(set(chosen)):
            raise ValueError("حداقل یک لوکیشن فعال از همین پنل انتخاب کنید.")
        group_ids = sorted({group_id for location in locations for group_id in location.group_ids})
        if not group_ids:
            raise ValueError("لوکیشن انتخابی هیچ Group معتبری ندارد.")
        full_values = {
            "description": "", "renewal_price_irt": values["price_irt"],
            "min_user_quota_gb": None, "max_user_quota_gb": values.get("total_quota_gb"),
            "min_user_expire_days": None, "max_user_expire_days": None,
            "group_ids": group_ids, "template_ids": [], "force_template": "false",
            "hwid_enabled": "false", "max_devices": None, "reset_allowed": "false",
            "can_renew": "true", "can_upgrade": "true", "can_buy_volume": "true",
            "can_upgrade_location": "true", "is_visible": "true",
            **values,
        }
        return await execute_wizard("plan", full_values, context, session, settings,
                                    secret_box, bot, actor_telegram_id)
    if flow == "pasarguard":
        client = PasarGuardClient(
            base_url=str(values["base_url"]), access_token=str(values["token"]),
            transport=HttpxTransport(),
        )
        await client.preflight()
        instance = await session.get(PasarGuardInstance, int(context["target_id"])) if context.get("target_id") else None
        if instance is None:
            instance = PasarGuardInstance(name=str(values["name"]), base_url=str(values["base_url"]),
                dashboard_url=str(values["dashboard_url"]),
                encrypted_access_token=secret_box.encrypt(str(values["token"])))
            session.add(instance)
        else:
            instance.name = str(values["name"])
            instance.base_url = str(values["base_url"])
            instance.dashboard_url = str(values["dashboard_url"])
            instance.encrypted_access_token = secret_box.encrypt(str(values["token"]))
        await session.flush()
        return f"پنل PasarGuard با شناسه #{instance.id} متصل شد."
    if flow == "location":
        instance = await session.get(PasarGuardInstance, int(values["instance_id"]))
        if instance is None or not instance.is_active:
            raise LookupError("پنل PasarGuard انتخاب‌شده وجود ندارد.")
        client = PasarGuardClient(instance.base_url, secret_box.decrypt(instance.encrypted_access_token),
                                 HttpxTransport(verify_tls=instance.verify_tls))
        groups = await client.list_groups()
        if not set(values["group_ids"]).issubset({int(item["id"]) for item in groups}):
            raise ValueError("Group انتخابی در پنل PasarGuard موجود نیست.")
        location = await session.get(Location, int(context["target_id"])) if context.get("target_id") else None
        if location is None:
            location = Location(name=str(values["name"]), flag=str(values["flag"]), pasarguard_instance_id=instance.id, group_ids=list(values["group_ids"]))
            session.add(location)
        else:
            location.name = str(values["name"])
            location.flag = str(values["flag"])
            location.pasarguard_instance_id = instance.id
            location.group_ids = list(values["group_ids"])
        await session.flush()
        return f"لوکیشن #{location.id} ساخته شد."
    if flow == "plan":
        mode = LimitMode(str(values["limit_mode"]))
        instance = await session.get(PasarGuardInstance, int(values["instance_id"]))
        if instance is None or not instance.is_active:
            raise LookupError("پنل PasarGuard انتخاب‌شده وجود ندارد.")
        group_ids = list(values["group_ids"])
        template_ids = list(values.get("template_ids") or [])
        if bool_value(values["force_template"]) and not template_ids:
            raise ValueError("برای اجبار Template حداقل یک Template مجاز لازم است.")
        if values.get("min_user_quota_gb") and values.get("max_user_quota_gb") and int(values["min_user_quota_gb"]) > int(values["max_user_quota_gb"]):
            raise ValueError("حداقل حجم هر User از حداکثر بیشتر است.")
        if values.get("min_user_expire_days") and values.get("max_user_expire_days") and int(values["min_user_expire_days"]) > int(values["max_user_expire_days"]):
            raise ValueError("حداقل اعتبار هر User از حداکثر بیشتر است.")
        locations = (await session.scalars(select(Location).where(Location.id.in_(list(values.get("location_ids") or []))))).all()
        if len(locations) != len(set(values.get("location_ids") or [])) or any(item.pasarguard_instance_id != instance.id or not set(item.group_ids).issubset(group_ids) for item in locations):
            raise ValueError("لوکیشن انتخاب‌شده به این پنل تعلق ندارد یا Group آن مجاز نیست.")
        client = PasarGuardClient(instance.base_url, secret_box.decrypt(instance.encrypted_access_token), HttpxTransport(verify_tls=instance.verify_tls))
        groups = await client.list_groups()
        if not set(group_ids).issubset({int(item["id"]) for item in groups}):
            raise ValueError("Group ID انتخاب‌شده در PasarGuard وجود ندارد.")
        if template_ids:
            templates = {int(item["id"]): item for item in await client.list_templates()}
            if not set(template_ids).issubset(templates):
                raise ValueError("Template ID انتخاب‌شده در PasarGuard وجود ندارد.")
            if mode in (LimitMode.VOLUME_ONLY, LimitMode.USER_AND_VOLUME):
                quota = int(values["total_quota_gb"])
                maximum = min(quota, int(values["max_user_quota_gb"])) if values.get("max_user_quota_gb") else quota
                minimum = max(1, int(values["min_user_quota_gb"])) if values.get("min_user_quota_gb") else 1
                for template_id in template_ids:
                    template = templates[template_id]
                    limit = template.get("data_limit")
                    if limit is None or not minimum <= int(limit) <= maximum:
                        raise ValueError(f"Template #{template_id} حجم نامحدود یا خارج از سقف پلن دارد.")
        plan = await session.get(PlanModel, int(context["target_id"])) if context.get("target_id") else None
        if plan is None:
            plan = PlanModel(name=str(values["name"]), description="", price_irt=0, renewal_price_irt=0, duration_days=1, limit_mode=mode.value, total_quota_bytes=None, max_users=None, min_user_quota_bytes=None, max_user_quota_bytes=None, min_user_expire_days=None, max_user_expire_days=None, location_ids=[], group_ids=[], template_ids=[], pasarguard_instance_id=instance.id)
            session.add(plan)
        plan.name = str(values["name"])
        plan.description = str(values.get("description") or "")
        plan.price_irt = int(values["price_irt"])
        plan.renewal_price_irt = int(values["renewal_price_irt"])
        plan.duration_days = int(values["duration_days"])
        plan.limit_mode = mode.value
        plan.total_quota_bytes = int(values["total_quota_gb"]) if values.get("total_quota_gb") else None
        plan.max_users = int(values["max_users"]) if values.get("max_users") else None
        plan.min_user_quota_bytes = int(values["min_user_quota_gb"]) if values.get("min_user_quota_gb") else None
        plan.max_user_quota_bytes = int(values["max_user_quota_gb"]) if values.get("max_user_quota_gb") else None
        plan.min_user_expire_days = int(values["min_user_expire_days"]) if values.get("min_user_expire_days") else None
        plan.max_user_expire_days = int(values["max_user_expire_days"]) if values.get("max_user_expire_days") else None
        plan.location_ids = list(values.get("location_ids") or [])
        plan.group_ids = list(values["group_ids"])
        plan.template_ids = list(values.get("template_ids") or [])
        plan.force_template = bool_value(values["force_template"])
        plan.hwid_enabled = bool_value(values["hwid_enabled"])
        plan.max_devices = int(values["max_devices"]) if values.get("max_devices") else None
        plan.reset_allowed = bool_value(values["reset_allowed"])
        plan.can_renew = bool_value(values["can_renew"])
        plan.can_upgrade = bool_value(values["can_upgrade"])
        plan.can_buy_volume = bool_value(values["can_buy_volume"])
        plan.can_upgrade_location = bool_value(values["can_upgrade_location"])
        plan.is_visible = bool_value(values["is_visible"])
        plan.pasarguard_instance_id = instance.id
        await session.flush()
        return f"پلن #{plan.id} ساخته شد."
    if flow == "addon":
        package = AddonPackage(
            kind=str(values["kind"]),
            name=str(values["name"]),
            amount_irt=int(values["amount_irt"]),
            quota_bytes=int(values["quota_gb"]) if values.get("quota_gb") else None,
            duration_days=int(values["duration_days"]) if values.get("duration_days") else None,
            location_id=int(values["location_id"]) if values.get("location_id") else None,
            from_plan_id=int(values["from_plan_id"]) if values.get("from_plan_id") else None,
            to_plan_id=int(values["to_plan_id"]) if values.get("to_plan_id") else None,
        )
        session.add(package)
        await session.flush()
        return f"بسته #{package.id} ساخته شد."
    if flow == "card_account":
        method = await session.scalar(select(PaymentMethod).where(PaymentMethod.kind == "manual"))
        if method is None:
            method = PaymentMethod(
                kind="manual",
                title="کارت به کارت",
                instructions="مبلغ دقیق را واریز و تصویر رسید را ارسال کنید.",
                is_active=True,
            )
            session.add(method)
            await session.flush()
        account = await session.get(PaymentAccount, int(context["target_id"])) if context.get("target_id") else None
        if account is None:
            account = PaymentAccount(payment_method_id=method.id, title="", account_value="")
            session.add(account)
        account.payment_method_id = method.id
        account.title = str(values["title"])
        account.account_value = str(values["account_value"])
        account.holder_name = str(values["holder_name"])
        await session.flush()
        return f"کارت #{account.id} اضافه شد."
    if flow == "admin":
        admin = await session.get(BotAdmin, int(context["target_id"])) if context.get("target_id") else await session.scalar(select(BotAdmin).where(BotAdmin.telegram_id == int(values["telegram_id"])))
        if admin is None:
            admin = BotAdmin(
                telegram_id=int(values["telegram_id"]),
                role_name=str(values["role_name"]),
                display_name=str(values["display_name"]),
            )
            session.add(admin)
        else:
            admin.role_name = str(values["role_name"])
            admin.display_name = str(values["display_name"])
            admin.is_active = True
        await session.flush()
        return f"ادمین {admin.display_name} ذخیره شد."
    if flow == "channel":
        lock = await session.get(ChannelLock, int(context["target_id"])) if context.get("target_id") else None
        if lock is None:
            lock = ChannelLock(chat_id=str(values["chat_id"]), title="", join_url="")
            session.add(lock)
        lock.chat_id = str(values["chat_id"])
        lock.title = str(values["title"])
        lock.join_url = str(values["join_url"])
        lock.scopes = [str(values["scope"])]
        await session.flush()
        return f"قفل کانال #{lock.id} ساخته شد."
    if flow == "receipt_group":
        chat_id = int(values["chat_id"])
        record = await session.get(RuntimeSetting, "receipt_group_id")
        if record is None:
            session.add(RuntimeSetting(key="receipt_group_id", value={"chat_id": chat_id}))
        else:
            record.value = {"chat_id": chat_id}
        settings.receipt_group_id = chat_id
        return "گروه رسید ذخیره شد."
    if flow == "bot_text":
        key = str(values["key"])
        if key not in {item[1] for item in TEXT_CHOICES}:
            raise ValueError("بخش متن معتبر نیست.")
        variables = {"welcome": {"name"}, "wallet_intro": {"balance"},
                     "receipt_registered": {"receipt_id"}, "ticket_created": {"ticket_id"},
                     "ticket_reviewed": {"ticket_id"},
                     "ticket_answered": {"ticket_id", "reply"},
                     "panel_delivery": {"order_id", "panel_url", "panel_username", "panel_password"}}.get(key, set())
        if key != "manual_payment":
            SafeTemplate(str(values["value"]), variables)
        record = await session.get(BotText, key)
        if record is None:
            session.add(BotText(key=key, value=str(values["value"]), allowed_variables=[]))
        else:
            record.value = str(values["value"])
        return "متن ربات ذخیره شد."
    if flow == "button":
        key = str(values["key"])
        if key not in {item[1] for item in BUTTON_CHOICES}:
            raise ValueError("دکمه معتبر نیست.")
        if len(str(values["text"])) > 64:
            raise ValueError("عنوان دکمه حداکثر ۶۴ نویسه باشد.")
        if values["style"] not in {"default", "primary", "success", "danger"}:
            raise ValueError("رنگ دکمه معتبر نیست.")
        record = await session.get(ButtonConfig, key)
        style = None if values["style"] == "default" else str(values["style"])
        if record is None:
            session.add(
                ButtonConfig(
                    key=key,
                    text=str(values["text"]),
                    style=style,
                    custom_emoji_id=str(values["custom_emoji_id"]) if values.get("custom_emoji_id") else None,
                )
            )
        else:
            record.text = str(values["text"])
            record.style = style
            record.custom_emoji_id = str(values["custom_emoji_id"]) if values.get("custom_emoji_id") else None
        return "ظاهر دکمه ذخیره شد."
    if flow == "wallet_adjust":
        customer = await session.scalar(
            select(Customer)
            .where(Customer.telegram_id == int(values["telegram_id"]))
            .with_for_update()
        )
        if customer is None:
            raise LookupError("مشتری با این Telegram ID پیدا نشد.")
        amount = int(values["amount_irt"])
        new_balance = customer.wallet_balance_irt + amount
        if new_balance < 0:
            raise ValueError("موجودی نهایی نمی‌تواند منفی شود.")
        customer.wallet_balance_irt = new_balance
        session.add(
            WalletEntry(
                customer_id=customer.id,
                amount_irt=amount,
                balance_after_irt=new_balance,
                transaction_type="manual_adjustment",
                description=str(values["description"]),
                idempotency_key=stable_idempotency_key(
                    "manual-wallet", actor_telegram_id, customer.id, secrets.token_hex(8)
                ),
            )
        )
        await bot.send_message(customer.telegram_id, f"💰 موجودی کیف پول شما به {new_balance:,} تومان تغییر کرد.")
        return "موجودی کیف پول اصلاح شد."
    if flow == "broadcast":
        telegram_ids = set((
            await session.scalars(select(Customer.telegram_id).where(Customer.is_blocked.is_(False)))
        ).all())
        telegram_ids.add(actor_telegram_id)
        sent = failed = 0
        for telegram_id in sorted(telegram_ids):
            try:
                await bot.send_message(telegram_id, str(values["text"]), parse_mode=None)
                sent += 1
            except Exception as exc:
                logger.info("broadcast delivery failed for %s: %s", telegram_id, type(exc).__name__)
                failed += 1
            await asyncio.sleep(0.04)
        return f"ارسال همگانی تمام شد؛ موفق {sent}، ناموفق {failed}. پیام به شناسهٔ شما نیز ارسال شد."
    if flow == "customer_search":
        query = str(values["query"]).lstrip("@")
        conditions = [Customer.username.ilike(f"%{query}%")]
        if query.isdigit():
            conditions.append(Customer.telegram_id == int(query))
        customer = await session.scalar(select(Customer).where(or_(*conditions)).limit(1))
        if customer is None:
            raise LookupError("مشتری پیدا نشد.")
        await bot.send_message(
            actor_telegram_id,
            f"👤 مشتری #{customer.id}\n{identity(customer.telegram_id, customer.username, customer.first_name)}\n"
            f"کیف پول: {customer.wallet_balance_irt:,} تومان",
            reply_markup=inline_keyboard(
                [[ButtonSpec("مدیریت مشتری", callback_data=f"admin:detail:customer:{customer.id}", style="primary")]],
                modern_features=settings.telegram_button_features,
            ),
        )
        return "نتیجه جستجو نمایش داده شد."
    if flow == "reseller_search":
        query = str(values["query"])
        conditions = [ResellerModel.panel_username.ilike(f"%{query}%")]
        if query.isdigit():
            conditions.append(ResellerModel.id == int(query))
        reseller = await session.scalar(select(ResellerModel).where(or_(*conditions)).limit(1))
        if reseller is None:
            raise LookupError("نمایندگی پیدا نشد.")
        await bot.send_message(
            actor_telegram_id,
            f"🖥 نمایندگی #{reseller.id} — {reseller.status}",
            reply_markup=inline_keyboard(
                [[ButtonSpec("مدیریت نمایندگی", callback_data=f"admin:detail:reseller:{reseller.id}", style="primary")]],
                modern_features=settings.telegram_button_features,
            ),
        )
        return "نتیجه جستجو نمایش داده شد."
    if flow == "user_search":
        reseller = await session.get(ResellerModel, int(context["target_id"]))
        if reseller is None or not reseller.pasarguard_admin_id:
            raise LookupError("نمایندگی پیدا نشد.")
        instance = await session.get(PasarGuardInstance, reseller.pasarguard_instance_id)
        if instance is None:
            raise LookupError("پنل PasarGuard پیدا نشد.")
        client = PasarGuardClient(instance.base_url, secret_box.decrypt(instance.encrypted_access_token),
                                 HttpxTransport(verify_tls=instance.verify_tls))
        snapshot = await client.reseller_snapshot(str(reseller.pasarguard_admin_id))
        query = str(values["query"]).strip().lstrip("@").casefold()
        matching = [x for x in snapshot.users if str(x.id) == query or x.username.casefold() == query]
        if not matching:
            raise LookupError("User در همین نمایندگی پیدا نشد.")
        user = matching[0]
        await bot.send_message(actor_telegram_id,
            f"👤 <b>{html.escape(user.username)}</b> · ID <code>{html.escape(str(user.id))}</code>\n"
            f"🖥 نمایندگی #{reseller.id}\nحجم: {format_bytes(user.data_limit_bytes)}\n"
            f"مصرف: {format_bytes(user.used_bytes)}\nوضعیت: {html.escape(user.status)}\n"
            f"انقضا: {html.escape(str(user.expire_at or '—'))}",
            reply_markup=inline_keyboard([[ButtonSpec("↩️ نمایندگی", callback_data=f"admin:detail:reseller:{reseller.id}")]],
                                         modern_features=settings.telegram_button_features))
        return "نتیجه جستجو نمایش داده شد."
    if flow == "customer_message":
        target_id = int(context["target_id"])
        customer = await session.get(Customer, target_id)
        if customer is None:
            raise LookupError("مشتری پیدا نشد.")
        await bot.send_message(customer.telegram_id, str(values["text"]))
        return "پیام برای مشتری ارسال شد."
    if flow == "ticket_reply":
        ticket = await session.scalar(select(Ticket).where(Ticket.id == int(context["target_id"])).with_for_update())
        if ticket is None or ticket.status not in {"open", "reviewed"}:
            raise LookupError("تیکت پیدا نشد.")
        customer = await session.get(Customer, ticket.customer_id)
        if customer is None:
            raise LookupError("مشتری تیکت پیدا نشد.")
        session.add(
            TicketMessage(
                ticket_id=ticket.id,
                sender_type="admin",
                sender_id=actor_telegram_id,
                body=str(values["text"]),
            )
        )
        ticket.status = "answered"
        from pasarbot.bot.copy import bot_copy
        await bot.send_message(customer.telegram_id, await bot_copy(session, "ticket_answered",
            "🎫 پاسخ تیکت #{ticket_id}\n\n{reply}",
            ticket_id=ticket.id, reply=str(values["text"])))
        if ticket.group_chat_id and ticket.group_message_id:
            try:
                await bot.edit_message_reply_markup(ticket.group_chat_id, ticket.group_message_id,
                                                    reply_markup=None)
            except Exception as exc:
                logger.info("ticket group markup update failed: %s", type(exc).__name__)
        return "پاسخ تیکت ارسال شد."
    if flow == "sync_interval":
        seconds = int(values["seconds"])
        settings.sync_interval_seconds = seconds
        record = await session.get(RuntimeSetting, "sync_interval_seconds")
        if record is None:
            session.add(RuntimeSetting(key="sync_interval_seconds", value={"seconds": seconds}))
        else:
            record.value = {"seconds": seconds}
        return f"فاصله Sync روی {seconds // 60} دقیقه قرار گرفت."
    if flow == "quota_mode":
        mode = str(values["mode"])
        settings.quota_enforcement_mode = mode
        record = await session.get(RuntimeSetting, "quota_enforcement_mode")
        if record is None:
            session.add(RuntimeSetting(key="quota_enforcement_mode", value={"mode": mode}))
        else:
            record.value = {"mode": mode}
        return "رفتار عبور از سهمیه ذخیره شد."
    raise ValueError("عملیات این فرم تعریف نشده است.")


async def load_existing_values(
    flow: str,
    target_id: int,
    session: AsyncSession,
) -> dict[str, object]:
    if flow == "pasarguard":
        instance = await session.get(PasarGuardInstance, target_id)
        if instance is None:
            raise LookupError("پنل PasarGuard پیدا نشد.")
        return {"name": instance.name, "base_url": instance.base_url,
                "dashboard_url": instance.dashboard_url}
    if flow == "plan":
        item = await session.get(PlanModel, target_id)
        if item is None:
            raise LookupError("پلن پیدا نشد.")
        return {
            "name": item.name,
            "description": item.description,
            "price_irt": item.price_irt,
            "renewal_price_irt": item.renewal_price_irt,
            "duration_days": item.duration_days,
            "limit_mode": item.limit_mode,
            "total_quota_gb": item.total_quota_bytes,
            "max_users": item.max_users,
            "min_user_quota_gb": item.min_user_quota_bytes,
            "max_user_quota_gb": item.max_user_quota_bytes,
            "min_user_expire_days": item.min_user_expire_days,
            "max_user_expire_days": item.max_user_expire_days,
            "instance_id": item.pasarguard_instance_id,
            "location_ids": item.location_ids,
            "group_ids": item.group_ids,
            "template_ids": item.template_ids,
            "force_template": str(item.force_template).lower(),
            "hwid_enabled": str(item.hwid_enabled).lower(),
            "max_devices": item.max_devices,
            "reset_allowed": str(item.reset_allowed).lower(),
            "can_renew": str(item.can_renew).lower(),
            "can_upgrade": str(item.can_upgrade).lower(),
            "can_buy_volume": str(item.can_buy_volume).lower(),
            "can_upgrade_location": str(item.can_upgrade_location).lower(),
            "is_visible": str(item.is_visible).lower(),
        }
    if flow == "location":
        item = await session.get(Location, target_id)
        if item is None:
            raise LookupError("لوکیشن پیدا نشد.")
        return {"name": item.name, "flag": item.flag, "instance_id": item.pasarguard_instance_id, "group_ids": item.group_ids}
    if flow == "card_account":
        item = await session.get(PaymentAccount, target_id)
        if item is None:
            raise LookupError("کارت پیدا نشد.")
        return {"title": item.title, "account_value": item.account_value, "holder_name": item.holder_name}
    if flow == "admin":
        item = await session.get(BotAdmin, target_id)
        if item is None:
            raise LookupError("ادمین پیدا نشد.")
        return {"telegram_id": item.telegram_id, "role_name": item.role_name, "display_name": item.display_name}
    if flow == "channel":
        item = await session.get(ChannelLock, target_id)
        if item is None:
            raise LookupError("کانال پیدا نشد.")
        return {"chat_id": item.chat_id, "title": item.title, "join_url": item.join_url, "scope": item.scopes[0] if item.scopes else "all"}
    return {}
