"""初始化 / 重建演示数据库。

用法：
    .venv\\Scripts\\python.exe scripts\\init_demo.py          # 首次初始化（已有数据则跳过）
    .venv\\Scripts\\python.exe scripts\\init_demo.py --force  # 清空并重建
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.services.seed import seed_all, seed_history_and_draft  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="清空现有数据后重建")
    parser.add_argument("--no-schedule", action="store_true", help="只建基础数据，不生成历史与草稿排班")
    args = parser.parse_args()

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        result = seed_all(db, force=args.force)
        print("[seed]", result)
        if not args.no_schedule:
            print("[schedule]", seed_history_and_draft(db, days_history=14))
    finally:
        db.close()


if __name__ == "__main__":
    main()
