from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_current_user, write_audit
from ..models import CalendarDay, DailyDemand, DemandTemplate, Position, User
from ..schemas import CalendarDayIn, CalendarDayOut, DailyDemandIn, DailyDemandOut
from ..services.solver import resolve_demand

router = APIRouter(prefix="/api", tags=["demands"], dependencies=[Depends(get_current_user)])


@router.get("/calendar-days", response_model=list[CalendarDayOut])
def list_calendar(
    start: dt.date = Query(...),
    end: dt.date = Query(...),
    db: Session = Depends(get_db),
):
    rows = db.query(CalendarDay).filter(CalendarDay.work_date >= start, CalendarDay.work_date <= end).all()
    existing = {r.work_date: r for r in rows}
    out = []
    cur = start
    while cur <= end:
        if cur in existing:
            out.append(existing[cur])
        else:
            out.append(
                CalendarDay(
                    work_date=cur,
                    day_type="weekend" if cur.weekday() >= 5 else "weekday",
                    is_holiday=False,
                    is_peak=cur.weekday() >= 5,
                    label="",
                    expected_traffic="normal",
                )
            )
        cur += dt.timedelta(days=1)
    return out


@router.put("/calendar-days/{work_date}", response_model=CalendarDayOut)
def upsert_calendar(
    work_date: dt.date,
    payload: CalendarDayIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    row = db.query(CalendarDay).filter(CalendarDay.work_date == work_date).first()
    data = payload.model_dump()
    data.pop("work_date", None)
    if row:
        before = {"day_type": row.day_type, "is_holiday": row.is_holiday, "is_peak": row.is_peak, "label": row.label}
        for k, v in data.items():
            setattr(row, k, v)
        write_audit(db, user.username, "update", "calendar_day", work_date.isoformat(), f"更新日期标记 {work_date}", before=before, after=data)
    else:
        row = CalendarDay(work_date=work_date, **data)
        db.add(row)
        write_audit(db, user.username, "create", "calendar_day", work_date.isoformat(), f"新增日期标记 {work_date}", after=data)
    db.commit()
    db.refresh(row)
    return row


@router.get("/daily-demands")
def list_daily_demands(
    start: dt.date = Query(...),
    end: dt.date = Query(...),
    db: Session = Depends(get_db),
):
    """返回有效需求（模板 + 覆盖），并标记来源，便于前端直接编辑。"""
    dates = []
    cur = start
    while cur <= end:
        dates.append(cur)
        cur += dt.timedelta(days=1)
    positions, demands, cal = resolve_demand(db, dates)
    overrides = {
        (o.work_date, o.position_code): o
        for o in db.query(DailyDemand).filter(DailyDemand.work_date >= start, DailyDemand.work_date <= end).all()
    }
    result = []
    for d in dates:
        day = cal.get(d)
        row = {
            "work_date": d.isoformat(),
            "day_type": day.day_type if day else ("weekend" if d.weekday() >= 5 else "weekday"),
            "is_holiday": bool(day.is_holiday) if day else False,
            "is_peak": bool(day.is_peak) if day else False,
            "label": day.label if day else "",
            "positions": [],
        }
        for p in positions:
            target, minimum = demands.get((d, p.code), (0, 0))
            ov = overrides.get((d, p.code))
            row["positions"].append(
                {
                    "position_code": p.code,
                    "position_name": p.name,
                    "department": p.department,
                    "area": p.area,
                    "shift": p.shift,
                    "target_count": target,
                    "min_count": minimum,
                    "is_override": ov is not None,
                    "note": ov.note if ov else "",
                }
            )
        result.append(row)
    return result


@router.put("/daily-demands")
def upsert_daily_demands(items: list[DailyDemandIn], db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    for item in items:
        row = (
            db.query(DailyDemand)
            .filter(DailyDemand.work_date == item.work_date, DailyDemand.position_code == item.position_code)
            .first()
        )
        if row:
            row.target_count = item.target_count
            row.min_count = item.min_count or item.target_count
            row.note = item.note
        else:
            db.add(
                DailyDemand(
                    work_date=item.work_date,
                    position_code=item.position_code,
                    target_count=item.target_count,
                    min_count=item.min_count or item.target_count,
                    note=item.note,
                )
            )
    write_audit(
        db, user.username, "update", "daily_demand", "bulk",
        f"调整每日需求（{len(items)} 条）",
        after={"items": [i.model_dump(mode="json") for i in items]},
    )
    db.commit()
    return {"ok": True, "count": len(items)}


@router.delete("/daily-demands")
def reset_daily_demand(work_date: dt.date, position_code: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = db.query(DailyDemand).filter(DailyDemand.work_date == work_date, DailyDemand.position_code == position_code).first()
    if row:
        db.delete(row)
        write_audit(db, user.username, "delete", "daily_demand", f"{work_date}:{position_code}", "恢复为模板需求")
        db.commit()
    return {"ok": True}
