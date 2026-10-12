from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
guide = (ROOT / "INSTALL_FA.md").read_text(encoding="utf-8")
operations = (ROOT / "OPERATIONS_FA.md").read_text(encoding="utf-8")
readme = (ROOT / "README.md").read_text(encoding="utf-8")

for required in (
    "./setup.sh",
    "BotFather",
    "Telegram ID",
    "Docker",
    "PasarGuard",
    "کارت‌به‌کارت",
    "کیف پول",
    "گروه رسیدها",
    "قفل کانال",
    "پلن",
    "عیب‌یابی",
    "نسخه پشتیبان",
):
    if required not in guide:
        raise SystemExit(f"simple guide topic missing: {required}")

for forbidden in (
    "/plan_add",
    "/pg_add",
    "/location_add",
    "/payment_add",
    "/addon_add",
    "/receipt_group",
    "/channel_add",
    "/admin_add",
    "/text_set",
    "/button_set",
    "/broadcast",
    "/admin_help",
):
    if forbidden in "\n".join((guide, operations, readme)):
        raise SystemExit(f"text admin command still documented: {forbidden}")

print("simple-deploy-guide-passed")
