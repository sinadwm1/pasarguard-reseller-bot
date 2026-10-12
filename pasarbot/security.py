from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass


class InvalidCallback(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CallbackSigner:
    key: bytes
    signature_size: int = 8

    def encode(self, action: str, resource_id: int, version: int = 1) -> str:
        body = f"{action}:{resource_id}:{version}"
        signature = hmac.new(self.key, body.encode(), hashlib.sha256).digest()[: self.signature_size]
        token = base64.urlsafe_b64encode(signature).decode().rstrip("=")
        result = f"{body}:{token}"
        if len(result.encode()) > 64:
            raise ValueError("Telegram callback_data exceeds 64 bytes")
        return result

    def decode(self, value: str) -> tuple[str, int, int]:
        try:
            action, resource_id, version, supplied = value.rsplit(":", 3)
            body = f"{action}:{resource_id}:{version}"
            padding = "=" * (-len(supplied) % 4)
            supplied_bytes = base64.urlsafe_b64decode(supplied + padding)
            expected = hmac.new(self.key, body.encode(), hashlib.sha256).digest()[: self.signature_size]
            if not hmac.compare_digest(supplied_bytes, expected):
                raise InvalidCallback("invalid callback signature")
            return action, int(resource_id), int(version)
        except (ValueError, TypeError) as exc:
            if isinstance(exc, InvalidCallback):
                raise
            raise InvalidCallback("malformed callback data") from exc


def stable_idempotency_key(*parts: object) -> str:
    canonical = json.dumps(parts, ensure_ascii=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()
