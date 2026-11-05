"""排班求解服务。

硬约束（需求文档第 12 节，不可违反）：
  1. 每岗位人数不超过设定名额；低于最低人数时产生缺口（strict 模式判为不可行）。
  2. 每员工每日最多一个岗位。
  3. 员工必须具备该岗位资格（skills.allowed=True，未被禁止）。
  4. 岗位的允许员工类型限制（A3/B3 仅长期员工/主管）。
  5. 运营角色只能由 is_supervisor=True 的员工承担。
  6. 已批准请假/不可用时间不能排班。
  7. 已锁定安排不参与求解、不可被移动。
软约束（权重可配置）：主管当值/周末/节假日/旺季公平、休息目标、班次偏好、
岗位优先级、搭班偏好、旺季能力匹配、工作量均衡。

优先使用 OR-Tools CP-SAT；依赖缺失或异常时退化为启发式，保证系统可用并给出明确提示。
"""
from __future__ import annotations

import datetime as dt
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from ..models import (
    AvailabilityRequest,
    CalendarDay,
    DailyDemand,
    DemandTemplate,
    Employee,
    EmployeePositionSkill,
    EmployeeTypeOption,
    PairingPreference,
    Position,
    ScheduleAssignment,
    ScheduleVersion,
)
from .config_service import get_setting, weights

try:  # pragma: no cover - 环境相关
    import contextlib
    import io as _io

    with contextlib.redirect_stdout(_io.StringIO()):  # Windows 下 OR-Tools 会打印 DLL 加载日志
        from ortools.sat.python import cp_model

    ORTOOLS_AVAILABLE = True
except Exception:  # pragma: no cover
    cp_model = None  # type: ignore
    ORTOOLS_AVAILABLE = False

BIG = 1_000_000


# --------------------------------------------------------------------------- #
# 数据结构
# --------------------------------------------------------------------------- #
@dataclass
class EmployeeInfo:
    id: int
    name: str
    employment_type: str
    is_supervisor: bool
    is_mentor: bool
    shift_preference: str
    formal: bool
    skills: dict[str, tuple[int, int, bool]] = field(default_factory=dict)
    leaves: set[dt.date] = field(default_factory=set)
    leave_shifts: set[tuple[dt.date, str]] = field(default_factory=set)
    max_days_per_week: int | None = None


@dataclass
class PositionInfo:
    code: str
    name: str
    department: str
    area: str
    shift: str
    allowed_types: list[str]
    min_supervisors: int
    supervisor_role_code: str | None
    color: str
    sort_order: int


@dataclass
class Problem:
    start: dt.date
    dates: list[dt.date]
    employees: dict[int, EmployeeInfo]
    positions: dict[str, PositionInfo]
    demands: dict[tuple[dt.date, str], tuple[int, int]]
    calendar: dict[dt.date, dict[str, Any]]
    locked: list[dict[str, Any]]
    pairing: list[dict[str, Any]]
    history: dict[int, dict[str, int]]
    period_start: dt.date
    period_end: dt.date
    weights: dict[str, float]
    settings: dict[str, Any]


@dataclass
class SolveResult:
    status: str
    solver: str
    assignments: list[dict[str, Any]]
    diagnostics: dict[str, Any]
    objective_value: float
    objective_breakdown: dict[str, float]
    duration_ms: int
    message: str


# --------------------------------------------------------------------------- #
# 周期与问题构建
# --------------------------------------------------------------------------- #
def period_bounds(work_date: dt.date, period_type: str = "calendar_month", start_day: int = 1) -> tuple[dt.date, dt.date]:
    if period_type == "calendar_month":
        first = work_date.replace(day=1)
        nxt = (first + dt.timedelta(days=32)).replace(day=1)
        return first, nxt - dt.timedelta(days=1)
    if period_type == "rolling_28":
        return work_date - dt.timedelta(days=27), work_date
    anchor = work_date.replace(day=min(max(start_day, 1), 28))
    if work_date < anchor:
        prev = anchor - dt.timedelta(days=1)
        anchor = prev.replace(day=min(max(start_day, 1), 28))
    return anchor, anchor + dt.timedelta(days=27)


def resolve_demand(
    db: Session, dates: list[dt.date]
) -> tuple[list[Position], dict[tuple[dt.date, str], tuple[int, int]], dict[dt.date, Any]]:
    positions = (
        db.query(Position).filter(Position.is_active.is_(True)).order_by(Position.sort_order, Position.code).all()
    )
    templates = {(t.day_type, t.position_code): t for t in db.query(DemandTemplate).all()}
    overrides = {(o.work_date, o.position_code): o for o in db.query(DailyDemand).filter(DailyDemand.work_date.in_(dates)).all()}
    cal = {c.work_date: c for c in db.query(CalendarDay).filter(CalendarDay.work_date.in_(dates)).all()}

    demands: dict[tuple[dt.date, str], tuple[int, int]] = {}
    for d in dates:
        day = cal.get(d)
        day_type = day.day_type if day else ("weekend" if d.weekday() >= 5 else "weekday")
        for p in positions:
            key = (d, p.code)
            if key in overrides:
                o = overrides[key]
                demands[key] = (o.target_count, o.min_count if o.min_count else o.target_count)
                continue
            tpl = templates.get((day_type, p.code)) or templates.get(("weekday", p.code))
            if tpl is None:
                demands[key] = (0, 0)
            else:
                demands[key] = (tpl.target_count, tpl.min_count if tpl.min_count else tpl.target_count)
    return positions, demands, cal


def build_problem(db: Session, start: dt.date, days: int = 3) -> Problem:
    dates = [start + dt.timedelta(days=i) for i in range(days)]
    positions_raw, demands, cal_rows = resolve_demand(db, dates)
    positions = {
        p.code: PositionInfo(
            code=p.code,
            name=p.name,
            department=p.department,
            area=p.area,
            shift=p.shift,
            allowed_types=list(p.allowed_employment_types or []),
            min_supervisors=p.min_supervisors,
            supervisor_role_code=p.supervisor_role_code,
            color=p.color,
            sort_order=p.sort_order,
        )
        for p in positions_raw
    }

    formal_codes = {t.code for t in db.query(EmployeeTypeOption).all() if t.counts_as_formal} or {"long_term"}

    pending_cfg = get_setting(db, "pending_leave_policy")
    hard_status = {"approved"}
    if pending_cfg.get("treat_pending_as_unavailable"):
        hard_status.add("pending")
    leave_rows = db.query(AvailabilityRequest).filter(AvailabilityRequest.status.in_(list(hard_status))).all()

    employees: dict[int, EmployeeInfo] = {}
    for e in db.query(Employee).filter(Employee.is_active.is_(True)).all():
        employees[e.id] = EmployeeInfo(
            id=e.id,
            name=e.name,
            employment_type=e.employment_type,
            is_supervisor=e.is_supervisor,
            is_mentor=e.is_mentor,
            shift_preference=e.shift_preference or "any",
            formal=e.employment_type in formal_codes,
            max_days_per_week=e.max_days_per_week,
        )
    for s in db.query(EmployeePositionSkill).all():
        if s.employee_id in employees:
            employees[s.employee_id].skills[s.position_code] = (s.priority, s.proficiency, s.allowed)

    for req in leave_rows:
        if req.request_type not in ("leave", "rest", "unavailable"):
            continue
        if req.employee_id not in employees:
            continue
        emp = employees[req.employee_id]
        cur = max(req.start_date, dates[0])
        end = min(req.end_date, dates[-1])
        while cur <= end:
            if req.shift:
                emp.leave_shifts.add((cur, req.shift))
            else:
                emp.leaves.add(cur)
            cur += dt.timedelta(days=1)

    calendar: dict[dt.date, dict[str, Any]] = {}
    for d in dates:
        c = cal_rows.get(d)
        calendar[d] = (
            {"day_type": c.day_type, "is_holiday": c.is_holiday, "is_peak": c.is_peak, "label": c.label}
            if c
            else {"day_type": "weekend" if d.weekday() >= 5 else "weekday", "is_holiday": False, "is_peak": False, "label": ""}
        )

    # 锁定安排：最近一个与本窗口有交集的草稿版本中的锁定行
    locked: list[dict[str, Any]] = []
    draft = (
        db.query(ScheduleVersion)
        .filter(
            ScheduleVersion.status == "draft",
            ScheduleVersion.start_date <= dates[-1],
            ScheduleVersion.end_date >= dates[0],
        )
        .order_by(ScheduleVersion.id.desc())
        .first()
    )
    if draft:
        for a in draft.assignments:
            if a.is_locked and dates[0] <= a.work_date <= dates[-1]:
                locked.append(
                    {
                        "work_date": a.work_date,
                        "employee_id": a.employee_id,
                        "position_code": a.position_code,
                        "shift": a.shift,
                        "role_code": a.role_code,
                    }
                )

    pairing: list[dict[str, Any]] = []
    for p in db.query(PairingPreference).filter(PairingPreference.is_active.is_(True)).all():
        if p.start_date and p.end_date and (p.end_date < dates[0] or p.start_date > dates[-1]):
            continue
        pairing.append(
            {"a": p.employee_a_id, "b": p.employee_b_id, "type": p.preference_type, "scope": p.scope, "weight": p.weight}
        )

    fair = get_setting(db, "fairness_period")
    period_start, period_end = period_bounds(
        dates[0], fair.get("period_type", "calendar_month"), int(fair.get("cycle_start_day", 1))
    )

    history: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    hist_rows = (
        db.query(ScheduleAssignment, CalendarDay)
        .join(ScheduleVersion, ScheduleVersion.id == ScheduleAssignment.version_id)
        .outerjoin(CalendarDay, CalendarDay.work_date == ScheduleAssignment.work_date)
        .filter(ScheduleVersion.status == "published")
        .filter(ScheduleAssignment.work_date >= period_start - dt.timedelta(days=400))
        .filter(ScheduleAssignment.work_date <= period_end)
        .all()
    )
    for a, cday in hist_rows:
        if dates[0] <= a.work_date <= dates[-1]:
            continue
        if not (period_start <= a.work_date <= period_end):
            continue
        entry = history[a.employee_id]
        entry["work"] += 1
        if a.role_code == "duty_supervisor":
            entry["duty"] += 1
        if a.work_date.weekday() >= 5:
            entry["weekend_work"] += 1
            if a.role_code == "duty_supervisor":
                entry["weekend_duty"] += 1
        if cday and cday.is_holiday:
            entry["holiday_duty"] += 1 if a.role_code == "duty_supervisor" else 0
        if cday and cday.is_peak:
            entry["peak_duty"] += 1 if a.role_code == "duty_supervisor" else 0

    return Problem(
        start=dates[0],
        dates=dates,
        employees=employees,
        positions=positions,
        demands=demands,
        calendar=calendar,
        locked=locked,
        pairing=pairing,
        history={k: dict(v) for k, v in history.items()},
        period_start=period_start,
        period_end=period_end,
        weights=weights(db),
        settings={
            "rest_rule": get_setting(db, "rest_rule"),
            "gap_policy": get_setting(db, "gap_policy"),
            "role_slot_rule": get_setting(db, "role_slot_rule"),
        },
    )


# --------------------------------------------------------------------------- #
# 资格判断（硬约束的唯一出口，测试直接覆盖）
# --------------------------------------------------------------------------- #
def is_eligible(emp: EmployeeInfo, pos: PositionInfo, work_date: dt.date) -> bool:
    skill = emp.skills.get(pos.code)
    if skill is None or not skill[2]:
        return False
    if pos.allowed_types and emp.employment_type not in pos.allowed_types:
        return False
    if work_date in emp.leaves:
        return False
    if (work_date, pos.shift) in emp.leave_shifts:
        return False
    return True


def skill_score(emp: EmployeeInfo, code: str) -> tuple[int, int]:
    p, prof, _ = emp.skills.get(code, (3, 3, False))
    return p, prof


# --------------------------------------------------------------------------- #
# 求解入口
# --------------------------------------------------------------------------- #
def solve(problem: Problem, mode: str = "draft", timeout: float = 20.0, seed: int = 20261105) -> SolveResult:
    t0 = time.time()
    if not ORTOOLS_AVAILABLE:
        return _solve_heuristic(problem, mode, t0)
    try:
        result = _solve_cpsat(problem, mode, timeout, seed)
    except Exception as exc:  # pragma: no cover - 兜底
        result = _solve_heuristic(problem, mode, t0)
        result.message = f"CP-SAT 求解异常，已退回启发式：{exc}"
    result.duration_ms = int((time.time() - t0) * 1000)
    return result


def _pen(md, buckets, key: str, expr, weight: float, name: str, ub: int = BIG):
    """把表达式固化为 IntVar，便于同时用于目标函数与目标值分解展示。"""
    if weight <= 0:
        return None
    v = md.NewIntVar(0, ub, f"pen_{key}_{name}")
    md.Add(v == expr)
    buckets[key].append((v, weight))
    return v


def _solve_cpsat(problem: Problem, mode: str, timeout: float, seed: int) -> SolveResult:
    md = cp_model.CpModel()
    w = problem.weights
    dates = problem.dates
    positions = problem.positions
    emps = problem.employees

    x: dict[tuple[dt.date, int, str], Any] = {}
    eligible_map: dict[tuple[dt.date, str], list[int]] = {}
    for d in dates:
        for code, pos in positions.items():
            target, _minimum = problem.demands.get((d, code), (0, 0))
            if target <= 0:
                continue
            eligible = [eid for eid, e in emps.items() if is_eligible(e, pos, d)]
            eligible_map[(d, code)] = eligible
            for eid in eligible:
                x[(d, eid, code)] = md.NewBoolVar(f"x_{d:%m%d}_{eid}_{code}")

    buckets: dict[str, list[tuple[Any, float]]] = defaultdict(list)

    # 硬约束：每人每日最多一个岗位
    by_emp_day: dict[tuple[dt.date, int], list[Any]] = defaultdict(list)
    for (d, eid, _code), var in x.items():
        by_emp_day[(d, eid)].append(var)
    for vars_ in by_emp_day.values():
        if len(vars_) > 1:
            md.Add(sum(vars_) <= 1)

    # 覆盖与缺口
    for d in dates:
        for code, pos in positions.items():
            target, minimum = problem.demands.get((d, code), (0, 0))
            if target <= 0:
                continue
            vars_ = [v for (dd, _e, c), v in x.items() if dd == d and c == code]
            if not vars_:
                continue
            md.Add(sum(vars_) <= target)
            if minimum > 0:
                gap = md.NewIntVar(0, minimum, f"gap_{d:%m%d}_{code}")
                md.Add(sum(vars_) + gap >= minimum)
                _pen(md, buckets, "gap", gap, w["gap_penalty"], f"gap_{d:%m%d}_{code}", minimum)
            if target > minimum:
                short = md.NewIntVar(0, target - minimum, f"short_{d:%m%d}_{code}")
                md.Add(sum(vars_) + short >= target)
                _pen(md, buckets, "coverage", short, w["coverage_target"], f"short_{d:%m%d}_{code}", target)

    # 主管运营角色
    role_vars: dict[tuple[dt.date, int, str], Any] = {}
    for d in dates:
        for code, pos in positions.items():
            if not pos.supervisor_role_code:
                continue
            target, _minimum = problem.demands.get((d, code), (0, 0))
            if target <= 0:
                continue
            vars_ = [v for (dd, _e, c), v in x.items() if dd == d and c == code]
            if not vars_:
                # 无任何排班变量，运营角色必然缺失
                _pen(md, buckets, "gap", 1, w["gap_penalty"], f"norole_{d:%m%d}_{code}", 1)
                continue
            covered = md.NewBoolVar(f"cov_{d:%m%d}_{code}")
            md.Add(sum(vars_) <= target * covered)
            sup_vars = {
                eid: x[(d, eid, code)]
                for eid in eligible_map.get((d, code), [])
                if emps[eid].is_supervisor and (d, eid, code) in x
            }
            if sup_vars:
                md.Add(sum(sup_vars.values()) == covered)
                for eid, rv in sup_vars.items():
                    role_vars[(d, eid, code)] = rv
                if pos.min_supervisors > 1:
                    md.Add(sum(sup_vars.values()) >= min(pos.min_supervisors, len(sup_vars)) * covered)
            else:
                md.Add(covered == 0)
                _pen(md, buckets, "gap", 1, w["gap_penalty"], f"norole_{d:%m%d}_{code}", 1)
            # 岗位要求主管时，若无人可担任则整个岗位不排（避免伪造满足）

    # 锁定安排：固定变量，并禁止当日改排其他岗位
    for lock in problem.locked:
        d, eid, code = lock["work_date"], lock["employee_id"], lock["position_code"]
        if (d, eid, code) in x:
            md.Add(x[(d, eid, code)] == 1)
        for (dd, ee, cc), var in x.items():
            if dd == d and ee == eid and cc != code:
                md.Add(var == 0)

    # 班次偏好 / 岗位优先级 / 旺季能力匹配
    for (d, eid, code), var in x.items():
        emp, pos = emps[eid], positions[code]
        priority, prof = skill_score(emp, code)
        if emp.shift_preference in ("early", "late") and emp.shift_preference != pos.shift:
            _pen(md, buckets, "shift_preference", var, w["shift_preference"], f"pref_{d:%m%d}_{eid}")
        if priority > 1:
            _pen(md, buckets, "position_priority", var, w["position_priority"] * (priority - 1), f"prio_{d:%m%d}_{eid}")
        if problem.calendar.get(d, {}).get("is_peak"):
            _pen(md, buckets, "proficiency", var, w["proficiency"] * max(0, 5 - prof), f"prof_{d:%m%d}_{eid}")

    # 主管公平（总当值 / 周末 / 节假日 / 旺季）
    # 口径必须与历史统计一致：只统计"晚班当值主管"（duty_supervisor），
    # 早班/晚班辅助角色属于岗位标记，不计入当值天数（需求 6）。
    supervisors = [eid for eid, e in emps.items() if e.is_supervisor]
    if supervisors:
        duty_role_vars = {
            key: var
            for key, var in role_vars.items()
            if positions[key[2]].supervisor_role_code == "duty_supervisor"
        }
        duty_terms: dict[int, list[Any]] = {eid: [] for eid in supervisors}
        for (d, eid, _code), var in duty_role_vars.items():
            duty_terms[eid].append(var)
        _add_fairness(md, buckets, "duty_fairness", supervisors, duty_terms, problem.history, "duty", w["duty_fairness"], "duty")

        subsets = [
            ("weekend_fairness", "weekend_duty", "wk", lambda d: d.weekday() >= 5),
            ("holiday_fairness", "holiday_duty", "hol", lambda d: bool(problem.calendar.get(d, {}).get("is_holiday"))),
            ("peak_fairness", "peak_duty", "peak", lambda d: bool(problem.calendar.get(d, {}).get("is_peak"))),
        ]
        for key, hist_key, tag, predicate in subsets:
            days = [d for d in dates if predicate(d)]
            if not days:
                continue
            subset_terms: dict[int, list[Any]] = {eid: [] for eid in supervisors}
            for (d, eid, _code), var in duty_role_vars.items():
                if d in days:
                    subset_terms[eid].append(var)
            _add_fairness(md, buckets, key, supervisors, subset_terms, problem.history, hist_key, w[key], tag)

    # 休息目标（长期员工周期内应休天数）
    rest_rule = problem.settings.get("rest_rule", {})
    rest_days = int(rest_rule.get("rest_days", 8) or 8)
    period_days = (problem.period_end - problem.period_start).days + 1
    max_work = max(1, period_days - rest_days)
    for eid, emp in emps.items():
        if rest_rule.get("apply_to_formal_only", True) and not emp.formal:
            continue
        vars_ = [v for (d, ee, _c), v in x.items() if ee == eid]
        if not vars_:
            continue
        hist_work = problem.history.get(eid, {}).get("work", 0)
        remaining = max(0, max_work - hist_work)
        over = md.NewIntVar(0, len(vars_), f"rest_over_{eid}")
        md.Add(sum(vars_) - remaining <= over)
        _pen(md, buckets, "rest_balance", over, w["rest_balance"], f"rest_{eid}", len(vars_))

    # 工作量均衡：员工之间排班天数极差（可解释、量纲一致）
    load_terms = []
    for eid in emps:
        vars_ = [v for (d, ee, _c), v in x.items() if ee == eid]
        if not vars_:
            continue
        t = md.NewIntVar(0, len(dates), f"load_{eid}")
        md.Add(t == sum(vars_))
        load_terms.append((eid, t))
    if len(load_terms) > 1 and w["workload_balance"] > 0:
        loads = [t for _eid, t in load_terms]
        mx = md.NewIntVar(0, len(dates), "load_max")
        mn = md.NewIntVar(0, len(dates), "load_min")
        md.AddMaxEquality(mx, loads)
        md.AddMinEquality(mn, loads)
        spread = md.NewIntVar(0, len(dates), "load_spread")
        md.Add(spread == mx - mn)
        _pen(md, buckets, "workload_balance", spread, w["workload_balance"], "load_spread", len(dates))

    # 搭班偏好（需求 8：可按同班次 / 同岗位 / 同日作用范围配置）
    for pr in problem.pairing:
        a, b = pr["a"], pr["b"]
        if a not in emps or b not in emps:
            continue
        weight = float(pr["weight"] or 1.0)
        scope = pr.get("scope") or "same_shift"
        for d in dates:
            va = [v for (dd, ee, _c), v in x.items() if dd == d and ee == a]
            vb = [v for (dd, ee, _c), v in x.items() if dd == d and ee == b]
            if not va or not vb:
                continue

            if scope == "same_day":
                both = md.NewBoolVar(f"pair_{a}_{b}_{d:%m%d}")
                md.Add(both >= sum(va) + sum(vb) - 1)
                md.Add(both <= sum(va))
                md.Add(both <= sum(vb))
                expr, ub = both, 1
            else:
                # 同班次：两人当天都被排班且班次相同；同岗位：同一岗位代码。
                # 注意两人的可用岗位集合可能完全不相交，因此需要分别累计各自的班次覆盖，
                # 不能只在"双方都有变量"的岗位上求交。
                groups: dict[str, list] = defaultdict(lambda: [[], []])
                for code, pos in positions.items():
                    xa, xb = x.get((d, a, code)), x.get((d, b, code))
                    if scope == "same_position":
                        if xa is None or xb is None:
                            continue
                        groups[code][0].append(xa)
                        groups[code][1].append(xb)
                    else:
                        if xa is not None:
                            groups[pos.shift][0].append(xa)
                        if xb is not None:
                            groups[pos.shift][1].append(xb)
                terms = []
                for key, (la, lb) in groups.items():
                    if not la or not lb:
                        continue
                    both = md.NewBoolVar(f"pair_{a}_{b}_{key}_{d:%m%d}")
                    md.Add(both <= sum(la))
                    md.Add(both <= sum(lb))
                    md.Add(both >= sum(la) + sum(lb) - 1)
                    terms.append(both)
                if not terms:
                    continue
                expr, ub = sum(terms), len(terms)

            if pr["type"] == "avoid":
                _pen(md, buckets, "pairing_avoid", expr, w["pairing_avoid"] * weight, f"pav_{a}_{b}_{d:%m%d}", ub)
            else:
                _pen(md, buckets, "pairing_prefer", ub - expr, w["pairing_prefer"] * weight, f"ppr_{a}_{b}_{d:%m%d}", ub)

    objective = sum(int(round(weight)) * var for terms in buckets.values() for var, weight in terms)
    md.Minimize(objective)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = max(1.0, timeout)
    solver.parameters.num_search_workers = 8
    solver.parameters.random_seed = seed
    status = solver.Solve(md)
    status_name = solver.StatusName(status)
    feasible = status in (cp_model.OPTIMAL, cp_model.FEASIBLE)

    assignments: list[dict[str, Any]] = []
    if feasible:
        for (d, eid, code), var in x.items():
            if solver.Value(var) == 1:
                role = None
                if positions[code].supervisor_role_code and (d, eid, code) in role_vars and solver.Value(role_vars[(d, eid, code)]) == 1:
                    role = positions[code].supervisor_role_code
                assignments.append(
                    {
                        "work_date": d,
                        "employee_id": eid,
                        "position_code": code,
                        "shift": positions[code].shift,
                        "role_code": role,
                        "is_locked": False,
                        "source": "auto",
                    }
                )
        for lock in problem.locked:
            existing = next(
                (a for a in assignments if a["work_date"] == lock["work_date"] and a["employee_id"] == lock["employee_id"]),
                None,
            )
            if existing:
                existing.update({"is_locked": True, "source": "manual"})
                if lock.get("role_code"):
                    existing["role_code"] = lock["role_code"]
            else:
                assignments.append({**lock, "is_locked": True, "source": "manual"})

    breakdown: dict[str, float] = {k: 0.0 for k in buckets}
    if feasible:
        for key, terms in buckets.items():
            breakdown[key] = float(sum(solver.Value(var) * weight for var, weight in terms))

    diagnostics = _diagnose(problem, assignments, status_name)
    result_status = "done"
    if not feasible:
        result_status = "infeasible"
    elif diagnostics["total_gap"] > 0 and mode == "strict":
        result_status = "infeasible"

    message = {
        "OPTIMAL": "已找到最优解",
        "FEASIBLE": "达到时间上限，返回当前最佳可行解",
        "INFEASIBLE": "约束冲突，无法生成完整排班",
        "UNKNOWN": "求解超时且未找到可行解",
        "MODEL_INVALID": "模型定义无效",
    }.get(status_name, status_name)

    return SolveResult(
        status=result_status,
        solver="cp-sat",
        assignments=assignments,
        diagnostics=diagnostics,
        objective_value=float(solver.ObjectiveValue()) if feasible else 0.0,
        objective_breakdown=breakdown,
        duration_ms=0,
        message=message,
    )


def _add_fairness(md, buckets, key, employees, terms_map, history, hist_key, weight, tag):
    if len(employees) <= 1 or weight <= 0:
        return
    totals = []
    for eid in employees:
        hist = int(history.get(eid, {}).get(hist_key, 0))
        t = md.NewIntVar(0, 999, f"{tag}_tot_{eid}_{key}")
        md.Add(t == hist + sum(terms_map.get(eid, [])))
        totals.append(t)
    mx = md.NewIntVar(0, 999, f"{tag}_max_{key}")
    mn = md.NewIntVar(0, 999, f"{tag}_min_{key}")
    md.AddMaxEquality(mx, totals)
    md.AddMinEquality(mn, totals)
    spread = md.NewIntVar(0, 999, f"{tag}_spread_{key}")
    md.Add(spread == mx - mn)
    buckets[key].append((spread, float(weight)))


# --------------------------------------------------------------------------- #
# 诊断
# --------------------------------------------------------------------------- #
def _diagnose(problem: Problem, assignments: list[dict[str, Any]], solver_status: str) -> dict[str, Any]:
    filled: dict[tuple[dt.date, str], list[int]] = defaultdict(list)
    per_emp_day: dict[tuple[dt.date, int], int] = defaultdict(int)
    for a in assignments:
        filled[(a["work_date"], a["position_code"])].append(a["employee_id"])
        per_emp_day[(a["work_date"], a["employee_id"])] += 1

    gaps: list[dict[str, Any]] = []
    total_gap = 0
    for d in problem.dates:
        for code, pos in problem.positions.items():
            target, minimum = problem.demands.get((d, code), (0, 0))
            if target <= 0:
                continue
            got = len(filled.get((d, code), []))
            if got >= target:
                continue
            eligible = [eid for eid, e in problem.employees.items() if is_eligible(e, pos, d)]
            idle = [eid for eid in eligible if per_emp_day.get((d, eid), 0) == 0]
            if not eligible:
                reason = "没有具备该岗位资格的在职员工"
            elif not idle:
                reason = "合格人员当日已被安排到其他岗位或已请假"
            else:
                reason = "同时段多个岗位竞争同一批合格人员，可增加兼职/实习或调整人数"
            total_gap += max(0, minimum - got)
            gaps.append(
                {
                    "work_date": d.isoformat(),
                    "position_code": code,
                    "position_name": pos.name,
                    "shift": pos.shift,
                    "target": target,
                    "min": minimum,
                    "assigned": got,
                    "shortage": target - got,
                    "below_min": got < minimum,
                    "eligible_total": len(eligible),
                    "eligible_idle": len(idle),
                    "reason": reason,
                }
            )

    missing_roles = [
        {"work_date": d.isoformat(), "position_code": code, "reason": "当日没有可用主管承担该运营角色"}
        for d in problem.dates
        for code, pos in problem.positions.items()
        if pos.supervisor_role_code
        and problem.demands.get((d, code), (0, 0))[0] > 0
        and not any(
            a["work_date"] == d and a["position_code"] == code and a["role_code"] == pos.supervisor_role_code
            for a in assignments
        )
    ]

    duplicates = [
        {"work_date": d.isoformat(), "employee_id": eid, "count": n}
        for (d, eid), n in per_emp_day.items()
        if n > 1
    ]

    return {
        "solver_status": solver_status,
        "total_gap": total_gap,
        "gaps": gaps,
        "missing_roles": missing_roles,
        "duplicate_assignments": duplicates,
        "assigned_count": len(assignments),
    }


# --------------------------------------------------------------------------- #
# 启发式回退
# --------------------------------------------------------------------------- #
def _solve_heuristic(problem: Problem, mode: str, t0: float) -> SolveResult:
    assignments: list[dict[str, Any]] = []
    used: set[tuple[dt.date, int]] = set()
    for lock in problem.locked:
        assignments.append({**lock, "is_locked": True, "source": "manual"})
        used.add((lock["work_date"], lock["employee_id"]))

    slots = []
    for d in problem.dates:
        for code, pos in problem.positions.items():
            target, _minimum = problem.demands.get((d, code), (0, 0))
            if target > 0:
                eligible = [eid for eid, e in problem.employees.items() if is_eligible(e, pos, d)]
                slots.append((len(eligible), d, code, pos, target))
    slots.sort(key=lambda s: s[0])

    load: dict[int, int] = defaultdict(int)
    for _n, d, code, pos, target in slots:
        current = [a for a in assignments if a["work_date"] == d and a["position_code"] == code]
        need = target - len(current)
        if need <= 0:
            continue
        has_role = any(a["role_code"] == pos.supervisor_role_code for a in current)
        candidates = [eid for eid, e in problem.employees.items() if is_eligible(e, pos, d) and (d, eid) not in used]
        candidates.sort(
            key=lambda eid: (
                1 if (problem.employees[eid].shift_preference in ("early", "late") and problem.employees[eid].shift_preference != pos.shift) else 0,
                0 if (pos.supervisor_role_code and problem.employees[eid].is_supervisor and not has_role) else 1,
                skill_score(problem.employees[eid], code)[0],
                load[eid],
                -skill_score(problem.employees[eid], code)[1],
            )
        )
        for eid in candidates:
            if need <= 0:
                break
            role = None
            if pos.supervisor_role_code and not has_role and problem.employees[eid].is_supervisor:
                role = pos.supervisor_role_code
                has_role = True
            assignments.append(
                {
                    "work_date": d,
                    "employee_id": eid,
                    "position_code": code,
                    "shift": pos.shift,
                    "role_code": role,
                    "is_locked": False,
                    "source": "auto",
                }
            )
            used.add((d, eid))
            load[eid] += 1
            need -= 1

    diagnostics = _diagnose(problem, assignments, "HEURISTIC")
    status = "infeasible" if diagnostics["total_gap"] > 0 and mode == "strict" else "done"
    return SolveResult(
        status=status,
        solver="heuristic",
        assignments=assignments,
        diagnostics=diagnostics,
        objective_value=0.0,
        objective_breakdown={},
        duration_ms=int((time.time() - t0) * 1000),
        message="使用启发式回退求解（OR-Tools 不可用）",
    )
