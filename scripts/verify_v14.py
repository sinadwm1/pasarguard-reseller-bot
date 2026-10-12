"""Behavior checks for the v1.4 customer and admin flows without live credentials."""
from __future__ import annotations

import asyncio
import sys
import zipfile
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendMessage
from aiogram.types import CallbackQuery as TelegramCallbackQuery
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, MessageEntity
from aiogram.types import User as TelegramUser

from pasarbot.bot.admin_schema import WIZARD_DEFINITIONS
from pasarbot.bot.button_fallback import ButtonFallback
from pasarbot.bot.copy import manual_payment_copy
from pasarbot.bot.handlers import admin_buttons, admin_wizard, customer
from pasarbot.bot.theme import load_button_theme
from pasarbot.config import Settings
from pasarbot.db.models import (
    BotAdmin,
    ButtonConfig,
    Customer,
    Order,
    PasarGuardInstance,
    PlanModel,
    Receipt,
    ResellerModel,
    Ticket,
    TicketMessage,
    WalletEntry,
)
from pasarbot.domain.enums import OrderStatus, Permission, ResellerStatus, TransactionType
from pasarbot.integrations.pasarguard import RestrictedRoleSpec
from pasarbot.services.authorization import effective_permissions
from pasarbot.services.runtime_provisioning import RuntimeProvisioner

ROOT = Path(__file__).resolve().parents[1]


def settings() -> Settings:
    return Settings(_env_file=None, bot_token="123456:" + "abcdefghijklmnopqrstuvwxyz",  # noqa: S106
                    owner_telegram_ids="12345", encryption_key="test-key",
                    callback_signing_key="test-key")


class State:
    def __init__(self, data=None):
        self.data = data or {}
        self.current = None

    async def get_data(self):
        return dict(self.data)

    async def update_data(self, **kw):
        self.data.update(kw)

    async def set_state(self, value):
        self.current = value

    async def set_data(self, value):
        self.data = value

    async def clear(self):
        self.data = {}
        self.current = None


class Message:
    def __init__(self, text="", photo=None, entities=None):
        self.text = text
        self.photo = photo
        self.entities = entities or []
        self.from_user = NS(id=23456)
        self.sent = []
        self.chat = NS(id=-100123)
        self.message_id = 21

    async def answer(self, text, **kw):
        self.sent.append(("answer", text, kw))

    async def edit_text(self, text, **kw):
        self.sent.append(("edit_text", text, kw))

    async def edit_reply_markup(self, **kw):
        self.sent.append(("edit_markup", "", kw))

    async def delete(self):
        pass


class Callback:
    def __init__(self, data, message=None):
        self.data = data
        self.message = message or Message()
        self.from_user = NS(id=12345, full_name="Owner")
        self.answers = []

    async def answer(self, text=None, **kw):
        self.answers.append(text)


def callbacks(message):
    markup = message.sent[-1][2].get("reply_markup")
    return [b.callback_data for row in markup.inline_keyboard for b in row] if markup else []


async def check_support():
    settings_obj = settings()
    buyer = NS(id=8, telegram_id=23456, username="buyer", first_name="Ali", is_blocked=False)
    ticket = NS(id=51, customer_id=8, subject="مشکل ورود", status="open",
                group_chat_id=None, group_message_id=None)
    session = NS(scalars=AsyncMock(return_value=NS(all=lambda: [ticket])),
                 get=AsyncMock(return_value=None),
                 add=lambda _: None, flush=AsyncMock())
    message = Message()
    with patch.object(customer, "current_customer", AsyncMock(return_value=buyer)):
        await customer.show_support(message, session, settings_obj, buyer.telegram_id)
    assert {"support:new", "support:view:51"}.issubset(callbacks(message))
    created = []

    def add(item):
        created.append(item)
        if isinstance(item, Ticket):
            item.id = 52

    session.add = add
    bot = NS(send_message=AsyncMock(return_value=NS(chat=NS(id=-100123), message_id=7)))
    settings_obj.receipt_group_id = -100123
    state = State()
    message = Message("ورود به پنل کار نمی‌کند")
    with patch.object(customer, "current_customer", AsyncMock(return_value=buyer)):
        await customer.support_create(message, session, state, bot, settings_obj)
    assert created[0].id == 52 and created[1].body == message.text
    markup = bot.send_message.call_args.kwargs["reply_markup"]
    actions = {b.callback_data for row in markup.inline_keyboard for b in row}
    assert actions == {"ticket:review:52", "ticket:reply:52"}
    assert created[0].group_chat_id == -100123
    # Reviewing in the group checks an admin permission and reaches the buyer.
    session.scalar = AsyncMock(return_value=ticket)
    session.get = AsyncMock(side_effect=lambda model, key: buyer if model is Customer else None)
    callback = Callback("ticket:review:51")
    with patch.object(admin_buttons, "allowed", AsyncMock(return_value=True)):
        await admin_buttons.ticket_group_action(callback, session, settings_obj, bot, state)
    assert ticket.status == "reviewed"
    assert "بررسی شد" in bot.send_message.call_args.args[1]
    ticket.group_chat_id = None
    ticket.group_message_id = None
    session.add = add
    reply = await admin_wizard.execute_wizard("ticket_reply", {"text": "دوباره تلاش کنید"},
                                              {"target_id": 51}, session, settings_obj,
                                              None, bot, 12345)
    assert reply == "پاسخ تیکت ارسال شد."
    assert ticket.status == "answered"
    assert any(isinstance(x, TicketMessage) and x.sender_type == "admin" for x in created)
    assert "دوباره تلاش کنید" in bot.send_message.call_args.args[1]
    assert customer.router.message.handlers[-1].callback.__name__ == "customer_reply_action"
    print("support-ok")


async def check_receipts():
    settings_obj = settings()
    payment = NS(id=13, customer_id=8, plan_id=6, payable_irt=100000,
                 order_type="initial_sale", status="awaiting_payment")
    buyer = NS(id=8, telegram_id=23456, first_name="Ali", username="ali")
    plan = NS(name="پلن آلمان")
    captured = []

    def add(item):
        captured.append(item)
        if isinstance(item, Receipt):
            item.id = 99
            item.version = 1

    session = NS(scalar=AsyncMock(side_effect=[payment, None]),
                 get=AsyncMock(side_effect=lambda model, key: plan if model is PlanModel else None),
                 scalars=AsyncMock(return_value=NS(all=lambda: [])),
                 add=add, flush=AsyncMock())
    state = State({"order_id": 13, "payment_method_id": 1})
    photo = NS(file_id="file123")
    message = Message(photo=[photo])
    bot = NS(download=AsyncMock(), send_photo=AsyncMock())
    signer = NS(encode=lambda action, identifier, version: f"{action}:{identifier}:{version}")
    with patch.object(customer, "current_customer", AsyncMock(return_value=buyer)):
        await customer.receive_receipt(message, session, state, bot, signer, settings_obj)
    receipt = next(item for item in captured if isinstance(item, Receipt))
    assert receipt.telegram_file_id == "file123" and receipt.stored_path is None
    assert bot.download.await_count == 0
    assert payment.status == "payment_review"
    caption = bot.send_photo.call_args.kwargs["caption"]
    assert "پلن آلمان" in caption and "tg://user?id=23456" in caption
    assert "99" in caption and "13" in caption
    # The copy-ready number is derived from the account, not hardcoded in the text.
    account = NS(title="ملت", account_value="1234-5678-9012-3456", holder_name="Ali")
    layout_session = NS(get=AsyncMock(return_value=NS(value="💳 | 👤 | 🏦 | 💰 | 🔢")))
    rendered = await manual_payment_copy(layout_session, account, payment)
    assert "<code>1234567890123456</code>" in rendered
    assert "1234-" not in rendered and "100,000" in rendered
    print("receipts-ok")


async def check_reseller():
    buyer = NS(id=8, telegram_id=23456, is_blocked=True)
    panel = NS(id=44, customer_id=8, pasarguard_admin_id="7", panel_username="reseller-44",
               pasarguard_instance_id=3,
               status=ResellerStatus.SUSPENDED.value,
               total_quota_bytes=100, max_users=4,
               expires_at=datetime.now(UTC) + timedelta(days=4), last_sync_error=None,
               manual_overrides={"suspended_by_customer_block": True})
    instance = NS(id=3)
    db = NS(get=AsyncMock(side_effect=lambda model, key: buyer if model is Customer else instance),
            scalars=AsyncMock(return_value=NS(all=lambda: [panel])), flush=AsyncMock())
    remote = NS(find_admin_by_username=AsyncMock(return_value={"status": "disabled"}),
                reseller_snapshot=AsyncMock(return_value=NS(allocated_bytes=80, unlimited_users=0,
                                                           users_count=2)),
                activate_admin=AsyncMock(), suspend_admin=AsyncMock())
    with patch.object(admin_buttons, "allowed", AsyncMock(return_value=True)), \
         patch.object(admin_buttons, "pg_client", return_value=remote):
        cb = Callback("admin:customer:block:8")
        await admin_buttons.toggle_customer_block(cb, db, settings(), None)
        assert buyer.is_blocked is False and panel.status == "active"
        remote.activate_admin.assert_awaited_once_with("7")
        buyer.is_blocked = True
        panel.status = "suspended"
        remote.reseller_snapshot.return_value = NS(allocated_bytes=101, unlimited_users=0, users_count=2)
        await admin_buttons.toggle_customer_block(Callback("admin:customer:block:8"), db, settings(), None)
        assert panel.status == "suspended"
        buyer.is_blocked = True
        panel.status = "active"
        remote.reseller_snapshot.return_value = NS(allocated_bytes=80, unlimited_users=0, users_count=2)
        await admin_buttons.toggle_customer_block(Callback("admin:customer:block:8"), db, settings(), None)
        assert panel.status == "active" and remote.activate_admin.await_count == 2
    spec = RestrictedRoleSpec(
        name="reseller-44", max_users=4, min_user_quota_bytes=None,
        max_user_quota_bytes=100, min_user_expire_days=None, max_user_expire_days=None,
        allowed_group_ids=(1,), allowed_template_ids=(), hwid_enabled=False,
        max_devices=None, total_quota_bytes=100)
    payload = spec.to_payload()
    assert payload["limits"]["data_limit_min"] == 1
    assert payload["limits"]["data_limit_max"] == 100
    assert payload["limits"]["max_users"] == 4
    for name, value in (("reseller_action", "admin:reseller:sync:44"),
                        ("reseller_users_admin", "admin:reseller:users:44")):
        cb = TelegramCallbackQuery(id="1", chat_instance="a", data=value,
                                   from_user=TelegramUser(id=12345, is_bot=False, first_name="Owner"))
        matching = [handler.callback.__name__ for handler in admin_buttons.router.callback_query.handlers
                    if handler.callback.__name__ in {"reseller_action", "reseller_users_admin"}
                    and (await handler.check(cb))[0]]
        assert matching == [name], matching
    print("reseller-ok")


async def check_wallet():
    buyer = NS(id=8, telegram_id=23456, wallet_balance_irt=100)
    order = NS(id=13, customer_id=8, order_type=TransactionType.WALLET_TOPUP.value,
               status=OrderStatus.PROVISIONING.value, payable_irt=200)
    created = []
    session = NS(get=AsyncMock(side_effect=lambda model, key: order if model is Order else buyer),
                 scalar=AsyncMock(return_value=buyer), add=lambda item: created.append(item),
                 commit=AsyncMock())

    @asynccontextmanager
    async def session_factory():
        yield session

    provisioner = RuntimeProvisioner(NS(session=session_factory), None)
    delivered = await provisioner._process(13)
    assert buyer.wallet_balance_irt == 300 and order.status == OrderStatus.COMPLETED.value
    assert delivered.reseller_id == 0 and delivered.telegram_id == 23456
    assert len(created) == 1 and isinstance(created[0], WalletEntry)
    session.commit.assert_awaited_once()
    print("wallet-ok")


async def check_admin():
    now = datetime.now(UTC)
    buyer = NS(id=8, telegram_id=23456, username="ali", first_name="Ali", last_name="",
               wallet_balance_irt=100, is_blocked=False, created_at=now, updated_at=now)
    panel = NS(id=44, customer_id=8, plan_id=6, pasarguard_instance_id=3,
               pasarguard_admin_id="7", panel_username="reseller-44",
               status="active", users_count=2, max_users=4, total_quota_bytes=100,
               allocated_bytes=80, actual_used_bytes=35, group_ids=[1], created_at=now,
               expires_at=now + timedelta(days=10), last_synced_at=now,
               last_sync_error=None)
    plan = NS(id=6, name="Plan")
    instance = NS(id=3, name="Main")
    admin = NS(id=2, telegram_id=787878, username=None, display_name="Helper",
               role_name="custom", permissions=["support.manage"], is_active=True,
               created_at=now, updated_at=now)
    def get(model, key):
        return {Customer: buyer, ResellerModel: panel, PlanModel: plan, PasarGuardInstance: instance,
                BotAdmin: admin}.get(model)
    db = NS(get=AsyncMock(side_effect=get), scalar=AsyncMock(return_value=None))
    txt, rows, _ = await admin_buttons.detail_view(db, "reseller", 44, settings())
    assert "tg://user?id=23456" in txt and "Admin ID" in txt and "بازاعمال" in str(rows)
    txt, rows, _ = await admin_buttons.detail_view(db, "customer", 8, settings())
    assert "tg://user?id=23456" in txt and any(
        button.text == "🛒 سفارش‌ها" for row in rows for button in row
    )
    txt, _, _ = await admin_buttons.detail_view(db, "admin", 2, settings())
    assert "tg://user?id=787878" in txt and "support.manage" in txt
    rights = effective_permissions(787878, [12345], "custom", ["support.manage"])
    assert Permission.SUPPORT_MANAGE in rights and Permission.PAYMENT_MANAGE not in rights
    scoped_client = NS(reseller_snapshot=AsyncMock(return_value=NS(users=[NS(
        id="42", username="only-this-admin", status="active", data_limit_bytes=1024,
        used_bytes=100, expire_at=None,
    )])))
    instance.base_url = "https://example.org"
    instance.encrypted_access_token = "sample" + "-ciphertext"  # noqa: S105
    instance.verify_tls = True
    msg = Message("42")
    scoped_db = NS(get=AsyncMock(return_value=instance))
    with patch.object(customer, "owned_panel", AsyncMock(return_value=panel)), \
         patch.object(customer, "PasarGuardClient", return_value=scoped_client):
        await customer.panel_user_search_result(msg, State({"panel_id": 44}), scoped_db,
            NS(decrypt=lambda _: "token"), settings())
    scoped_client.reseller_snapshot.assert_awaited_once_with("7")
    assert "only-this-admin" in msg.sent[-1][1]
    print("admin-ok")


async def check_ui():
    theme_record = NS(key="customer.buy", text="خرید", style="purple",
                      custom_emoji_id="invalid")
    session = NS(scalars=AsyncMock(return_value=NS(all=lambda: [theme_record])))
    theme = await load_button_theme(session)
    assert theme["customer.buy"].style is None and theme["customer.buy"].custom_emoji_id is None
    markup = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Buy", callback_data="buy", style="success",
                             icon_custom_emoji_id="123456")
    ]])
    method = SendMessage(chat_id=1, text="test", reply_markup=markup)
    attempts = []

    async def make_request(bot, request):
        attempts.append(request)
        if len(attempts) == 1:
            raise TelegramBadRequest(method=request, message="Bad Request: custom emoji cannot be used")
        return NS(ok=True)
    await ButtonFallback()(make_request, None, method)
    assert len(attempts) == 2
    assert attempts[1].reply_markup.inline_keyboard[0][0].icon_custom_emoji_id is None
    assert attempts[1].reply_markup.inline_keyboard[0][0].style == "success"
    state = State({"flow": "button", "values": {"key": "customer.buy"}, "context": {}})
    entity = MessageEntity(type="custom_emoji", offset=0, length=2, custom_emoji_id="123456")
    msg = Message("🙂 خرید", entities=[entity])
    with patch.object(admin_wizard, "prompt_next", AsyncMock()):
        await admin_wizard.wizard_text(msg, state, settings(), None, None)
    assert state.data["values"]["text"] == "خرید"
    assert state.data["values"]["custom_emoji_id"] == "123456"
    saved = []
    db = NS(get=AsyncMock(return_value=None), add=lambda item: saved.append(item))
    await admin_wizard.execute_wizard("button",
          {"key": "customer.buy", "text": "خرید", "style": "primary",
           "custom_emoji_id": "123456"}, {}, db, settings(), None, None, 12345)
    assert isinstance(saved[0], ButtonConfig)
    assert saved[0].style == "primary" and saved[0].custom_emoji_id == "123456"
    assert "💳" in WIZARD_DEFINITIONS["bot_text"].fields[1].prompt
    print("ui-ok")


def check_package():
    archive = ROOT / "PasarGuard-Reseller-Bot-v1.6.0.zip"
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None
        names = set(z.namelist())
        assert ".env" not in names
        for required in ("docker-compose.yml", "backup.sh",
                         "alembic/versions/20260929_02_receipts_tickets.py",
                         "pasarbot/bot/help_texts.py", "scripts/verify_v14.py"):
            assert required in names
        assert b"receipt_data" not in z.read("docker-compose.yml")
        assert b"v1.6.0.zip" in z.read("INSTALL_FA.md")
    print("package-ok")


if __name__ == "__main__":
    cases = {"support": check_support, "receipts": check_receipts,
             "reseller": check_reseller, "admin": check_admin,
             "wallet": check_wallet, "ui": check_ui}
    choice = sys.argv[1]
    if choice == "package":
        check_package()
    elif choice in cases:
        asyncio.run(cases[choice]())
    else:
        raise SystemExit("unknown check")
