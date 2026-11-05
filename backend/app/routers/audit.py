from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_current_user
from ..models import AuditLog

router = APIRouter(prefix="/api/audit", tags=["audit"], dependencies=[Depends(get_current_user)])


@router.get("")
def list_audit(
    limit: int = Query(100, le=500),
    action: str | None = None,
    entity: str | None = None,
    db: Session = Depends(get_db),
):
    q = db.query(AuditLog)
    if action:
        q = q.filter(AuditLog.action == action)
    if entity:
        q = q.filter(AuditLog.entity == entity)
    rows = q.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(limit).all()
    return [
        {
            "id": r.id,
            "actor": r.actor,
            "action": r.action,
            "entity": r.entity,
            "entity_id": r.entity_id,
            "summary": r.summary,
            "before": r.before,
            "after": r.after,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]
