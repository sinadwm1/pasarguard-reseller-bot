from .admin import router as admin_router
from .admin_buttons import router as admin_buttons_router
from .admin_wizard import router as admin_wizard_router
from .customer import router as customer_router
from .receipts import router as receipt_router

__all__ = [
    "admin_buttons_router",
    "admin_router",
    "admin_wizard_router",
    "customer_router",
    "receipt_router",
]
