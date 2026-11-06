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
from tests.support import force_demand, plain_long_term, set_day

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
    """同时具备早/晚班岗位资格的员工，长期不应只排单一班次，但也不该天天来回切。

    为了让这条软约束可判定，这里构造一个"只能由他顶 A1/B7"的场景：
    其他人不具备这两个岗位资格、其他岗位需求清零，这样早/晚次数完全由他决定，
    求解器没有"换个人来上"的捷径。软约束在多人竞争场景下会被其他目标拉扯，
    那属于目标取舍，不适合作为回归断言。

    口径调整（补充需求）：班次连续性优先，因此不再要求 4 天内就切换，
    而是要求"整段连续 + 早晚都能轮到 + 切换次数很少"。
    """
    from app.models import EmployeePositionSkill

    emp = plain_long_term(db)
    # 其他人去掉 A1/B7 资格，保证这两个班次只能由被测员工承担
    db.query(EmployeePositionSkill).filter(
        EmployeePositionSkill.employee_id != emp.id,
        EmployeePositionSkill.position_code.in_(["A1", "B7"]),
    ).delete(synchronize_session=False)
    # 被测员工只保留一早在（A1）一晚（B7）两个资格
    db.query(EmployeePositionSkill).filter(EmployeePositionSkill.employee_id == emp.id).delete(synchronize_session=False)
    db.flush()
    db.add_all(
        [
            EmployeePositionSkill(employee_id=emp.id, position_code="A1", priority=1, proficiency=3, allowed=True),
            EmployeePositionSkill(employee_id=emp.id, position_code="B7", priority=1, proficiency=3, allowed=True),
        ]
    )
    days = DATES[:8]
    for d in days:
        set_day(db, d)
    force_demand(db, days, {"A1": 1, "B7": 1}, zero_others=True)
    db.commit()

    problem = build_problem(db, START, len(days))
    res = solve(problem, mode="draft", timeout=20.0)
    mine = sorted(
        (a for a in res.assignments if a["employee_id"] == emp.id), key=lambda a: a["work_date"]
    )
    assert mine, "该员工应有排班"
    shifts = {a["shift"] for a in mine}
    assert shifts == {"early", "late"}, f"整窗口只排了单一班次，未被纠正：{shifts}"
    switches = sum(
        1 for i in range(1, len(mine)) if mine[i]["shift"] != mine[i - 1]["shift"]
    )
    assert switches <= 2, f"8 天内切换 {switches} 次，过于频繁：{[a['shift'] for a in mine]}"


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
