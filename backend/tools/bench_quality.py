"""检查 30 天解的质量：重点岗位连续同岗、岗位承担次数极差、早晚班差。"""
from __future__ import annotations

import datetime as dt
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.services.config_service import ensure_defaults, get_setting
from app.services.solver import build_problem, solve

days = int(sys.argv[1]) if len(sys.argv) > 1 else 30
db = sessionmaker(bind=create_engine(f"sqlite:///{(ROOT / 'data' / 'schedule.db').as_posix()}"),
                  autoflush=False, expire_on_commit=False)()
ensure_defaults(db)

problem = build_problem(db, dt.date(2026, 11, 9), days)
res = solve(problem, mode="draft", timeout=float(sys.argv[2]) if len(sys.argv) > 2 else 40.0)
focus = set((get_setting(db, "rotation_rule").get("focus_positions") or []))

by_emp = defaultdict(dict)
for a in res.assignments:
    by_emp[a["employee_id"]][a["work_date"]] = a["position_code"]

print("== 重点岗位连续同岗天数（同一员工连续两天同岗的次数）==")
for code in sorted(focus):
    consec = 0
    for eid, days_map in by_emp.items():
        ds = sorted(d for d, c in days_map.items() if c == code)
        consec += sum(1 for x, y in zip(ds, ds[1:]) if (y - x).days == 1)
    print(f"  {code}: {consec}")

print("== 重点岗位承担次数（应按人头平均）==")
for code in sorted(focus):
    cnt = Counter()
    for a in res.assignments:
        if a["position_code"] == code:
            cnt[a["employee_id"]] += 1
    vals = sorted(cnt.values())
    print(f"  {code}: 人数 {len(cnt)} 次数 {vals} 极差 {vals[-1] - vals[0] if vals else 0}")

print("== 每人早/晚班差 ==")
es, ls = Counter(), Counter()
for a in res.assignments:
    (es if a["shift"] == "early" else ls)[a["employee_id"]] += 1
diffs = sorted(abs(es[e] - ls[e]) for e in set(es) | set(ls))
print(f"  差 {diffs} 最大 {diffs[-1] if diffs else 0}")

print("== 每日三个主管角色 ==")
roles = defaultdict(dict)
for a in res.assignments:
    if a["role_code"]:
        roles[a["work_date"]][a["role_code"]] = a["employee_id"]
missing = [d for d in problem.dates if len(roles[d]) < 3]
print(f"  缺失天数 {len(missing)} / {len(problem.dates)}")

print("== 主管角色次数（B2/A2/B1 应均衡）==")
for rc in ("duty_supervisor", "early_support", "late_support"):
    c = Counter(a["employee_id"] for a in res.assignments if a["role_code"] == rc)
    print(f"  {rc}: {sorted(c.values())}")
db.close()
