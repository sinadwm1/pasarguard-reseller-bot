from __future__ import annotations

import unittest

from pasarbot.domain.enums import Permission
from pasarbot.domain.telegram import (
    ButtonSpec,
    RequiredChannel,
    SafeTemplate,
    check_channel_access,
)
from pasarbot.security import CallbackSigner, InvalidCallback
from pasarbot.services.authorization import AccessDenied, effective_permissions, require_permission


class Membership:
    def __init__(self, statuses: dict[str, str]) -> None:
        self.statuses = statuses

    async def member_status(self, chat_id: str, user_id: int) -> str:
        return self.statuses[chat_id]


class BotSecurityAndUiTests(unittest.IsolatedAsyncioTestCase):
    def test_signed_callback_detects_tampering(self) -> None:
        signer = CallbackSigner(b"a-secret-long-enough")
        token = signer.encode("receipt.approve", 91, 3)
        self.assertEqual(signer.decode(token), ("receipt.approve", 91, 3))
        with self.assertRaises(InvalidCallback):
            signer.decode(token.replace(":91:", ":92:"))

    def test_admin_permissions_are_server_side(self) -> None:
        support = effective_permissions(10, [1], "support")
        self.assertIn(Permission.CUSTOMER_VIEW, support)
        with self.assertRaises(AccessDenied):
            require_permission(support, Permission.WALLET_MANAGE)
        owner = effective_permissions(1, [1], None, active=False)
        self.assertEqual(owner, frozenset(Permission))

    def test_modern_buttons_and_safe_fallback(self) -> None:
        spec = ButtonSpec(
            "تأیید",
            callback_data="approve:1",
            style="success",
            custom_emoji_id="5368",
        )
        modern = spec.payload()
        self.assertEqual(modern["style"], "success")
        self.assertEqual(modern["icon_custom_emoji_id"], "5368")
        legacy = spec.payload(modern_features=False)
        self.assertNotIn("style", legacy)
        self.assertNotIn("icon_custom_emoji_id", legacy)

    async def test_channel_lock_requires_every_channel(self) -> None:
        channels = (
            RequiredChannel("@one", "One", "https://t.me/one"),
            RequiredChannel("@two", "Two", "https://t.me/two"),
        )
        result = await check_channel_access(
            Membership({"@one": "member", "@two": "left"}), 7, channels
        )
        self.assertFalse(result.allowed)
        self.assertEqual([item.chat_id for item in result.missing], ["@two"])

    def test_text_variables_are_allowlisted_and_escaped(self) -> None:
        template = SafeTemplate("سلام {name}، سفارش {order_id}", {"name", "order_id"})
        rendered = template.render(name="<b>x</b>", order_id=2)
        self.assertIn("&lt;b&gt;x&lt;/b&gt;", rendered)
        with self.assertRaises(ValueError):
            SafeTemplate("{secret}", {"name"})


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(BotSecurityAndUiTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if result.wasSuccessful():
        print("bot-security-ui-tests-passed")
    raise SystemExit(not result.wasSuccessful())
