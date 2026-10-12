from __future__ import annotations

from enum import StrEnum


class LimitMode(StrEnum):
    VOLUME_ONLY = "volume_only"
    USER_ONLY = "user_only"
    USER_AND_VOLUME = "user_and_volume"


class ResellerStatus(StrEnum):
    PENDING_CREATION = "pending_creation"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    EXPIRED = "expired"
    ERROR = "error"
    DELETED = "deleted"


class OrderStatus(StrEnum):
    AWAITING_PAYMENT = "awaiting_payment"
    PAYMENT_REVIEW = "payment_review"
    PAID = "paid"
    PROVISIONING = "provisioning"
    COMPLETED = "completed"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"
    FAILED = "failed"


class ReceiptStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class TransactionType(StrEnum):
    INITIAL_SALE = "initial_sale"
    RENEWAL = "renewal"
    EXTRA_VOLUME = "extra_volume"
    PLAN_UPGRADE = "plan_upgrade"
    LOCATION_UPGRADE = "location_upgrade"
    WALLET_TOPUP = "wallet_topup"
    REFUND = "refund"
    DISCOUNT = "discount"
    MANUAL_ADJUSTMENT = "manual_adjustment"


class Permission(StrEnum):
    DASHBOARD_VIEW = "dashboard.view"
    CUSTOMER_VIEW = "customer.view"
    CUSTOMER_EDIT = "customer.edit"
    RESELLER_VIEW = "reseller.view"
    RESELLER_EDIT = "reseller.edit"
    PLAN_MANAGE = "plan.manage"
    RECEIPT_APPROVE = "receipt.approve"
    RECEIPT_REJECT = "receipt.reject"
    WALLET_MANAGE = "wallet.manage"
    ACCOUNTING_VIEW = "accounting.view"
    PAYMENT_MANAGE = "payment.manage"
    BROADCAST_SEND = "broadcast.send"
    TEXT_MANAGE = "text.manage"
    CHANNEL_LOCK_MANAGE = "channel_lock.manage"
    PASARGUARD_MANAGE = "pasarguard.manage"
    ADMIN_MANAGE = "admin.manage"
    SUPPORT_MANAGE = "support.manage"


ROLE_PRESETS: dict[str, frozenset[Permission]] = {
    "owner": frozenset(Permission),
    "custom": frozenset({Permission.DASHBOARD_VIEW}),
    "manager": frozenset(permission for permission in Permission if permission != Permission.ADMIN_MANAGE),
    "sales": frozenset(
        {
            Permission.DASHBOARD_VIEW,
            Permission.CUSTOMER_VIEW,
            Permission.RESELLER_VIEW,
            Permission.RECEIPT_APPROVE,
            Permission.RECEIPT_REJECT,
        }
    ),
    "financial": frozenset(
        {
            Permission.DASHBOARD_VIEW,
            Permission.CUSTOMER_VIEW,
            Permission.RECEIPT_APPROVE,
            Permission.RECEIPT_REJECT,
            Permission.WALLET_MANAGE,
            Permission.ACCOUNTING_VIEW,
            Permission.PAYMENT_MANAGE,
        }
    ),
    "support": frozenset(
        {
            Permission.DASHBOARD_VIEW,
            Permission.CUSTOMER_VIEW,
            Permission.RESELLER_VIEW,
            Permission.SUPPORT_MANAGE,
        }
    ),
}
