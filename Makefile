.PHONY: up down logs migrate test verify keys

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f --tail=200 bot

migrate:
	docker compose run --rm migrate

test:
	python scripts/run_tests.py

verify:
	python scripts/verify_delivery.py

keys:
	python scripts/generate_keys.py
