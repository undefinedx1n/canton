"""统计报表：人数口径、岗位覆盖、员工班次/岗位分布、周期休息、主管公平。"""
from __future__ import annotations

import datetime as dt
from collections import Counter, defaultdict

from sqlalchemy.orm import Session

from ..models import CalendarDay, Employee, EmployeeTypeOption, OperationalRole, Position, ScheduleAssignment, ScheduleVersion
from .config_service import get_setting
from .solver import period_bounds, resolve_demand


def _published_in_period(db: Session, start: dt.date, end: dt.date, exclude_version: int | None) -> list[ScheduleAssignment]:
    q = (
        db.query(ScheduleAssignment)
        .join(ScheduleVersion, ScheduleVersion.id == ScheduleAssignment.version_id)
        .filter(ScheduleVersion.status == "published")
        .filter(ScheduleAssignment.work_date >= start, ScheduleAssignment.work_date <= end)
    )
    if exclude_version:
        q = q.filter(ScheduleVersion.id != exclude_version)
    return q.all()


def version_stats(db: Session, version: ScheduleVersion) -> dict:
    dates = sorted({a.work_date for a in version.assignments}) or [version.start_date + dt.timedelta(days=i) for i in range((version.end_date - version.start_date).days + 1)]
    assignments = version.assignments
    positions = {p.code: p for p in db.query(Position).order_by(Position.sort_order, Position.code).all()}
    emps = {e.id: e for e in db.query(Employee).all()}
    types = {t.code: t.name for t in db.query(EmployeeTypeOption).all()}
    roles = {r.code: r for r in db.query(OperationalRole).all()}
    cal = {c.work_date: c for c in db.query(CalendarDay).filter(CalendarDay.work_date.in_(dates)).all()}
    _, demands, _ = resolve_demand(db, dates)

    by_day_emp: dict[dt.date, set[int]] = defaultdict(set)
    by_day_slot: dict[tuple[dt.date, str], list[ScheduleAssignment]] = defaultdict(list)
    emp_pos_count: dict[int, Counter] = defaultdict(Counter)
    emp_shift = {eid: {"early": 0, "late": 0} for eid in emps}
    for a in assignments:
        by_day_emp[a.work_date].add(a.employee_id)
        by_day_slot[(a.work_date, a.position_code)].append(a)
        emp_pos_count[a.employee_id][a.position_code] += 1
        emp_shift.setdefault(a.employee_id, {"early": 0, "late": 0})[a.shift] = emp_shift.get(a.employee_id, {"early": 0, "late": 0}).get(a.shift, 0) + 1

    days = []
    for d in dates:
        day_assignments = [a for a in assignments if a.work_date == d]
        people = by_day_emp.get(d, set())
        early = len({a.employee_id for a in day_assignments if a.shift == "early"})
        late = len({a.employee_id for a in day_assignments if a.shift == "late"})
        sales = len({a.employee_id for a in day_assignments if positions.get(a.position_code) and positions[a.position_code].department == "sales"})
        photo = len({a.employee_id for a in day_assignments if positions.get(a.position_code) and positions[a.position_code].department == "photography"})
        type_counts = Counter(emps[a.employee_id].employment_type for a in day_assignments if a.employee_id in emps)
        slot_rows = []
        for code, pos in positions.items():
            target, minimum = demands.get((d, code), (0, 0))
            got = len(by_day_slot.get((d, code), []))
            if target == 0 and got == 0:
                continue
            slot_rows.append(
                {
                    "position_code": code,
                    "position_name": pos.name,
                    "department": pos.department,
                    "area": pos.area,
                    "shift": pos.shift,
                    "target": target,
                    "min": minimum,
                    "assigned": got,
                    "shortage": max(0, target - got),
                    "supervisors": sum(1 for a in by_day_slot.get((d, code), []) if a.employee_id in emps and emps[a.employee_id].is_supervisor),
                }
            )
        c = cal.get(d)
        days.append(
            {
                "work_date": d.isoformat(),
                "weekday": "一二三四五六日"[d.weekday()],
                "day_type": c.day_type if c else ("weekend" if d.weekday() >= 5 else "weekday"),
                "is_holiday": bool(c.is_holiday) if c else False,
                "is_peak": bool(c.is_peak) if c else False,
                "label": c.label if c else "",
                "total_people": len(people),
                "early_count": early,
                "late_count": late,
                "sales_count": sales,
                "photography_count": photo,
                "by_type": {types.get(k, k): v for k, v in type_counts.items()},
                "positions": slot_rows,
                "supervisors_on_duty": [
                    {
                        "employee_id": a.employee_id,
                        "name": emps[a.employee_id].name if a.employee_id in emps else "",
                        "role_code": a.role_code,
                        "role_name": roles[a.role_code].name if a.role_code in roles else (a.role_code or ""),
                    }
                    for a in day_assignments
                    if a.role_code
                ],
            }
        )

    # 周期休息口径
    rest_rule = get_setting(db, "rest_rule")
    p_start, p_end = period_bounds(dates[0], rest_rule.get("period_type", "calendar_month"), int(rest_rule.get("cycle_start_day", 1)))
    hist = _published_in_period(db, p_start, p_end, version.id)
    hist_days: dict[int, set[dt.date]] = defaultdict(set)
    for a in hist:
        hist_days[a.employee_id].add(a.work_date)
    period_days = (p_end - p_start).days + 1
    target_rest = int(rest_rule.get("rest_days", 8) or 8)

    employee_rows = []
    for eid, emp in sorted(emps.items(), key=lambda kv: (not kv[1].is_supervisor, kv[1].employment_type, kv[1].name)):
        work_dates = {a.work_date for a in assignments if a.employee_id == eid}
        if not work_dates and not emp.is_active:
            continue
        period_work = len(work_dates | hist_days.get(eid, set()))
        employee_rows.append(
            {
                "employee_id": eid,
                "name": emp.name,
                "employment_type": emp.employment_type,
                "employment_type_name": types.get(emp.employment_type, emp.employment_type),
                "is_supervisor": emp.is_supervisor,
                "is_mentor": emp.is_mentor,
                "job_title": emp.job_title,
                "work_days": len(work_dates),
                "early_count": emp_shift.get(eid, {}).get("early", 0),
                "late_count": emp_shift.get(eid, {}).get("late", 0),
                "rest_days_in_window": len(dates) - len(work_dates),
                "period_work_days": period_work,
                "period_rest_days": max(0, period_days - period_work) if period_days else 0,
                "period_target_rest": target_rest if emp.employment_type in {t.code for t in db.query(EmployeeTypeOption).all() if t.counts_as_formal} else None,
                "rest_gap": (max(0, period_days - period_work) - target_rest) if (target_rest and emp.employment_type in {t.code for t in db.query(EmployeeTypeOption).all() if t.counts_as_formal}) else None,
                "positions": dict(emp_pos_count.get(eid, {})),
                "position_total": sum(emp_pos_count.get(eid, {}).values()),
            }
        )

    # 主管公平
    fair = get_setting(db, "fairness_period")
    f_start, f_end = period_bounds(dates[0], fair.get("period_type", "calendar_month"), int(fair.get("cycle_start_day", 1)))
    fair_hist = _published_in_period(db, f_start, f_end, version.id)
    fair_cal = {c.work_date: c for c in db.query(CalendarDay).all()}
    hist_counts: dict[int, Counter] = defaultdict(Counter)
    for a in fair_hist:
        if f_start <= a.work_date <= f_end and not (version.start_date <= a.work_date <= version.end_date):
            c = fair_cal.get(a.work_date)
            if a.role_code == "duty_supervisor":
                hist_counts[a.employee_id]["duty"] += 1
                if a.work_date.weekday() >= 5:
                    hist_counts[a.employee_id]["weekend"] += 1
                if c and c.is_holiday:
                    hist_counts[a.employee_id]["holiday"] += 1
                if c and c.is_peak:
                    hist_counts[a.employee_id]["peak"] += 1

    cur_counts: dict[int, Counter] = defaultdict(Counter)
    for a in assignments:
        if a.role_code != "duty_supervisor":
            continue
        c = cal.get(a.work_date)
        cur_counts[a.employee_id]["duty"] += 1
        if a.work_date.weekday() >= 5:
            cur_counts[a.employee_id]["weekend"] += 1
        if c and c.is_holiday:
            cur_counts[a.employee_id]["holiday"] += 1
        if c and c.is_peak:
            cur_counts[a.employee_id]["peak"] += 1

    supervisors = []
    sup_emps = [e for e in emps.values() if e.is_supervisor and e.is_active]
    totals = {eid: hist_counts[eid]["duty"] + cur_counts[eid]["duty"] for eid in (e.id for e in sup_emps)}
    avg = round(sum(totals.values()) / len(totals), 2) if totals else 0
    weekend_totals = {eid: hist_counts[eid]["weekend"] + cur_counts[eid]["weekend"] for eid in (e.id for e in sup_emps)}
    weekend_avg = round(sum(weekend_totals.values()) / len(weekend_totals), 2) if weekend_totals else 0
    for e in sup_emps:
        supervisors.append(
            {
                "employee_id": e.id,
                "name": e.name,
                "window_duty": cur_counts[e.id]["duty"],
                "period_duty": totals[e.id],
                "window_weekend": cur_counts[e.id]["weekend"],
                "period_weekend": weekend_totals[e.id],
                "window_holiday": cur_counts[e.id]["holiday"],
                "period_holiday": hist_counts[e.id]["holiday"] + cur_counts[e.id]["holiday"],
                "window_peak": cur_counts[e.id]["peak"],
                "period_peak": hist_counts[e.id]["peak"] + cur_counts[e.id]["peak"],
                "deviation": round(totals[e.id] - avg, 2),
                "weekend_deviation": round(weekend_totals[e.id] - weekend_avg, 2),
            }
        )

    total_slots = sum(len(d["positions"]) for d in days)
    total_assigned = len(assignments)
    total_needed = sum(p["target"] for d in days for p in d["positions"])
    gap_total = sum(d_["shortage"] for d in days for d_ in d["positions"])

    return {
        "version_id": version.id,
        "status": version.status,
        "period": {"start": p_start.isoformat(), "end": p_end.isoformat(), "days": period_days, "target_rest": target_rest},
        "days": days,
        "employees": employee_rows,
        "supervisors": supervisors,
        "summary": {
            "dates": len(dates),
            "total_assignments": total_assigned,
            "total_needed": total_needed,
            "total_gap": gap_total,
            "slot_rows": total_slots,
            "coverage_rate": round(100 * (total_needed - gap_total) / total_needed, 1) if total_needed else 100.0,
            "people": len({a.employee_id for a in assignments}),
            "avg_per_day": round(total_assigned / len(dates), 1) if dates else 0,
        },
    }


def dashboard_summary(db: Session) -> dict:
    """首页概览：员工构成、最近发布版本、待审申请等。"""
    from ..models import AvailabilityRequest

    emps = db.query(Employee).all()
    types = {t.code: t.name for t in db.query(EmployeeTypeOption).all()}
    by_type = Counter(e.employment_type for e in emps if e.is_active)
    pending = db.query(AvailabilityRequest).filter(AvailabilityRequest.status == "pending").count()
    last_pub = (
        db.query(ScheduleVersion)
        .filter(ScheduleVersion.status == "published")
        .order_by(ScheduleVersion.published_at.desc())
        .first()
    )
    return {
        "employees_total": len(emps),
        "employees_active": sum(1 for e in emps if e.is_active),
        "supervisors": sum(1 for e in emps if e.is_supervisor and e.is_active),
        "mentors": sum(1 for e in emps if e.is_mentor and e.is_active),
        "by_type": [{"code": k, "name": types.get(k, k), "count": v} for k, v in by_type.items()],
        "pending_requests": pending,
        "last_published": (
            {
                "id": last_pub.id,
                "name": last_pub.name,
                "start_date": last_pub.start_date.isoformat(),
                "end_date": last_pub.end_date.isoformat(),
                "published_at": last_pub.published_at.isoformat() if last_pub.published_at else None,
            }
            if last_pub
            else None
        ),
    }
