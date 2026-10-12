from __future__ import annotations

import base64
import secrets

fernet_key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
signing_key = secrets.token_urlsafe(48)

print(f"ENCRYPTION_KEY={fernet_key}")
print(f"CALLBACK_SIGNING_KEY={signing_key}")
