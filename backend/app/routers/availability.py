from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_current_user, write_audit
from ..models import AvailabilityRequest, Employee, User
from ..schemas import AvailabilityIn, AvailabilityOut, AvailabilityReview

router = APIRouter(prefix="/api/availability-requests", tags=["availability"], dependencies=[Depends(get_current_user)])


@router.get("", response_model=list[AvailabilityOut])
def list_requests(
    status: str | None = None,
    start: dt.date | None = None,
    end: dt.date | None = None,
    db: Session = Depends(get_db),
):
    q = db.query(AvailabilityRequest)
    if status:
        q = q.filter(AvailabilityRequest.status == status)
    if start:
        q = q.filter(AvailabilityRequest.end_date >= start)
    if end:
        q = q.filter(AvailabilityRequest.start_date <= end)
    return q.order_by(AvailabilityRequest.start_date.desc(), AvailabilityRequest.id.desc()).all()


@router.post("", response_model=AvailabilityOut)
def create_request(payload: AvailabilityIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if payload.end_date < payload.start_date:
        raise HTTPException(400, "结束日期不能早于开始日期")
    if not db.get(Employee, payload.employee_id):
        raise HTTPException(404, "员工不存在")
    row = AvailabilityRequest(**payload.model_dump())
    db.add(row)
    db.flush()
    emp = db.get(Employee, payload.employee_id)
    write_audit(
        db, user.username, "create", "availability_request", str(row.id),
        f"为 {emp.name} 提交{payload.request_type}申请 {payload.start_date}~{payload.end_date}",
        after=payload.model_dump(mode="json"),
    )
    db.commit()
    db.refresh(row)
    return row


@router.patch("/{request_id}/review", response_model=AvailabilityOut)
def review_request(
    request_id: int,
    payload: AvailabilityReview,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    row = db.get(AvailabilityRequest, request_id)
    if not row:
        raise HTTPException(404, "申请不存在")
    before = {"status": row.status}
    row.status = payload.status
    row.reviewer = payload.reviewer or user.display_name or user.username
    row.reviewed_at = dt.datetime.now()
    write_audit(
        db, user.username, "review", "availability_request", str(request_id),
        f"审批申请 #{request_id}：{payload.status}",
        before=before, after={"status": row.status, "reviewer": row.reviewer},
    )
    db.commit()
    db.refresh(row)
    return row


@router.delete("/{request_id}")
def delete_request(request_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = db.get(AvailabilityRequest, request_id)
    if not row:
        raise HTTPException(404, "申请不存在")
    db.delete(row)
    write_audit(db, user.username, "delete", "availability_request", str(request_id), f"删除申请 #{request_id}")
    db.commit()
    return {"ok": True}


@router.get("/calendar")
def availability_calendar(
    start: dt.date = Query(...),
    end: dt.date = Query(...),
    db: Session = Depends(get_db),
):
    """返回区间内每位员工不可用日期，供排班界面提示。"""
    rows = (
        db.query(AvailabilityRequest)
        .filter(AvailabilityRequest.status == "approved")
        .filter(AvailabilityRequest.end_date >= start, AvailabilityRequest.start_date <= end)
        .all()
    )
    agg: dict[int, list[dict]] = {}
    for r in rows:
        agg.setdefault(r.employee_id, []).append(
            {
                "start": r.start_date.isoformat(),
                "end": r.end_date.isoformat(),
                "shift": r.shift,
                "type": r.request_type,
                "reason": r.reason,
            }
        )
    return agg
