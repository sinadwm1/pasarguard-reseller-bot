from __future__ import annotations

import asyncio

from pasarbot.bot.admin_schema import ADMIN_SECTION_ACTIONS, WIZARD_DEFINITIONS, WizardField
from pasarbot.bot.handlers.admin_wizard import active_fields, next_field, parse_value, prompt_next
from pasarbot.config import Settings

SAMPLE_BOT_VALUE = "123456:abcdefghijklmnopqrstuvwxyz"


def test_choice_stage_renders_real_inline_buttons() -> None:
    class State:
        async def get_data(self):
            return {"flow": "admin", "values": {"telegram_id": 1190907109}}

    class Message:
        def __init__(self):
            self.markup = None

        async def answer(self, text, reply_markup):
            self.markup = reply_markup

    message = Message()
    settings = Settings(_env_file=None, bot_token=SAMPLE_BOT_VALUE,
                        owner_telegram_ids="12345", encryption_key="test-key",
                        callback_signing_key="test-key")
    asyncio.run(prompt_next(message, State(), settings, None, None))
    assert message.markup is not None
    callbacks = [button.callback_data for row in message.markup.inline_keyboard for button in row]
    assert "admin:wizard_choice:manager" in callbacks
    assert "admin:wizard_choice:support" in callbacks


def test_every_wizard_button_has_a_definition() -> None:
    callbacks = {
        action.callback
        for actions in ADMIN_SECTION_ACTIONS.values()
        for action in actions
        if action.callback.startswith("admin:wizard:")
    }
    keys = {callback.split(":", 2)[2] for callback in callbacks}
    assert keys.issubset(WIZARD_DEFINITIONS)


def test_plan_wizard_covers_all_contract_controls() -> None:
    keys = {field.key for field in WIZARD_DEFINITIONS["plan"].fields}
    assert {
        "name", "description", "price_irt", "renewal_price_irt", "duration_days",
        "limit_mode", "total_quota_gb", "max_users", "min_user_quota_gb",
        "max_user_quota_gb", "min_user_expire_days", "max_user_expire_days",
        "instance_id", "location_ids", "group_ids", "template_ids", "force_template",
        "hwid_enabled", "max_devices", "reset_allowed", "can_renew", "can_upgrade",
        "can_buy_volume", "can_upgrade_location", "is_visible",
    }.issubset(keys)


def test_conditional_plan_steps_follow_limit_mode() -> None:
    definition = WIZARD_DEFINITIONS["plan"]
    volume_keys = {field.key for field in active_fields(definition, {"limit_mode": "volume_only"})}
    user_keys = {field.key for field in active_fields(definition, {"limit_mode": "user_only"})}
    combined_keys = {field.key for field in active_fields(definition, {"limit_mode": "user_and_volume"})}
    assert "total_quota_gb" in volume_keys and "max_users" not in volume_keys
    assert "max_users" in user_keys and "total_quota_gb" not in user_keys
    assert {"total_quota_gb", "max_users"}.issubset(combined_keys)


def test_value_parser_and_next_step() -> None:
    assert parse_value(WizardField("card", "card", "", "card"), "6037 9975 1234 5678") == "6037-9975-1234-5678"
    assert parse_value(WizardField("quota", "quota", "", "gb"), "2.5") == int(2.5 * 1024**3)
    assert parse_value(WizardField("ids", "ids", "", "ids"), "4, 7") == [4, 7]
    definition = WIZARD_DEFINITIONS["card_account"]
    assert next_field(definition, {}).key == "title"
    assert next_field(definition, {"title": "ملت"}).key == "account_value"


if __name__ == "__main__":
    test_every_wizard_button_has_a_definition()
    test_plan_wizard_covers_all_contract_controls()
    test_conditional_plan_steps_follow_limit_mode()
    test_value_parser_and_next_step()
    print("admin-wizard-tests-passed")
