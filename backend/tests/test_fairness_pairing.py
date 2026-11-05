"""软约束测试：主管当值公平（总/周末/节假日）、班次偏好、搭班偏好（需求 6 / 7 / 8 / 12）。

公平是软目标：这里验证"分布尽量均衡"的方向性，而不是某一个人的固定结果。
为消除运行日期带来的周末差异，窗口起点与需求全部在用例内固定。
"""
from __future__ import annotations

import datetime as dt

import pytest

from app.models import AvailabilityRequest, Employee, PairingPreference
from app.services.config_service import set_setting
from app.services.solver import build_problem, solve

from tests.support import co_occurrences, force_demand, plain_long_term, role_counts, set_day, spread, supervisors

WEEKDAY_DEMAND = {
    "A1": 1, "B1": 2, "A2": 3, "B2": 4, "A3": 1, "B3": 1,
    "A7": 1, "B7": 1, "A8": 1, "B8": 1, "AK": 1, "BK": 2,
}


def _week_starts_next_monday(today: dt.date) -> dt.date:
    """返回下一个周一（含今天），保证 7 天窗口内恰好有周六、周日各一天。"""
    return today + dt.timedelta(days=(7 - today.weekday()) % 7)


@pytest.fixture()
def week(db):
    today = dt.date.today()
    monday = _week_starts_next_monday(today)
    dates = [monday + dt.timedelta(days=i) for i in range(7)]
    for d in dates:
        set_day(db, d, "weekend" if d.weekday() >= 5 else "weekday")
    db.commit()
    # 周末按工作日口径给需求，避免人力不足掩盖公平性
    force_demand(db, dates, WEEKDAY_DEMAND)
    return dates


# --------------------------------------------------------------------------- #
# 主管当值公平
# --------------------------------------------------------------------------- #
def test_duty_fairness_total_spread(db, window):
    """3 天内当值主管不重复：6 位主管抢 3 个当值，极差应 ≤ 1。"""
    result = solve(build_problem(db, window[0], 3), mode="draft", timeout=25)
    assert result.status == "done"

    universe = [s.id for s in supervisors(db)]
    counts = role_counts(result.assignments, "duty_supervisor")
    assert sum(counts.values()) == 3, "每天应有 1 名当值主管"
    assert spread(counts, universe) <= 1


def test_weekend_duty_fairness(db, week):
    """周末当值在主管之间轮换：2 个周末日的当值应落在不同主管身上。"""
    result = solve(build_problem(db, week[0], 7), mode="draft", timeout=40)
    assert result.status == "done"

    cal = {a["work_date"] for a in result.assignments}
    assert set(week) <= cal, "7 天窗口应全部生成"

    weekend_days = [d for d in week if d.weekday() >= 5]
    counts: dict[int, int] = {}
    for a in result.assignments:
        if a["role_code"] == "duty_supervisor" and a["work_date"] in weekend_days:
            counts[a["employee_id"]] = counts.get(a["employee_id"], 0) + 1
    assert sum(counts.values()) == len(weekend_days), "每个周末日应有当值主管"
    assert spread(counts, [s.id for s in supervisors(db)]) <= 1


def test_holiday_duty_is_tracked(db, window):
    """节假日当值单独统计：标记 holiday 后，当值仍落在主管且只落 1 人。"""
    holiday = window[1]
    set_day(db, holiday, "holiday", is_holiday=True, label="测试节假日")
    db.commit()

    result = solve(build_problem(db, window[0], 3), mode="draft", timeout=25)
    holiday_duty = [
        a for a in result.assignments
        if a["work_date"] == holiday and a["role_code"] == "duty_supervisor"
    ]
    assert len(holiday_duty) == 1
    assert db.get(Employee, holiday_duty[0]["employee_id"]).is_supervisor is True


def test_fairness_never_overrides_leave(db, window):
    """公平是软目标：主管请假时不得为"公平"排他上班（需求 6.5）。"""
    sup = supervisors(db)[1]
    db.add(
        AvailabilityRequest(
            employee_id=sup.id, request_type="leave", start_date=window[0], end_date=window[-1],
            reason="年假", status="approved", reviewer="测试主管",
        )
    )
    db.commit()

    result = solve(build_problem(db, window[0], 3), mode="draft", timeout=25)
    assert all(a["employee_id"] != sup.id for a in result.assignments)
    assert any(a["role_code"] == "duty_supervisor" for a in result.assignments)


# --------------------------------------------------------------------------- #
# 班次偏好
# --------------------------------------------------------------------------- #
def test_shift_preference_is_honoured_when_weighted(db, window):
    """班次偏好为软约束：提高权重后，只具备早/晚各一岗的员工应全部排早班。"""
    emp = db.query(Employee).filter(Employee.employment_type == "part_time").first()
    from app.models import EmployeePositionSkill

    db.query(EmployeePositionSkill).filter(EmployeePositionSkill.employee_id == emp.id).delete()
    db.add(EmployeePositionSkill(employee_id=emp.id, position_code="A7", priority=1, proficiency=4, allowed=True))
    db.add(EmployeePositionSkill(employee_id=emp.id, position_code="B7", priority=1, proficiency=4, allowed=True))
    emp.shift_preference = "early"
    db.commit()

    set_setting(db, "solver_weights", {"shift_preference": 400, "gap_penalty": 400})
    db.commit()

    result = solve(build_problem(db, window[0], 3), mode="draft", timeout=25)
    mine = [a for a in result.assignments if a["employee_id"] == emp.id]
    assert mine, "该员工应被排班"
    assert all(a["shift"] == "early" for a in mine), [a["position_code"] for a in mine]


def test_shift_preference_does_not_break_coverage(db, window):
    """偏好不能压过覆盖硬约束：缺口仍为 0 时才算合格。"""
    set_setting(db, "solver_weights", {"shift_preference": 400})
    db.commit()
    result = solve(build_problem(db, window[0], 3), mode="draft", timeout=25)
    assert result.diagnostics["total_gap"] == 0


# --------------------------------------------------------------------------- #
# 搭班偏好
# --------------------------------------------------------------------------- #
def _pick_two_plain(db, n: int = 2):
    rows = (
        db.query(Employee)
        .filter(
            Employee.employment_type == "long_term",
            Employee.is_supervisor.is_(False),
            Employee.is_mentor.is_(False),
            Employee.is_active.is_(True),
        )
        .order_by(Employee.id)
        .limit(n)
        .all()
    )
    return rows


def test_pairing_avoid_keeps_employees_apart(db, window):
    """负向搭班：加大权重后两人不应出现在同一班次（需求 8）。"""
    a, b = _pick_two_plain(db)
    db.query(PairingPreference).delete()
    db.add(PairingPreference(employee_a_id=a.id, employee_b_id=b.id, preference_type="avoid", scope="same_shift", weight=20))
    db.commit()

    result = solve(build_problem(db, window[0], 3), mode="draft", timeout=25)
    assert co_occurrences(result.assignments, a.id, b.id, same_shift=True) == 0
    assert result.diagnostics["total_gap"] == 0, "搭班避让不应以牺牲覆盖为代价"


def test_pairing_prefer_brings_employees_together(db, window):
    """正向搭班：加大权重后两人至少同班一次。"""
    a, b = _pick_two_plain(db)
    db.query(PairingPreference).delete()
    db.add(PairingPreference(employee_a_id=a.id, employee_b_id=b.id, preference_type="prefer", scope="same_shift", weight=20))
    db.commit()

    result = solve(build_problem(db, window[0], 3), mode="draft", timeout=25)
    assert co_occurrences(result.assignments, a.id, b.id, same_shift=True) >= 1


def test_pairing_scope_same_position(db, window):
    """作用范围 same_position：避让目标是"同一岗位"，实现需按岗位判断而非仅按天。"""
    a, b = _pick_two_plain(db)
    db.query(PairingPreference).delete()
    db.add(PairingPreference(employee_a_id=a.id, employee_b_id=b.id, preference_type="avoid", scope="same_position", weight=20))
    db.commit()

    result = solve(build_problem(db, window[0], 3), mode="draft", timeout=25)
    same_position = 0
    by_day: dict[dt.date, dict[int, str]] = {}
    for row in result.assignments:
        if row["employee_id"] in (a.id, b.id):
            by_day.setdefault(row["work_date"], {})[row["employee_id"]] = row["position_code"]
    for d, m in by_day.items():
        if m.get(a.id) is not None and m.get(a.id) == m.get(b.id):
            same_position += 1
    assert same_position == 0


def test_pairing_cannot_break_hard_rules(db, window):
    """搭班偏好属于软约束，不得让不合资格的人上岗（需求 8 末条）。"""
    a, b = _pick_two_plain(db)
    db.query(PairingPreference).delete()
    db.add(PairingPreference(employee_a_id=a.id, employee_b_id=b.id, preference_type="prefer", scope="same_shift", weight=50))
    db.commit()

    from app.services.solver import is_eligible

    problem = build_problem(db, window[0], 3)
    result = solve(problem, mode="draft", timeout=25)
    for row in result.assignments:
        pos = problem.positions[row["position_code"]]
        emp = problem.employees[row["employee_id"]]
        assert is_eligible(emp, pos, row["work_date"]), f"{emp.name} 不具备 {pos.code} 资格却被排班"
