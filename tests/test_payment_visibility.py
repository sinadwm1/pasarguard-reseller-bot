from __future__ import annotations

from pathlib import Path

from pasarbot.bot.admin_schema import ACTIVE_PAYMENT_KINDS, ADMIN_SECTION_ACTIONS

ROOT = Path(__file__).resolve().parents[1]


def test_only_manual_and_wallet_are_customer_visible() -> None:
    assert ACTIVE_PAYMENT_KINDS == frozenset({"manual", "wallet"})


def test_admin_payment_menu_has_no_gateway_or_crypto_action() -> None:
    combined = " ".join(
        f"{action.label} {action.callback}" for action in ADMIN_SECTION_ACTIONS["payments"]
    ).lower()
    assert "crypto" not in combined
    assert "gateway" not in combined
    assert "درگاه" not in combined
    assert "کریپتو" not in combined


def test_customer_query_enforces_the_allowlist() -> None:
    source = (ROOT / "pasarbot/bot/handlers/customer.py").read_text(encoding="utf-8")
    assert "PaymentMethod.kind.in_(ACTIVE_PAYMENT_KINDS)" in source
    assert "method.kind not in ACTIVE_PAYMENT_KINDS" in source


if __name__ == "__main__":
    test_only_manual_and_wallet_are_customer_visible()
    test_admin_payment_menu_has_no_gateway_or_crypto_action()
    test_customer_query_enforces_the_allowlist()
    print("payment-visibility-tests-passed")
