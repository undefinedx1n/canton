"""班次连续性与连休上限的回归测试。

对应补充需求（排得多之后暴露的问题）：
1. 尽量同一班次连续上，不要频繁早晚切换；
2. 晚班转早班尽量排在休息之后；
3. 休息不允许连续超过 3 天；
4. 人工排班（锁定/请假）不受这两条规则影响，也不能因此排不出班。
"""
from __future__ import annotations

import datetime as dt

from app.models import AvailabilityRequest, Employee, EmployeePositionSkill
from app.services.config_service import set_setting
from app.services.solver import build_problem, solve

from tests.support import force_demand, set_day

START = dt.date(2026, 3, 2)  # 周一


def _dates(n: int) -> list[dt.date]:
    return [START + dt.timedelta(days=i) for i in range(n)]


def _prepare(db, dates, codes: list[str], keep: int = 2) -> list[Employee]:
    """只留下 keep 名普通长期员工具备 codes 的资格，其余人清空这批岗位资格。"""
    emps = (
        db.query(Employee)
        .filter(Employee.is_active.is_(True), Employee.is_supervisor.is_(False), Employee.employment_type == "long_term")
        .order_by(Employee.id)
        .limit(keep)
        .all()
    )
    keep_ids = [e.id for e in emps]
    db.query(EmployeePositionSkill).filter(
        EmployeePositionSkill.position_code.in_(codes),
        EmployeePositionSkill.employee_id.notin_(keep_ids),
    ).delete(synchronize_session=False)
    for e in emps:
        db.query(EmployeePositionSkill).filter(EmployeePositionSkill.employee_id == e.id).delete(synchronize_session=False)
        db.flush()
        for code in codes:
            db.add(EmployeePositionSkill(employee_id=e.id, position_code=code, priority=1, proficiency=3, allowed=True))
    for d in dates:
        set_day(db, d)
    db.commit()
    return emps


def _seq(assignments, eid: int, dates) -> list[str]:
    by_day = {a["work_date"]: a["shift"] for a in assignments if a["employee_id"] == eid}
    return [by_day.get(d, "rest") for d in dates]


def _max_rest_run(seq: list[str]) -> int:
    best = cur = 0
    for s in seq:
        if s == "rest":
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


# --------------------------------------------------------------------------- #
# 班次连续性
# --------------------------------------------------------------------------- #
def test_same_shift_preferred_over_daily_switching(db):
    """两个班次都有人手、且每天都得上班时，应保持同一班次而不是天天来回切。"""
    dates = _dates(4)
    emps = _prepare(db, dates, ["A1", "B7"], keep=2)
    force_demand(db, dates, {"A1": 1, "B7": 1}, zero_others=True)

    res = solve(build_problem(db, START, len(dates)), mode="draft", timeout=20.0)
    assert res.status == "done", res.message

    seqs = [_seq(res.assignments, e.id, dates) for e in emps]
    switches = sum(
        1 for seq in seqs for i in range(1, len(seq)) if seq[i - 1] in ("early", "late") and seq[i] in ("early", "late") and seq[i - 1] != seq[i]
    )
    assert switches == 0, f"存在 {switches} 次班次切换：{seqs}"
    assert res.objective_breakdown.get("shift_switch", 0) == 0
    assert {s for seq in seqs for s in seq} == {"early", "late"}, "两个班次都应被覆盖"


def test_late_to_early_switch_is_penalised_more_than_reverse(db):
    """同样是"没休息就换班"，晚→早的代价必须高于早→晚。"""
    # 晚 → 早（相邻两天，中间没有休息）
    dates = _dates(2)
    emps = _prepare(db, dates, ["A1", "B7"], keep=2)
    force_demand(db, dates, {}, zero_others=True)
    force_demand(db, [dates[0]], {"B7": 2}, zero_others=True)
    force_demand(db, [dates[1]], {"A1": 2}, zero_others=True)

    res = solve(build_problem(db, START, 2), mode="draft", timeout=15.0)
    assert res.status == "done", res.message
    assert res.objective_breakdown.get("shift_switch", 0) > 0
    assert res.objective_breakdown.get("shift_switch_late_to_early", 0) > 0
    for e in emps:
        assert _seq(res.assignments, e.id, dates) == ["late", "early"]


def test_early_to_late_switch_carries_no_late_to_early_penalty(db):
    """早 → 晚不触发"晚转早"额外惩罚（顺序相反时惩罚桶必须为 0）。"""
    dates = _dates(2)
    _prepare(db, dates, ["A1", "B7"], keep=2)
    force_demand(db, dates, {}, zero_others=True)
    force_demand(db, [dates[0]], {"A1": 2}, zero_others=True)
    force_demand(db, [dates[1]], {"B7": 2}, zero_others=True)

    res = solve(build_problem(db, START, 2), mode="draft", timeout=15.0)
    assert res.status == "done", res.message
    assert res.objective_breakdown.get("shift_switch", 0) > 0
    assert res.objective_breakdown.get("shift_switch_late_to_early", 0) == 0


# --------------------------------------------------------------------------- #
# 连休上限
# --------------------------------------------------------------------------- #
def test_no_more_than_three_consecutive_rest_days(db):
    """名额少、人多时必须把人摊开，出现第 4 天连休即为违规。"""
    dates = _dates(10)
    emps = _prepare(db, dates, ["A1"], keep=5)
    force_demand(db, dates, {"A1": 2}, zero_others=True)

    problem = build_problem(db, START, len(dates))
    assert problem.settings["rest_rule"]["max_consecutive_rest_days"] == 3
    res = solve(problem, mode="draft", timeout=30.0)
    assert res.status == "done", res.message

    for e in emps:
        seq = _seq(res.assignments, e.id, dates)
        assert _max_rest_run(seq) <= 3, f"{e.name} 连休 {_max_rest_run(seq)} 天：{seq}"


def test_rest_cap_degrades_gracefully_when_staffing_too_thin(db):
    """人均名额不足时（5 人抢 1 个名额/天）硬约束必然无解，必须降级出表而不是排不出来。"""
    dates = _dates(8)
    _prepare(db, dates, ["A1"], keep=5)
    force_demand(db, dates, {"A1": 1}, zero_others=True)

    res = solve(build_problem(db, START, len(dates)), mode="draft", timeout=25.0)
    assert res.status == "done", res.message
    assert res.diagnostics["total_gap"] == 0, "名额应被填满"
    assert "连休上限" in res.message, "降级时必须给出明确提示"


def test_rest_cap_can_be_switched_off_by_setting(db):
    """连休上限是配置项：设为 0 时不再生成该约束（允许长连休）。"""
    dates = _dates(6)
    _prepare(db, dates, ["A1"], keep=5)
    force_demand(db, dates, {"A1": 1}, zero_others=True)
    set_setting(db, "rest_rule", {"max_consecutive_rest_days": 0}, "测试关闭连休上限")

    problem = build_problem(db, START, len(dates))
    assert problem.settings["rest_rule"]["max_consecutive_rest_days"] == 0


def test_long_leave_is_exempt_from_rest_cap(db):
    """整段请假造成的长时间不在岗不能把排班顶成不可行（人工/请假优先）。"""
    dates = _dates(8)
    emps = _prepare(db, dates, ["A1"], keep=3)
    force_demand(db, dates, {"A1": 1}, zero_others=True)

    away = emps[0]
    db.add(
        AvailabilityRequest(
            employee_id=away.id,
            request_type="leave",
            start_date=dates[0],
            end_date=dates[5],
            reason="年假",
            status="approved",
            reviewer="测试主管",
        )
    )
    db.commit()

    res = solve(build_problem(db, START, len(dates)), mode="draft", timeout=30.0)
    assert res.status == "done", res.message
    assert all(a["employee_id"] != away.id for a in res.assignments if a["work_date"] <= dates[5])
    assert res.diagnostics["total_gap"] == 0, "名额应由其余员工补满"
