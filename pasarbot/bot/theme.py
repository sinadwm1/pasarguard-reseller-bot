from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pasarbot.db.models import ButtonConfig
from pasarbot.domain.telegram import VALID_BUTTON_STYLES, ButtonSpec


async def load_button_theme(session: AsyncSession) -> dict[str, ButtonSpec]:
    records = (
        await session.scalars(
            select(ButtonConfig).where(ButtonConfig.is_visible.is_(True)).order_by(ButtonConfig.display_order)
        )
    ).all()
    result: dict[str, ButtonSpec] = {}
    for record in records:
        # callback/url is supplied by the menu; these values are visual overrides only.
        if not record.text or len(record.text) > 128:
            continue
        result[record.key] = ButtonSpec(
            record.text,
            callback_data="theme:placeholder",
            style=record.style if record.style in VALID_BUTTON_STYLES else None,
            custom_emoji_id=(record.custom_emoji_id if record.custom_emoji_id and
                             record.custom_emoji_id.isdigit() else None),
        )
    return result


def themed(
    theme: dict[str, ButtonSpec] | None,
    key: str,
    default: ButtonSpec,
) -> ButtonSpec:
    if not theme or key not in theme:
        return default
    visual = theme[key]
    return ButtonSpec(
        visual.text,
        callback_data=default.callback_data,
        url=default.url,
        style=visual.style,
        custom_emoji_id=visual.custom_emoji_id,
    )
