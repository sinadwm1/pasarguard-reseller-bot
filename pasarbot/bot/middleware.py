from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from pasarbot.db.session import Database


class DatabaseMiddleware(BaseMiddleware):
    def __init__(self, database: Database) -> None:
        self.database = database

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        async with self.database.session() as session:
            data["session"] = session
            result = await handler(event, data)
            await session.commit()
            return result
