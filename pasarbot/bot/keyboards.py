from __future__ import annotations

from collections.abc import Iterable

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from pasarbot.bot.admin_schema import ADMIN_REPLY_SECTIONS
from pasarbot.bot.theme import themed
from pasarbot.domain.telegram import ButtonSpec


def inline_button(spec: ButtonSpec, *, modern_features: bool = True) -> InlineKeyboardButton:
    return InlineKeyboardButton(**spec.payload(modern_features=modern_features))


def inline_keyboard(
    rows: Iterable[Iterable[ButtonSpec]], *, modern_features: bool = True
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [inline_button(item, modern_features=modern_features) for item in row] for row in rows
        ]
    )


def reply_button(
    text: str,
    *,
    style: str | None = None,
    custom_emoji_id: str | None = None,
    modern_features: bool = True,
) -> KeyboardButton:
    payload: dict[str, str] = {"text": text}
    if modern_features and style:
        payload["style"] = style
    if modern_features and custom_emoji_id:
        payload["icon_custom_emoji_id"] = custom_emoji_id
    return KeyboardButton(**payload)


CUSTOMER_MENU = (
    ("customer.buy", "🛒 خرید نمایندگی", "success"),
    ("customer.panels", "🖥 پنل‌های من", "primary"),
    ("customer.wallet", "💰 کیف پول", None),
    ("customer.orders", "🧾 سفارش‌های من", None),
    ("customer.support", "🎫 پشتیبانی", None),
    ("customer.help", "📚 راهنما", None),
)


def customer_menu(
    *, modern_features: bool = True, theme: dict[str, ButtonSpec] | None = None
) -> ReplyKeyboardMarkup:
    buttons = []
    for key, label, style in CUSTOMER_MENU:
        visual = themed(theme, key, ButtonSpec(label, callback_data="customer:menu", style=style))
        buttons.append(reply_button(visual.text, style=visual.style,
                                    custom_emoji_id=visual.custom_emoji_id,
                                    modern_features=modern_features))
    return ReplyKeyboardMarkup(
        keyboard=[buttons[0:1], buttons[1:3], buttons[3:5], buttons[5:6]],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="از منوی پایین انتخاب کنید",
    )


def admin_menu(
    *, modern_features: bool = True, theme: dict[str, ButtonSpec] | None = None
) -> InlineKeyboardMarkup:
    sections = (
        ("📊 داشبورد", "dashboard"),
        ("📦 پلن‌ها", "plans"),
        ("🌍 لوکیشن‌ها", "locations"),
        ("👤 مشتریان", "customers"),
        ("🖥 نمایندگی‌ها", "resellers"),
        ("🎫 مدیریت تیکت‌ها", "support"),
        ("🛒 سفارش‌ها", "orders"),
        ("🧾 رسیدها", "receipts"),
        ("💳 پرداخت‌ها", "payments"),
        ("💰 کیف پول و حسابداری", "accounting"),
        ("📈 گزارش‌ها", "reports"),
        ("👥 گروه رسیدها", "receipt_groups"),
        ("📣 قفل کانال", "channel_locks"),
        ("🛡 ادمین‌ها", "admins"),
        ("📝 متن‌ها", "texts"),
        ("🎨 دکمه‌ها", "buttons"),
        ("📢 ارسال همگانی", "broadcast"),
        ("🔌 PasarGuard", "pasarguard"),
        ("⚙️ تنظیمات", "settings"),
        ("📚 راهنمای کامل", "help"),
    )
    rows = [
        [themed(theme, f"admin.{key}", ButtonSpec(title, callback_data=f"admin:{key}", style="primary" if i == 0 else None))]
        for i, (title, key) in enumerate(sections)
    ]
    return inline_keyboard(rows, modern_features=modern_features)


def admin_reply_menu(*, modern_features: bool = True,
                     theme: dict[str, ButtonSpec] | None = None) -> ReplyKeyboardMarkup:
    labels = list(ADMIN_REPLY_SECTIONS)
    rows: list[list[KeyboardButton]] = []
    styles = {
        "📊 داشبورد": "primary",
        "🧾 رسیدها": "danger",
        "📦 پلن‌ها": "success",
        "💳 پرداخت": "success",
        "📢 ارسال همگانی": "danger",
        "⚙️ تنظیمات": "primary",
    }
    for index in range(0, len(labels), 2):
        row = []
        for label in labels[index : index + 2]:
            visual = themed(theme, f"admin.{ADMIN_REPLY_SECTIONS[label]}",
                            ButtonSpec(label, callback_data="admin:menu", style=styles.get(label)))
            row.append(reply_button(visual.text, style=visual.style,
                                    custom_emoji_id=visual.custom_emoji_id,
                                    modern_features=modern_features))
        rows.append(row)
    rows.append(
        [
            reply_button(
                "🚪 خروج از مدیریت",
                style="danger",
                modern_features=modern_features,
            )
        ]
    )
    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="یک بخش مدیریتی را انتخاب کنید",
    )
