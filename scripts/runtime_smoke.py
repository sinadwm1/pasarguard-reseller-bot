from __future__ import annotations

from sqlalchemy import create_engine

from pasarbot.bot.handlers import admin_router, customer_router
from pasarbot.bot.keyboards import customer_menu
from pasarbot.config import Settings
from pasarbot.db.models import Base

settings = Settings(
    bot_token="123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk",
    owner_telegram_ids=[1, 2],
    encryption_key="MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
    callback_signing_key="runtime-smoke-signing-key",
)
if settings.owner_telegram_ids != [1, 2]:
    raise SystemExit("settings owner parsing failed")

engine = create_engine("sqlite://")
Base.metadata.create_all(engine)
if len(Base.metadata.tables) < 20:
    raise SystemExit("database metadata is incomplete")

payload = customer_menu().model_dump(exclude_none=True)
if payload["keyboard"][0][0]["style"] != "success" or not payload["is_persistent"]:
    raise SystemExit("modern Telegram button fields were not preserved")
if admin_router.name != "admin" or customer_router.name != "customer":
    raise SystemExit("bot routers did not load")

print(f"runtime-import-smoke-passed ({len(Base.metadata.tables)} tables)")
