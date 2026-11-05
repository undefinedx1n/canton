"""主管排班均衡（30 天窗口）的可重复验证。

诉求来源：六名主管在 30 天内当值次数应尽量平均，节假日/周末/高峰期与平日
工作日都要算进去，避免「总是同一个人上节假日」引发争议。

判定口径：N 人分 T 次，极差的数学下界是 0（T 能被 N 整除）或 1（不能整除）。
等于下界即「已均衡」，低于下界不可能，高于下界说明还有优化空间。

注意：这些用例有意使用 30 天窗口 —— 短窗口（如 3 天）下每人分到 1 次当值属于正常，
     `test_rolling_windows_accumulate_fairly` 单独覆盖滚动排班的情形。
"""
from __future__ import annotations

import datetime as dt

import pytest

from app.models import Employee
from app.services.solver import build_problem, solve
from tests.support import force_demand, make_published_version, set_day

HOLIDAY_OFFSETS = {7, 8, 14, 15, 21}
PEAK_OFFSETS = {5, 6, 7, 8, 12, 13, 14, 15, 19, 20, 21, 22}


def _all_supervisors(db) -> list[Employee]:
    return (
        db.query(Employee)
        .filter(Employee.is_supervisor.is_(True), Employee.is_active.is_(True))
        .order_by(Employee.id)
        .all()
    )


def _optimal_spread(total: int, people: int) -> int:
    if people <= 0:
        return 0
    return 1 if total % people else 0


def _counts(problem, assignments, sups) -> dict[int, dict[str, int]]:
    stat = {s.id: {"duty": 0, "work": 0, "weekend": 0, "holiday": 0, "peak": 0} for s in sups}
    for a in assignments:
        if a["employee_id"] not in stat:
            continue
        stat[a["employee_id"]]["work"] += 1
        if a.get("role_code") != "duty_supervisor":
            continue
        d = a["work_date"]
        cal = problem.calendar.get(d, {})
        row = stat[a["employee_id"]]
        row["duty"] += 1
        if d.weekday() >= 5:
            row["weekend"] += 1
        if cal.get("is_holiday"):
            row["holiday"] += 1
        if cal.get("is_peak"):
            row["peak"] += 1
    return stat


def _build_month(db, start: dt.date, days: int = 30) -> list[dt.date]:
    dates = [start + dt.timedelta(days=i) for i in range(days)]
    for i, d in enumerate(dates):
        set_day(
            db,
            d,
            day_type="holiday" if i in HOLIDAY_OFFSETS else ("weekend" if d.weekday() >= 5 else "weekday"),
            is_holiday=i in HOLIDAY_OFFSETS,
            is_peak=i in PEAK_OFFSETS,
        )
    return dates


@pytest.fixture()
def month(db):
    return _build_month(db, dt.date(2026, 11, 2))


@pytest.fixture()
def six_supervisors(db) -> list[Employee]:
    sups = _all_supervisors(db)
    assert len(sups) >= 2, "测试数据应包含多名主管"
    return sups


def test_duty_total_spread_is_minimal_in_30_days(db, month, six_supervisors):
    """30 天内总当值（含工作日）分配极差应为理论下界。"""
    force_demand(db, month, {"B2": 1}, zero_others=True)
    problem = build_problem(db, month[0], len(month))
    result = solve(problem, mode="draft", timeout=60)
    assert result.status in {"done", "OPTIMAL", "FEASIBLE"}, result.message

    stat = _counts(problem, result.assignments, six_supervisors)
    values = [stat[s.id]["duty"] for s in six_supervisors]
    total = sum(values)
    assert total == len(month), f"每天应有一名当值主管，实际 {total}"
    assert max(values) - min(values) <= _optimal_spread(total, len(six_supervisors)), (
        f"当值分布 {sorted(values)} 未达均衡下界"
    )


def test_weekend_holiday_peak_spreads_are_minimal(db, month, six_supervisors):
    """周末、节假日、高峰当值分别独立均衡，不能只看总数。"""
    force_demand(db, month, {"B2": 1}, zero_others=True)
    problem = build_problem(db, month[0], len(month))
    result = solve(problem, mode="draft", timeout=60)

    stat = _counts(problem, result.assignments, six_supervisors)
    n = len(six_supervisors)
    for key, label in (("weekend", "周末"), ("holiday", "节假日"), ("peak", "高峰")):
        values = [stat[s.id][key] for s in six_supervisors]
        total = sum(values)
        if total == 0:
            continue
        assert max(values) - min(values) <= _optimal_spread(total, n), (
            f"{label}当值分布 {sorted(values)} 未达均衡下界（共 {total} 次 / {n} 人）"
        )


def test_balance_with_real_demand_templates(db, month, six_supervisors):
    """不构造固定需求，直接用系统需求模板跑 30 天，检验真实场景下是否仍然均衡。"""
    problem = build_problem(db, month[0], len(month))
    result = solve(problem, mode="draft", timeout=60)
    assert result.status in {"done", "OPTIMAL", "FEASIBLE"}, result.message
    assert result.diagnostics.get("duplicate_assignments") == [], "同日重复安排同一个人属于硬错误"

    stat = _counts(problem, result.assignments, six_supervisors)
    values = [stat[s.id]["duty"] for s in six_supervisors]
    total = sum(values)
    if total == 0:
        pytest.skip("窗口内没有当值主管需求")
    assert max(values) - min(values) <= _optimal_spread(total, len(six_supervisors)), (
        f"真实模板下当值分布 {sorted(values)} 未达均衡下界"
    )


def test_rolling_windows_accumulate_fairly(db, six_supervisors):
    """3 天滚动窗口连续排 30 天，靠已发布历史累计也应达到均衡。

    这条覆盖「管理员习惯每次只排 3 天」的真实用法。
    """
    start = dt.date(2026, 11, 2)
    all_dates = _build_month(db, start, 30)
    stat = {s.id: {"duty": 0, "weekend": 0, "work": 0} for s in six_supervisors}

    for offset in range(0, 30, 3):
        chunk = all_dates[offset : offset + 3]
        force_demand(db, all_dates, {"B2": 1}, zero_others=True)
        problem = build_problem(db, chunk[0], len(chunk))
        result = solve(problem, mode="draft", timeout=30)
        assert result.status in {"done", "OPTIMAL", "FEASIBLE"}, result.message
        for a in result.assignments:
            if a["employee_id"] in stat:
                stat[a["employee_id"]]["work"] += 1
                if a.get("role_code") == "duty_supervisor":
                    stat[a["employee_id"]]["duty"] += 1
                    if a["work_date"].weekday() >= 5:
                        stat[a["employee_id"]]["weekend"] += 1
        make_published_version(db, start=chunk[0], days=len(chunk), assignments=result.assignments)

    values = [stat[s.id]["duty"] for s in six_supervisors]
    total = sum(values)
    assert total == 30, f"30 天应产生 30 次当值，实际 {total}"
    assert max(values) - min(values) <= _optimal_spread(total, len(six_supervisors)), (
        f"滚动排班累计当值 {sorted(values)} 未达均衡下界"
    )


def test_peak_is_subset_of_weekend_both_stay_balanced(db):
    """回归：线上日历里高峰日恰好全部落在周末，两个公平目标会互相压制。

    真实缺陷：原实现下 peak 分布为 [0,1,1,2,2,2]（极差 2，下界 1），
    因为压 weekend 的收益(权重30)大于压 peak(权重18)，求解器选择牺牲 peak。
    修复后「超出下界的人次」汇总成一个目标，不再在指标间做权重交易。
    """
    start = dt.date(2026, 11, 1)  # 与线上一致的周日起 30 天
    dates = [start + dt.timedelta(days=i) for i in range(30)]
    for d in dates:
        weekend = d.weekday() >= 5
        # 11/1 是周日但不是高峰，其余周末均为高峰 —— 复刻线上 calendar_days
        set_day(db, d, "weekend" if weekend else "weekday", is_peak=weekend and d.day != 1)
    force_demand(db, dates, {"B2": 1}, zero_others=True)

    problem = build_problem(db, start, 30)
    result = solve(problem, mode="draft", timeout=60)
    sups = _all_supervisors(db)

    stat = {s.id: {"duty": 0, "weekend": 0, "peak": 0} for s in sups}
    for a in result.assignments:
        if a["employee_id"] not in stat or a.get("role_code") != "duty_supervisor":
            continue
        cal = problem.calendar.get(a["work_date"], {})
        stat[a["employee_id"]]["duty"] += 1
        if a["work_date"].weekday() >= 5:
            stat[a["employee_id"]]["weekend"] += 1
        if cal.get("is_peak"):
            stat[a["employee_id"]]["peak"] += 1

    n = len(sups)
    for key, label in (("duty", "总当值"), ("weekend", "周末"), ("peak", "高峰")):
        vals = [stat[s.id][key] for s in sups]
        total = sum(vals)
        if total == 0:
            continue
        assert max(vals) - min(vals) <= _optimal_spread(total, n), (
            f"{label}分布 {sorted(vals)} 未达均衡下界（共 {total} 次 / {n} 人）"
        )


def test_fairness_never_breaks_leave_hard_constraint(db, month, six_supervisors):
    """均衡是软目标：不得为了拉平次数而安排请假中的人。"""
    from app.models import AvailabilityRequest

    forced = six_supervisors[0]
    db.add(
        AvailabilityRequest(
            employee_id=forced.id,
            start_date=month[0],
            end_date=month[-1],
            request_type="leave",
            status="approved",
            reason="测试全月请假",
        )
    )
    db.commit()

    force_demand(db, month, {"B2": 1}, zero_others=True)
    problem = build_problem(db, month[0], len(month))
    result = solve(problem, mode="draft", timeout=60)

    assert not [a for a in result.assignments if a["employee_id"] == forced.id], "请假期间不得被安排任何班次"


def test_thirty_day_solve_stays_within_generation_budget(db, month):
    """30 天窗口必须在接口允许的时间预算内返回，否则前端会一直转圈。"""
    problem = build_problem(db, month[0], len(month))
    result = solve(problem, mode="draft", timeout=min(90.0, max(20.0, len(month) * 2.5)))
    assert result.status in {"done", "OPTIMAL", "FEASIBLE"}, result.message
    assert result.duration_ms < 90_000, f"求解耗时 {result.duration_ms}ms 超出接口预算"
