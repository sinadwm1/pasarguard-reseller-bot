from __future__ import annotations

import unittest
from typing import Any

from pasarbot.integrations.pasarguard import (
    AdminSpec,
    PasarGuardClient,
    RestrictedRoleSpec,
)


class FakeTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []
        self.user_page = 0

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, Any] | None = None,
        timeout: float = 20,
    ) -> tuple[int, Any]:
        self.calls.append((method, url, json))
        self.assert_auth(headers)
        if method == "POST" and url.endswith("/api/admin-role"):
            return 201, {"id": 3}
        if method == "POST" and url.endswith("/api/admin"):
            return 201, {"id": 7}
        if "/api/users?" in url:
            if "offset=0" in url:
                return 200, {"users": [
                    {"id": i, "username": f"u{i}", "admin": {"id": 7}, "data_limit": 10, "used_traffic": 3, "status": "active"}
                    for i in range(200)
                ]}
            return 200, {"users": [
                {"id": 200, "username": "u200", "admin": {"id": 7}, "data_limit": 20, "used_traffic": 4, "status": "active"}
            ]}
        return 200, {}

    @staticmethod
    def assert_auth(headers: dict[str, str]) -> None:
        if headers.get("X-Api-Key") != "pg_key_test":
            raise AssertionError("missing API key")


class PasarGuardAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.transport = FakeTransport()
        self.client = PasarGuardClient("https://pg.example", "pg_key_test", self.transport)

    async def test_role_payload_is_restricted(self) -> None:
        role_id = await self.client.create_role(
            RestrictedRoleSpec(
                name="reseller",
                max_users=50,
                min_user_quota_bytes=1,
                max_user_quota_bytes=100,
                min_user_expire_days=1,
                max_user_expire_days=30,
                allowed_group_ids=(4, 7),
                allowed_template_ids=(2,),
                hwid_enabled=True,
                max_devices=2,
                total_quota_bytes=200,
            )
        )
        self.assertEqual(role_id, "3")
        sent = self.transport.calls[-1][2]
        self.assertEqual(sent["permissions"]["users"]["read"], {"scope": 1})
        self.assertIs(sent["hwid"]["enabled"], True)
        self.assertEqual(sent["access"]["allowed_group_ids"], [4, 7])
        self.assertNotIn("admins", sent["permissions"])
        self.assertNotIn("api_keys", sent["permissions"])
        self.assertEqual(sent["limits"]["expire_max"], 30 * 86400)
        self.assertEqual(sent["limits"]["data_limit_min"], 1)

    async def test_admin_creation_assigns_role_and_groups(self) -> None:
        admin_id = await self.client.create_admin(AdminSpec("r1", "pw", "3", (4,), (2,)))
        self.assertEqual(admin_id, "7")
        payload = self.transport.calls[-1][2]
        self.assertEqual(payload["role_id"], 3)
        self.assertEqual(payload["status"], "active")

    async def test_snapshot_paginates_and_aggregates(self) -> None:
        snapshot = await self.client.reseller_snapshot("7")
        self.assertEqual(snapshot.users_count, 201)
        self.assertEqual(snapshot.allocated_bytes, 2020)
        self.assertEqual(snapshot.actual_used_bytes, 604)
        calls = [call for call in self.transport.calls if "/api/users?" in call[1]]
        self.assertEqual(len(calls), 2)
        self.assertIn("admin_ids=7", calls[0][1])


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(PasarGuardAdapterTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if result.wasSuccessful():
        print("pasarguard-adapter-tests-passed")
    raise SystemExit(not result.wasSuccessful())
