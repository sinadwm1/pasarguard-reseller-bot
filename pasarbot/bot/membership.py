from __future__ import annotations

from aiogram import Bot


class TelegramMembership:
    def __init__(self, bot: Bot) -> None:
        self.bot = bot

    async def member_status(self, chat_id: str, user_id: int) -> str:
        member = await self.bot.get_chat_member(chat_id=chat_id, user_id=user_id)
        return str(member.status)
