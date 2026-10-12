# امنیت، Backup و بازیابی

## مرزهای امنیتی

- دستور مخفی Admin به‌تنهایی امنیت نیست؛ Telegram ID و permission مبنا هستند.
- عضویت در گروه رسید مجوز تأیید نیست.
- callback رسید HMAC و version دارد، ولی permission در لحظه اجرا دوباره از دیتابیس خوانده می‌شود.
- قیمت، Plan، Customer و مبلغ هرگز از callback پذیرفته نمی‌شوند؛ Server آن‌ها را از دیتابیس می‌خواند.
- کلید API PasarGuard و رمز پنل با Fernet رمز می‌شوند.
- Role نماینده باید `scope=own` و permission حداقلی داشته باشد.
- تمام ارتباطات PasarGuard در production باید HTTPS و TLS verification فعال داشته باشند.

## Secretها

`.env` را commit، پیام یا screenshot نکنید. دسترسی پیشنهادی:

```bash
chmod 600 .env
```

`ENCRYPTION_KEY` را همراه Backup دیتابیس، ولی در محل جداگانه نگه دارید. بدون آن Tokenها و رمزهای ذخیره‌شده قابل بازیابی نیستند. تغییر این کلید بدون re-encrypt کردن رکوردها باعث توقف اتصال می‌شود.

اگر Bot Token لو رفت:

1. در BotFather آن را revoke کنید.
2. مقدار جدید را در `.env` قرار دهید.
3. `docker compose up -d --force-recreate bot` را اجرا کنید.

اگر کلید API PasarGuard لو رفت، برای پنل ربات کلید تازه بسازید و از `🔌 پاسارگارد` ← `📋 پنل‌های متصل` ← انتخاب پنل ← `✏️ ویرایش اتصال / تعویض کلید` جایگزین کنید. پس از تست موفق، کلید قبلی را در PasarGuard revoke کنید.

## Backup روزانه PostgreSQL

```bash
mkdir -p /opt/backups/pasarbot
docker compose exec -T postgres pg_dump \
  -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc \
  > /opt/backups/pasarbot/pasarbot-$(date +%F-%H%M).dump
```

این command متغیرهای shell میزبان را می‌خواند. اگر export نشده‌اند، مقادیر واقعی user/database را صریح وارد کنید. فایل Backup را رمزنگاری و به سرور دیگری منتقل کنید.

Redis منبع نهایی مالی نیست؛ Stateهای کوتاه FSM در Redis هستند و از دست‌رفتن آن فقط wizard فعال کاربر را قطع می‌کند. PostgreSQL منبع حقیقت است.

## تست بازیابی

بازیابی را ابتدا روی دیتابیس آزمایشی انجام دهید:

```bash
createdb pasarbot_restore_test
pg_restore --clean --if-exists -d pasarbot_restore_test /path/to/backup.dump
```

بعد تعداد رکوردهای `orders`, `receipts`, `resellers`, `accounting_entries` را با production مقایسه کنید. Backup بدون تست restore قابل اعتماد نیست.

## Rotation کلید رمزنگاری

Rotation نیازمند decrypt با کلید قدیم و encrypt با کلید جدید در یک maintenance script تراکنشی است. هرگز ابتدا `.env` را عوض نکنید. ترتیب امن:

1. توقف Worker provisioning و Sync
2. Backup کامل
3. re-encrypt تمام `encrypted_*`ها
4. تغییر `.env`
5. تست یک instance و سپس شروع Worker

## Rate limit و Flood

ارسال همگانی با فاصله 40ms انجام می‌شود، ولی Telegram ممکن است `retry_after` بدهد. برای مخاطب بسیار زیاد، queue جدا و احترام به مقدار `retry_after` اضافه کنید. خطای یک گیرنده نباید ارسال بقیه را متوقف کند.

## لاگ و داده حساس

کد خطا Token یا رمز خام را log نمی‌کند. در production سطح `INFO` مناسب است. logها را برای مدت محدود نگه دارید و دسترسی Docker group را فقط به مدیر سیستم بدهید؛ عضویت در گروه docker عملاً دسترسی root می‌دهد.

## بررسی امنیتی قبل از فروش واقعی

- ورود اکانت عادی با دستور Admin رد شود.
- Support نتواند Wallet یا Plan را تغییر دهد.
- عضو عادی گروه نتواند Receipt را approve کند.
- Admin نماینده نتواند Admin، Role، API Key، Node، Core، Host یا User سایر نمایندگان را ببیند.
- Group ID غیرمجاز قابل assign نباشد.
- دو کلیک همزمان فقط یک Approval بسازد.
- HTTP 503 پول را از بین نبرد و Order برای retry باقی بماند.
- Backup و Restore آزمایشی موفق باشد.

## به‌روزرسانی

```bash
cd /opt/pasarbot
cp .env /opt/backups/pasarbot/env-before-update
docker compose exec -T postgres pg_dump -U pasarbot -d pasarbot -Fc > /opt/backups/pasarbot/before-update.dump
docker compose build --pull
docker compose run --rm migrate
docker compose up -d
docker compose logs --tail=200 bot
```

قبل از update بزرگ PasarGuard، اگر OpenAPI در تنظیمات پنل فعال است، `scripts/pasarguard_probe.py --url https://YOUR_DOMAIN:8000` را اجرا کنید. اگر OpenAPI غیرفعال باشد، این تست 404 می‌دهد؛ از دکمه «تست اتصال» در بات استفاده کنید. نسخه پنل ممکن است route یا schema را تغییر دهد؛ تغییر نام فیلدهای payload باید در محیط تست بررسی شود.
