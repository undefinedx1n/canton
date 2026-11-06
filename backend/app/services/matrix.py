"""人员 × 日期矩阵：对齐线下 Excel 排班表（姓名 × 日期，V 表示休息）。

单独抽成服务的原因：
- 前端表格视图与 Excel 导出必须使用同一份口径，否则会出现"页面有 V、导出没有"；
- 休息人员不在 schedule_assignments 里，只能由"在职员工 - 当日已排"推导，
  这个推导逻辑只写一次；
- 后续导入历史班表时，同一结构可以直接回填，不需要再改展示层。
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict

from sqlalchemy.orm import Session

from ..models import (
    AvailabilityRequest,
    CalendarDay,
    Employee,
    OperationalRole,
    Position,
    ScheduleAssignment,
    ScheduleVersion,
)
from .config_service import get_setting

# 单元格类型
CELL_WORK = "work"
CELL_REST = "rest"
CELL_LEAVE = "leave"
CELL_INACTIVE = "inactive"

WEEKDAY_CN = "一二三四五六日"


def _rest_cap(db: Session) -> int:
    """连休上限（来自 rest_rule 配置，缺失时按默认 3 天）。"""
    try:
        return int(get_setting(db, "rest_rule").get("max_consecutive_rest_days", 3) or 0)
    except (TypeError, ValueError):
        return 3


def _approved_leaves(
    db: Session, employee_ids: list[int], start: dt.date, end: dt.date
) -> dict[tuple[int, dt.date], dict]:
    """已批准的请假/不可用，按天展开。班次级别的申请仍然落在整天格上，用 shift 标注。"""
    if not employee_ids:
        return {}
    rows = (
        db.query(AvailabilityRequest)
        .filter(
            AvailabilityRequest.employee_id.in_(employee_ids),
            AvailabilityRequest.status == "approved",
            AvailabilityRequest.request_type.in_(("leave", "unavailable")),
            AvailabilityRequest.end_date >= start,
            AvailabilityRequest.start_date <= end,
        )
        .all()
    )
    out: dict[tuple[int, dt.date], dict] = {}
    for r in rows:
        d = max(r.start_date, start)
        last = min(r.end_date, end)
        while d <= last:
            out[(r.employee_id, d)] = {
                "request_type": r.request_type,
                "shift": r.shift,
                "reason": r.reason or "",
                "range": f"{r.start_date}~{r.end_date}",
            }
            d += dt.timedelta(days=1)
    return out


def version_matrix(db: Session, version: ScheduleVersion) -> dict:
    """返回与线下排班表一一对应的矩阵。

    - 行 = 员工（主管在前，其余按导入顺序）
    - 列 = 日期
    - 每格 = 岗位代码 / V(休息) / 假(已批准请假) / 停用
    """
    span = (version.end_date - version.start_date).days + 1
    dates = [version.start_date + dt.timedelta(days=i) for i in range(max(1, span))]

    positions = {p.code: p for p in db.query(Position).all()}
    roles = {r.code: r for r in db.query(OperationalRole).all()}
    cal = {c.work_date: c for c in db.query(CalendarDay).filter(CalendarDay.work_date.in_(dates)).all()}

    assignments = list(version.assignments)
    by_key = {(a.work_date, a.employee_id): a for a in assignments}
    assigned_ids = {a.employee_id for a in assignments}

    employees = db.query(Employee).order_by(Employee.id).all()
    # 停用员工若在本版本里仍有排班，也要出现，否则会"有班无人"
    people = [e for e in employees if e.is_active or e.id in assigned_ids]
    # 主管在前 + 保持导入顺序，和线下表的习惯一致
    people.sort(key=lambda e: (not e.is_supervisor, e.id))

    leaves = _approved_leaves(db, [e.id for e in people], dates[0], dates[-1])

    rows: list[dict] = []
    for e in people:
        cells: dict[str, dict] = {}
        work_days = early = late = duty = rest_days = leave_days = 0
        switches = late_to_early = 0
        max_rest_run = cur_rest_run = 0
        prev_shift: str | None = None
        for d in dates:
            key = d.isoformat()
            a = by_key.get((d, e.id))
            if a:
                pos = positions.get(a.position_code)
                role = roles.get(a.role_code) if a.role_code else None
                cells[key] = {
                    "kind": CELL_WORK,
                    "assignment_id": a.id,
                    "position_code": a.position_code,
                    "position_name": pos.name if pos else a.position_code,
                    "shift": a.shift,
                    "role_code": a.role_code,
                    "role_name": role.name if role else (a.role_code or ""),
                    "role_color": role.color if role else "",
                    "source": a.source,
                    "is_locked": bool(a.is_locked),
                }
                work_days += 1
                if a.shift == "early":
                    early += 1
                elif a.shift == "late":
                    late += 1
                if a.role_code == "duty_supervisor":
                    duty += 1
                # 班次连续性质量指标：相邻两天都上班且班次不同算一次切换
                if a.shift in ("early", "late"):
                    if prev_shift and prev_shift != a.shift:
                        switches += 1
                        if prev_shift == "late" and a.shift == "early":
                            late_to_early += 1
                    prev_shift = a.shift
                cur_rest_run = 0
                continue

            leave = leaves.get((e.id, d))
            if leave:
                cells[key] = {"kind": CELL_LEAVE, **leave}
                leave_days += 1
            elif not e.is_active:
                cells[key] = {"kind": CELL_INACTIVE}
            else:
                cells[key] = {"kind": CELL_REST}
                rest_days += 1
            # 休息/请假都算"不在岗"，会打断班次连续性，同时累计连休天数
            prev_shift = None
            cur_rest_run += 1
            max_rest_run = max(max_rest_run, cur_rest_run)

        rows.append(
            {
                "employee_id": e.id,
                "name": e.name,
                "employee_no": e.employee_no or "",
                "employment_type": e.employment_type,
                "job_title": e.job_title,
                "is_supervisor": bool(e.is_supervisor),
                "is_mentor": bool(e.is_mentor),
                "is_active": bool(e.is_active),
                "work_days": work_days,
                "rest_days": rest_days,
                "leave_days": leave_days,
                "early_count": early,
                "late_count": late,
                "duty_count": duty,
                "shift_switches": switches,
                "late_to_early_switches": late_to_early,
                "max_rest_run": max_rest_run,
                "cells": cells,
            }
        )

    days: list[dict] = []
    for d in dates:
        c = cal.get(d)
        work = sum(1 for r in rows if r["cells"][d.isoformat()]["kind"] == CELL_WORK)
        leave = sum(1 for r in rows if r["cells"][d.isoformat()]["kind"] == CELL_LEAVE)
        days.append(
            {
                "date": d.isoformat(),
                "weekday": WEEKDAY_CN[d.weekday()],
                "day_type": c.day_type if c else ("weekend" if d.weekday() >= 5 else "weekday"),
                "is_holiday": bool(c.is_holiday) if c else False,
                "is_peak": bool(c.is_peak) if c else False,
                "label": c.label if c else "",
                "work_count": work,
                "rest_count": max(0, len(rows) - work - leave),
                "leave_count": leave,
            }
        )

    total_work = sum(r["work_days"] for r in rows)
    total_switches = sum(r["shift_switches"] for r in rows)
    rest_cap = _rest_cap(db)
    summary = {
        "employees": len(rows),
        "supervisors": sum(1 for r in rows if r["is_supervisor"]),
        "dates": len(dates),
        "total_work": total_work,
        "total_rest": sum(r["rest_days"] for r in rows),
        "total_leave": sum(r["leave_days"] for r in rows),
        "avg_work_days": round(total_work / len(rows), 1) if rows else 0,
        "avg_rest_per_day": round(sum(d["rest_count"] for d in days) / len(days), 1) if days else 0,
        # 班次连续性 / 连休质量：用来快速判断"是不是又要手动微调"
        "total_shift_switches": total_switches,
        "avg_shift_switches": round(total_switches / len(rows), 2) if rows else 0,
        "total_late_to_early": sum(r["late_to_early_switches"] for r in rows),
        "max_rest_run": max((r["max_rest_run"] for r in rows), default=0),
        "rest_cap": rest_cap,
        "over_rest_cap_staff": sum(1 for r in rows if rest_cap and r["max_rest_run"] > rest_cap),
    }

    return {
        "version_id": version.id,
        "name": version.name,
        "status": version.status,
        "start_date": version.start_date.isoformat(),
        "end_date": version.end_date.isoformat(),
        "days": days,
        "rows": rows,
        "summary": summary,
    }
