from __future__ import annotations

from dataclasses import dataclass

from pasarbot.domain.enums import Permission


@dataclass(frozen=True, slots=True)
class AdminAction:
    label: str
    callback: str
    style: str | None = None


@dataclass(frozen=True, slots=True)
class WizardField:
    key: str
    title: str
    prompt: str
    kind: str = "text"
    choices: tuple[tuple[str, str], ...] = ()
    optional: bool = False
    secret: bool = False
    when_key: str | None = None
    when_values: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class WizardDefinition:
    key: str
    title: str
    permission: Permission
    fields: tuple[WizardField, ...]
    success_section: str


ADMIN_REPLY_SECTIONS: dict[str, str] = {
    "📊 داشبورد": "dashboard",
    "🧾 رسیدها": "receipts",
    "📦 پلن‌ها": "plans",
    "🌍 لوکیشن‌ها": "locations",
    "👤 مشتریان": "customers",
    "🖥 نمایندگی‌ها": "resellers",
    "🎫 مدیریت تیکت‌ها": "support",
    "🛒 سفارش‌ها": "orders",
    "💳 پرداخت": "payments",
    "💰 حسابداری": "accounting",
    "📈 گزارش‌ها": "reports",
    "🛡 ادمین‌ها": "admins",
    "📣 قفل کانال": "channel_locks",
    "👥 گروه رسید": "receipt_groups",
    "🔌 پاسارگارد": "pasarguard",
    "📝 متن‌ها": "texts",
    "🎨 دکمه‌ها": "buttons",
    "📢 ارسال همگانی": "broadcast",
    "⚙️ تنظیمات": "settings",
    "📚 راهنمای مدیریت": "help",
}


ADMIN_SECTION_ACTIONS: dict[str, tuple[AdminAction, ...]] = {
    "dashboard": (
        AdminAction("🔄 بروزرسانی", "admin:dashboard", "primary"),
        AdminAction("🧾 رسیدهای منتظر", "admin:receipts"),
    ),
    "plans": (
        AdminAction("⚡ ساخت سریع پلن", "admin:wizard:quick_plan", "success"),
        AdminAction("⚙️ ساخت پیشرفته پلن", "admin:wizard:plan"),
        AdminAction("📋 فهرست پلن‌ها", "admin:list:plan"),
        AdminAction("🧩 بسته‌های تمدید و ارتقا", "admin:list:addon"),
        AdminAction("➕ بسته جدید", "admin:wizard:addon"),
    ),
    "locations": (
        AdminAction("➕ افزودن لوکیشن", "admin:wizard:location", "success"),
        AdminAction("📋 فهرست لوکیشن‌ها", "admin:list:location"),
    ),
    "customers": (
        AdminAction("🔎 جستجوی مشتری", "admin:wizard:customer_search", "primary"),
        AdminAction("📋 آخرین مشتریان", "admin:list:customer"),
    ),
    "resellers": (
        AdminAction("🔎 جستجوی نمایندگی", "admin:wizard:reseller_search", "primary"),
        AdminAction("📋 نمایندگی‌ها", "admin:list:reseller"),
    ),
    "support": (
        AdminAction("🎫 تیکت‌های باز", "admin:list:ticket", "primary"),
        AdminAction("📋 همه تیکت‌ها", "admin:list:all_ticket"),
    ),
    "orders": (
        AdminAction("📋 آخرین سفارش‌ها", "admin:list:order"),
        AdminAction("⚠️ سفارش‌های خطادار", "admin:list:failed_order", "danger"),
    ),
    "receipts": (
        AdminAction("🧾 رسیدهای منتظر", "admin:list:receipt", "primary"),
        AdminAction("✅ بررسی‌شده‌ها", "admin:list:reviewed_receipt"),
    ),
    "payments": (
        AdminAction("💳 تنظیم کارت‌ها", "admin:list:card_account", "primary"),
        AdminAction("➕ افزودن کارت", "admin:wizard:card_account", "success"),
        AdminAction("💰 وضعیت کیف پول", "admin:payment:wallet"),
    ),
    "accounting": (
        AdminAction("➕/➖ اصلاح کیف پول", "admin:wizard:wallet_adjust", "primary"),
        AdminAction("📋 آخرین تراکنش‌ها", "admin:list:accounting"),
    ),
    "reports": (
        AdminAction("📊 بروزرسانی گزارش", "admin:reports", "primary"),
        AdminAction("🟠 نزدیک انقضا", "admin:list:expiring_reseller"),
    ),
    "receipt_groups": (
        AdminAction("⚙️ تنظیم گروه", "admin:wizard:receipt_group", "primary"),
        AdminAction("🧪 ارسال پیام آزمایشی", "admin:receipt_group:test"),
        AdminAction("⛔ غیرفعال‌کردن", "admin:receipt_group:disable", "danger"),
    ),
    "channel_locks": (
        AdminAction("➕ افزودن کانال", "admin:wizard:channel", "success"),
        AdminAction("📋 کانال‌ها", "admin:list:channel"),
    ),
    "admins": (
        AdminAction("➕ افزودن ادمین", "admin:wizard:admin", "success"),
        AdminAction("📋 فهرست ادمین‌ها", "admin:list:admin"),
    ),
    "texts": (
        AdminAction("✏️ ویرایش متن", "admin:wizard:bot_text", "primary"),
        AdminAction("📋 متن‌های ذخیره‌شده", "admin:list:bot_text"),
    ),
    "buttons": (
        AdminAction("🎨 شخصی‌سازی دکمه", "admin:wizard:button", "primary"),
        AdminAction("📋 دکمه‌های سفارشی", "admin:list:button"),
    ),
    "broadcast": (
        AdminAction("📢 ساخت ارسال همگانی", "admin:wizard:broadcast", "danger"),
    ),
    "pasarguard": (
        AdminAction("➕ اتصال پنل", "admin:wizard:pasarguard", "success"),
        AdminAction("📋 پنل‌های متصل", "admin:list:pasarguard"),
    ),
    "settings": (
        AdminAction("⏱ فاصله Sync", "admin:wizard:sync_interval", "primary"),
        AdminAction("🛡 رفتار عبور از سهمیه", "admin:wizard:quota_mode"),
        AdminAction("ℹ️ وضعیت سیستم", "admin:settings:status"),
    ),
    "help": (
        AdminAction("🚀 راه‌اندازی اولیه", "admin:help:setup", "primary"),
        AdminAction("📦 ساخت پلن", "admin:help:plans"),
        AdminAction("🧾 مدیریت رسید", "admin:help:receipts"),
        AdminAction("🔌 اتصال PasarGuard", "admin:help:pasarguard"),
    ),
}


YES_NO = (("روشن ✅", "true"), ("خاموش ⛔", "false"))

BUTTON_CHOICES = (
    ("🛒 خرید نمایندگی", "customer.buy"), ("🖥 پنل‌های من", "customer.panels"),
    ("💰 کیف پول", "customer.wallet"), ("🧾 سفارش‌های من", "customer.orders"),
    ("🎫 پشتیبانی", "customer.support"), ("📚 راهنمای مشتری", "customer.help"),
) + tuple((label, f"admin.{section}") for label, section in ADMIN_REPLY_SECTIONS.items())

TEXT_CHOICES = (
    ("خوش‌آمدگویی", "welcome"), ("قفل کانال", "channel_lock"),
    ("آغاز خرید", "buy_intro"), ("فهرست پنل‌ها", "panels_intro"),
    ("راهنمای کیف پول", "wallet_intro"), ("پشتیبانی", "support_intro"),
    ("متن کارت‌به‌کارت", "manual_payment"), ("رسید ثبت شد", "receipt_registered"),
    ("رسید تأیید شد", "receipt_approved"), ("رسید رد شد", "receipt_rejected"),
    ("تحویل نمایندگی", "panel_delivery"), ("اعلان تیکت", "ticket_created"),
    ("بررسی تیکت", "ticket_reviewed"),
    ("پاسخ تیکت", "ticket_answered"), ("راهنمای مشتری", "customer_help"),
    ("فهرست سفارش‌ها", "orders_intro"), ("خطای عمومی", "error"),
)


WIZARD_DEFINITIONS: dict[str, WizardDefinition] = {
    "quick_plan": WizardDefinition(
        "quick_plan", "ساخت سریع پلن", Permission.PLAN_MANAGE,
        (
            WizardField("name", "نام پلن", "نام قابل نمایش را بفرستید."),
            WizardField("price_irt", "قیمت خرید و تمدید", "قیمت را به تومان بفرستید.", "positive_int"),
            WizardField("duration_days", "مدت پلن", "تعداد روز اعتبار را بفرستید.", "positive_int"),
            WizardField("limit_mode", "مدل محدودیت", "یک مدل را انتخاب کنید.", "choice",
                        (("فقط حجم", "volume_only"), ("فقط User", "user_only"), ("User + حجم", "user_and_volume"))),
            WizardField("total_quota_gb", "حجم کل", "مقدار GB را بفرستید.", "gb",
                        when_key="limit_mode", when_values=("volume_only", "user_and_volume")),
            WizardField("max_users", "سقف User", "حداکثر تعداد را بفرستید.", "positive_int",
                        when_key="limit_mode", when_values=("user_only", "user_and_volume")),
            WizardField("instance_id", "پنل PasarGuard", "پنل مقصد را از فهرست انتخاب کنید.", "positive_int"),
            WizardField("location_ids", "لوکیشن‌ها", "لوکیشن‌های مجاز را انتخاب و ثبت کنید.", "ids"),
        ), "plans",
    ),
    "pasarguard": WizardDefinition(
        "pasarguard",
        "اتصال PasarGuard",
        Permission.PASARGUARD_MANAGE,
        (
            WizardField("name", "نام پنل", "یک نام کوتاه مثل «پنل اصلی» بفرستید."),
            WizardField("base_url", "آدرس API", "آدرس HTTPS پنل را بفرستید.", "url"),
            WizardField("dashboard_url", "آدرس ورود", "آدرس صفحه Dashboard را بفرستید.", "url"),
            WizardField("token", "کلید API مالک", "در پنل PasarGuard، بخش API Keys، کلید Owner با inherit permissions بسازید. مقدار pg_key_... را بفرستید.", secret=True),
        ),
        "pasarguard",
    ),
    "location": WizardDefinition(
        "location",
        "لوکیشن جدید",
        Permission.PLAN_MANAGE,
        (
            WizardField("name", "نام", "نام لوکیشن، مثلاً Germany را بفرستید."),
            WizardField("flag", "پرچم", "ایموجی پرچم، مثلاً 🇩🇪 را بفرستید."),
            WizardField("instance_id", "پنل مقصد", "پنل PasarGuard را از فهرست انتخاب کنید.", "positive_int"),
            WizardField("group_ids", "Groupها", "Groupهای PasarGuard را از فهرست انتخاب کنید.", "ids"),
        ),
        "locations",
    ),
    "plan": WizardDefinition(
        "plan",
        "ساخت پلن",
        Permission.PLAN_MANAGE,
        (
            WizardField("name", "نام پلن", "نام قابل نمایش پلن را بفرستید."),
            WizardField("description", "توضیحات", "توضیح کوتاه پلن را بفرستید.", optional=True),
            WizardField("price_irt", "قیمت خرید", "قیمت خرید را به تومان و فقط عدد بفرستید.", "positive_int"),
            WizardField("renewal_price_irt", "قیمت تمدید", "قیمت تمدید را به تومان بفرستید.", "positive_int"),
            WizardField("duration_days", "مدت پلن", "تعداد روز اعتبار را بفرستید.", "positive_int"),
            WizardField(
                "limit_mode",
                "مدل محدودیت",
                "نوع محدودیت پلن را انتخاب کنید.",
                "choice",
                (("فقط حجم", "volume_only"), ("فقط User", "user_only"), ("User + حجم", "user_and_volume")),
            ),
            WizardField("total_quota_gb", "حجم کل", "حجم کل را به GB بفرستید.", "gb", when_key="limit_mode", when_values=("volume_only", "user_and_volume")),
            WizardField("max_users", "سقف User", "حداکثر تعداد User را بفرستید.", "positive_int", when_key="limit_mode", when_values=("user_only", "user_and_volume")),
            WizardField("min_user_quota_gb", "حداقل حجم هر User", "حداقل GB هر User را بفرستید یا ردکردن را بزنید.", "optional_gb", optional=True),
            WizardField("max_user_quota_gb", "حداکثر حجم هر User", "حداکثر GB هر User را بفرستید یا ردکردن را بزنید.", "optional_gb", optional=True),
            WizardField("min_user_expire_days", "حداقل اعتبار User", "حداقل روز را بفرستید یا ردکردن را بزنید.", "optional_int", optional=True),
            WizardField("max_user_expire_days", "حداکثر اعتبار User", "حداکثر روز را بفرستید یا ردکردن را بزنید.", "optional_int", optional=True),
            WizardField("instance_id", "پنل مقصد", "پنل PasarGuard را از فهرست انتخاب کنید.", "positive_int"),
            WizardField("location_ids", "لوکیشن‌های مجاز", "لوکیشن‌ها را انتخاب کنید؛ در صورت نیاز مرحله را رد کنید.", "ids", optional=True),
            WizardField("group_ids", "Groupهای مجاز", "Groupهای PasarGuard را از فهرست انتخاب کنید.", "ids"),
            WizardField("template_ids", "Templateهای مجاز", "Templateها را انتخاب کنید یا مرحله را رد کنید.", "ids", optional=True),
            WizardField("force_template", "اجبار Template", "این گزینه را انتخاب کنید.", "choice", YES_NO),
            WizardField("hwid_enabled", "HWID", "HWID فعال باشد؟", "choice", YES_NO),
            WizardField("max_devices", "حداکثر Device", "تعداد Device را بفرستید یا ردکردن را بزنید.", "optional_int", optional=True, when_key="hwid_enabled", when_values=("true",)),
            WizardField("reset_allowed", "Reset Strategy", "Reset مجاز باشد؟", "choice", YES_NO),
            WizardField("can_renew", "تمدید", "مشتری بتواند تمدید کند؟", "choice", YES_NO),
            WizardField("can_upgrade", "ارتقای پلن", "ارتقای پلن مجاز باشد؟", "choice", YES_NO),
            WizardField("can_buy_volume", "خرید حجم", "خرید حجم اضافه مجاز باشد؟", "choice", YES_NO),
            WizardField("can_upgrade_location", "ارتقای لوکیشن", "ارتقای لوکیشن مجاز باشد؟", "choice", YES_NO),
            WizardField("is_visible", "نمایش در بات", "پلن در فروشگاه نمایش داده شود؟", "choice", YES_NO),
        ),
        "plans",
    ),
    "addon": WizardDefinition(
        "addon",
        "بسته تمدید یا ارتقا",
        Permission.PLAN_MANAGE,
        (
            WizardField("kind", "نوع بسته", "نوع بسته را انتخاب کنید.", "choice", (("افزایش حجم", "volume"), ("تمدید", "renewal"), ("ارتقای پلن", "plan_upgrade"), ("ارتقای لوکیشن", "location_upgrade"))),
            WizardField("name", "نام بسته", "نام قابل نمایش بسته را بفرستید."),
            WizardField("amount_irt", "قیمت", "قیمت را به تومان بفرستید.", "positive_int"),
            WizardField("quota_gb", "حجم", "حجم افزایشی را به GB بفرستید.", "gb", when_key="kind", when_values=("volume",)),
            WizardField("duration_days", "مدت", "تعداد روز تمدید را بفرستید.", "positive_int", when_key="kind", when_values=("renewal",)),
            WizardField("from_plan_id", "پلن مبدأ", "شناسه پلن مبدأ را بفرستید.", "positive_int", when_key="kind", when_values=("plan_upgrade",)),
            WizardField("to_plan_id", "پلن مقصد", "شناسه پلن مقصد را بفرستید.", "positive_int", when_key="kind", when_values=("plan_upgrade",)),
            WizardField("location_id", "لوکیشن", "شناسه لوکیشن را بفرستید.", "positive_int", when_key="kind", when_values=("location_upgrade",)),
        ),
        "plans",
    ),
    "card_account": WizardDefinition(
        "card_account",
        "کارت بانکی",
        Permission.PAYMENT_MANAGE,
        (
            WizardField("title", "عنوان کارت", "مثلاً «کارت ملت» را بفرستید."),
            WizardField("account_value", "شماره کارت", "شماره کارت را بفرستید.", "card"),
            WizardField("holder_name", "صاحب حساب", "نام صاحب حساب را بفرستید."),
        ),
        "payments",
    ),
    "admin": WizardDefinition(
        "admin",
        "ادمین جدید",
        Permission.ADMIN_MANAGE,
        (
            WizardField("telegram_id", "Telegram ID", "Telegram ID عددی ادمین را بفرستید.", "positive_int"),
            WizardField("role_name", "نقش", "نقش را انتخاب کنید. برای دسترسی دقیق «سفارشی» را بزنید و بعداً در صفحهٔ ادمین مجوزها را روشن کنید.", "choice", (("سفارشی", "custom"), ("مدیر", "manager"), ("فروش", "sales"), ("مالی", "financial"), ("پشتیبانی", "support"))),
            WizardField("display_name", "نام نمایشی", "نام ادمین را بفرستید."),
        ),
        "admins",
    ),
    "channel": WizardDefinition(
        "channel",
        "قفل کانال",
        Permission.CHANNEL_LOCK_MANAGE,
        (
            WizardField("chat_id", "شناسه کانال", "@username یا Chat ID کانال را بفرستید."),
            WizardField("title", "عنوان", "عنوان دکمه عضویت را بفرستید."),
            WizardField("join_url", "لینک عضویت", "لینک https://t.me/... را بفرستید.", "url"),
            WizardField("scope", "محل اجبار", "محل اعمال قفل را انتخاب کنید.", "choice", (("همه بخش‌ها", "all"), ("فقط خرید", "buy"), ("ورود و دانلود", "login"))),
        ),
        "channel_locks",
    ),
    "receipt_group": WizardDefinition(
        "receipt_group",
        "گروه بررسی رسید",
        Permission.PAYMENT_MANAGE,
        (WizardField("chat_id", "Chat ID گروه", "Chat ID منفی گروه را بفرستید؛ مثل -100123...", "int"),),
        "receipt_groups",
    ),
    "bot_text": WizardDefinition(
        "bot_text",
        "ویرایش متن ربات",
        Permission.TEXT_MANAGE,
        (
            WizardField("key", "بخش متن", "بخشی را که متن آن باید عوض شود انتخاب کنید.", "choice", TEXT_CHOICES),
            WizardField("value", "متن", "متن جدید را بفرستید. در متن کارت‌به‌کارت: 💳 شمارهٔ کارتِ قابل کپی، 👤 نام صاحب، 🏦 نام کارت، 💰 مبلغ و 🔢 سفارش است؛ این نشانه‌ها خودکار جایگزین می‌شوند."),
        ),
        "texts",
    ),
    "button": WizardDefinition(
        "button",
        "ظاهر دکمه",
        Permission.TEXT_MANAGE,
        (
            WizardField("key", "دکمه", "دکمهٔ موردنظر را انتخاب کنید.", "choice", BUTTON_CHOICES),
            WizardField("text", "عنوان و ایموجی", "عنوان را با ایموجی دلخواه در همان پیام بفرستید. اگر ایموجی تلگرام Custom باشد، بات ID آن را خودکار می‌خواند."),
            WizardField("style", "رنگ", "فقط رنگ‌های رسمی تلگرام پشتیبانی می‌شوند.", "choice", (("پیش‌فرض", "default"), ("آبی", "primary"), ("سبز", "success"), ("قرمز", "danger"))),
        ),
        "buttons",
    ),
    "broadcast": WizardDefinition(
        "broadcast",
        "ارسال همگانی",
        Permission.BROADCAST_SEND,
        (WizardField("text", "پیام", "متن پیام همگانی را بفرستید."),),
        "broadcast",
    ),
    "customer_search": WizardDefinition(
        "customer_search", "جستجوی مشتری", Permission.CUSTOMER_VIEW,
        (WizardField("query", "جستجو", "Telegram ID یا username را بفرستید."),), "customers",
    ),
    "reseller_search": WizardDefinition(
        "reseller_search", "جستجوی نمایندگی", Permission.RESELLER_VIEW,
        (WizardField("query", "جستجو", "شناسه نمایندگی یا username پنل را بفرستید."),), "resellers",
    ),
    "user_search": WizardDefinition(
        "user_search", "جستجوی User نمایندگی", Permission.RESELLER_VIEW,
        (WizardField("query", "جستجوی User", "ID عددی یا username User را بفرستید."),), "resellers",
    ),
    "wallet_adjust": WizardDefinition(
        "wallet_adjust", "اصلاح موجودی", Permission.WALLET_MANAGE,
        (
            WizardField("telegram_id", "مشتری", "Telegram ID مشتری را بفرستید.", "positive_int"),
            WizardField("amount_irt", "مبلغ", "مبلغ را به تومان بفرستید؛ برای کسر با منفی.", "int"),
            WizardField("description", "دلیل", "دلیل اصلاح موجودی را بفرستید."),
        ), "accounting",
    ),
    "customer_message": WizardDefinition(
        "customer_message", "ارسال پیام", Permission.CUSTOMER_EDIT,
        (WizardField("text", "پیام", "متن پیام مشتری را بفرستید."),), "customers",
    ),
    "ticket_reply": WizardDefinition(
        "ticket_reply", "پاسخ تیکت", Permission.SUPPORT_MANAGE,
        (WizardField("text", "پاسخ", "متن پاسخ به مشتری را بفرستید."),), "support",
    ),
    "sync_interval": WizardDefinition(
        "sync_interval", "فاصله Sync", Permission.PASARGUARD_MANAGE,
        (WizardField("seconds", "فاصله", "فاصله را انتخاب کنید.", "choice", (("۱ دقیقه", "60"), ("۵ دقیقه", "300"), ("۱۵ دقیقه", "900"), ("۳۰ دقیقه", "1800"))),), "settings",
    ),
    "quota_mode": WizardDefinition(
        "quota_mode", "رفتار سهمیه", Permission.PASARGUARD_MANAGE,
        (WizardField("mode", "حالت", "رفتار عبور از سهمیه را انتخاب کنید.", "choice", (("تعلیق خودکار", "suspend"), ("فقط هشدار", "alert"))),), "settings",
    ),
}


ACTIVE_PAYMENT_KINDS = frozenset({"manual", "wallet"})
