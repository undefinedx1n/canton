from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_current_user, write_audit
from ..models import DemandTemplate, OperationalRole, Position, User
from ..schemas import (
    DemandTemplateIn,
    DemandTemplateOut,
    OperationalRoleIn,
    OperationalRoleOut,
    PositionIn,
    PositionOut,
)

router = APIRouter(prefix="/api", tags=["positions"], dependencies=[Depends(get_current_user)])


@router.get("/positions", response_model=list[PositionOut])
def list_positions(include_inactive: bool = True, db: Session = Depends(get_db)):
    q = db.query(Position)
    if not include_inactive:
        q = q.filter(Position.is_active.is_(True))
    return q.order_by(Position.sort_order, Position.code).all()


@router.post("/positions", response_model=PositionOut)
def create_position(payload: PositionIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if db.query(Position).filter(Position.code == payload.code).first():
        raise HTTPException(400, f"岗位代码 {payload.code} 已存在")
    row = Position(**payload.model_dump())
    db.add(row)
    write_audit(db, user.username, "create", "position", payload.code, f"新增岗位 {payload.code} {payload.name}")
    db.commit()
    db.refresh(row)
    return row


@router.put("/positions/{position_id}", response_model=PositionOut)
def update_position(position_id: int, payload: PositionIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = db.get(Position, position_id)
    if not row:
        raise HTTPException(404, "岗位不存在")
    before = {"code": row.code, "name": row.name, "min_supervisors": row.min_supervisors, "allowed_types": row.allowed_employment_types}
    for k, v in payload.model_dump().items():
        setattr(row, k, v)
    write_audit(
        db, user.username, "update", "position", row.code,
        f"修改岗位 {row.code}",
        before=before,
        after={"code": row.code, "name": row.name, "min_supervisors": row.min_supervisors, "allowed_types": row.allowed_employment_types},
    )
    db.commit()
    return row


@router.delete("/positions/{position_id}")
def deactivate_position(position_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = db.get(Position, position_id)
    if not row:
        raise HTTPException(404, "岗位不存在")
    row.is_active = False
    write_audit(db, user.username, "deactivate", "position", row.code, f"停用岗位 {row.code}")
    db.commit()
    return {"ok": True}


# ------------------------------- 运营角色 ---------------------------------- #
@router.get("/operational-roles", response_model=list[OperationalRoleOut])
def list_roles(db: Session = Depends(get_db)):
    return db.query(OperationalRole).order_by(OperationalRole.id).all()


@router.put("/operational-roles/{role_id}", response_model=OperationalRoleOut)
def update_role(role_id: int, payload: OperationalRoleIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = db.get(OperationalRole, role_id)
    if not row:
        raise HTTPException(404, "运营角色不存在")
    before = {"counts_extra_slot": row.counts_extra_slot, "bound_position_code": row.bound_position_code}
    for k, v in payload.model_dump().items():
        setattr(row, k, v)
    write_audit(
        db, user.username, "update", "operational_role", row.code,
        f"修改运营角色 {row.name}（额外占名额={row.counts_extra_slot}）",
        before=before, after={"counts_extra_slot": row.counts_extra_slot, "bound_position_code": row.bound_position_code},
    )
    db.commit()
    return row


# ------------------------------- 需求模板 ---------------------------------- #
@router.get("/demand-templates", response_model=list[DemandTemplateOut])
def list_templates(db: Session = Depends(get_db)):
    return db.query(DemandTemplate).order_by(DemandTemplate.day_type, DemandTemplate.position_code).all()


@router.put("/demand-templates")
def upsert_templates(items: list[DemandTemplateIn], db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    for item in items:
        row = (
            db.query(DemandTemplate)
            .filter(DemandTemplate.day_type == item.day_type, DemandTemplate.position_code == item.position_code)
            .first()
        )
        if row:
            row.target_count = item.target_count
            row.min_count = item.min_count or item.target_count
        else:
            db.add(
                DemandTemplate(
                    day_type=item.day_type,
                    position_code=item.position_code,
                    target_count=item.target_count,
                    min_count=item.min_count or item.target_count,
                )
            )
    write_audit(db, user.username, "update", "demand_template", "bulk", f"批量更新需求模板（{len(items)} 条）", after={"items": [i.model_dump() for i in items]})
    db.commit()
    return {"ok": True, "count": len(items)}


@router.post("/demand-templates/copy")
def copy_templates(from_day_type: str, to_day_type: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = db.query(DemandTemplate).filter(DemandTemplate.day_type == from_day_type).all()
    for r in rows:
        exists = (
            db.query(DemandTemplate)
            .filter(DemandTemplate.day_type == to_day_type, DemandTemplate.position_code == r.position_code)
            .first()
        )
        if exists:
            exists.target_count = r.target_count
            exists.min_count = r.min_count
        else:
            db.add(DemandTemplate(day_type=to_day_type, position_code=r.position_code, target_count=r.target_count, min_count=r.min_count))
    write_audit(db, user.username, "copy", "demand_template", to_day_type, f"复制需求模板 {from_day_type} → {to_day_type}")
    db.commit()
    return {"ok": True}
