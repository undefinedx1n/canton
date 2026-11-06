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
from dataclasses import dataclass, field, replace
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
    # 分块求解时上一块最后一天的岗位，用于跨块"避免连续同岗"
    prev_positions: dict[int, str] = field(default_factory=dict)


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

    # 锁定安排：所有与本窗口有交集的草稿版本中的锁定/人工行（跨草稿汇总，避免漏读）
    locked: list[dict[str, Any]] = []
    seen_locked: set[tuple[dt.date, int]] = set()
    draft_versions = (
        db.query(ScheduleVersion)
        .filter(
            ScheduleVersion.status == "draft",
            ScheduleVersion.start_date <= dates[-1],
            ScheduleVersion.end_date >= dates[0],
        )
        .order_by(ScheduleVersion.id.desc())
        .all()
    )
    for a in (
        db.query(ScheduleAssignment)
        .filter(
            ScheduleAssignment.version_id.in_([v.id for v in draft_versions]),
            ScheduleAssignment.work_date >= dates[0],
            ScheduleAssignment.work_date <= dates[-1],
        )
        .order_by(ScheduleAssignment.version_id.desc(), ScheduleAssignment.work_date)
        .all()
        if draft_versions
        else []
    ):
        # 人工预排优先级最高：锁定的行、以及人工新增/修改过的行都固定不动，
        # 自动排班只补剩下的空缺。
        if not (a.is_locked or a.source == "manual"):
            continue
        key = (a.work_date, a.employee_id)
        if key in seen_locked:
            continue
        seen_locked.add(key)
        locked.append(
            {
                "work_date": a.work_date,
                "employee_id": a.employee_id,
                "position_code": a.position_code,
                "shift": a.shift,
                "role_code": a.role_code,
                "source": a.source,
                "is_locked": a.is_locked,
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
        # 岗位轮换均衡需要按岗位统计历史承担次数（同一公平周期口径）
        entry[f"pos:{a.position_code}"] += 1
        # 早晚班平衡同样要跨周期累计，否则分块求解时每块都从 0 起算
        entry[a.shift] = entry.get(a.shift, 0) + 1
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

    # 重点岗位的人均承担上限按**整个窗口**预先算好，分块求解时各块共用，
    # 避免"块内名额少 + 跨块历史多"把正常轮换误判成超额。
    rotation_cfg = get_setting(db, "rotation_rule")
    rotation_caps: dict[str, int] = {}
    for code in rotation_cfg.get("focus_positions") or []:
        pos = positions.get(code)
        if pos is None:
            continue
        candidates = [
            e
            for e in employees.values()
            if code in e.skills
            and e.skills[code][2]
            and (not pos.allowed_types or e.employment_type in pos.allowed_types)
        ]
        if len(candidates) < 2:
            continue
        slots = sum(demands.get((d, code), (0, 0))[0] for d in dates)
        rotation_caps[code] = -(-slots // len(candidates)) + 1

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
            "rotation_rule": get_setting(db, "rotation_rule"),
            "rotation_caps": rotation_caps,
            # 长窗口分块滚动求解的块长度（0 = 不分块，一次性求解）
            "solve_chunk_days": int(get_setting(db, "solve_chunk_days").get("days", 7) or 0),
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
        # 30 天一次性求解在真实名单上只能拿到"可行解"（45s 仍非最优），
        # 因此长窗口改为分块滚动：每块 7 天、跨块继承历史与昨日岗位，
        # 既能把单块解到接近最优，也把单次耗时控制在可接受范围。
        chunk_days = int(problem.settings.get("solve_chunk_days", 7) or 0)
        if chunk_days > 0 and len(problem.dates) > chunk_days and mode != "strict":
            result = _solve_chunked(problem, mode, timeout, seed, chunk_days)
        else:
            result = _solve_cpsat(problem, mode, timeout, seed)
        # 人工预排被硬固定时，若与请假/资格/名额冲突会导致整体不可行。
        # 这种情况下降级为"只固定显式锁定的行"，并对人工行给出复核提示，
        # 避免因为一条冲突的人工作业让整张班表排不出来。
        if result.status == "infeasible" and result.diagnostics.get("solver_status") == "INFEASIBLE":
            manual_only = [l for l in problem.locked if l.get("source") == "manual" and not l.get("is_locked")]
            if manual_only:
                relaxed = replace(problem, locked=[l for l in problem.locked if l.get("is_locked")])
                retry = _solve_cpsat(relaxed, mode, timeout, seed)
                if retry.status != "infeasible":
                    retry.message = (
                        (retry.message or "").strip()
                        + f"；人工预排中有 {len(manual_only)} 条与资格/请假/名额冲突，已按软约束重排，请复核"
                    )
                    result = retry
    except Exception as exc:  # pragma: no cover - 兜底
        result = _solve_heuristic(problem, mode, t0)
        result.message = f"CP-SAT 求解异常，已退回启发式：{exc}"
    result.duration_ms = int((time.time() - t0) * 1000)
    return result


def _carry_history(history: dict[int, dict[str, int]], assignments: list[dict[str, Any]], calendar: dict[dt.date, dict]) -> None:
    """把已求解的一段结果累加进历史计数，供下一块继续做公平/轮换。"""
    for a in assignments:
        entry = history.setdefault(a["employee_id"], {})
        entry["work"] = entry.get("work", 0) + 1
        key = f"pos:{a['position_code']}"
        entry[key] = entry.get(key, 0) + 1
        entry[a["shift"]] = entry.get(a["shift"], 0) + 1
        if a.get("role_code") == "duty_supervisor":
            entry["duty"] = entry.get("duty", 0) + 1
        if a.get("role_code") in ("early_support", "late_support"):
            entry["support"] = entry.get("support", 0) + 1
        if a["work_date"].weekday() >= 5:
            entry["weekend_work"] = entry.get("weekend_work", 0) + 1
            if a.get("role_code") == "duty_supervisor":
                entry["weekend_duty"] = entry.get("weekend_duty", 0) + 1
        c = calendar.get(a["work_date"]) or {}
        if c.get("is_holiday") and a.get("role_code") == "duty_supervisor":
            entry["holiday_duty"] = entry.get("holiday_duty", 0) + 1
        if c.get("is_peak") and a.get("role_code") == "duty_supervisor":
            entry["peak_duty"] = entry.get("peak_duty", 0) + 1


def _solve_chunked(problem: Problem, mode: str, timeout: float, seed: int, chunk_days: int) -> SolveResult:
    total = len(problem.dates)
    blocks = [problem.dates[i : i + chunk_days] for i in range(0, total, chunk_days)]
    per_budget = max(4.0, timeout / max(1, len(blocks)))

    history = {k: dict(v) for k, v in problem.history.items()}
    assignments: list[dict[str, Any]] = []
    prev_positions: dict[int, str] = {}
    breakdown: dict[str, float] = defaultdict(float)
    statuses: list[str] = []
    objective = 0.0

    for idx, dates in enumerate(blocks):
        window = set(dates)
        sub = replace(
            problem,
            dates=dates,
            demands={k: v for k, v in problem.demands.items() if k[0] in window},
            calendar={k: v for k, v in problem.calendar.items() if k in window},
            locked=[l for l in problem.locked if l["work_date"] in window],
            history=history,
            prev_positions=prev_positions,
        )
        res = _solve_cpsat(sub, mode, per_budget, seed + idx)
        statuses.append(res.diagnostics.get("solver_status", ""))
        assignments.extend(res.assignments)
        objective += res.objective_value or 0.0
        for k, v in (res.objective_breakdown or {}).items():
            breakdown[k] += float(v or 0)
        _carry_history(history, res.assignments, problem.calendar)

        last_day = dates[-1]
        prev_positions = {
            a["employee_id"]: a["position_code"] for a in res.assignments if a["work_date"] == last_day
        }

    diagnostics = _diagnose(problem, assignments, "+".join(statuses))
    status = "done" if all(s in ("OPTIMAL", "FEASIBLE") for s in statuses) else "infeasible"
    if status == "done" and diagnostics["total_gap"] > 0 and mode == "strict":
        status = "infeasible"
    return SolveResult(
        status=status,
        solver="cp-sat",
        assignments=assignments,
        diagnostics=diagnostics,
        objective_value=objective,
        objective_breakdown={k: float(v) for k, v in breakdown.items()},
        duration_ms=0,
        message=f"分块滚动求解（每块 {chunk_days} 天，共 {len(blocks)} 块），缺口 {diagnostics['total_gap']}",
    )


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
                gap_weight = w.get("role_gap", w["gap_penalty"])
                _pen(md, buckets, "role_gap", 1, gap_weight, f"norole_{d:%m%d}_{code}", 1)
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
                gap_weight = w.get("role_gap", w["gap_penalty"])
                _pen(md, buckets, "role_gap", 1, gap_weight, f"norole_{d:%m%d}_{code}", 1)
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

        # 各公平指标的「超标人次」汇总成一个目标，避免它们在同一权重体系里互相压制
        excess_pool: list[Any] = []
        _add_fairness(
            md,
            buckets,
            "duty_fairness",
            supervisors,
            duty_terms,
            problem.history,
            "duty",
            w["duty_fairness"],
            "duty",
            expected_total=_duty_target_total(dates, positions, problem.demands),
            excess_pool=excess_pool,
        )

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
            _add_fairness(
                md,
                buckets,
                key,
                supervisors,
                subset_terms,
                problem.history,
                hist_key,
                w[key],
                tag,
                expected_total=_duty_target_total(dates, positions, problem.demands, predicate),
                excess_pool=excess_pool,
            )

        if excess_pool:
            # 权重取低于缺口惩罚(400)、高于单个公平项，确保「能均衡时一定均衡」
            _pen(
                md,
                buckets,
                "fairness_excess",
                sum(excess_pool),
                w.get("fairness_excess", 130),
                "fair_excess",
                999,
            )

    # --------------------------- 岗位轮换 ---------------------------- #
    # 需求：A3/B3（销售）、B8（摄影）、A2（早辅）、B1（晚辅）尽量不由同一人连续担任，
    # 并像当值主管 B2 一样按周期平均；其他岗位做弱约束即可。
    rotation_cfg = problem.settings.get("rotation_rule", {}) or {}
    focus_set = {c for c in (rotation_cfg.get("focus_positions") or []) if c in positions}
    w_cons_focus = w.get("rotation_consecutive_focus", 55)
    w_cons_other = w.get("rotation_consecutive_other", 6)

    if rotation_cfg.get("avoid_consecutive", True):
        # 跨分块边界：与上一块最后一天同一岗位也要罚
        for eid, code in problem.prev_positions.items():
            v = x.get((dates[0], eid, code))
            if v is None:
                continue
            weight = w_cons_focus if code in focus_set else w_cons_other
            if weight > 0:
                buckets["rotation_consecutive"].append((v, weight))
        for i in range(len(dates) - 1):
            d1, d2 = dates[i], dates[i + 1]
            for code in positions:
                weight = w_cons_focus if code in focus_set else w_cons_other
                if weight <= 0:
                    continue
                for eid in emps:
                    v1, v2 = x.get((d1, eid, code)), x.get((d2, eid, code))
                    if v1 is None or v2 is None:
                        continue
                    both = md.NewBoolVar(f"cons_{code}_{eid}_{i}")
                    md.Add(both <= v1)
                    md.Add(both <= v2)
                    md.Add(both >= v1 + v2 - 1)
                    buckets["rotation_consecutive"].append((both, weight))

    w_rot = w.get("rotation_balance", 25)
    if rotation_cfg.get("balance_within_period", True) and w_rot > 0:
        caps = problem.settings.get("rotation_caps") or {}
        for code in sorted(focus_set):
            candidates = [eid for eid in emps if any((d, eid, code) in x for d in dates)]
            if len(candidates) < 2:
                continue
            slots = sum(problem.demands.get((d, code), (0, 0))[0] for d in dates)
            # 人均可承担次数 +1 的容差；分块求解时必须用整个窗口算出的上限，
            # 否则块内名额少、跨块历史多，会把历史占用误判成超额。
            cap = int(caps.get(code) or 0) or (-(-slots // len(candidates)) + 1)
            for eid in candidates:
                vars_ = [x[(d, eid, code)] for d in dates if (d, eid, code) in x]
                if not vars_:
                    continue
                hist = int(problem.history.get(eid, {}).get(f"pos:{code}", 0))
                # 历史已经超过上限时不应再为"过去"付罚，只约束本次窗口的增量
                eff_cap = max(cap, hist)
                over = md.NewIntVar(0, len(dates) + hist, f"rotover_{code}_{eid}")
                md.Add(over >= sum(vars_) + hist - eff_cap)
                buckets["rotation_balance"].append((over, w_rot))

    # 辅助主管（A2 早班 / B1 晚班）的承担次数在任何两位主管之间也要均衡
    w_support = w.get("support_fairness", 40)
    if w_support > 0 and supervisors:
        support_role_codes = {"early_support", "late_support"}
        support_terms: dict[int, list[Any]] = {eid: [] for eid in supervisors}
        for (d, eid, code), var in role_vars.items():
            if positions[code].supervisor_role_code in support_role_codes and eid in support_terms:
                support_terms[eid].append(var)
        if any(support_terms.values()):
            counts = []
            for eid, terms in support_terms.items():
                hist = int(problem.history.get(eid, {}).get("support", 0))
                c = md.NewIntVar(0, len(dates) + hist, f"supcnt_{eid}")
                md.Add(c == sum(terms) + hist)
                counts.append(c)
            if len(counts) > 1:
                mx = md.NewIntVar(0, len(dates) + 400, "supmax")
                mn = md.NewIntVar(0, len(dates) + 400, "supmin")
                md.AddMaxEquality(mx, counts)
                md.AddMinEquality(mn, counts)
                spread = md.NewIntVar(0, len(dates) + 400, "supspread")
                md.Add(spread == mx - mn)
                buckets["support_fairness"].append((spread, w_support))

    # 每人早/晚班次数差：避免同一个人长期只站早班或只站晚班
    w_shift = w.get("shift_balance", 12)
    if w_shift > 0:
        for eid, emp in emps.items():
            # 只具备单班次岗位资格的人无法平衡，跳过以免白白拉扯目标函数
            avail_shifts = {
                positions[c].shift
                for c, (_p, _pr, allowed) in emp.skills.items()
                if allowed and c in positions
            }
            if len(avail_shifts) < 2:
                continue
            early_vars = [v for (d, ee, c), v in x.items() if ee == eid and positions[c].shift == "early"]
            late_vars = [v for (d, ee, c), v in x.items() if ee == eid and positions[c].shift == "late"]
            if not early_vars or not late_vars:
                continue
            e_cnt = md.NewIntVar(0, len(dates) + 400, f"ecnt_{eid}")
            l_cnt = md.NewIntVar(0, len(dates) + 400, f"lcnt_{eid}")
            # 带上周期内已有的早/晚班次数，分块求解才能跨块纠偏
            hist_e = int(problem.history.get(eid, {}).get("early", 0))
            hist_l = int(problem.history.get(eid, {}).get("late", 0))
            md.Add(e_cnt == sum(early_vars) + hist_e)
            md.Add(l_cnt == sum(late_vars) + hist_l)
            diff = md.NewIntVar(0, len(dates) + 400, f"sdiff_{eid}")
            md.Add(diff >= e_cnt - l_cnt)
            md.Add(diff >= l_cnt - e_cnt)
            buckets["shift_balance"].append((diff, w_shift))

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


def _add_fairness(
    md,
    buckets,
    key,
    employees,
    terms_map,
    history,
    hist_key,
    weight,
    tag,
    *,
    expected_total: int = 0,
    excess_pool: list | None = None,
):
    """最小化「最大值 − 最小值」的极差。

    单靠加权极差有个固有问题：几个公平目标（总当值 / 周末 / 节假日 / 高峰）会互相
    压制 —— 为了保住权重高的周末均衡，求解器宁可让高峰分布多偏一个人次。
    因此这里做两层处理：

    1. `buckets[key]` 只留 0.5 倍权重的基础压力，用于同等达标时再比精细；
    2. 超出「均摊下界」（0 或 1）的部分单独计入 `excess_pool`，由调用方汇总成一个
       `fairness_excess` 目标。这样求解器优先减少「各项超标的合计人次」，
       而不是在指标之间做权重交易。
    """
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
    buckets[key].append((spread, float(weight) * 0.5))

    n = len(employees)
    # 均摊后必然出现的极差：整除为 0，不整除为 1
    base = 1 if (expected_total > 0 and expected_total % n) else 0
    excess = md.NewIntVar(0, 999, f"{tag}_ex_{key}")
    md.Add(excess >= spread - base)
    if excess_pool is not None:
        excess_pool.append(excess)


def _duty_target_total(dates, positions, demands, predicate=None) -> int:
    """按需求目标估算「当值主管」应产生的人次，用来推算均衡下界。

    实际排班可能因人员不足低于该值；估高只会让下界更宽松，不会误伤。
    """
    total = 0
    for d in dates:
        if predicate is not None and not predicate(d):
            continue
        for code, pos in positions.items():
            if pos.supervisor_role_code != "duty_supervisor":
                continue
            total += demands.get((d, code), (0, 0))[0]
    return total



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
