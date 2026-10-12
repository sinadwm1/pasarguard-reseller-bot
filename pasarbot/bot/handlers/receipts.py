from aiogram import Router

# Receipt callbacks live in admin.py so permission checking and dashboard state share one transaction.
router = Router(name="receipt_helpers")
