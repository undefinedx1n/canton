"""30 天窗口求解耗时基准（只读本地 SQLite，不写库）。"""
from __future__ import annotations

import datetime as dt
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.services.config_service import ensure_defaults
from app.services.solver import build_problem, solve

days = int(sys.argv[1]) if len(sys.argv) > 1 else 30
db_path = ROOT / "data" / "schedule.db"
engine = create_engine(f"sqlite:///{db_path.as_posix()}")
Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
db = Session()
ensure_defaults(db)

start = dt.date(2026, 11, 9)
t0 = time.time()
problem = build_problem(db, start, days)
t_build = time.time() - t0
print(f"窗口 {days} 天 | 员工 {len(problem.employees)} | 岗位 {len(problem.positions)} | 锁定 {len(problem.locked)} | 建题 {t_build:.2f}s")

t0 = time.time()
res = solve(problem, mode="draft", timeout=float(sys.argv[2]) if len(sys.argv) > 2 else 30.0)
print(f"状态 {res.status} | 求解 {time.time()-t0:.2f}s | 安排 {len(res.assignments)} 条 | 缺口 {res.diagnostics.get('total_gap')} | 缺角色 {len(res.diagnostics.get('missing_roles', []))}")
for k, v in sorted(res.objective_breakdown.items()):
    if v:
        print(f"  {k}: {v:.0f}")
db.close()
