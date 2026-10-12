from __future__ import annotations

import html
import string
from dataclasses import dataclass
from typing import Protocol

VALID_BUTTON_STYLES = frozenset({"primary", "success", "danger"})
MEMBER_STATUSES = frozenset({"creator", "administrator", "member", "restricted"})


@dataclass(frozen=True, slots=True)
class ButtonSpec:
    text: str
    callback_data: str | None = None
    url: str | None = None
    style: str | None = None
    custom_emoji_id: str | None = None

    def __post_init__(self) -> None:
        if not self.text:
            raise ValueError("button text is required")
        if (self.callback_data is None) == (self.url is None):
            raise ValueError("exactly one of callback_data or url is required")
        if self.callback_data and len(self.callback_data.encode()) > 64:
            raise ValueError("callback_data exceeds Telegram's 64-byte limit")
        if self.style is not None and self.style not in VALID_BUTTON_STYLES:
            raise ValueError("invalid Telegram button style")

    def payload(self, *, modern_features: bool = True) -> dict[str, str]:
        result = {"text": self.text}
        if self.callback_data is not None:
            result["callback_data"] = self.callback_data
        if self.url is not None:
            result["url"] = self.url
        if modern_features and self.style is not None:
            result["style"] = self.style
        if modern_features and self.custom_emoji_id is not None:
            result["icon_custom_emoji_id"] = self.custom_emoji_id
        return result


@dataclass(frozen=True, slots=True)
class RequiredChannel:
    chat_id: str
    title: str
    join_url: str


class MembershipPort(Protocol):
    async def member_status(self, chat_id: str, user_id: int) -> str: ...


@dataclass(frozen=True, slots=True)
class ChannelAccessResult:
    allowed: bool
    missing: tuple[RequiredChannel, ...]


async def check_channel_access(
    membership: MembershipPort,
    user_id: int,
    channels: tuple[RequiredChannel, ...],
) -> ChannelAccessResult:
    missing: list[RequiredChannel] = []
    for channel in channels:
        try:
            status = await membership.member_status(channel.chat_id, user_id)
        except Exception:
            missing.append(channel)
            continue
        if status not in MEMBER_STATUSES:
            missing.append(channel)
    return ChannelAccessResult(not missing, tuple(missing))


class SafeTemplate:
    """Small allow-listed formatter; all values are HTML escaped."""

    def __init__(self, source: str, allowed_variables: set[str]) -> None:
        self.source = source
        self.allowed_variables = allowed_variables
        fields = {
            field_name
            for _, field_name, _, _ in string.Formatter().parse(source)
            if field_name is not None
        }
        unknown = fields - allowed_variables
        if unknown:
            raise ValueError(f"unknown template variables: {', '.join(sorted(unknown))}")

    def render(self, **values: object) -> str:
        safe = {key: html.escape(str(value)) for key, value in values.items()}
        missing = self.allowed_variables.intersection(self._used_fields()) - safe.keys()
        if missing:
            raise ValueError(f"missing template values: {', '.join(sorted(missing))}")
        return self.source.format_map(safe)

    def _used_fields(self) -> set[str]:
        return {
            field_name
            for _, field_name, _, _ in string.Formatter().parse(self.source)
            if field_name is not None
        }
