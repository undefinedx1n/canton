"""校验规则测试：人工调整后即时校验的分级与全部错误码（需求 9.6 / 15）。

校验分两类：
- 硬错误（error）：资格、类型、请假、主管角色、重复排班、超编、岗位缺主管 —— 会阻止发布；
- 提醒（warn）：低于最低人数（默认）、运营角色与岗位不匹配（通常仍可接受）。
"""
from __future__ import annotations

import datetime as dt

import pytest

from app.models import AvailabilityRequest, Employee, EmployeePositionSkill
from app.services.validation import validate_assignments

from tests.support import force_demand, plain_long_term, supervisors


def _codes(issues):
    return {i["code"] for i in issues}


def _by_code(issues, code):
    return [i for i in issues if i["code"] == code]


def _assign(day, emp_id, code, shift="early", role=None):
    return {"work_date": day, "employee_id": emp_id, "position_code": code, "shift": shift, "role_code": role}


def test_unknown_entity(db, window):
    issues = validate_assignments(db, [window[0]], [_assign(window[0], 999999, "A1")])
    assert "UNKNOWN_ENTITY" in _codes(issues)


def test_inactive_employee(db, window):
    emp = plain_long_term(db)
    emp.is_active = False
    db.commit()
    force_demand(db, [window[0]], {"A1": 1})
    issues = validate_assignments(db, [window[0]], [_assign(window[0], emp.id, "A1")])
    assert "INACTIVE_EMPLOYEE" in _codes(issues)


def test_no_skill(db, window):
    force_demand(db, [window[0]], {"A8": 1})
    emp = (
        db.query(Employee)
        .filter(~Employee.skills.any(EmployeePositionSkill.position_code == "A8"))
        .filter(Employee.is_active.is_(True))
        .first()
    )
    assert emp is not None
    issues = validate_assignments(db, [window[0]], [_assign(window[0], emp.id, "A8", "early")])
    assert "NO_SKILL" in _codes(issues)
    assert all(i["level"] == "error" for i in _by_code(issues, "NO_SKILL"))


def test_type_restricted(db, window):
    """A3 仅限长期员工：兼职即使有资格记录也必须报错。"""
    part = db.query(Employee).filter(Employee.employment_type == "part_time").first()
    db.add(EmployeePositionSkill(employee_id=part.id, position_code="A3", priority=1, proficiency=4, allowed=True))
    db.commit()
    force_demand(db, [window[0]], {"A3": 1})

    issues = validate_assignments(db, [window[0]], [_assign(window[0], part.id, "A3")])
    assert "TYPE_RESTRICTED" in _codes(issues)


def test_on_leave(db, window):
    emp = plain_long_term(db)
    db.add(
        AvailabilityRequest(
            employee_id=emp.id, request_type="leave", start_date=window[0], end_date=window[0],
            reason="病假", status="approved", reviewer="主管",
        )
    )
    db.commit()
    force_demand(db, [window[0]], {"A7": 1})

    issues = validate_assignments(db, [window[0]], [_assign(window[0], emp.id, "A7")])
    assert "ON_LEAVE" in _codes(issues)


def test_selective_leave_only_blocks_that_shift(db, window):
    """班次级请假：早班请假不应把晚班也判违规。"""
    emp = plain_long_term(db)
    db.add(
        AvailabilityRequest(
            employee_id=emp.id, request_type="leave", start_date=window[0], end_date=window[0], shift="early",
            reason="上午看病", status="approved", reviewer="主管",
        )
    )
    db.commit()
    force_demand(db, [window[0]], {"A7": 1, "B7": 1})

    early = validate_assignments(db, [window[0]], [_assign(window[0], emp.id, "A7", "early")])
    assert "ON_LEAVE" in _codes(early)

    late = validate_assignments(db, [window[0]], [_assign(window[0], emp.id, "B7", "late")])
    assert "ON_LEAVE" not in _codes(late)


def test_duplicate_assignment(db, window):
    emp = plain_long_term(db)
    force_demand(db, [window[0]], {"A7": 1, "B8": 1})
    issues = validate_assignments(
        db,
        [window[0]],
        [_assign(window[0], emp.id, "A7", "early"), _assign(window[0], emp.id, "B8", "late")],
    )
    dup = _by_code(issues, "DUPLICATE_ASSIGNMENT")
    assert dup and dup[0]["level"] == "error"


def test_unknown_role(db, window):
    sup = supervisors(db)[0]
    force_demand(db, [window[0]], {"B2": 1})
    issues = validate_assignments(db, [window[0]], [_assign(window[0], sup.id, "B2", "late", "not_exists")])
    assert "UNKNOWN_ROLE" in _codes(issues)


def test_role_not_supervisor(db, window):
    emp = plain_long_term(db)
    force_demand(db, [window[0]], {"B2": 1})
    issues = validate_assignments(db, [window[0]], [_assign(window[0], emp.id, "B2", "late", "duty_supervisor")])
    assert "ROLE_NOT_SUPERVISOR" in _codes(issues)


def test_role_position_mismatch_is_warning(db, window):
    """当值主管通常绑定 B2，排到其他岗位只提醒不阻断（需求 6 / 17.3 待确认项）。"""
    sup = supervisors(db)[0]
    force_demand(db, [window[0]], {"B1": 2})
    issues = validate_assignments(db, [window[0]], [_assign(window[0], sup.id, "B1", "late", "duty_supervisor")])
    mism = _by_code(issues, "ROLE_POSITION_MISMATCH")
    assert mism and mism[0]["level"] == "warn"
    assert all(i["level"] != "error" for i in mism)


def test_over_capacity(db, window):
    force_demand(db, [window[0]], {"A1": 1})
    emps = db.query(Employee).filter(Employee.skills.any(EmployeePositionSkill.position_code == "A1")).limit(3).all()
    issues = validate_assignments(db, [window[0]], [_assign(window[0], e.id, "A1") for e in emps])
    over = _by_code(issues, "OVER_CAPACITY")
    assert over and over[0]["level"] == "error"


def test_below_min_is_warning_by_default(db, window):
    force_demand(db, [window[0]], {"A2": 3})
    emp = plain_long_term(db)
    issues = validate_assignments(db, [window[0]], [_assign(window[0], emp.id, "A2")])
    below = _by_code(issues, "BELOW_MIN")
    assert below and below[0]["level"] == "warn"


def test_below_min_becomes_error_when_requested(db, window):
    force_demand(db, [window[0]], {"A2": 3})
    emp = plain_long_term(db)
    issues = validate_assignments(db, [window[0]], [_assign(window[0], emp.id, "A2")], gap_as_error=True)
    below = _by_code(issues, "BELOW_MIN")
    assert below and below[0]["level"] == "error"


def test_missing_supervisor_and_role(db, window):
    """B2 要求至少 1 名主管且必须有人承担当值角色。"""
    force_demand(db, [window[0]], {"B2": 4})
    emps = (
        db.query(Employee)
        .filter(Employee.skills.any(EmployeePositionSkill.position_code == "B2"))
        .filter(Employee.is_supervisor.is_(False))
        .limit(4)
        .all()
    )
    assert len(emps) == 4
    issues = validate_assignments(db, [window[0]], [_assign(window[0], e.id, "B2", "late") for e in emps])
    codes = _codes(issues)
    assert "MISSING_SUPERVISOR" in codes
    assert "MISSING_ROLE" in codes
