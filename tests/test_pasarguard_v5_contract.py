from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any

from pasarbot.integrations.pasarguard import (
    AdminSpec,
    PasarGuardClient,
    PasarGuardError,
    PasarGuardRoutes,
    RestrictedRoleSpec,
)
from pasarbot.services.runtime_provisioning import RuntimeProvisioner


class ContractTransport:
    """V5 request and response shapes checked against PasarGuard/panel b56ffe369f54."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    async def request(self, method: str, url: str, *, headers: dict[str, str],
                      json: dict[str, Any] | None = None, timeout: float = 20) -> tuple[int, Any]:
        assert headers.get("X-Api-Key") == "pg_key_test"
        assert "Authorization" not in headers
        self.calls.append((method, url, json))
        path = url.removeprefix("https://pg.example")
        if path == "/api/admin-role" and method == "POST":
            assert set(json or {}) == {"name", "permissions", "limits", "features", "access", "hwid",
                                      "disabled_when_limited", "disconnect_users_when_limited",
                                      "disconnect_users_when_disabled"}
            return 201, {"id": 19}
        if path == "/api/admin-role/19" and method == "PUT":
            return 200, {"id": 19}
        if path.startswith("/api/admin-roles?") and method == "GET":
            return 200, {"roles": [{"id": 19, "name": "reseller-2"}], "total": 1}
        if path == "/api/admin" and method == "POST":
            assert set(json or {}) == {"username", "password", "role_id", "status", "data_limit"}
            return 201, {"id": 7}
        if path.startswith("/api/admins?") and method == "GET":
            assert "usernames=r1" in path or "ids=7" in path
            return 200, {"admins": [{"id": 7, "username": "r1", "data_limit": 2048,
                                      "role": {"id": 19, "name": "reseller-2"}}], "total": 1}
        if path == "/api/admin/by-id/7" and method == "PUT":
            assert json == {"status": "disabled"}
            return 200, {"id": 7}
        if path == "/api/admin/by-id/7" and method == "DELETE":
            return 204, {}
        if path.startswith("/api/users?") and method == "GET":
            return 200, {"users": [
                {"id": 81, "username": "u1", "admin": {"id": 7},
                 "data_limit": 0, "used_traffic": 20, "status": "active"},
            ], "total": 1}
        if path.startswith("/api/groups?") and method == "GET":
            return 200, {"groups": [{"id": 4, "name": "de"}], "total": 1}
        if path.startswith("/api/user_templates?") and method == "GET":
            return 200, [{"id": 2, "name": "basic"}]
        return 404, {"detail": "unexpected route"}


def role() -> RestrictedRoleSpec:
    return RestrictedRoleSpec(
        name="reseller-2", max_users=None, min_user_quota_bytes=None,
        max_user_quota_bytes=None, min_user_expire_days=1, max_user_expire_days=30,
        allowed_group_ids=(4,), allowed_template_ids=(2,), hwid_enabled=True,
        max_devices=2, force_template=True, reset_allowed=False,
        total_quota_bytes=2 * 1024**4,
    )


class V5ContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_routes_payloads_and_scope(self) -> None:
        transport = ContractTransport()
        client = PasarGuardClient("https://pg.example", "pg_key_test", transport)
        self.assertEqual(await client.create_role(role()), "19")
        payload = transport.calls[-1][2]
        self.assertEqual(payload["permissions"]["users"]["delete"], {"scope": 1})
        self.assertIs(payload["permissions"]["users"]["create"], True)
        self.assertEqual(payload["limits"]["data_limit_min"], 1)
        self.assertEqual(payload["limits"]["data_limit_max"], 2 * 1024**4)
        self.assertEqual(payload["features"]["can_use_reset_strategy"], False)
        self.assertEqual(payload["access"]["allowed_group_ids"], [4])
        self.assertEqual(payload["hwid"]["mode"], "override")
        self.assertIs(payload["hwid"]["enabled"], True)
        await client.update_role("19", role())
        self.assertEqual((await client.find_role_by_name("reseller-2"))["id"], 19)
        self.assertEqual(await client.create_admin(AdminSpec("r1", "long-password", "19", (4,), (2,))), "7")
        self.assertEqual((await client.find_admin_by_username("r1"))["id"], 7)
        await client.verify_admin_limits("7", "19", 2048)
        self.assertTrue(any("/api/admins?ids=7" in call[1] for call in transport.calls))
        self.assertFalse(any(call[0] == "GET" and call[1].endswith("/api/admin/by-id/7")
                             for call in transport.calls))
        await client.suspend_admin("7")
        await client.delete_admin("7")
        self.assertEqual((await client.list_groups())[0]["id"], 4)
        self.assertEqual((await client.list_templates())[0]["id"], 2)
        snap = await client.reseller_snapshot("7")
        self.assertEqual(snap.unlimited_users, 1)

    async def test_owner_scope_fails_closed(self) -> None:
        class LeakingTransport(ContractTransport):
            async def request(self, method: str, url: str, **kwargs: Any) -> tuple[int, Any]:
                if "/api/users?" in url:
                    return 200, {"users": [{"id": 2, "username": "other", "admin": {"id": 9}}], "total": 1}
                return await super().request(method, url, **kwargs)

        client = PasarGuardClient("https://pg.example", "pg_key_test", LeakingTransport())
        with self.assertRaises(PasarGuardError):
            await client.reseller_snapshot("7")

    def test_route_defaults(self) -> None:
        self.assertEqual(PasarGuardRoutes().roles, "/api/admin-role")
        self.assertEqual(PasarGuardRoutes().admins, "/api/admin")
        self.assertEqual(PasarGuardRoutes().admin_list, "/api/admins")

    async def test_role_isolated_per_reseller(self) -> None:
        plan = SimpleNamespace(max_users=50, min_user_quota_bytes=None,
            max_user_quota_bytes=None, min_user_expire_days=None,
            max_user_expire_days=None, hwid_enabled=False, max_devices=None,
            force_template=False, reset_allowed=False)
        role_a = RuntimeProvisioner._role_spec(plan, role_name=RuntimeProvisioner._role_name(10),
            groups=[4], templates=[], quota=100, max_users=50)
        role_b = RuntimeProvisioner._role_spec(plan, role_name=RuntimeProvisioner._role_name(11),
            groups=[7], templates=[], quota=100, max_users=50)
        self.assertNotEqual(role_a.name, role_b.name)
        self.assertEqual(role_a.to_payload()["access"]["allowed_group_ids"], [4])
        self.assertEqual(role_b.to_payload()["access"]["allowed_group_ids"], [7])

        class FakeClient:
            async def find_admin_by_username(self, username: str) -> dict[str, Any]:
                return {"id": 3, "username": username, "role": {"id": 19, "name": "reseller-10"}}

        reseller = SimpleNamespace(id=11, panel_username="r11", pasarguard_admin_id="3")
        with self.assertRaisesRegex(PasarGuardError, "isolated role"):
            await RuntimeProvisioner._reseller_role_id(FakeClient(), reseller)


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(V5ContractTests)
    )
    if result.wasSuccessful():
        print("pasarguard-v5-contract-passed")
    raise SystemExit(not result.wasSuccessful())
