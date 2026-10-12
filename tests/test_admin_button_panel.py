from __future__ import annotations

from pathlib import Path

from pasarbot.bot.admin_schema import ADMIN_REPLY_SECTIONS, ADMIN_SECTION_ACTIONS
from pasarbot.bot.keyboards import admin_reply_menu

ROOT = Path(__file__).resolve().parents[1]


def test_all_admin_sections_have_reply_and_inline_controls() -> None:
    expected = {
        "dashboard", "receipts", "plans", "locations", "customers", "resellers",
        "orders", "payments", "accounting", "reports", "admins", "channel_locks", "support",
        "receipt_groups", "pasarguard", "texts", "buttons", "broadcast", "settings", "help",
    }
    assert set(ADMIN_REPLY_SECTIONS.values()) == expected
    assert set(ADMIN_SECTION_ACTIONS) == expected
    assert all(ADMIN_SECTION_ACTIONS[section] for section in expected)


def test_reply_keyboard_contains_every_section_and_exit() -> None:
    markup = admin_reply_menu()
    labels = {button.text for row in markup.keyboard for button in row}
    assert set(ADMIN_REPLY_SECTIONS).issubset(labels)
    assert "🚪 خروج از مدیریت" in labels
    assert markup.resize_keyboard is True
    assert markup.is_persistent is True


def test_admin_actions_use_callbacks_not_text_commands() -> None:
    for actions in ADMIN_SECTION_ACTIONS.values():
        for action in actions:
            assert action.callback.startswith("admin:")
            assert "/" not in action.callback
    assert not (ROOT / "pasarbot/bot/handlers/admin_commands.py").exists()
    app_source = (ROOT / "pasarbot/bot/app.py").read_text(encoding="utf-8")
    assert "admin_commands_router" not in app_source


if __name__ == "__main__":
    test_all_admin_sections_have_reply_and_inline_controls()
    test_reply_keyboard_contains_every_section_and_exit()
    test_admin_actions_use_callbacks_not_text_commands()
    print("admin-button-panel-tests-passed")
