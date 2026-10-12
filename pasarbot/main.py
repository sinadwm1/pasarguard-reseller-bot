from __future__ import annotations

import asyncio
import logging

from pasarbot.bot.app import build_app, set_commands
from pasarbot.config import get_settings


async def run() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    bot, dispatcher, database, jobs = await build_app(settings)
    try:
        await set_commands(bot, settings)
        jobs.start()
        await dispatcher.start_polling(bot, allowed_updates=dispatcher.resolve_used_update_types())
    finally:
        await jobs.stop()
        await dispatcher.storage.close()
        await database.close()
        await bot.session.close()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
