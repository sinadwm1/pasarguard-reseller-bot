"""Retry a Telegram method without unsupported button decorations."""
from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import TelegramMethod
from aiogram.types import InlineKeyboardMarkup, ReplyKeyboardMarkup

logger = logging.getLogger(__name__)


def without_decoration(method: TelegramMethod, field: str) -> TelegramMethod:
    markup = getattr(method, "reply_markup", None)
    if not isinstance(markup, (InlineKeyboardMarkup, ReplyKeyboardMarkup)):
        return method
    name = "inline_keyboard" if isinstance(markup, InlineKeyboardMarkup) else "keyboard"
    rows = getattr(markup, name)
    updated = [[button.model_copy(update={field: None}) if getattr(button, field, None)
                else button for button in row] for row in rows]
    return method.model_copy(update={"reply_markup": markup.model_copy(update={name: updated})})


class ButtonFallback(BaseRequestMiddleware):
    def __init__(self) -> None:
        self.disable_icon = False
        self.disable_style = False

    async def __call__(self, make_request, bot: Bot, method: TelegramMethod):
        if self.disable_icon:
            method = without_decoration(method, "icon_custom_emoji_id")
        if self.disable_style:
            method = without_decoration(method, "style")
        try:
            return await make_request(bot, method)
        except TelegramBadRequest as exc:
            error = str(exc).casefold()
            if not any(word in error for word in ("button", "emoji", "style")):
                raise
            if not self.disable_icon:
                simplified = without_decoration(method, "icon_custom_emoji_id")
                if simplified is not method and simplified != method:
                    self.disable_icon = True
                    logger.warning("Telegram rejected custom emoji on buttons; continuing without icons")
                    method = simplified
                    try:
                        return await make_request(bot, method)
                    except TelegramBadRequest:
                        pass
            simplified = without_decoration(method, "style")
            if simplified == method:
                raise
            self.disable_style = True
            logger.warning("Telegram rejected button style; continuing with default colors")
            return await make_request(bot, simplified)
