"""生产环境基础数据初始化（幂等，可重复执行）。

只写入系统必需的基础数据：员工类型、运营角色、12 个岗位、4 类需求模板、日历。
不会创建任何示例员工 / 请假 / 搭班偏好 / 排班记录，可安全用于真实环境。

用法（容器内）：
    docker compose exec api python /app/scripts/init_base.py
    docker compose exec api python /app/scripts/init_base.py --days-ahead 90
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.models import DemandTemplate, Employee, Position, User  # noqa: E402
from app.services.seed import seed_base  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="初始化生产基础数据（岗位/模板/角色/日历）")
    parser.add_argument("--days-before", type=int, default=3, help="日历向前覆盖天数，默认 3")
    parser.add_argument("--days-ahead", type=int, default=60, help="日历向后覆盖天数，默认 60")
    args = parser.parse_args()

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        stats = seed_base(db, days_before=args.days_before, days_ahead=args.days_ahead)
        print("[seed_base] 新增：", stats)
        print(
            "[现状] 岗位 %d 个 / 需求模板 %d 条 / 员工 %d 人 / 账号 %d 个"
            % (
                db.query(Position).count(),
                db.query(DemandTemplate).count(),
                db.query(Employee).count(),
                db.query(User).count(),
            )
        )
        print("[提示] 这是基础数据，未创建任何示例员工；请在「员工档案」导入真实名单后再排班。")
    finally:
        db.close()


if __name__ == "__main__":
    main()
