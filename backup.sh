#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

mkdir -p backups
backup_file="backups/pasarbot-$(date +%Y%m%d-%H%M%S).sql.gz"
docker compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' | gzip > "$backup_file"
printf 'نسخه پشتیبان ساخته شد: %s\n' "$backup_file"
