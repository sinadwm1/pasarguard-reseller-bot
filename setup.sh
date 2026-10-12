#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

say() { printf '\n%s\n' "$1"; }
fail() { printf '\nخطا: %s\n' "$1" >&2; exit 1; }

say "راه‌انداز خودکار ربات نمایندگی PasarGuard"

command -v docker >/dev/null 2>&1 || fail "Docker نصب نیست. بخش «نصب Docker» در INSTALL_FA.md را انجام دهید."
docker compose version >/dev/null 2>&1 || fail "Docker Compose در دسترس نیست. Docker رسمی را نصب یا به‌روزرسانی کنید."
docker info >/dev/null 2>&1 || fail "Docker اجرا نیست یا کاربر فعلی دسترسی ندارد. دسترسی Docker را طبق راهنما درست کنید."
command -v openssl >/dev/null 2>&1 || fail "بسته openssl نصب نیست."

if [[ -f .env ]]; then
  say "فایل .env موجود است. کلیدها و رمز دیتابیس حفظ می‌شوند و سرویس‌ها با همین تنظیمات به‌روزرسانی می‌شوند."
else
  read -r -p "توکن BotFather را وارد کنید: " bot_token
  [[ "$bot_token" =~ ^[0-9]{6,}:[A-Za-z0-9_-]{20,}$ ]] || fail "قالب توکن درست نیست. توکن کامل BotFather را دوباره کپی کنید."

  read -r -p "Telegram ID عددی مالک را وارد کنید: " owner_id
  [[ "$owner_id" =~ ^[0-9]{5,20}$ ]] || fail "Telegram ID باید فقط عدد باشد."

  db_password="$(openssl rand -hex 24)"
  encryption_key="$(openssl rand -base64 32 | tr '+/' '-_' | tr -d '\n')"
  callback_key="$(openssl rand -hex 32)"

  cat > .env <<EOF
BOT_TOKEN=$bot_token
OWNER_TELEGRAM_IDS=$owner_id
ADMIN_COMMAND=/owner
TELEGRAM_BUTTON_FEATURES=true
ENCRYPTION_KEY=$encryption_key
CALLBACK_SIGNING_KEY=$callback_key
DATABASE_URL=postgresql+asyncpg://pasarbot:$db_password@postgres:5432/pasarbot
POSTGRES_DB=pasarbot
POSTGRES_USER=pasarbot
POSTGRES_PASSWORD=$db_password
REDIS_URL=redis://redis:6379/0
ENVIRONMENT=production
LOG_LEVEL=INFO
SYNC_INTERVAL_SECONDS=300
QUOTA_ENFORCEMENT_MODE=suspend
EXPIRE_ALERT_DAYS=7,3,1
DEFAULT_CURRENCY=IRT
EOF
  chmod 600 .env
fi

say "تنظیمات ساخته شد؛ دیتابیس و بات در حال راه‌اندازی هستند..."
docker compose up -d --build

say "وضعیت سرویس‌ها:"
docker compose ps

if ! docker compose ps --status running --services | grep -qx "bot"; then
  docker compose logs --tail=120 bot
  fail "بات Running نشد. خروجی بالا را بررسی کنید و بخش عیب‌یابی راهنما را بخوانید."
fi

say "راه‌اندازی تمام شد. در تلگرام وارد بات شوید، /start و سپس /owner را بزنید. تمام تنظیمات بعدی دکمه‌ای است."
