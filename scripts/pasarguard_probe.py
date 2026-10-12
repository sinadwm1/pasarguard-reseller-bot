from __future__ import annotations

import argparse
import json
import ssl
import urllib.request
from typing import Any
from urllib.parse import urlparse


def fetch(url: str, token: str | None, insecure: bool) -> dict[str, Any]:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("URL must use http or https")
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1"}:
        raise ValueError("plain HTTP is allowed only for a local PasarGuard probe")
    request = urllib.request.Request(url.rstrip("/") + "/openapi.json")  # noqa: S310
    if token:
        request.add_header("X-Api-Key", token)
    context = ssl._create_unverified_context() if insecure else None  # noqa: S323
    with urllib.request.urlopen(request, timeout=20, context=context) as response:  # noqa: S310
        return json.load(response)


def discover(document: dict[str, Any]) -> dict[str, str]:
    paths: dict[str, Any] = document.get("paths", {})
    result: dict[str, str] = {}
    candidates = {
        "admins": ("/api/admin", "post"),
        "admin_list": ("/api/admins", "get"),
        "admin_detail": ("/api/admin/by-id/{admin_id}", "put"),
        "roles": ("/api/admin-role", "post"),
        "role_list": ("/api/admin-roles", "get"),
        "role_detail": ("/api/admin-role/{role_id}", "put"),
        "users": ("/api/users", "get"),
        "groups": ("/api/groups", "get"),
        "templates": ("/api/user_templates", "get"),
        "current_admin": ("/api/admin", "get"),
    }
    for key, (path, method) in candidates.items():
        if method in paths.get(path, {}):
            result[key] = path
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect PasarGuard OpenAPI route compatibility")
    parser.add_argument("--url", required=True, help="PasarGuard base URL")
    parser.add_argument("--token", help="Optional pg_key_ API key")
    parser.add_argument("--insecure", action="store_true")
    args = parser.parse_args()
    routes = discover(fetch(args.url, args.token, args.insecure))
    required = set(("users", "admins", "admin_list", "admin_detail", "roles", "role_list",
                    "role_detail", "groups", "templates", "current_admin"))
    print(json.dumps({"route_overrides": routes}, indent=2, ensure_ascii=False))
    missing = required - routes.keys()
    if missing:
        raise SystemExit(f"Missing required operations: {', '.join(sorted(missing))}")
    print("PasarGuard OpenAPI compatibility probe passed")


if __name__ == "__main__":
    main()
