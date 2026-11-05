"""测试公用工具：构造确定性场景，避免依赖真实日期/周末/节假日带来的波动。"""
from __future__ import annotations

import datetime as dt
from typing import Iterable

from sqlalchemy.orm import Session

from app.models import (
    CalendarDay,
    DailyDemand,
    Employee,
    Position,
    ScheduleAssignment,
    ScheduleVersion,
)


def set_day(
    db: Session,
    day: dt.date,
    day_type: str = "weekday",
    *,
    is_holiday: bool = False,
    is_peak: bool = False,
    label: str = "",
) -> CalendarDay:
    row = db.query(CalendarDay).filter(CalendarDay.work_date == day).first()
    if row is None:
        row = CalendarDay(work_date=day)
        db.add(row)
    row.day_type = day_type
    row.is_holiday = is_holiday
    row.is_peak = is_peak
    row.label = label
    db.flush()
    return row


def force_demand(
    db: Session,
    dates: Iterable[dt.date],
    mapping: dict[str, int] | None = None,
    *,
    zero_others: bool = True,
) -> None:
    """把窗口内的需求写成固定值，消除模板/周末差异。

    mapping: {岗位代码: 目标人数}；target == min，保持"最低=目标"的默认口径。
    """
    dates = list(dates)
    mapping = mapping or {}
    db.query(DailyDemand).filter(DailyDemand.work_date.in_(dates)).delete(synchronize_session=False)
    db.flush()

    codes = [p.code for p in db.query(Position).filter(Position.is_active.is_(True)).all()]
    want: dict[tuple[dt.date, str], int] = {}
    if zero_others:
        for d in dates:
            for code in codes:
                want[(d, code)] = 0
    for d in dates:
        for code, count in mapping.items():
            want[(d, code)] = count

    for (d, code), count in want.items():
        db.add(DailyDemand(work_date=d, position_code=code, target_count=count, min_count=count, note="测试固定需求"))
    db.commit()


def supervisors(db: Session) -> list[Employee]:
    return db.query(Employee).filter(Employee.is_supervisor.is_(True), Employee.is_active.is_(True)).order_by(Employee.id).all()


def employee_by(db: Session, **kwargs) -> Employee:
    return db.query(Employee).filter_by(**kwargs).first()


def plain_long_term(db: Session) -> Employee:
    """普通长期员工（非主管、非导师）。"""
    return (
        db.query(Employee)
        .filter(
            Employee.employment_type == "long_term",
            Employee.is_supervisor.is_(False),
            Employee.is_mentor.is_(False),
            Employee.is_active.is_(True),
        )
        .order_by(Employee.id)
        .first()
    )


def role_counts(assignments: Iterable[dict], role: str) -> dict[int, int]:
    out: dict[int, int] = {}
    for a in assignments:
        if a.get("role_code") == role:
            out[a["employee_id"]] = out.get(a["employee_id"], 0) + 1
    return out


def spread(counts: dict[int, int], universe: Iterable[int]) -> int:
    values = [counts.get(eid, 0) for eid in universe]
    return (max(values) - min(values)) if values else 0


def co_occurrences(assignments: Iterable[dict], a_id: int, b_id: int, *, same_shift: bool = True) -> int:
    """统计两名员工同班次（same_shift=True）或同日（False）搭班次数。"""
    days: dict[dt.date, set[int]] = {}
    shifts: dict[dt.date, dict[int, str]] = {}
    for a in assignments:
        if a["employee_id"] in (a_id, b_id):
            days.setdefault(a["work_date"], set()).add(a["employee_id"])
            shifts.setdefault(a["work_date"], {})[a["employee_id"]] = a["shift"]
    n = 0
    for d, ids in days.items():
        if len(ids) < 2:
            continue
        if same_shift and shifts[d].get(a_id) != shifts[d].get(b_id):
            continue
        n += 1
    return n


def make_published_version(
    db: Session,
    *,
    start: dt.date,
    days: int,
    assignments: list[dict],
    name: str = "测试历史版本",
    created_by: str = "测试",
) -> ScheduleVersion:
    version = ScheduleVersion(
        name=name,
        start_date=start,
        end_date=start + dt.timedelta(days=days - 1),
        status="published",
        version_no=1,
        published_at=dt.datetime.now(),
        published_by=created_by,
        created_by=created_by,
        change_note="测试构造",
    )
    db.add(version)
    db.flush()
    for a in assignments:
        db.add(
            ScheduleAssignment(
                version_id=version.id,
                work_date=a["work_date"],
                employee_id=a["employee_id"],
                position_code=a["position_code"],
                shift=a.get("shift") or "early",
                role_code=a.get("role_code"),
                is_locked=a.get("is_locked", False),
                source="manual",
            )
        )
    db.commit()
    db.refresh(version)
    return version
