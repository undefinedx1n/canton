"""岗位轮换、三主管角色、早晚班平衡与人工预排优先的回归测试。

对应补充需求：
1. A3/B3/B8 与 A2/B1/B2 尽量不由同一人连续担任，并在周期内平均；
2. 每天 B2 当值主管、A2 早班辅助、B1 晚班辅助各 1 人，且只能是主管；
3. 每人早/晚班次数尽量均衡；
4. 人工预排优先级最高，自动只补空缺。
"""
from __future__ import annotations

import datetime as dt

from app.models import Employee, ScheduleVersion
from app.services.solver import build_problem, solve
from tests.support import force_demand, set_day

START = dt.date(2026, 3, 2)  # 周一
DATES = [START + dt.timedelta(days=i) for i in range(10)]


def _roles_by_day(assignments) -> dict[dt.date, dict[str, list[int]]]:
    out: dict[dt.date, dict[str, list[int]]] = {}
    for a in assignments:
        if not a.get("role_code"):
            continue
        out.setdefault(a["work_date"], {}).setdefault(a["role_code"], []).append(a["employee_id"])
    return out


def test_three_supervisor_roles_present_every_day(db):
    for d in DATES:
        set_day(db, d)
    force_demand(db, DATES, {"A2": 1, "B1": 1, "B2": 1}, zero_others=False)
    db.commit()

    problem = build_problem(db, START, len(DATES))
    res = solve(problem, mode="draft", timeout=12.0)
    assert res.status == "done", res.message

    roles = _roles_by_day(res.assignments)
    supers = {e.id for e in db.query(Employee).filter(Employee.is_supervisor.is_(True)).all()}
    for d in DATES:
        got = roles.get(d, {})
        for code in ("duty_supervisor", "early_support", "late_support"):
            assert got.get(code), f"{d} 缺少角色 {code}"
            assert len(got[code]) == 1, f"{d} 角色 {code} 人数应恰好 1 人"
            assert got[code][0] in supers, f"{d} 角色 {code} 由非主管承担"


def test_role_positions_map_to_expected_codes(db):
    """B2=当值、A2=早辅、B1=晚辅，角色与岗位的绑定不能串。"""
    for d in DATES:
        set_day(db, d)
    force_demand(db, DATES, {}, zero_others=False)
    db.commit()

    problem = build_problem(db, START, 5)
    expected = {"B2": "duty_supervisor", "A2": "early_support", "B1": "late_support"}
    for code, role in expected.items():
        assert problem.positions[code].supervisor_role_code == role


def test_focus_position_rotates_between_people(db):
    """A3 连排 6 天时，不应由同一个人从头占到底。"""
    days = [START + dt.timedelta(days=i) for i in range(6)]
    for d in days:
        set_day(db, d)
    force_demand(db, days, {"A3": 1}, zero_others=False)
    db.commit()

    problem = build_problem(db, START, 6)
    res = solve(problem, mode="draft", timeout=12.0)
    holders = [a["employee_id"] for a in res.assignments if a["position_code"] == "A3"]
    assert len(holders) == 6
    longest = 1
    run = 1
    for prev, cur in zip(holders, holders[1:]):
        run = run + 1 if cur == prev else 1
        longest = max(longest, run)
    assert longest <= 2, f"A3 连续同人过长：{holders}"


def test_shift_balance_for_multi_shift_employee(db):
    """同时具备早/晚班岗位资格的员工，不应整窗口只排单一班次。"""
    emp = db.query(Employee).filter(Employee.is_active.is_(True)).first()
    emp.skills.clear()
    db.flush()
    from app.models import EmployeePositionSkill

    db.add_all(
        [
            EmployeePositionSkill(employee_id=emp.id, position_code="A1", priority=1, proficiency=3, allowed=True),
            EmployeePositionSkill(employee_id=emp.id, position_code="B7", priority=1, proficiency=3, allowed=True),
        ]
    )
    for d in DATES:
        set_day(db, d)
    force_demand(db, DATES, {"A1": 1, "B7": 1}, zero_others=False)
    db.commit()

    problem = build_problem(db, START, len(DATES))
    res = solve(problem, mode="draft", timeout=12.0)
    mine = [a for a in res.assignments if a["employee_id"] == emp.id]
    assert mine, "该员工应有排班"
    shifts = {a["shift"] for a in mine}
    assert shifts == {"early", "late"}, f"单一班次未被纠正：{shifts}"


def test_manual_assignment_survives_regeneration(db, client):
    """人工预排（source=manual）在重新生成时必须保留，自动只补空缺。"""
    emp = db.query(Employee).filter(Employee.is_active.is_(True)).first()
    version = ScheduleVersion(
        name="人工预排草稿", start_date=START, end_date=START, version_no=1, status="draft", created_by="测试"
    )
    db.add(version)
    db.commit()
    db.refresh(version)

    from app.models import ScheduleAssignment

    db.add(
        ScheduleAssignment(
            version_id=version.id,
            work_date=START,
            employee_id=emp.id,
            position_code="A1",
            shift="early",
            role_code=None,
            is_locked=False,
            source="manual",
        )
    )
    db.commit()

    problem = build_problem(db, START, 1)
    assert any(l["employee_id"] == emp.id and l["source"] == "manual" for l in problem.locked)
    res = solve(problem, mode="draft", timeout=8.0)
    row = next((a for a in res.assignments if a["employee_id"] == emp.id), None)
    assert row is not None and row["position_code"] == "A1" and row["source"] == "manual"
