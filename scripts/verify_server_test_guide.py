from pathlib import Path

root = Path(__file__).resolve().parents[1]
guide = (root / "SERVER_TEST_FA.md").read_text(encoding="utf-8")
setup = (root / "setup.sh").read_text(encoding="utf-8")
adapter = (root / "pasarbot/integrations/pasarguard.py").read_text(encoding="utf-8")

for part in (
    "https://github.com/PasarGuard/scripts/raw/main/pasarguard.sh",
    "pg_key_", "inherit permissions", "/api/admin", "/dashboard/",
    "docker compose ps", "./setup.sh", "SERVER_IP", "BotFather", "Sync فوری",
    "PasarGuard-Reseller-Bot-v1.6.0.zip",
):
    if part not in guide:
        raise SystemExit(f"server guide missing: {part}")

if 'if [[ -f .env ]]; then' not in setup or 'فایل .env موجود است' not in setup:
    raise SystemExit("setup must preserve the existing .env")
if 'docker compose ps --status running --services | grep -qx "bot"' not in setup:
    raise SystemExit("setup must check that bot service is running")
if '"X-Api-Key": self.access_token' not in adapter:
    raise SystemExit("API key header not found")

print("server-test-guide-passed")
