#!/usr/bin/env bash
# 从备份恢复。用法: scripts/restore.sh backups/schedule-20261105-120000.db
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="${1:-}"
[[ -z "$SRC" || ! -f "$SRC" ]] && { echo "usage: $0 <backup-file>"; exit 1; }

echo "!! 停止应用后再执行恢复，避免写入冲突"
DB_FILE="${DB_FILE:-$ROOT/data/schedule.db}"
cp -f "$DB_FILE" "$DB_FILE.before-restore-$(date +%Y%m%d-%H%M%S)" 2>/dev/null || true

case "$SRC" in
  *.sql.gz) gunzip -c "$SRC" | psql "${DATABASE_URL/postgresql+psycopg/postgresql}" ;;
  *.db)     cp -f "$SRC" "$DB_FILE" ;;
  *)        echo "unsupported backup type"; exit 1 ;;
esac

echo "restored from $SRC"
