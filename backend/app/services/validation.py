"""排班校验：人工调整后即时检查，不允许无提示地保存违规排班。"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict

from sqlalchemy.orm import Session

from ..models import AvailabilityRequest, CalendarDay, Employee, EmployeePositionSkill, OperationalRole, Position
from .config_service import get_setting
from .solver import resolve_demand

LEVEL_ERROR = "error"
LEVEL_WARN = "warn"


def validate_assignments(
    db: Session,
    dates: list[dt.date],
    assignments: list[dict],
    gap_as_error: bool = False,
    capacity_as_error: bool = True,
) -> list[dict]:
    """assignments: [{work_date, employee_id, position_code, shift, role_code}]

    gap_as_error=False：低于最低人数记为提醒（允许带缺口草稿，对应 gap_policy 配置）。
    capacity_as_error=False：编辑过程中的暂时超编记为提醒，发布时仍为硬错误。
    资格、类型、请假、主管角色、重复排班始终为硬错误。
    """
    issues: list[dict] = []
    emps = {e.id: e for e in db.query(Employee).all()}
    positions = {p.code: p for p in db.query(Position).all()}
    roles = {r.code: r for r in db.query(OperationalRole).all()}
    skills: dict[tuple[int, str], EmployeePositionSkill] = {
        (s.employee_id, s.position_code): s for s in db.query(EmployeePositionSkill).all()
    }
    pending_cfg = get_setting(db, "pending_leave_policy")
    hard_status = {"approved"} | ({"pending"} if pending_cfg.get("treat_pending_as_unavailable") else set())
    leaves = db.query(AvailabilityRequest).filter(AvailabilityRequest.status.in_(list(hard_status))).all()

    _, demands, _ = resolve_demand(db, dates)

    per_emp_day: dict[tuple[dt.date, int], list[dict]] = defaultdict(list)
    per_slot: dict[tuple[dt.date, str], list[dict]] = defaultdict(list)
    for a in assignments:
        per_emp_day[(a["work_date"], a["employee_id"])].append(a)
        per_slot[(a["work_date"], a["position_code"])].append(a)

    # 1. 一人同日多岗
    for (d, eid), items in per_emp_day.items():
        if len(items) > 1:
            emp = emps.get(eid)
            issues.append(
                {
                    "level": LEVEL_ERROR,
                    "code": "DUPLICATE_ASSIGNMENT",
                    "work_date": d.isoformat(),
                    "employee_id": eid,
                    "employee_name": emp.name if emp else str(eid),
                    "message": f"{emp.name if emp else eid} 在 {d} 被安排了 {len(items)} 个岗位（每人每日最多 1 个）",
                }
            )

    for a in assignments:
        d: dt.date = a["work_date"]
        eid = a["employee_id"]
        code = a["position_code"]
        emp = emps.get(eid)
        pos = positions.get(code)
        if not emp or not pos:
            issues.append(
                {
                    "level": LEVEL_ERROR,
                    "code": "UNKNOWN_ENTITY",
                    "work_date": d.isoformat(),
                    "employee_id": eid,
                    "message": f"员工或岗位不存在：{eid} / {code}",
                }
            )
            continue
        if not emp.is_active:
            issues.append(
                {
                    "level": LEVEL_ERROR,
                    "code": "INACTIVE_EMPLOYEE",
                    "work_date": d.isoformat(),
                    "employee_id": eid,
                    "employee_name": emp.name,
                    "message": f"{emp.name} 已停用，不能排班",
                }
            )
        # 2. 资格
        sk = skills.get((eid, code))
        if sk is None or not sk.allowed:
            issues.append(
                {
                    "level": LEVEL_ERROR,
                    "code": "NO_SKILL",
                    "work_date": d.isoformat(),
                    "employee_id": eid,
                    "employee_name": emp.name,
                    "position_code": code,
                    "message": f"{emp.name} 不具备 {code} 岗位资格",
                }
            )
        # 3. 员工类型限制
        allowed_types = list(pos.allowed_employment_types or [])
        if allowed_types and emp.employment_type not in allowed_types:
            issues.append(
                {
                    "level": LEVEL_ERROR,
                    "code": "TYPE_RESTRICTED",
                    "work_date": d.isoformat(),
                    "employee_id": eid,
                    "employee_name": emp.name,
                    "position_code": code,
                    "message": f"{emp.name}（{emp.employment_type}）不满足 {code} 的类型要求（仅 {'/'.join(allowed_types)}）",
                }
            )
        # 4. 请假 / 不可用
        for lv in leaves:
            if lv.employee_id != eid or not (lv.start_date <= d <= lv.end_date):
                continue
            if lv.request_type not in ("leave", "rest", "unavailable"):
                continue
            if lv.shift and lv.shift != a.get("shift"):
                continue
            issues.append(
                {
                    "level": LEVEL_ERROR,
                    "code": "ON_LEAVE",
                    "work_date": d.isoformat(),
                    "employee_id": eid,
                    "employee_name": emp.name,
                    "message": f"{emp.name} 在 {d} 已批准{('请假' if lv.request_type == 'leave' else '不可用')}，不能排班",
                }
            )
            break
        # 5. 运营角色只能由主管担任
        role_code = a.get("role_code")
        if role_code:
            role = roles.get(role_code)
            if role is None:
                issues.append(
                    {
                        "level": LEVEL_ERROR,
                        "code": "UNKNOWN_ROLE",
                        "work_date": d.isoformat(),
                        "employee_id": eid,
                        "message": f"运营角色 {role_code} 不存在",
                    }
                )
            elif not emp.is_supervisor:
                issues.append(
                    {
                        "level": LEVEL_ERROR,
                        "code": "ROLE_NOT_SUPERVISOR",
                        "work_date": d.isoformat(),
                        "employee_id": eid,
                        "employee_name": emp.name,
                        "message": f"{emp.name} 不是主管（当前职务：{emp.job_title}），不能承担「{role.name}」",
                    }
                )
            elif role.bound_position_code and role.bound_position_code != code:
                issues.append(
                    {
                        "level": LEVEL_WARN,
                        "code": "ROLE_POSITION_MISMATCH",
                        "work_date": d.isoformat(),
                        "employee_id": eid,
                        "employee_name": emp.name,
                        "message": f"「{role.name}」通常绑定 {role.bound_position_code} 岗位，当前为 {code}",
                    }
                )

    # 6. 岗位覆盖
    for d in dates:
        for code, pos in positions.items():
            if not pos.is_active:
                continue
            target, minimum = demands.get((d, code), (0, 0))
            if target <= 0:
                continue
            got = len(per_slot.get((d, code), []))
            if got > target:
                issues.append(
                    {
                        "level": LEVEL_ERROR if capacity_as_error else LEVEL_WARN,
                        "code": "OVER_CAPACITY",
                        "work_date": d.isoformat(),
                        "position_code": code,
                        "message": f"{d} {code}（{pos.name}）已排 {got} 人，超过名额 {target} 人",
                    }
                )
            if got < minimum:
                issues.append(
                    {
                        "level": LEVEL_ERROR if gap_as_error else LEVEL_WARN,
                        "code": "BELOW_MIN",
                        "work_date": d.isoformat(),
                        "position_code": code,
                        "message": f"{d} {code}（{pos.name}）缺 {minimum - got} 人（最低 {minimum} 人，已排 {got} 人）",
                    }
                )
            # 7. 岗位要求主管但无主管
            if (pos.min_supervisors or 0) > 0:
                sup_count = sum(1 for x in per_slot.get((d, code), []) if emps.get(x["employee_id"]) and emps[x["employee_id"]].is_supervisor)
                if got >= minimum and sup_count < pos.min_supervisors:
                    issues.append(
                        {
                            "level": LEVEL_ERROR,
                            "code": "MISSING_SUPERVISOR",
                            "work_date": d.isoformat(),
                            "position_code": code,
                            "message": f"{d} {code}（{pos.name}）至少需要 {pos.min_supervisors} 名主管，当前 {sup_count} 名",
                        }
                    )
            if pos.supervisor_role_code and (pos.min_supervisors or 0) > 0:
                role_ok = any(a.get("role_code") == pos.supervisor_role_code for a in per_slot.get((d, code), []))
                if got >= minimum and not role_ok:
                    issues.append(
                        {
                            "level": LEVEL_ERROR,
                            "code": "MISSING_ROLE",
                            "work_date": d.isoformat(),
                            "position_code": code,
                            "message": f"{d} {code}（{pos.name}）未指定运营角色「{pos.supervisor_role_code}」承担人",
                        }
                    )
    return issues
