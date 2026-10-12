from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Customer(Base, TimestampMixin):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    username: Mapped[str | None] = mapped_column(String(64))
    first_name: Mapped[str] = mapped_column(String(128), default="")
    last_name: Mapped[str] = mapped_column(String(128), default="")
    wallet_balance_irt: Mapped[int] = mapped_column(BigInteger, default=0)
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    locale: Mapped[str] = mapped_column(String(8), default="fa")

    __table_args__ = (CheckConstraint("wallet_balance_irt >= 0", name="wallet_non_negative"),)


class BotAdmin(Base, TimestampMixin):
    __tablename__ = "bot_admins"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(128))
    role_name: Mapped[str] = mapped_column(String(32), default="support")
    permissions: Mapped[list[str]] = mapped_column(JSON, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    added_by_id: Mapped[int | None] = mapped_column(ForeignKey("bot_admins.id"))


class PasarGuardInstance(Base, TimestampMixin):
    __tablename__ = "pasarguard_instances"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    base_url: Mapped[str] = mapped_column(String(512))
    encrypted_access_token: Mapped[str] = mapped_column(Text)
    dashboard_url: Mapped[str] = mapped_column(String(512))
    api_version: Mapped[str] = mapped_column(String(32), default="v5")
    route_overrides: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    verify_tls: Mapped[bool] = mapped_column(Boolean, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_health_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_health_error: Mapped[str | None] = mapped_column(Text)


class Location(Base, TimestampMixin):
    __tablename__ = "locations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    flag: Mapped[str] = mapped_column(String(16), default="🌍")
    pasarguard_instance_id: Mapped[int] = mapped_column(ForeignKey("pasarguard_instances.id"))
    group_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class PlanModel(Base, TimestampMixin):
    __tablename__ = "plans"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(160), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    price_irt: Mapped[int] = mapped_column(BigInteger)
    renewal_price_irt: Mapped[int] = mapped_column(BigInteger)
    duration_days: Mapped[int] = mapped_column(Integer)
    limit_mode: Mapped[str] = mapped_column(String(32))
    total_quota_bytes: Mapped[int | None] = mapped_column(BigInteger)
    max_users: Mapped[int | None] = mapped_column(Integer)
    min_user_quota_bytes: Mapped[int | None] = mapped_column(BigInteger)
    max_user_quota_bytes: Mapped[int | None] = mapped_column(BigInteger)
    min_user_expire_days: Mapped[int | None] = mapped_column(Integer)
    max_user_expire_days: Mapped[int | None] = mapped_column(Integer)
    location_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    group_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    template_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    force_template: Mapped[bool] = mapped_column(Boolean, default=False)
    hwid_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    max_devices: Mapped[int | None] = mapped_column(Integer)
    reset_allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    can_renew: Mapped[bool] = mapped_column(Boolean, default=True)
    can_upgrade: Mapped[bool] = mapped_column(Boolean, default=True)
    can_buy_volume: Mapped[bool] = mapped_column(Boolean, default=True)
    can_upgrade_location: Mapped[bool] = mapped_column(Boolean, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_visible: Mapped[bool] = mapped_column(Boolean, default=True)
    pasarguard_instance_id: Mapped[int] = mapped_column(ForeignKey("pasarguard_instances.id"))
    role_id: Mapped[str | None] = mapped_column(String(128))

    __table_args__ = (
        CheckConstraint("price_irt >= 0 AND renewal_price_irt >= 0", name="plan_prices_valid"),
        CheckConstraint("duration_days > 0", name="plan_duration_valid"),
    )


class ResellerModel(Base, TimestampMixin):
    __tablename__ = "resellers"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("plans.id"))
    pasarguard_instance_id: Mapped[int] = mapped_column(ForeignKey("pasarguard_instances.id"))
    pasarguard_admin_id: Mapped[str | None] = mapped_column(String(128), unique=True)
    panel_username: Mapped[str | None] = mapped_column(String(128))
    encrypted_panel_password: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="pending_creation", index=True)
    total_quota_bytes: Mapped[int | None] = mapped_column(BigInteger)
    allocated_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    actual_used_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    max_users: Mapped[int | None] = mapped_column(Integer)
    users_count: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    group_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    template_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    manual_overrides: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_sync_error: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1)

    customer: Mapped[Customer] = relationship()
    plan: Mapped[PlanModel] = relationship()

    __table_args__ = (
        CheckConstraint("allocated_bytes >= 0", name="reseller_allocated_non_negative"),
        CheckConstraint("actual_used_bytes >= 0", name="reseller_used_non_negative"),
        CheckConstraint("users_count >= 0", name="reseller_users_non_negative"),
        Index("ix_reseller_expiry_status", "status", "expires_at"),
    )


class Order(Base, TimestampMixin):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    plan_id: Mapped[int | None] = mapped_column(ForeignKey("plans.id"))
    reseller_id: Mapped[int | None] = mapped_column(ForeignKey("resellers.id"))
    order_type: Mapped[str] = mapped_column(String(32), default="initial_sale")
    status: Mapped[str] = mapped_column(String(32), default="awaiting_payment", index=True)
    amount_irt: Mapped[int] = mapped_column(BigInteger)
    discount_irt: Mapped[int] = mapped_column(BigInteger, default=0)
    payable_irt: Mapped[int] = mapped_column(BigInteger)
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
    metadata_json: Mapped[dict[str, object]] = mapped_column("metadata", JSON, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text)
    provision_attempts: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (CheckConstraint("payable_irt >= 0", name="order_payable_non_negative"),)


class PaymentMethod(Base, TimestampMixin):
    __tablename__ = "payment_methods"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    instructions: Mapped[str] = mapped_column(Text, default="")
    min_amount_irt: Mapped[int | None] = mapped_column(BigInteger)
    max_amount_irt: Mapped[int | None] = mapped_column(BigInteger)
    config_encrypted: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class PaymentAccount(Base, TimestampMixin):
    __tablename__ = "payment_accounts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    payment_method_id: Mapped[int] = mapped_column(ForeignKey("payment_methods.id"))
    title: Mapped[str] = mapped_column(String(128))
    account_value: Mapped[str] = mapped_column(Text)
    holder_name: Mapped[str] = mapped_column(String(128), default="")
    display_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Receipt(Base, TimestampMixin):
    __tablename__ = "receipts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), unique=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    payment_method_id: Mapped[int] = mapped_column(ForeignKey("payment_methods.id"))
    telegram_file_id: Mapped[str] = mapped_column(String(512))
    stored_path: Mapped[str | None] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    review_reason: Mapped[str | None] = mapped_column(Text)
    reviewed_by_admin_id: Mapped[int | None] = mapped_column(ForeignKey("bot_admins.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    group_chat_id: Mapped[int | None] = mapped_column(BigInteger)
    group_message_id: Mapped[int | None] = mapped_column(BigInteger)
    version: Mapped[int] = mapped_column(Integer, default=1)


class WalletEntry(Base, TimestampMixin):
    __tablename__ = "wallet_entries"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    amount_irt: Mapped[int] = mapped_column(BigInteger)
    balance_after_irt: Mapped[int] = mapped_column(BigInteger)
    transaction_type: Mapped[str] = mapped_column(String(32))
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"))
    actor_admin_id: Mapped[int | None] = mapped_column(ForeignKey("bot_admins.id"))
    description: Mapped[str] = mapped_column(Text, default="")
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)


class AccountingEntry(Base, TimestampMixin):
    __tablename__ = "accounting_entries"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"), index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    admin_id: Mapped[int | None] = mapped_column(ForeignKey("bot_admins.id"))
    transaction_type: Mapped[str] = mapped_column(String(32), index=True)
    amount_irt: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(32), index=True)
    payment_method: Mapped[str] = mapped_column(String(32))
    metadata_json: Mapped[dict[str, object]] = mapped_column("metadata", JSON, default=dict)


class AddonPackage(Base, TimestampMixin):
    __tablename__ = "addon_packages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(128))
    amount_irt: Mapped[int] = mapped_column(BigInteger)
    quota_bytes: Mapped[int | None] = mapped_column(BigInteger)
    duration_days: Mapped[int | None] = mapped_column(Integer)
    location_id: Mapped[int | None] = mapped_column(ForeignKey("locations.id"))
    from_plan_id: Mapped[int | None] = mapped_column(ForeignKey("plans.id"))
    to_plan_id: Mapped[int | None] = mapped_column(ForeignKey("plans.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class ChannelLock(Base, TimestampMixin):
    __tablename__ = "channel_locks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    chat_id: Mapped[str] = mapped_column(String(128), unique=True)
    title: Mapped[str] = mapped_column(String(128))
    join_url: Mapped[str] = mapped_column(String(512))
    scopes: Mapped[list[str]] = mapped_column(JSON, default=lambda: ["all"])
    display_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class BotText(Base, TimestampMixin):
    __tablename__ = "bot_texts"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    allowed_variables: Mapped[list[str]] = mapped_column(JSON, default=list)


class ButtonConfig(Base, TimestampMixin):
    __tablename__ = "button_configs"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    text: Mapped[str] = mapped_column(String(128))
    style: Mapped[str | None] = mapped_column(String(16))
    custom_emoji_id: Mapped[str | None] = mapped_column(String(64))
    is_visible: Mapped[bool] = mapped_column(Boolean, default=True)
    display_order: Mapped[int] = mapped_column(Integer, default=0)


class RuntimeSetting(Base, TimestampMixin):
    __tablename__ = "runtime_settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)


class Ticket(Base, TimestampMixin):
    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    subject: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(32), default="open", index=True)
    assigned_admin_id: Mapped[int | None] = mapped_column(ForeignKey("bot_admins.id"))
    group_chat_id: Mapped[int | None] = mapped_column(BigInteger)
    group_message_id: Mapped[int | None] = mapped_column(BigInteger)


class TicketMessage(Base, TimestampMixin):
    __tablename__ = "ticket_messages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), index=True)
    sender_type: Mapped[str] = mapped_column(String(16))
    sender_id: Mapped[int] = mapped_column(BigInteger)
    body: Mapped[str] = mapped_column(Text)
    telegram_file_id: Mapped[str | None] = mapped_column(String(512))


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    actor_telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)
    action: Mapped[str] = mapped_column(String(128), index=True)
    resource_type: Mapped[str] = mapped_column(String(64))
    resource_id: Mapped[str] = mapped_column(String(128))
    before_json: Mapped[dict[str, object] | None] = mapped_column(JSON)
    after_json: Mapped[dict[str, object] | None] = mapped_column(JSON)
    ip_address: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NotificationDelivery(Base, TimestampMixin):
    __tablename__ = "notification_deliveries"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_key: Mapped[str] = mapped_column(String(160))
    recipient_telegram_id: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("event_key", "recipient_telegram_id", name="uq_notification_once"),
    )
