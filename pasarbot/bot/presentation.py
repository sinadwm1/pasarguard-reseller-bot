"""Consistent compact HTML cards for Telegram management screens."""
from __future__ import annotations

import html
from datetime import UTC, datetime


def when(value: datetime | None) -> str:
    if value is None:
        return "—"
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")


def identity(telegram_id: int, username: str | None = None, name: str | None = None) -> str:
    label = html.escape(name or username or str(telegram_id))
    handle = f" · @{html.escape(username)}" if username else ""
    return (f"👤 <b>{label}</b>{handle}\n"
            f"🆔 <code>{telegram_id}</code>\n"
            f"🔗 <a href='tg://user?id={telegram_id}'>پیام خصوصی</a>")
