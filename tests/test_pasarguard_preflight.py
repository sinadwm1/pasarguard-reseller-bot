from __future__ import annotations

import unittest
from typing import Any

from pasarbot.integrations.pasarguard import PasarGuardClient, PasarGuardError


class PreflightTransport:
    def __init__(self, *, owner: bool = True, forbidden_path: str | None = None) -> None:
        self.owner = owner
        self.forbidden_path = forbidden_path
        self.paths: list[str] = []

    async def request(self, method: str, url: str, *, headers: dict[str, str],
                      json: dict[str, Any] | None = None, timeout: float = 20) -> tuple[int, Any]:
        self.paths.append(url)
        assert method == "GET"
        assert headers["X-Api-Key"] == "pg_key_test"
        if url.endswith(self.forbidden_path or "no-forbidden-path"):
            return 403, {"detail": "Permission denied"}
        if url.endswith("/api/admin"):
            return 200, {"username": "owner", "role": {"is_owner": self.owner}}
        return 200, {}


class PreflightTests(unittest.IsolatedAsyncioTestCase):
    async def test_full_preflight(self) -> None:
        transport = PreflightTransport()
        client = PasarGuardClient("https://pg.example", "pg_key_test", transport)
        self.assertEqual((await client.preflight())["username"], "owner")
        self.assertEqual(len(transport.paths), 6)
        self.assertTrue(any("/api/users?limit=1" in path for path in transport.paths))

    async def test_reject_short_lived_token(self) -> None:
        transport = PreflightTransport()
        with self.assertRaisesRegex(PasarGuardError, "pg_key_"):
            await PasarGuardClient("https://pg.example", "eyJ.jwt", transport).preflight()
        self.assertEqual(transport.paths, [])

    async def test_reject_non_owner_key(self) -> None:
        client = PasarGuardClient("https://pg.example", "pg_key_test", PreflightTransport(owner=False))
        with self.assertRaisesRegex(PasarGuardError, "Owner"):
            await client.preflight()

    async def test_reject_missing_permission(self) -> None:
        client = PasarGuardClient("https://pg.example", "pg_key_test",
                                  PreflightTransport(forbidden_path="/api/groups?limit=1"))
        with self.assertRaisesRegex(PasarGuardError, "403"):
            await client.preflight()


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(PreflightTests)
    )
    if result.wasSuccessful():
        print("pasarguard-preflight-passed")
    raise SystemExit(not result.wasSuccessful())
