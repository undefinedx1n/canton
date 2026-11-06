"""临时基准：对比开启/关闭班次连续性约束的求解耗时（seed 数据，非生产库）。"""
from __future__ import annotations

import datetime as dt
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from dataclasses import replace

from app.db import Base
from app.services.config_service import ensure_defaults, get_setting, set_setting
from app.services.seed import seed_all
from app.services.solver import build_problem, solve

engine = create_engine(f"sqlite:///{ROOT / 'data' / '_bench_tmp.db'}", connect_args={"check_same_thread": False})
(ROOT / "data").mkdir(exist_ok=True)
Base.metadata.drop_all(bind=engine)
Base.metadata.create_all(bind=engine)
S = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
db = S()
ensure_defaults(db)
seed_all(db)

start = dt.date.today() + dt.timedelta(days=1)
for days in (3, 7, 14, 30):
    t = time.time()
    p = build_problem(db, start, days)
    res = solve(p, mode="draft", timeout=60.0)
    dur = time.time() - t
    bd = res.objective_breakdown or {}
    print(
        f"{days:>2}天 用时 {dur:5.1f}s 状态={res.status} 缺口={res.diagnostics['total_gap']} "
        f"切换={bd.get('shift_switch', 0):.0f} 晚转早={bd.get('shift_switch_late_to_early', 0):.0f} "
        f"连休超标={bd.get('max_rest_run', 0):.0f}"
    )
db.close()
