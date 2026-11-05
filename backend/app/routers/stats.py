from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_current_user
from ..models import ScheduleVersion
from ..services.stats import dashboard_summary, version_stats

router = APIRouter(prefix="/api/stats", tags=["stats"], dependencies=[Depends(get_current_user)])


@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db)):
    return dashboard_summary(db)


@router.get("/versions/{version_id}")
def stats_for_version(version_id: int, db: Session = Depends(get_db)):
    version = db.get(ScheduleVersion, version_id)
    if not version:
        raise HTTPException(404, "版本不存在")
    return version_stats(db, version)


@router.get("/daily")
def daily_totals(start: dt.date = Query(...), end: dt.date = Query(...), db: Session = Depends(get_db)):
    """按日查看已发布排班的人数口径（每人每日只计一次）。"""
    versions = (
        db.query(ScheduleVersion)
        .filter(ScheduleVersion.status.in_(["published", "draft"]))
        .filter(ScheduleVersion.start_date <= end, ScheduleVersion.end_date >= start)
        .order_by(ScheduleVersion.id.desc())
        .all()
    )
    if not versions:
        return {"days": [], "note": "该区间暂无排班版本"}
    latest_by_date: dict[dt.date, int] = {}
    for v in versions:
        for d_off in range((v.end_date - v.start_date).days + 1):
            d = v.start_date + dt.timedelta(days=d_off)
            if start <= d <= end and d not in latest_by_date:
                latest_by_date[d] = v.id
    merged = []
    for d, vid in sorted(latest_by_date.items()):
        v = db.get(ScheduleVersion, vid)
        s = version_stats(db, v)
        day = next((x for x in s["days"] if x["work_date"] == d.isoformat()), None)
        if day:
            merged.append({**day, "version_id": vid, "version_status": v.status})
    return {"days": merged}
