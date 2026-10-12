from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = [
    "README.md",
    "INSTALL_FA.md",
    "OPERATIONS_FA.md",
    "SECURITY_FA.md",
    ".env.example",
    "Dockerfile",
    "docker-compose.yml",
    "setup.sh",
    "backup.sh",
    "alembic.ini",
    "alembic/env.py",
    "alembic/versions/20260926_01_initial.py",
    "pasarbot/main.py",
    "pasarbot/domain/quota.py",
    "pasarbot/integrations/pasarguard.py",
    "pasarbot/bot/handlers/customer.py",
    "pasarbot/bot/handlers/admin.py",
    "pasarbot/bot/handlers/admin_buttons.py",
    "pasarbot/bot/handlers/admin_wizard.py",
]

missing = [path for path in REQUIRED if not (ROOT / path).is_file()]
if missing:
    raise SystemExit(f"missing delivery files: {', '.join(missing)}")

for path in (ROOT / "pasarbot").rglob("*.py"):
    ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
for path in (ROOT / "scripts").glob("*.py"):
    ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
for service in ("postgres:", "redis:", "migrate:", "bot:"):
    if service not in compose:
        raise SystemExit(f"docker compose service missing: {service}")

env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
for key in (
    "BOT_TOKEN",
    "OWNER_TELEGRAM_IDS",
    "ENCRYPTION_KEY",
    "CALLBACK_SIGNING_KEY",
    "DATABASE_URL",
    "REDIS_URL",
):
    if not re.search(rf"^{key}=", env_example, flags=re.MULTILINE):
        raise SystemExit(f"environment key missing: {key}")

combined = "\n".join(
    path.read_text(encoding="utf-8")
    for directory in (ROOT / "pasarbot", ROOT / "scripts")
    for path in directory.rglob("*.py")
    if path.name != "verify_delivery.py"
)
if re.search(r"\b(TODO|FIXME|NotImplementedError)\b", combined):
    raise SystemExit("unfinished placeholder found in delivery code")

install = (ROOT / "INSTALL_FA.md").read_text(encoding="utf-8")
for topic in ("Docker", "PasarGuard", "پلن", "گروه رسیدها", "قفل کانال", "عیب‌یابی", "./setup.sh"):
    if topic not in install:
        raise SystemExit(f"installation guide topic missing: {topic}")

print("delivery-verification-passed")
