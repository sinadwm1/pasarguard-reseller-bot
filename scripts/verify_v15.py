"""Focused offline acceptance checks for version 1.5; no live Telegram credentials needed."""
from __future__ import annotations

import asyncio
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

from aiogram.dispatcher.event.bases import SkipHandler

from pasarbot.bot.handlers import admin_buttons, admin_wizard, customer
from pasarbot.bot.presentation import identity
from pasarbot.config import Settings
from pasarbot.db.models import Receipt
from pasarbot.integrations.pasarguard import (
    AdminSpec,
    PasarGuardClient,
    PasarGuardLimitMismatch,
    RestrictedRoleSpec,
)
from pasarbot.services.runtime_provisioning import RuntimeProvisioner

ROOT = Path(__file__).resolve().parents[1]


def settings() -> Settings:
    return Settings(_env_file=None, bot_token="123456:" + "a" * 32,
                    owner_telegram_ids="12345", encryption_key="test-key",
                    callback_signing_key="test-key")


class State:
    def __init__(self, values=None, name=None):
        self.data = values or {}
        self.name = name

    async def get_data(self):
        return self.data

    async def get_state(self):
        return self.name

    async def clear(self):
        self.data = {}
        self.name = None


class Message:
    def __init__(self, text="🎫 پشتیبانی", photo=None, user_id=12345):
        self.text = text
        self.photo = photo
        self.from_user = NS(id=user_id)
        self.answers = []

    async def answer(self, text, **kwargs):
        self.answers.append((text, kwargs))


async def check_support():
    message = Message()
    session = NS()
    with patch.object(admin_buttons, "load_button_theme", AsyncMock(return_value={})), \
         patch.object(customer, "load_button_theme", AsyncMock(return_value={})), \
         patch.object(customer, "current_customer", AsyncMock(return_value=NS(is_blocked=False))), \
         patch.object(customer, "show_support", AsyncMock()) as show:
        try:
            await admin_buttons.reply_section(message, session, settings(), State())
        except SkipHandler:
            pass
        else:
            raise AssertionError("customer support was swallowed by admin Reply handler")
        await customer.customer_reply_action(message, session, settings(), State())
        show.assert_awaited_once()
    # The actual customer screen has the ticket creation button.
    from pasarbot.bot.handlers.customer import show_support
    buyer = NS(id=4, telegram_id=12345, is_blocked=False)
    db = NS(scalars=AsyncMock(return_value=NS(all=lambda: [])))
    message = Message()
    with patch.object(customer, "current_customer", AsyncMock(return_value=buyer)), \
         patch.object(customer, "bot_copy", AsyncMock(return_value="پشتیبانی")):
        await show_support(message, db, settings(), 12345)
    buttons = message.answers[0][1]["reply_markup"].inline_keyboard
    assert any(b.callback_data == "support:new" for row in buttons for b in row)
    print("support-ok")


async def check_broadcast():
    db = NS(scalars=AsyncMock(return_value=NS(all=lambda: [23456])))
    sent = []

    async def send(user_id, body, **kwargs):
        sent.append((user_id, body, kwargs))

    bot = NS(send_message=AsyncMock(side_effect=send))
    callback = NS(from_user=NS(id=12345), answers=[], answer=AsyncMock())
    state = State({"flow": "broadcast", "values": {"text": "<hello> & test"}})
    with patch.object(admin_wizard, "ensure_permission", AsyncMock(return_value=True)):
        await admin_wizard.broadcast_test(callback, state, db, settings(), bot)
    assert sent == [(12345, "<hello> & test", {"parse_mode": None})]
    with patch.object(admin_wizard.asyncio, "sleep", AsyncMock()):
        result = await admin_wizard.execute_wizard("broadcast", {"text": "<hello> & test"},
             {}, db, settings(), None, bot, 12345)
    assert {row[0] for row in sent} == {12345, 23456}
    assert all(row[2]["parse_mode"] is None for row in sent)
    assert "موفق 2" in result and "ناموفق 0" in result
    print("broadcast-ok")


class Transport:
    def __init__(self, spec):
        self.spec = spec
        self.admin_quota = 1024
        self.bad = False

    async def request(self, method, url, *, headers, json=None, timeout=20):
        if url.endswith("/api/admin-role/19"):
            if method == "GET":
                payload = self.spec.to_payload()
                if self.bad:
                    payload["limits"]["max_users"] = None
                return 200, payload
            return 200, {}
        if "/api/admins?" in url and "ids=7" in url:
            return 200, {"admins": [{"id": 7, "role": {"id": 19},
                                      "data_limit": self.admin_quota}], "total": 1}
        raise AssertionError(url)


async def check_quota():
    spec = RestrictedRoleSpec(name="reseller-1", max_users=2, min_user_quota_bytes=None,
        max_user_quota_bytes=None, min_user_expire_days=None, max_user_expire_days=None,
        allowed_group_ids=(4,), allowed_template_ids=(), hwid_enabled=False,
        max_devices=None, total_quota_bytes=1024)
    payload = spec.to_payload()
    assert payload["limits"]["max_users"] == 2
    assert payload["limits"]["data_limit_max"] == 1024
    assert payload["permissions"]["users"]["read"] == {"scope": 1}
    assert payload["permissions"]["users"]["create"] is True
    assert AdminSpec("r", "p", "19", (4,), (), data_limit_bytes=1024).to_payload()["data_limit"] == 1024
    transport = Transport(spec)
    client = PasarGuardClient("https://pg.example", "pg_key_test", transport)
    await client.apply_role("19", spec)
    await client.verify_admin_limits("7", "19", 1024)
    transport.bad = True
    try:
        await client.verify_role("19", spec)
    except PasarGuardLimitMismatch:
        pass
    else:
        raise AssertionError("unrestricted role accepted")
    transport.bad = False
    transport.admin_quota = None
    try:
        await client.verify_admin_limits("7", "19", 1024)
    except PasarGuardLimitMismatch:
        pass
    else:
        raise AssertionError("unlimited admin accepted")
    plan = NS(id=5, pasarguard_instance_id=3, total_quota_bytes=1024,
              max_users=2, group_ids=[4], template_ids=[], duration_days=30,
              min_user_quota_bytes=None, max_user_quota_bytes=None,
              min_user_expire_days=None, max_user_expire_days=None,
              hwid_enabled=False, max_devices=None, force_template=False,
              reset_allowed=False)
    instance = NS(id=3, is_active=True, dashboard_url="https://pg.example/dashboard/")
    order = NS(id=9, plan_id=5, reseller_id=None, status="paid")
    customer_record = NS(id=6, telegram_id=12345)
    remote = NS(find_admin_by_username=AsyncMock(return_value=None),
                find_role_by_name=AsyncMock(return_value=None),
                create_role=AsyncMock(return_value="19"),
                verify_role=AsyncMock(), create_admin=AsyncMock(return_value="7"),
                verify_admin_limits=AsyncMock(), activate_admin=AsyncMock(),
                suspend_admin=AsyncMock())

    def add(reseller):
        reseller.id = 10

    db = NS(scalar=AsyncMock(return_value=plan), get=AsyncMock(return_value=instance),
            add=add, flush=AsyncMock(), commit=AsyncMock())
    provisioner = RuntimeProvisioner(None, NS(encrypt=lambda password: "encrypted"))
    provisioner._make_client = lambda unused: remote
    remote.verify_admin_limits.side_effect = PasarGuardLimitMismatch("quota lost")
    try:
        await provisioner._initial_sale(db, order, customer_record)
    except PasarGuardLimitMismatch:
        pass
    else:
        raise AssertionError("panel activated despite missing limits")
    assert remote.activate_admin.await_count == 0
    assert remote.create_admin.call_args.args[0].enabled is False
    # v1.6 persists the pending reseller identity before remote writes so a
    # retry reuses the same Admin/Role instead of creating an orphan.
    assert db.commit.await_count == 1
    print("quota-ok")


async def check_receipts():
    order = NS(id=13, customer_id=4, plan_id=5, payable_irt=1000,
               order_type="initial_sale", status="awaiting_payment")
    buyer = NS(id=4, telegram_id=12345, username="buyer", first_name="Ali")
    captured = []

    def add(item):
        captured.append(item)
        if isinstance(item, Receipt):
            item.id, item.version = 33, 1

    db = NS(scalar=AsyncMock(side_effect=[order, None]),
            get=AsyncMock(return_value=NS(name="Basic")),
            scalars=AsyncMock(return_value=NS(all=lambda: [])), add=add, flush=AsyncMock())
    msg = Message(text=None, photo=[NS(file_id="photo-id")], user_id=12345)
    bot = NS(download=AsyncMock(side_effect=AssertionError("disk write")), send_photo=AsyncMock())
    signer = NS(encode=lambda action, ident, version: f"{action}:{ident}:{version}")
    with patch.object(customer, "current_customer", AsyncMock(return_value=buyer)), \
         patch.object(customer, "bot_copy", AsyncMock(return_value="ثبت شد")):
        await customer.receive_receipt(msg, db, State({"order_id": 13, "payment_method_id": 2}),
                                       bot, signer, settings())
    receipt = next(x for x in captured if isinstance(x, Receipt))
    assert receipt.telegram_file_id == "photo-id" and receipt.stored_path is None
    assert order.status == "payment_review" and bot.download.await_count == 0
    assert "tg://user?id=12345" in bot.send_photo.call_args.kwargs["caption"]
    compose = (ROOT / "docker-compose.yml").read_text()
    assert "receipt_data" not in compose and "receipts" not in (ROOT / "backup.sh").read_text()
    print("receipts-ok")


def check_identity():
    card = identity(12345, "buyer", "Ali")
    assert "<code>12345</code>\n🔗" in card
    assert "@buyer" in card and "tg://user?id=12345" in card
    assert "identity(customer.telegram_id" in (ROOT / "pasarbot/bot/handlers/customer.py").read_text()
    print("identity-ok")


def check_package():
    archive = ROOT / "PasarGuard-Reseller-Bot-v1.6.0.zip"
    with zipfile.ZipFile(archive) as package:
        names = set(package.namelist())
        assert package.testzip() is None
        assert {"UPGRADE_V16_FA.md", "scripts/verify_v15.py", "docker-compose.yml"} <= names
        assert not any(n == ".env" or n.startswith("backups/") for n in names)
        assert b"receipt_data" not in package.read("docker-compose.yml")
    print("package-ok")


CASES = {"support": check_support, "broadcast": check_broadcast, "quota": check_quota,
         "receipts": check_receipts, "identity": check_identity, "package": check_package}


if __name__ == "__main__":
    check = CASES[sys.argv[1]]
    result = check()
    if asyncio.iscoroutine(result):
        asyncio.run(result)
