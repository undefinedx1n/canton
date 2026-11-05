#!/usr/bin/env bash
# 数据库备份：SQLite 直接复制（WAL 模式下用 .backup 更安全），PostgreSQL 用 pg_dump
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-$ROOT/backups}"
mkdir -p "$BACKUP_DIR"
STAMP="$(date +%Y%m%d-%H%M%S)"

if [[ -n "${DATABASE_URL:-}" && "${DATABASE_URL}" == postgresql* ]]; then
  OUT="$BACKUP_DIR/schedule-$STAMP.sql.gz"
  pg_dump "${DATABASE_URL/postgresql+psycopg/postgresql}" | gzip > "$OUT"
else
  DB_FILE="${DB_FILE:-$ROOT/data/schedule.db}"
  OUT="$BACKUP_DIR/schedule-$STAMP.db"
  python - "$DB_FILE" "$OUT" <<'PY'
import sqlite3, sys, pathlib
src, dst = sys.argv[1], sys.argv[2]
if not pathlib.Path(src).exists():
    raise SystemExit(f"database not found: {src}")
with sqlite3.connect(src) as s, sqlite3.connect(dst) as d:
    s.backup(d)
print("ok")
PY
fi

echo "backup written: $OUT"
# 保留最近 30 份
ls -1t "$BACKUP_DIR"/schedule-* 2>/dev/null | tail -n +31 | xargs -r rm -f
