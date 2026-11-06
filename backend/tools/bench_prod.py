"""只读质量检查：直接用应用自己的数据库连接（线上容器内运行，不写任何数据）。"""
from __future__ import annotations

import datetime as dt
import sys
from collections import Counter, defaultdict

from app.db import SessionLocal
from app.models import Employee, EmployeePositionSkill, Position
from app.services.config_service import get_setting
from app.services.solver import build_problem, solve

days = int(sys.argv[1]) if len(sys.argv) > 1 else 30
start = dt.date.fromisoformat(sys.argv[3]) if len(sys.argv) > 3 else dt.date(2026, 11, 9)
db = SessionLocal()

problem = build_problem(db, start, days)
res = solve(problem, mode="draft", timeout=float(sys.argv[2]) if len(sys.argv) > 2 else 45.0)
focus = set(get_setting(db, "rotation_rule").get("focus_positions") or [])

print(f"窗口 {start} 起 {days} 天 | 员工 {len(problem.employees)} | 状态 {res.status} | 安排 {len(res.assignments)} | 缺口 {res.diagnostics.get('total_gap')} | 缺角色 {len(res.diagnostics.get('missing_roles', []))} | 求解状态 {res.diagnostics.get('solver_status')} | 耗时 {res.duration_ms}ms")

# 结构性限制：只具备早班或只具备晚班岗位的人
pos_shift = {p.code: p.shift for p in db.query(Position).all()}
skills = defaultdict(set)
for s in db.query(EmployeePositionSkill).all():
    if s.allowed and s.position_code in pos_shift:
        skills[s.employee_id].add(pos_shift[s.position_code])
only_early = [eid for eid, s in skills.items() if s == {"early"}]
only_late = [eid for eid, s in skills.items() if s == {"late"}]
print(f"仅早班资格 {len(only_early)} 人 | 仅晚班资格 {len(only_late)} 人")

by_emp = defaultdict(dict)
es, ls = Counter(), Counter()
for a in res.assignments:
    by_emp[a["employee_id"]][a["work_date"]] = a["position_code"]
    (es if a["shift"] == "early" else ls)[a["employee_id"]] += 1

print("== 重点岗位连续同岗 ==")
for code in sorted(focus):
    consec = 0
    for eid, dm in by_emp.items():
        ds = sorted(d for d, c in dm.items() if c == code)
        consec += sum(1 for x, y in zip(ds, ds[1:]) if (y - x).days == 1)
    print(f"  {code}: {consec}")

print("== 重点岗位承担次数 ==")
for code in sorted(focus):
    c = Counter(a["employee_id"] for a in res.assignments if a["position_code"] == code)
    v = sorted(c.values())
    print(f"  {code}: 人数 {len(c)} 极差 {v[-1] - v[0] if v else 0} | {v}")

print("== 早晚班差（跳过结构性单班次人员）==")
names = {e.id: e.name for e in db.query(Employee).all()}
rows = []
for eid in set(es) | set(ls):
    d = es[eid] - ls[eid]
    rows.append((abs(d), names.get(eid, eid), es[eid], ls[eid]))
rows.sort(reverse=True)
print("  最大 5 人:", rows[:5])

print("== 主管角色 ==")
for rc in ("duty_supervisor", "early_support", "late_support"):
    c = Counter(a["employee_id"] for a in res.assignments if a["role_code"] == rc)
    print(f"  {rc}: {sorted(c.values())}")
db.close()
