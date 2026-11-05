from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..deps import get_current_user, write_audit
from ..models import (
    CalendarDay,
    Employee,
    OperationalRole,
    Position,
    ScheduleAssignment,
    ScheduleRun,
    ScheduleVersion,
    User,
)
from ..schemas import AssignmentIn, AssignmentOut, AssignmentPatch, GenerateRequest, PublishRequest, RunOut, VersionDetailOut, VersionOut
from ..services.solver import build_problem, solve
from ..services.validation import validate_assignments

router = APIRouter(prefix="/api/schedule", tags=["schedule"])


# --------------------------------------------------------------------------- #
# 序列化辅助
# --------------------------------------------------------------------------- #
def _assignment_out(a: ScheduleAssignment, emps: dict[int, Employee]) -> dict:
    emp = emps.get(a.employee_id)
    return {
        "id": a.id,
        "work_date": a.work_date,
        "employee_id": a.employee_id,
        "position_code": a.position_code,
        "shift": a.shift,
        "role_code": a.role_code,
        "is_locked": a.is_locked,
        "source": a.source,
        "note": a.note,
        "employee_name": emp.name if emp else "",
        "employee_type": emp.employment_type if emp else "",
        "is_supervisor": bool(emp.is_supervisor) if emp else False,
    }


def _coverage(db: Session, version: ScheduleVersion) -> list[dict]:
    from ..services.solver import resolve_demand

    dates = sorted({a.work_date for a in version.assignments})
    if not dates:
        dates = [version.start_date + dt.timedelta(days=i) for i in range((version.end_date - version.start_date).days + 1)]
    positions, demands, cal = resolve_demand(db, dates)
    counts: dict[tuple[dt.date, str], int] = {}
    sup_counts: dict[tuple[dt.date, str], int] = {}
    emps = {e.id: e for e in db.query(Employee).all()}
    for a in version.assignments:
        counts[(a.work_date, a.position_code)] = counts.get((a.work_date, a.position_code), 0) + 1
        if a.employee_id in emps and emps[a.employee_id].is_supervisor:
            sup_counts[(a.work_date, a.position_code)] = sup_counts.get((a.work_date, a.position_code), 0) + 1
    rows = []
    for d in dates:
        c = cal.get(d)
        for p in positions:
            target, minimum = demands.get((d, p.code), (0, 0))
            got = counts.get((d, p.code), 0)
            if target == 0 and got == 0:
                continue
            rows.append(
                {
                    "work_date": d.isoformat(),
                    "day_type": c.day_type if c else ("weekend" if d.weekday() >= 5 else "weekday"),
                    "position_code": p.code,
                    "position_name": p.name,
                    "department": p.department,
                    "area": p.area,
                    "shift": p.shift,
                    "target": target,
                    "min": minimum,
                    "assigned": got,
                    "shortage": max(0, target - got),
                    "below_min": got < minimum,
                    "supervisors": sup_counts.get((d, p.code), 0),
                    "required_supervisors": p.min_supervisors,
                    "role_code": p.supervisor_role_code,
                }
            )
    return rows


def _version_detail(db: Session, version: ScheduleVersion) -> dict:
    emps = {e.id: e for e in db.query(Employee).all()}
    data = {
        "id": version.id,
        "name": version.name,
        "start_date": version.start_date,
        "end_date": version.end_date,
        "status": version.status,
        "version_no": version.version_no,
        "parent_version_id": version.parent_version_id,
        "published_at": version.published_at,
        "published_by": version.published_by,
        "change_note": version.change_note,
        "created_by": version.created_by,
        "created_at": version.created_at,
        "assignments": [_assignment_out(a, emps) for a in sorted(version.assignments, key=lambda x: (x.work_date, x.position_code))],
        "coverage": _coverage(db, version),
        "violations": validate_assignments(
            db,
            sorted({a.work_date for a in version.assignments}) or [version.start_date],
            [
                {
                    "work_date": a.work_date,
                    "employee_id": a.employee_id,
                    "position_code": a.position_code,
                    "shift": a.shift,
                    "role_code": a.role_code,
                }
                for a in version.assignments
            ],
            capacity_as_error=True,
        ),
    }
    return data


def _next_version_no(db: Session, start: dt.date) -> int:
    rows = db.query(ScheduleVersion).filter(ScheduleVersion.start_date == start).all()
    return max([r.version_no for r in rows], default=0) + 1


def _latest_draft(db: Session, start: dt.date) -> ScheduleVersion | None:
    return (
        db.query(ScheduleVersion)
        .filter(ScheduleVersion.status == "draft", ScheduleVersion.start_date <= start, ScheduleVersion.end_date >= start)
        .order_by(ScheduleVersion.id.desc())
        .first()
    )


def _ensure_editable(db: Session, version: ScheduleVersion, user: User) -> tuple[ScheduleVersion, bool]:
    """已发布版本不可直接修改：自动派生新草稿版本（需求文档 9.7）。"""
    if version.status != "published":
        return version, False
    clone = ScheduleVersion(
        name=f"{version.name}（修订）",
        start_date=version.start_date,
        end_date=version.end_date,
        status="draft",
        version_no=_next_version_no(db, version.start_date),
        parent_version_id=version.id,
        created_by=user.display_name or user.username,
        change_note=f"基于已发布版本 v{version.version_no} 的修改",
    )
    db.add(clone)
    db.flush()
    for a in version.assignments:
        db.add(
            ScheduleAssignment(
                version_id=clone.id,
                work_date=a.work_date,
                employee_id=a.employee_id,
                position_code=a.position_code,
                shift=a.shift,
                role_code=a.role_code,
                is_locked=a.is_locked,
                source=a.source,
                note=a.note,
            )
        )
    write_audit(
        db, user.username, "fork", "schedule_version", str(clone.id),
        f"从已发布版本 v{version.version_no} 派生新草稿 v{clone.version_no}",
        before={"version_id": version.id}, after={"version_id": clone.id},
    )
    db.flush()
    db.refresh(clone)
    return clone, True


# --------------------------------------------------------------------------- #
# 生成
# --------------------------------------------------------------------------- #
@router.post("/generate")
def generate(payload: GenerateRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if payload.days < 1 or payload.days > 31:
        raise HTTPException(400, "排班天数需在 1~31 天之间（一个自然月内）")
    problem = build_problem(db, payload.start_date, payload.days)
    # 求解时间随窗口线性放宽：30 天规模实测约 6s，留足余量给人数更多的真实名单，
    # 上限 90s 避免长时间占用请求；超时也会返回当前最优解而不是失败。
    budget = min(90.0, max(settings.solver_max_time_seconds, payload.days * 2.5))
    result = solve(
        problem,
        mode=payload.mode,
        timeout=budget,
        seed=settings.solver_random_seed,
    )

    run = ScheduleRun(
        start_date=payload.start_date,
        end_date=payload.start_date + dt.timedelta(days=payload.days - 1),
        status=result.status,
        solver=result.solver,
        params={"mode": payload.mode, "days": payload.days, "keep_locks": payload.keep_locks, "weights": problem.weights},
        objective_value=result.objective_value,
        objective_breakdown=result.objective_breakdown,
        diagnostics=result.diagnostics,
        message=result.message,
        duration_ms=result.duration_ms,
        created_by=user.display_name or user.username,
    )
    db.add(run)
    db.flush()

    # 归档同日期的旧草稿，保证"当前草稿"唯一
    old_drafts = (
        db.query(ScheduleVersion)
        .filter(ScheduleVersion.status == "draft", ScheduleVersion.start_date == payload.start_date)
        .all()
    )
    for old in old_drafts:
        old.status = "archived"
        old.change_note = (old.change_note or "") + "；被新草稿替换"

    version = ScheduleVersion(
        name=payload.version_name or f"预排班 {payload.start_date:%Y-%m-%d} ~ {(payload.start_date + dt.timedelta(days=payload.days - 1)):%Y-%m-%d}",
        start_date=payload.start_date,
        end_date=payload.start_date + dt.timedelta(days=payload.days - 1),
        status="draft",
        version_no=_next_version_no(db, payload.start_date),
        run_id=run.id,
        created_by=user.display_name or user.username,
        change_note=result.message,
    )
    db.add(version)
    db.flush()
    for a in result.assignments:
        db.add(ScheduleAssignment(version_id=version.id, **a))

    write_audit(
        db, user.username, "generate", "schedule_version", str(version.id),
        f"自动排班 {version.start_date}~{version.end_date}：{len(result.assignments)} 条安排，缺口 {result.diagnostics.get('total_gap', 0)}",
        after={"status": result.status, "solver": result.solver, "objective": result.objective_value},
    )
    db.commit()
    db.refresh(version)
    return {
        "run": RunOut.model_validate(run).model_dump(mode="json"),
        "version": _version_detail(db, version),
    }


@router.post("/versions/{version_id}/reoptimize")
def reoptimize(version_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """锁定已有安排后重新优化其余人员（需求文档 9 手动调整后选项）。"""
    version = db.get(ScheduleVersion, version_id)
    if not version:
        raise HTTPException(404, "版本不存在")
    days = (version.end_date - version.start_date).days + 1
    problem = build_problem(db, version.start_date, days)
    budget = min(90.0, max(settings.solver_max_time_seconds, days * 2.5))
    result = solve(problem, mode="draft", timeout=budget, seed=settings.solver_random_seed)
    target, forked = _ensure_editable(db, version, user)
    db.query(ScheduleAssignment).filter(ScheduleAssignment.version_id == target.id).delete()
    db.flush()
    for a in result.assignments:
        db.add(ScheduleAssignment(version_id=target.id, **a))
    run = ScheduleRun(
        start_date=target.start_date,
        end_date=target.end_date,
        status=result.status,
        solver=result.solver,
        params={"mode": "reoptimize"},
        objective_value=result.objective_value,
        objective_breakdown=result.objective_breakdown,
        diagnostics=result.diagnostics,
        message=result.message,
        duration_ms=result.duration_ms,
        created_by=user.display_name or user.username,
    )
    db.add(run)
    db.flush()
    target.run_id = run.id
    write_audit(
        db, user.username, "reoptimize", "schedule_version", str(target.id),
        f"锁定排班后重新优化 v{target.version_no}，缺口 {result.diagnostics.get('total_gap', 0)}",
    )
    db.commit()
    db.refresh(target)
    return {"run": RunOut.model_validate(run).model_dump(mode="json"), "version": _version_detail(db, target), "forked": forked}


# --------------------------------------------------------------------------- #
# 版本
# --------------------------------------------------------------------------- #
@router.get("/versions", response_model=list[VersionOut])
def list_versions(status: str | None = None, limit: int = 50, db: Session = Depends(get_db)):
    q = db.query(ScheduleVersion)
    if status:
        q = q.filter(ScheduleVersion.status == status)
    return q.order_by(ScheduleVersion.created_at.desc()).limit(limit).all()


@router.get("/versions/{version_id}")
def get_version(version_id: int, db: Session = Depends(get_db)):
    version = db.get(ScheduleVersion, version_id)
    if not version:
        raise HTTPException(404, "版本不存在")
    return _version_detail(db, version)


@router.get("/current")
def get_current(start: dt.date | None = None, db: Session = Depends(get_db)):
    anchor = start or dt.date.today()
    version = _latest_draft(db, anchor)
    if not version:
        version = (
            db.query(ScheduleVersion)
            .filter(ScheduleVersion.start_date <= anchor, ScheduleVersion.end_date >= anchor)
            .order_by(ScheduleVersion.id.desc())
            .first()
        )
    if not version and start is None:
        # 当天没有班表时回退到最近的未来班表：排班通常提前一天生成并发布
        version = (
            db.query(ScheduleVersion)
            .filter(ScheduleVersion.start_date > anchor)
            .order_by(ScheduleVersion.start_date.asc(), ScheduleVersion.id.desc())
            .first()
        )
    if not version and start is None:
        # 仍没有则展示最近一期历史班表，避免首页空白
        version = db.query(ScheduleVersion).order_by(ScheduleVersion.end_date.desc(), ScheduleVersion.id.desc()).first()
    if not version:
        raise HTTPException(404, "暂无排班版本，请先生成预排班")
    return _version_detail(db, version)


@router.post("/versions/{version_id}/publish")
def publish_version(
    version_id: int,
    payload: PublishRequest,
    allow_gap: bool = Query(False, description="确认已知悉岗位缺口后仍发布"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    version = db.get(ScheduleVersion, version_id)
    if not version:
        raise HTTPException(404, "版本不存在")
    if version.status == "published":
        raise HTTPException(400, "该版本已发布")
    dates = sorted({a.work_date for a in version.assignments}) or [version.start_date]
    issues = validate_assignments(
        db,
        dates,
        [
            {
                "work_date": a.work_date,
                "employee_id": a.employee_id,
                "position_code": a.position_code,
                "shift": a.shift,
                "role_code": a.role_code,
            }
            for a in version.assignments
        ],
        gap_as_error=not allow_gap,
    )
    errors = [i for i in issues if i["level"] == "error"]
    if errors:
        raise HTTPException(
            status_code=409,
            detail={"message": "存在违规排班，无法发布", "violations": errors[:50]},
        )
    # 归档同日期区间旧的已发布版本
    prev = (
        db.query(ScheduleVersion)
        .filter(
            ScheduleVersion.status == "published",
            ScheduleVersion.start_date == version.start_date,
            ScheduleVersion.id != version.id,
        )
        .all()
    )
    for p in prev:
        p.status = "archived"
    version.status = "published"
    version.published_at = dt.datetime.now()
    version.published_by = user.display_name or user.username
    version.change_note = payload.change_note or version.change_note
    write_audit(
        db, user.username, "publish", "schedule_version", str(version.id),
        f"发布排班 v{version.version_no}（{version.start_date}~{version.end_date}）",
        after={"published_at": version.published_at.isoformat(), "note": version.change_note},
    )
    db.commit()
    db.refresh(version)
    return _version_detail(db, version)


@router.delete("/versions/{version_id}")
def delete_version(version_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    version = db.get(ScheduleVersion, version_id)
    if not version:
        raise HTTPException(404, "版本不存在")
    if version.status == "published":
        raise HTTPException(400, "已发布版本不可删除（如需废止请新建修订版本）")
    db.delete(version)
    write_audit(db, user.username, "delete", "schedule_version", str(version_id), f"删除草稿版本 #{version_id}")
    db.commit()
    return {"ok": True}


# --------------------------------------------------------------------------- #
# 人工调整
# --------------------------------------------------------------------------- #
@router.post("/versions/{version_id}/assignments")
def add_assignment(
    version_id: int,
    payload: AssignmentIn,
    force: bool = Query(False),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    version = db.get(ScheduleVersion, version_id)
    if not version:
        raise HTTPException(404, "版本不存在")
    target, forked = _ensure_editable(db, version, user)
    exists = (
        db.query(ScheduleAssignment)
        .filter(
            ScheduleAssignment.version_id == target.id,
            ScheduleAssignment.work_date == payload.work_date,
            ScheduleAssignment.employee_id == payload.employee_id,
        )
        .first()
    )
    if exists:
        raise HTTPException(400, "该员工当日已有排班，请直接修改原安排")
    row = ScheduleAssignment(version_id=target.id, **payload.model_dump(), source="manual")
    db.add(row)
    db.flush()
    issues = _validate_version(db, target)
    errors = [i for i in issues if i["level"] == "error"]
    if errors and not force:
        db.rollback()
        raise HTTPException(409, detail={"message": "该调整会产生违规排班", "violations": errors[:50]})
    write_audit(
        db, user.username, "manual_add", "schedule_assignment", str(row.id),
        f"{payload.work_date} 为员工#{payload.employee_id} 手工新增 {payload.position_code}",
        after=payload.model_dump(mode="json"),
    )
    db.commit()
    return {"version": _version_detail(db, target), "violations": issues, "forked": forked}


@router.patch("/versions/{version_id}/assignments/{assignment_id}")
def patch_assignment(
    version_id: int,
    assignment_id: int,
    payload: AssignmentPatch,
    force: bool = Query(False),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    version = db.get(ScheduleVersion, version_id)
    if not version:
        raise HTTPException(404, "版本不存在")
    target, forked = _ensure_editable(db, version, user)
    row = db.get(ScheduleAssignment, assignment_id)
    if not row or row.version_id != target.id:
        row = db.get(ScheduleAssignment, assignment_id)
        if not row:
            raise HTTPException(404, "排班记录不存在")
        # 若由已发布版本派生，重新定位到新版本中的对应记录
        row = (
            db.query(ScheduleAssignment)
            .filter(
                ScheduleAssignment.version_id == target.id,
                ScheduleAssignment.work_date == row.work_date,
                ScheduleAssignment.employee_id == row.employee_id,
            )
            .first()
        )
        if not row:
            raise HTTPException(404, "排班记录不存在于当前版本")
    before = {"position_code": row.position_code, "shift": row.shift, "role_code": row.role_code, "is_locked": row.is_locked}
    data = payload.model_dump(exclude_none=True)
    if "position_code" in data:
        pos = db.query(Position).filter(Position.code == data["position_code"]).first()
        if not pos:
            raise HTTPException(400, "岗位不存在")
        data["shift"] = pos.shift
        if not data.get("role_code"):
            data["role_code"] = pos.supervisor_role_code if pos.supervisor_role_code else None
    for k, v in data.items():
        setattr(row, k, v)
    if "role_code" not in data and "position_code" in data and data.get("role_code") is None:
        row.role_code = None
    row.source = "manual"
    db.flush()
    issues = _validate_version(db, target)
    errors = [i for i in issues if i["level"] == "error"]
    if errors and not force:
        db.rollback()
        raise HTTPException(409, detail={"message": "该调整会产生违规排班", "violations": errors[:50]})
    write_audit(
        db, user.username, "manual_update", "schedule_assignment", str(row.id),
        f"{row.work_date} 员工#{row.employee_id} 调整：{before['position_code']} → {row.position_code}",
        before=before,
        after={"position_code": row.position_code, "shift": row.shift, "role_code": row.role_code, "is_locked": row.is_locked},
    )
    db.commit()
    return {"version": _version_detail(db, target), "violations": issues, "forked": forked}


@router.delete("/versions/{version_id}/assignments/{assignment_id}")
def delete_assignment(version_id: int, assignment_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    version = db.get(ScheduleVersion, version_id)
    if not version:
        raise HTTPException(404, "版本不存在")
    target, forked = _ensure_editable(db, version, user)
    row = db.get(ScheduleAssignment, assignment_id)
    if not row:
        raise HTTPException(404, "排班记录不存在")
    if row.version_id != target.id:
        row = (
            db.query(ScheduleAssignment)
            .filter(
                ScheduleAssignment.version_id == target.id,
                ScheduleAssignment.work_date == row.work_date,
                ScheduleAssignment.employee_id == row.employee_id,
            )
            .first()
        )
        if not row:
            raise HTTPException(404, "排班记录不存在于当前版本")
    before = {"work_date": row.work_date.isoformat(), "employee_id": row.employee_id, "position_code": row.position_code}
    db.delete(row)
    write_audit(db, user.username, "manual_delete", "schedule_assignment", str(assignment_id), f"删除 {before['work_date']} 员工#{before['employee_id']} 的安排", before=before)
    db.commit()
    return {"version": _version_detail(db, target), "forked": forked}


def _validate_version(db: Session, version: ScheduleVersion, capacity_as_error: bool = False) -> list[dict]:
    return validate_assignments(
        db,
        sorted({a.work_date for a in version.assignments}) or [version.start_date],
        [
            {
                "work_date": a.work_date,
                "employee_id": a.employee_id,
                "position_code": a.position_code,
                "shift": a.shift,
                "role_code": a.role_code,
            }
            for a in version.assignments
        ],
        capacity_as_error=capacity_as_error,
    )


@router.get("/versions/{version_id}/validate")
def validate_version(version_id: int, db: Session = Depends(get_db)):
    version = db.get(ScheduleVersion, version_id)
    if not version:
        raise HTTPException(404, "版本不存在")
    return {"violations": _validate_version(db, version, capacity_as_error=True)}


# --------------------------------------------------------------------------- #
# 版本对比
# --------------------------------------------------------------------------- #
@router.get("/versions/{version_id}/compare/{other_id}")
def compare_versions(version_id: int, other_id: int, db: Session = Depends(get_db)):
    a = db.get(ScheduleVersion, version_id)
    b = db.get(ScheduleVersion, other_id)
    if not a or not b:
        raise HTTPException(404, "版本不存在")
    emps = {e.id: e for e in db.query(Employee).all()}
    def index(v: ScheduleVersion):
        return {(x.work_date, x.employee_id): x for x in v.assignments}
    ia, ib = index(a), index(b)
    changes = []
    for key in sorted(set(ia) | set(ib)):
        ra, rb = ia.get(key), ib.get(key)
        if ra and rb and ra.position_code == rb.position_code and ra.role_code == rb.role_code:
            continue
        changes.append(
            {
                "work_date": key[0].isoformat(),
                "employee_id": key[1],
                "employee_name": emps[key[1]].name if key[1] in emps else "",
                "before_position": ra.position_code if ra else None,
                "after_position": rb.position_code if rb else None,
                "before_role": ra.role_code if ra else None,
                "after_role": rb.role_code if rb else None,
                "type": "added" if not ra else ("removed" if not rb else "changed"),
            }
        )
    return {
        "left": {"id": a.id, "name": a.name, "version_no": a.version_no, "status": a.status},
        "right": {"id": b.id, "name": b.name, "version_no": b.version_no, "status": b.status},
        "changes": changes,
        "summary": {
            "added": sum(1 for c in changes if c["type"] == "added"),
            "removed": sum(1 for c in changes if c["type"] == "removed"),
            "changed": sum(1 for c in changes if c["type"] == "changed"),
        },
    }
