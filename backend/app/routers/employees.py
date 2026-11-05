from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_current_user, write_audit
from ..models import (
    AvailabilityRequest,
    Employee,
    EmployeePositionSkill,
    EmployeeTypeOption,
    PairingPreference,
    Position,
    ScheduleAssignment,
    ScheduleVersion,
    User,
)
from ..schemas import (
    EmployeeIn,
    EmployeeOut,
    PairingIn,
    PairingOut,
    SkillIn,
)

router = APIRouter(prefix="/api", tags=["employees"], dependencies=[Depends(get_current_user)])


def _payload_dict(emp: Employee) -> dict:
    return {
        "name": emp.name,
        "employment_type": emp.employment_type,
        "job_title": emp.job_title,
        "is_supervisor": emp.is_supervisor,
        "is_active": emp.is_active,
        "shift_preference": emp.shift_preference,
        "skills": [s.position_code for s in emp.skills],
    }


@router.get("/employees", response_model=list[EmployeeOut])
def list_employees(
    include_inactive: bool = Query(True),
    department: str | None = None,
    db: Session = Depends(get_db),
):
    q = db.query(Employee)
    if not include_inactive:
        q = q.filter(Employee.is_active.is_(True))
    rows = q.order_by(Employee.is_supervisor.desc(), Employee.employment_type, Employee.name).all()
    if department:
        allowed = {p.code for p in db.query(Position).filter(Position.department == department).all()}
        rows = [r for r in rows if any(s.position_code in allowed for s in r.skills)]
    return rows


@router.get("/employees/{employee_id}", response_model=EmployeeOut)
def get_employee(employee_id: int, db: Session = Depends(get_db)):
    emp = db.get(Employee, employee_id)
    if not emp:
        raise HTTPException(404, "员工不存在")
    return emp


@router.post("/employees", response_model=EmployeeOut)
def create_employee(payload: EmployeeIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if payload.employee_no and db.query(Employee).filter(Employee.employee_no == payload.employee_no).first():
        raise HTTPException(400, f"工号 {payload.employee_no} 已存在")
    emp = Employee(**payload.model_dump(exclude={"skills"}))
    if not emp.employee_no:
        emp.employee_no = None
    db.add(emp)
    db.flush()
    for s in payload.skills:
        db.add(EmployeePositionSkill(employee_id=emp.id, **s.model_dump()))
    write_audit(db, user.username, "create", "employee", str(emp.id), f"新增员工 {emp.name}", after=_payload_dict(emp))
    db.commit()
    db.refresh(emp)
    return emp


@router.put("/employees/{employee_id}", response_model=EmployeeOut)
def update_employee(employee_id: int, payload: EmployeeIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    emp = db.get(Employee, employee_id)
    if not emp:
        raise HTTPException(404, "员工不存在")
    before = _payload_dict(emp)
    for k, v in payload.model_dump(exclude={"skills"}).items():
        setattr(emp, k, v)
    db.query(EmployeePositionSkill).filter(EmployeePositionSkill.employee_id == emp.id).delete()
    for s in payload.skills:
        db.add(EmployeePositionSkill(employee_id=emp.id, **s.model_dump()))
    db.flush()
    db.refresh(emp)
    after = _payload_dict(emp)
    write_audit(db, user.username, "update", "employee", str(emp.id), f"修改员工 {emp.name}", before=before, after=after)
    db.commit()
    return emp


@router.patch("/employees/{employee_id}/skills", response_model=EmployeeOut)
def set_skills(employee_id: int, skills: list[SkillIn], db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    emp = db.get(Employee, employee_id)
    if not emp:
        raise HTTPException(404, "员工不存在")
    before = [s.position_code for s in emp.skills]
    db.query(EmployeePositionSkill).filter(EmployeePositionSkill.employee_id == emp.id).delete()
    for s in skills:
        db.add(EmployeePositionSkill(employee_id=emp.id, **s.model_dump()))
    db.flush()
    db.refresh(emp)
    write_audit(
        db, user.username, "update_skills", "employee", str(emp.id),
        f"调整 {emp.name} 的岗位资格", before={"skills": before}, after={"skills": [s.position_code for s in skills]},
    )
    db.commit()
    return emp


@router.delete("/employees/{employee_id}")
def deactivate_employee(employee_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    emp = db.get(Employee, employee_id)
    if not emp:
        raise HTTPException(404, "员工不存在")
    emp.is_active = False
    write_audit(db, user.username, "deactivate", "employee", str(emp.id), f"停用员工 {emp.name}")
    db.commit()
    return {"ok": True}


# --------------------------- 批量停用 / 批量删除 ----------------------------- #
class BatchDeleteIn(BaseModel):
    employee_ids: list[int] = Field(default_factory=list)
    # deactivate: 仅停用（保留全部历史，可恢复）
    # purge: 彻底删除（物理删除，仅限从未进入过任何排班版本的员工）
    mode: str = "deactivate"


MAX_BATCH = 500


@router.post("/employees/batch-delete")
def batch_delete_employees(
    payload: BatchDeleteIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """批量停用或删除员工。

    - `deactivate`：置为停用，历史排班/请假/偏好全部保留，可随时恢复。
    - `purge`：物理删除。**存在历史排班记录的员工一律拒绝**，否则已发布的班表
      会指向不存在的员工；这类员工自动降级为「停用」并在结果里说明原因。
    """
    ids = sorted({int(i) for i in payload.employee_ids})
    if not ids:
        raise HTTPException(400, "请先选择员工")
    if len(ids) > MAX_BATCH:
        raise HTTPException(400, f"单次最多处理 {MAX_BATCH} 人，请分批操作")
    mode = (payload.mode or "deactivate").strip().lower()
    if mode not in {"deactivate", "purge"}:
        raise HTTPException(400, "mode 只能是 deactivate 或 purge")

    emps = db.query(Employee).filter(Employee.id.in_(ids)).all()
    found = {e.id: e for e in emps}
    skipped: list[dict] = [
        {"employee_id": i, "name": "", "reason": "员工不存在或已被删除"} for i in ids if i not in found
    ]

    # 只有真正要物理删除时才查排班引用，避免多做无用的聚合。
    # 口径：只统计**已发布**版本 —— 草稿是临时产物，随删除一起清理即可；
    # 已发布版本才是对外生效、需要保留可追溯性的班表。
    assigned: dict[int, int] = {}
    if mode == "purge":
        rows = (
            db.query(ScheduleAssignment.employee_id, func.count(ScheduleAssignment.id))
            .join(ScheduleVersion, ScheduleVersion.id == ScheduleAssignment.version_id)
            .filter(ScheduleAssignment.employee_id.in_([e.id for e in emps]))
            .filter(ScheduleVersion.status == "published")
            .group_by(ScheduleAssignment.employee_id)
            .all()
        )
        assigned = {eid: cnt for eid, cnt in rows}

    deactivated = 0
    purged = 0
    for i in ids:
        emp = found.get(i)
        if emp is None:
            continue
        if mode == "purge":
            used = assigned.get(emp.id, 0)
            if used:
                # 有排班历史：不物理删除，避免破坏已发布版本的可追溯性
                emp.is_active = False
                deactivated += 1
                skipped.append(
                    {
                        "employee_id": emp.id,
                        "name": emp.name,
                        "reason": f"已有 {used} 条排班记录，为保留班表可追溯性改为停用",
                    }
                )
                continue
            # 手动清理关联，SQLite 默认不启用外键级联，不能只依赖 ondelete=CASCADE
            db.query(ScheduleAssignment).filter(ScheduleAssignment.employee_id == emp.id).delete(synchronize_session=False)
            db.query(EmployeePositionSkill).filter(EmployeePositionSkill.employee_id == emp.id).delete(synchronize_session=False)
            db.query(AvailabilityRequest).filter(AvailabilityRequest.employee_id == emp.id).delete(synchronize_session=False)
            db.query(PairingPreference).filter(
                (PairingPreference.employee_a_id == emp.id) | (PairingPreference.employee_b_id == emp.id)
            ).delete(synchronize_session=False)
            db.delete(emp)
            purged += 1
        else:
            if not emp.is_active:
                skipped.append({"employee_id": emp.id, "name": emp.name, "reason": "已是停用状态"})
                continue
            emp.is_active = False
            deactivated += 1

    if deactivated or purged:
        names = "、".join(found[i].name for i in ids if i in found)[:180]
        action = "purge" if purged else "deactivate"
        write_audit(
            db,
            user.username,
            f"batch_{action}",
            "employee",
            ",".join(str(i) for i in ids[:50]),
            f"批量{'删除' if purged else '停用'} {deactivated + purged} 人：{names}",
        )
    db.commit()
    return {
        "ok": True,
        "mode": mode,
        "deactivated": deactivated,
        "purged": purged,
        "skipped": skipped,
    }



# ------------------------------- 员工类型 ---------------------------------- #
@router.get("/employee-types")
def list_types(db: Session = Depends(get_db)):
    return [
        {"code": t.code, "name": t.name, "counts_as_formal": t.counts_as_formal}
        for t in db.query(EmployeeTypeOption).order_by(EmployeeTypeOption.sort_order).all()
    ]


@router.post("/employee-types")
def upsert_type(code: str, name: str, counts_as_formal: bool = False, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = db.query(EmployeeTypeOption).filter(EmployeeTypeOption.code == code).first()
    if row:
        row.name = name
        row.counts_as_formal = counts_as_formal
    else:
        row = EmployeeTypeOption(code=code, name=name, counts_as_formal=counts_as_formal)
        db.add(row)
    write_audit(db, user.username, "upsert", "employee_type", code, f"维护员工类型 {name}")
    db.commit()
    return {"ok": True}


# -------------------------------- 搭班偏好 --------------------------------- #
@router.get("/pairing-preferences", response_model=list[PairingOut])
def list_pairing(db: Session = Depends(get_db)):
    return db.query(PairingPreference).order_by(PairingPreference.id.desc()).all()


@router.post("/pairing-preferences", response_model=PairingOut)
def create_pairing(payload: PairingIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if payload.employee_a_id == payload.employee_b_id:
        raise HTTPException(400, "不能与自己建立搭班偏好")
    row = PairingPreference(**payload.model_dump())
    db.add(row)
    write_audit(db, user.username, "create", "pairing", "0", f"新增搭班偏好 {payload.preference_type}")
    db.commit()
    db.refresh(row)
    return row


@router.delete("/pairing-preferences/{pairing_id}")
def delete_pairing(pairing_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = db.get(PairingPreference, pairing_id)
    if not row:
        raise HTTPException(404, "记录不存在")
    db.delete(row)
    write_audit(db, user.username, "delete", "pairing", str(pairing_id), "删除搭班偏好")
    db.commit()
    return {"ok": True}
