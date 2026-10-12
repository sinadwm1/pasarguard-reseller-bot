"""Focused offline acceptance checks for the v1.6 provisioning repair."""
from __future__ import annotations

import asyncio
import sys
import zipfile
from pathlib import Path
from typing import Any

from pasarbot.integrations.pasarguard import (
    AdminSpec,
    PasarGuardClient,
    RestrictedRoleSpec,
)

ROOT = Path(__file__).resolve().parents[1]


def spec() -> RestrictedRoleSpec:
    return RestrictedRoleSpec(
        name="reseller-42",
        max_users=50,
        min_user_quota_bytes=1024**3,
        max_user_quota_bytes=100 * 1024**3,
        min_user_expire_days=1,
        max_user_expire_days=30,
        allowed_group_ids=(1,),
        allowed_template_ids=(),
        hwid_enabled=False,
        max_devices=None,
        total_quota_bytes=2 * 1024**4,
    )


class Transport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    async def request(self, method: str, url: str, *, headers: dict[str, str],
                      json: dict[str, Any] | None = None, timeout: float = 20) -> tuple[int, Any]:
        self.calls.append((method, url, json))
        path = url.removeprefix("https://pg.example")
        if path == "/api/admin-role" and method == "POST":
            return 201, {"id": 19}
        if path == "/api/admin-role/19" and method == "GET":
            return 200, {"id": 19, **spec().to_payload()}
        if path == "/api/admin" and method == "POST":
            return 201, {"id": 7, **(json or {})}
        if path.startswith("/api/admins?") and method == "GET" and "ids=7" in path:
            return 200, {"admins": [{
                "id": 7,
                "username": "reseller-42",
                "data_limit": 2 * 1024**4,
                "role": {"id": 19, "name": "reseller-42"},
            }], "total": 1}
        return 405, {"detail": "unsupported method in contract test"}


async def check_contract() -> None:
    role = spec()
    payload = role.to_payload()
    assert payload["permissions"]["users"]["read"] == {"scope": 1}
    assert payload["permissions"]["users"]["update"] == {"scope": 1}
    assert payload["permissions"]["users"]["delete"] == {"scope": 1}
    assert payload["limits"]["max_users"] == 50
    assert payload["limits"]["data_limit_max"] == 100 * 1024**3
    assert payload["hwid"]["enabled"] is False
    assert payload["disabled_when_limited"] is True

    transport = Transport()
    client = PasarGuardClient("https://pg.example", "pg_key_test", transport)
    role_id = await client.create_role(role)
    await client.verify_role(role_id, role)
    admin_id = await client.create_admin(AdminSpec(
        "reseller-42", "safe-password", role_id, (1,), (),
        enabled=False, data_limit_bytes=2 * 1024**4,
    ))
    await client.verify_admin_limits(admin_id, role_id, 2 * 1024**4)
    assert not any(method == "GET" and "/api/admin/by-id/" in url
                   for method, url, _ in transport.calls)
    assert any(method == "GET" and "/api/admins?ids=7" in url
               for method, url, _ in transport.calls)
    print("v16-contract-passed")


def check_recovery() -> None:
    runtime = (ROOT / "pasarbot/services/runtime_provisioning.py").read_text(encoding="utf-8")
    sync = (ROOT / "pasarbot/services/sync.py").read_text(encoding="utf-8")
    app = (ROOT / "pasarbot/bot/app.py").read_text(encoding="utf-8")
    assert 'Order.error_message.ilike("%returned 405%")' in runtime
    assert "Recovered abandoned provisioning claim" in runtime
    assert "await session.commit()" in runtime
    assert "await provisioner.recover_incomplete_orders()" in app
    assert "repaired_limits" in sync and '"data_limit": reseller.total_quota_bytes' in sync
    print("v16-recovery-passed")


def check_package() -> None:
    archive = ROOT / "PasarGuard-Reseller-Bot-v1.6.0.zip"
    with zipfile.ZipFile(archive) as package:
        names = set(package.namelist())
        assert package.testzip() is None
        assert {"UPGRADE_V16_FA.md", "scripts/verify_v16.py",
                "tests/test_runtime_provisioning_v16.py"} <= names
        assert ".env" not in names
        assert not any(name.startswith("backups/") for name in names)
    print("v16-package-passed")


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "contract":
        asyncio.run(check_contract())
    elif command == "recovery":
        check_recovery()
    elif command == "package":
        check_package()
    else:
        raise SystemExit("usage: verify_v16.py contract|recovery|package")
