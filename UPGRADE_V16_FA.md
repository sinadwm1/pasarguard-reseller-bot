# ارتقا به نسخهٔ ۱.۶ و رفع خرابی ساخت نمایندگی

این راهنما برای سروری است که ربات در `/opt/pasarbot` و PasarGuard روی همان سرور یا یک سرور قابل دسترس اجرا می‌شود. فایل `.env` و دیتابیس فعلی حفظ می‌شوند.

## چه چیزی در نسخهٔ قبلی خراب بود؟

نسخهٔ ۱.۵ بعد از ساخت یا ویرایش Admin برای کنترل نتیجه، درخواست زیر را می‌فرستاد:

```text
GET /api/admin/by-id/{id}
```

PasarGuard 5.4.1 این آدرس را برای `PUT` و `DELETE` دارد، اما برای `GET` ندارد. در نتیجه PasarGuard ممکن بود Role و Admin را ساخته باشد، ولی مرحلهٔ بررسی با خطای `405 Method Not Allowed` متوقف می‌شد و سفارش در ربات `FAILED` می‌شد.

یک خطای امنیتی دیگر هم اصلاح شد: در PasarGuard 5.4.1 مقدار scope برابر `1` یعنی `OWN` و مقدار `2` یعنی `ALL` است. نسخهٔ ۱.۵ به اشتباه `2` نوشته بود. نسخهٔ ۱.۶ برای خواندن، ویرایش و حذف User فقط `scope=1` می‌فرستد.

نسخهٔ ۱.۶ اطلاعات Admin را از مسیر معتبر زیر می‌خواند:

```text
GET /api/admins?ids=ADMIN_ID
```

همچنین شناسهٔ محلی نمایندگی قبل از درخواست خارجی ثبت می‌شود تا قطع شبکه باعث ساخت Admin تکراری در تلاش بعدی نشود.

## ۱. فایل جدید را با Bitvise روی سرور بفرستید

فایل زیر را در مسیر `/opt` آپلود کنید:

```text
PasarGuard-Reseller-Bot-v1.6.0.zip
```

## ۲. قبل از ارتقا بکاپ بگیرید

در ترمینال Bitvise بزنید:

```bash
cd /opt/pasarbot
mkdir -p backups
docker compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' | gzip > backups/before-v16.sql.gz
cp .env backups/env-before-v16
ls -lh backups/before-v16.sql.gz backups/env-before-v16
```

اگر دو فایل در خروجی دیده شدند، بکاپ ساخته شده است.

## ۳. نسخهٔ جدید را جایگزین کنید

```bash
cd /opt/pasarbot
docker compose stop bot
cd /opt
unzip -o PasarGuard-Reseller-Bot-v1.6.0.zip -d /opt/pasarbot
cd /opt/pasarbot
chmod +x setup.sh backup.sh
docker compose up -d --build
```

از `docker compose down -v` استفاده نکنید؛ گزینهٔ `-v` دیتابیس را پاک می‌کند.

## ۴. وضعیت سرویس‌ها را بررسی کنید

```bash
cd /opt/pasarbot
docker compose ps
docker compose logs --tail=120 migrate
docker compose logs --tail=200 bot
```

وضعیت درست:

- `postgres` و `redis`: سالم و Running
- `migrate`: خروج با کد صفر
- `bot`: Running
- در لاگ، Traceback تکرارشونده وجود نداشته باشد

هنگام اولین اجرا، سفارش‌هایی که در نسخهٔ ۱.۵ با خطای 405 خراب شده‌اند خودکار دوباره وارد صف می‌شوند. اگر چنین سفارشی وجود داشته باشد، این پیام در لاگ دیده می‌شود:

```text
requeued provisioning orders: v1.5_compatibility=... abandoned=...
```

سفارش‌هایی که بیش از ۱۵ دقیقه در `PROVISIONING` مانده‌اند نیز دوباره وارد صف می‌شوند.

## ۵. نتیجهٔ سفارش‌های قبلی را ببینید

```bash
docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "select id,status,order_type,provision_attempts,error_message from orders order by id desc limit 20;"'
```

بعد از چند ثانیه، سفارش سالم باید `completed` شود. اگر سفارشی به علت دیگری هنوز `failed` است، در تلگرام بروید:

`/owner` ← `🛒 سفارش‌ها` ← `⚠️ سفارش‌های خطادار` ← سفارش ← `🔄 تلاش مجدد ساخت`

متن خطا در جزئیات سفارش حالا متد و مسیر دقیق API را هم نشان می‌دهد.

## ۶. محدودیت‌های نمایندگی را در PasarGuard بررسی کنید

برای هر نمایندگی دو محل را ببینید:

| محل | چیزی که باید دیده شود |
| --- | --- |
| Admin نمایندگی | `data_limit` برابر حجم پلن برای پلن حجمی، Role اختصاصی `reseller-ID` |
| Role اختصاصی | `max_users`، `data_limit_min`، `data_limit_max`، Groupها، Templateها و دسترسی User با scope برابر OWN |

در نسخهٔ ۱.۶، Sync اولیه نمایندگی‌های فعال را بررسی می‌کند. اگر محدودیت Admin یا Role قدیمی ناقص باشد، ابتدا Admin را می‌بندد، محدودیت را دوباره می‌نویسد و پس از تأیید و سالم بودن سهمیه دوباره فعالش می‌کند.

برای اجرای دستی همین کار:

`/owner` ← `🖥 نمایندگی‌ها` ← نمایندگی ← `🛠 بازاعمال Role و سقف‌ها`

سپس `🔄 Sync فوری` را بزنید و خطای Sync را در جزئیات همان نمایندگی ببینید.

## سه مدل پلن چه چیزی باید نشان دهند؟

| مدل | Admin `data_limit` | Role `max_users` | Role سقف حجم هر User |
| --- | ---: | ---: | ---: |
| فقط حجم | حجم کل پلن | نامحدود | حداکثر حجم هر User یا کل حجم پلن |
| فقط User | نامحدود | تعداد پلن | اگر مدیر در پلن تعیین کرده باشد |
| User + حجم | حجم کل پلن | تعداد پلن | حداکثر حجم هر User یا کل حجم پلن |

اگر پلن «فقط User» است، خالی بودن `data_limit` روی Admin ایراد نیست. اگر پلن «فقط حجم» است، خالی بودن `max_users` ایراد نیست.

## تفاوت سه کنترل حجم

| کنترل | محل اجرا | معنی |
| --- | --- | --- |
| `Admin.data_limit` | PasarGuard | سقف مصرف واقعی کل Userهای Admin |
| `Role.data_limit_min/max` | PasarGuard | حداقل و حداکثر حجم هر User هنگام ساخت یا ویرایش |
| `allocated_bytes <= total_quota_bytes` | ربات | مجموع سقف حجمی که نماینده بین Userها تقسیم کرده است |

کنترل سوم برای Userهایی که مستقیم در Dashboard ساخته می‌شوند، در Sync دوره‌ای انجام می‌شود. برای تست، از مدیریت ربات فاصلهٔ Sync را روی ۳۰ ثانیه و رفتار عبور از سهمیه را روی «تعلیق» بگذارید. کنترل لحظه‌ای جمع تخصیص‌ها فقط با تغییر مسیر Create/Update داخل خود PasarGuard ممکن است.

## ۷. تست کامل خرید

1. یک پلن آزمایشی `User + حجم` با سقف کم بسازید.
2. با اکانت مشتری پلن را بخرید و عکس رسید بفرستید.
3. رسید را از گروه یا پنل مدیریت تأیید کنید.
4. سفارش باید از `paid` به `provisioning` و سپس `completed` برسد.
5. مشتری باید آدرس، Username و Password را دریافت کند.
6. در PasarGuard، Admin و Role اختصاصی را باز و مقادیر جدول بالا را کنترل کنید.
7. با اکانت نماینده وارد PasarGuard شوید؛ User بیشتر از `max_users` و حجم بیشتر از `data_limit_max` باید رد شود.
8. یک User نامحدود یا مجموع تخصیص بیش از ظرفیت بسازید؛ Sync ربات باید آن را تشخیص دهد و در حالت `suspend` نمایندگی را تعلیق کند.

## اگر هنوز سفارش Failed شد

این سه خروجی را بررسی کنید:

```bash
cd /opt/pasarbot
docker compose logs --tail=250 bot
docker compose ps
curl -fsS -H 'X-Api-Key: YOUR_PG_KEY' 'https://YOUR_PANEL:8000/api/admin' | python3 -m json.tool
```

در دستور آخر، کلید واقعی را فقط روی خود سرور وارد کنید. پاسخ باید Owner و Role مالک را نشان دهد. خطاهای رایج:

- `401/403`: کلید API اشتباه یا بدون دسترسی Owner است.
- `404`: آدرس پایه اشتباه است؛ `/dashboard/` یا `/api` را به Base URL اضافه نکنید.
- خطای SSL: گواهی یا ساعت سرور مشکل دارد.
- `409`: Username یا Role هم‌نام از تلاش قدیمی مانده است؛ جزئیات سفارش و Adminهای غیرفعال را بررسی کنید.
- `422`: یکی از مقادیر پلن با مدل PasarGuard سازگار نیست؛ متن کامل خطا حالا در سفارش ذخیره می‌شود.

Adminهای غیرفعال قدیمی را خودکار حذف نمی‌کنیم، چون ممکن است User داشته باشند. بعد از موفق شدن سفارش جدید، مورد قدیمی را با Username و Role آن بررسی کنید و فقط وقتی مطمئن شدید متعلق به تلاش خراب و بدون User است حذفش کنید.

## Node نصب‌شده چه کاری می‌کند؟

Node هستهٔ پروکسی و گزارش مصرف را اجرا می‌کند. ساخت Role، Admin و محدودیت فروش توسط API پنل PasarGuard و ربات انجام می‌شود. نصب Node روی همان سرور برای تست مشکلی ایجاد نمی‌کند؛ فقط منابع CPU، RAM، پورت و ترافیک را با پنل و ربات مشترک مصرف می‌کند.
