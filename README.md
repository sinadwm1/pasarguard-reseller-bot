# PasarGuard Reseller Bot

[فارسی](README_FA.md) · [Persian installation guide](INSTALL_FA.md) · [Operations guide](OPERATIONS_FA.md) · [Security guide](SECURITY_FA.md)

A Telegram-first sales and reseller management application for PasarGuard. Customer flows and the owner panel run inside Telegram; there is no separate web dashboard for this bot.

## What the code implements

- Plans based on data volume, user count, or both; purchases, renewals, add-ons, and upgrades.
- Multiple PasarGuard panels with location-to-group and template mapping.
- Reseller provisioning with scoped admins and roles.
- Wallet ledger, bank-transfer receipt review, customer tickets, and owner broadcasts.
- Periodic quota synchronization, encrypted panel credentials, signed callbacks, and backend permission checks.

The aggregate allocated-volume limit is checked during synchronization. Changes made directly in PasarGuard may be detected later rather than blocked immediately. See [the Persian technical notes](README_FA.md) for the exact behavior and compatibility assumptions.

## Stack

Python 3.12+, aiogram 3, SQLAlchemy (async), PostgreSQL, Redis, Alembic, httpx, cryptography, Docker Compose.

## Run locally

1. Install Python 3.12+, Docker and Docker Compose.
2. Copy `.env.example` to `.env` and set a new Telegram bot token and the owner's Telegram ID. Generate the encryption and callback signing keys with `python scripts/generate_keys.py`.
3. Run `docker compose up --build -d`.
4. Open the bot in Telegram, send `/start`, then `/owner` for the initial owner menu.

For a guided setup, use [`setup.sh`](setup.sh) and the [Persian installation guide](INSTALL_FA.md). Never commit `.env`, database dumps, receipt images, or production credentials.

## Verification

```bash
python -m ruff check .
python scripts/run_tests.py
python scripts/runtime_smoke.py
```

Additional contract and workflow checks live under `scripts/`. The test suite is designed to run without live Telegram or PasarGuard connections. Real provisioning still needs an isolated integration test against the exact PasarGuard version in use.

## Repository notes

This is a source snapshot of version 1.6.0. Released under the MIT License.
