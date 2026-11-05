"""重置管理员/主管账号密码（运维用途）。

不改动任何业务数据，只更新 users.password_hash 并写一条审计日志。

用法（容器内）：
    docker compose exec api python /app/scripts/reset_password.py admin 'NewPass12345'
    docker compose exec api python /app/scripts/reset_password.py --list
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.db import SessionLocal  # noqa: E402
from app.deps import write_audit  # noqa: E402
from app.models import User  # noqa: E402
from app.security import hash_password  # noqa: E402

MIN_LEN = 8


def main() -> int:
    parser = argparse.ArgumentParser(description="重置账号密码")
    parser.add_argument("username", nargs="?", help="要重置的账号")
    parser.add_argument("new_password", nargs="?", help="新密码（至少 8 位）")
    parser.add_argument("--list", action="store_true", help="只列出账号，不修改")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        if args.list or not args.username:
            rows = db.query(User).order_by(User.id).all()
            if not rows:
                print("没有任何账号")
                return 1
            for u in rows:
                print(f"  #{u.id} {u.username:<16} {u.role:<12} {u.display_name}  active={u.is_active}")
            print("\n用法: python /app/scripts/reset_password.py <username> '<new_password>'")
            return 0

        if not args.new_password:
            print("缺少新密码", file=sys.stderr)
            return 2
        if len(args.new_password) < MIN_LEN:
            print(f"新密码至少 {MIN_LEN} 位", file=sys.stderr)
            return 2

        user = db.query(User).filter(User.username == args.username).first()
        if user is None:
            print(f"账号不存在: {args.username}", file=sys.stderr)
            return 3

        user.password_hash = hash_password(args.new_password)
        write_audit(db, "system", "reset_password", "user", str(user.id), f"重置 {user.username} 的密码")
        db.commit()
        print(f"已重置 {user.username} 的密码（{len(args.new_password)} 位）")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
