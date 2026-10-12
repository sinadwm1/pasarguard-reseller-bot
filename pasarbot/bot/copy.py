"""Editable customer copy and copy-ready manual payment layout."""
from __future__ import annotations

import html

from sqlalchemy.ext.asyncio import AsyncSession

from pasarbot.db.models import BotText, Order, PaymentAccount
from pasarbot.domain.telegram import SafeTemplate


async def bot_copy(session: AsyncSession, key: str, default: str, **values: object) -> str:
    record = await session.get(BotText, key)
    if record is None:
        return SafeTemplate(default, set(values)).render(**values)
    try:
        return SafeTemplate(record.value, set(values)).render(**values)
    except (ValueError, KeyError, IndexError):
        return SafeTemplate(default, set(values)).render(**values)


async def manual_payment_copy(session: AsyncSession, account: PaymentAccount,
                              order: Order) -> str:
    record = await session.get(BotText, "manual_payment")
    source = record.value if record else "🏦\n💳\n👤\nمبلغ: 💰\nسفارش: 🔢"
    # Admin-entered text is plain text. Emoji markers become escaped HTML values.
    rendered = html.escape(source)
    values = {
        "🏦": html.escape(account.title),
        "💳": f"<code>{html.escape(''.join(x for x in account.account_value if x.isdigit()))}</code>",
        "👤": html.escape(account.holder_name),
        "💰": f"{order.payable_irt:,} تومان",
        "🔢": f"#{order.id}",
    }
    for marker, value in values.items():
        rendered = rendered.replace(marker, value)
    return rendered
