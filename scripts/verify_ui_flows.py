"""Offline checks of the actual Telegram UI handlers and delivery archive."""
from __future__ import annotations

import asyncio
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram.types import ReplyKeyboardMarkup

from pasarbot.bot.admin_schema import WIZARD_DEFINITIONS
from pasarbot.bot.handlers import admin_wizard, customer
from pasarbot.bot.keyboards import customer_menu
from pasarbot.config import Settings
from pasarbot.db.models import PasarGuardInstance, PlanModel
from pasarbot.integrations.pasarguard import PasarGuardClient

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_BOT_VALUE = "123456:abcdefghijklmnopqrstuvwxyz"


class State:
    def __init__(self, flow: str, values: dict | None = None):
        self.data = {"flow": flow, "values": values or {}, "context": {}}
        self.current = None

    async def get_data(self):
        return dict(self.data)

    async def update_data(self, **items):
        self.data.update(items)

    async def set_state(self, value):
        self.current = value


class Message:
    def __init__(self):
        self.sent = []

    async def answer(self, text, **kw):
        self.sent.append((text, kw.get("reply_markup")))

    async def edit_text(self, text, **kw):
        self.sent.append((text, kw.get("reply_markup")))


class Callback:
    def __init__(self, message: Message, data: str):
        self.message = message
        self.data = data
        self.from_user = SimpleNamespace(id=12345)
        self.answers = []

    async def answer(self, text=None, **kw):
        self.answers.append(text)


def settings():
    return Settings(_env_file=None, bot_token=SAMPLE_BOT_VALUE,
                    owner_telegram_ids="12345", encryption_key="test-key",
                    callback_signing_key="test-key")


def buttons(message: Message):
    markup = message.sent[-1][1]
    return [button for row in markup.inline_keyboard for button in row]


async def check_customer() -> None:
    menu = customer_menu()
    assert isinstance(menu, ReplyKeyboardMarkup) and menu.is_persistent
    assert len([b for row in menu.keyboard for b in row]) == 6
    assert not hasattr(menu, "inline_keyboard")
    themed_menu = customer_menu(theme={"customer.buy": SimpleNamespace(
        text="خرید شخصی", style="success", custom_emoji_id=None)})
    assert themed_menu.keyboard[0][0].text == "خرید شخصی"
    message = Message()
    plan = SimpleNamespace(id=7, name="آزمایشی", price_irt=1000)
    session = SimpleNamespace(scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: [plan])),
                              get=AsyncMock(return_value=None))
    await customer.show_buy(message, session, settings())
    assert any(b.callback_data == "buy:plan:7" for b in buttons(message))
    message = Message()
    session.scalars = AsyncMock(return_value=SimpleNamespace(all=lambda: []))
    await customer.show_buy(message, session, settings())
    assert "پلن فعال" in message.sent[-1][0]
    assert message.sent[-1][1] is None
    print("customer-ui-ok")


async def check_callbacks() -> None:
    message = Message()
    state = State("admin", {"telegram_id": 1190907109})
    await admin_wizard.prompt_next(message, state, settings(), None, None)
    assert {b.callback_data for b in buttons(message) if b.callback_data}.issuperset({
        "admin:wizard_choice:manager", "admin:wizard_choice:support"})
    # Every predefined choice stage must produce a valid InlineKeyboardMarkup.
    for definition in WIZARD_DEFINITIONS.values():
        for field in definition.fields:
            if field.choices:
                markup = admin_wizard.inline_keyboard([
                    [admin_wizard.ButtonSpec(label, callback_data=f"admin:wizard_choice:{value}")]
                    for label, value in field.choices])
                assert len(markup.inline_keyboard) == len(field.choices)
    message = Message()
    plan = SimpleNamespace(id=7, name="آزمایشی", description="تست", price_irt=1000,
                           total_quota_bytes=10 * 1024**3, max_users=None, duration_days=30,
                           is_active=True, is_visible=True)
    method = SimpleNamespace(id=3, title="💳 کارت‌به‌کارت")
    session = SimpleNamespace(get=AsyncMock(return_value=plan))
    with patch.object(customer, "available_payment_methods", AsyncMock(return_value=[method])):
        await customer.select_plan(Callback(message, "buy:plan:7"), session, settings())
    assert any(b.callback_data == "buy:pay:7:3" for b in buttons(message))
    # An existing manual receipt under review must not be charged from the wallet.
    wallet = SimpleNamespace(id=8, kind="wallet", is_active=True)
    buyer = SimpleNamespace(id=9, wallet_balance_irt=1000000)
    query = SimpleNamespace(scalar=AsyncMock(side_effect=[None, SimpleNamespace(id=12)]),
                            get=AsyncMock(side_effect=[plan, wallet]))
    with patch.object(customer, "current_customer", AsyncMock(return_value=buyer)):
        callback = Callback(Message(), "buy:pay:7:8")
        await customer.choose_payment(callback, query, State("payment"), settings())
    assert "در حال بررسی" in callback.answers[-1]
    assert buyer.wallet_balance_irt == 1000000
    print("callbacks-ok")


async def check_admin_selectors() -> None:
    async def options(field, values, session, secret):
        available = {"instance_id": [(3, "پنل اصلی")], "location_ids": [(5, "🇩🇪 Germany")],
                     "group_ids": [(1, "تست فروش")], "template_ids": [(9, "Template test")]}
        return available.get(field, [])

    state = State("quick_plan", {"name": "تست", "price_irt": 1000, "duration_days": 20,
                                  "limit_mode": "volume_only", "total_quota_gb": 10 * 1024**3})
    message = Message()
    with patch.object(admin_wizard, "selector_options", side_effect=options), \
         patch.object(admin_wizard, "ensure_permission", AsyncMock(return_value=True)):
        await admin_wizard.prompt_next(message, state, settings(), None, None)
        assert any(b.callback_data == "admin:wizard_pick:3" for b in buttons(message))
        await admin_wizard.wizard_pick(Callback(message, "admin:wizard_pick:3"), state, settings(), None, None)
        assert any(b.callback_data == "admin:wizard_pick:5" for b in buttons(message))
        await admin_wizard.wizard_pick(Callback(message, "admin:wizard_pick:5"), state, settings(), None, None)
        assert any(b.callback_data == "admin:wizard_done" for b in buttons(message))
        await admin_wizard.wizard_done(Callback(message, "admin:wizard_done"), state, settings(), None, None)
        assert state.data["values"]["instance_id"] == 3
        assert state.data["values"]["location_ids"] == [5]
        assert state.current == admin_wizard.AdminWizardState.confirming
        state = State("location", {"name": "Germany", "flag": "🇩🇪", "instance_id": 3})
        await admin_wizard.prompt_next(message, state, settings(), None, None)
        assert any(b.callback_data == "admin:wizard_pick:1" for b in buttons(message))
        state = State("plan", {"instance_id": 3, "location_ids": [5], "group_ids": [1]})
        assert (await options("template_ids", state.data["values"], None, None))[0][0] == 9
    # Saving a quick plan derives its groups and survives numeric IDs returned as strings by PG.
    location = SimpleNamespace(id=5, is_active=True, pasarguard_instance_id=3, group_ids=[1])
    instance_fields = {"id": 3, "is_active": True, "base_url": "https://example.org",
                       "encrypted_access_token": "opaque", "verify_tls": True}
    instance = SimpleNamespace(**instance_fields)
    entities = []

    async def get_entity(model, entity_id):
        if model is PasarGuardInstance:
            return instance
        if model is PlanModel:
            return None
        return None

    async def flush():
        for entity in entities:
            if isinstance(entity, PlanModel):
                entity.id = 14

    database = SimpleNamespace(
        get=AsyncMock(side_effect=get_entity),
        scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: [location])),
        add=lambda entity: entities.append(entity), flush=flush,
    )
    quick = {"name": "تست", "price_irt": 1000, "duration_days": 20,
             "limit_mode": "volume_only", "total_quota_gb": 10 * 1024**3,
             "instance_id": 3, "location_ids": [5]}
    with patch.object(admin_wizard, "PasarGuardClient") as client:
        client.return_value.list_groups = AsyncMock(return_value=[{"id": "1", "name": "test"}])
        result = await admin_wizard.execute_wizard("quick_plan", quick, {}, database,
                                                    settings(), SimpleNamespace(decrypt=lambda _: "test"), None, 12345)
    assert "#14" in result
    assert entities[-1].group_ids == [1] and entities[-1].is_visible
    assert entities[-1].max_users is None
    assert PasarGuardClient._extract_items({"user_templates": [{"id": 9}]}) == [{"id": 9}]
    print("admin-selectors-ok")


def check_package() -> None:
    path = ROOT / "PasarGuard-Reseller-Bot-v1.3.0.zip"
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        assert ".env" not in names
        assert "pasarbot/bot/keyboards.py" in names
        assert "pasarbot/bot/handlers/admin_wizard.py" in names
        assert b"quick_plan" in archive.read("pasarbot/bot/admin_schema.py")
        assert b"ReplyKeyboardMarkup" in archive.read("pasarbot/bot/keyboards.py")
    print("package-ok")


if __name__ == "__main__":
    name = sys.argv[1]
    if name == "customer":
        asyncio.run(check_customer())
    elif name == "callbacks":
        asyncio.run(check_callbacks())
    elif name == "admin-selectors":
        asyncio.run(check_admin_selectors())
    elif name == "package":
        check_package()
    else:
        raise SystemExit("unknown check")
