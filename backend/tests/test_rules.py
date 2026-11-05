"""硬规则测试（需求文档 12「硬约束」、15「MVP 验收标准」）。

覆盖：岗位资格、员工类型限制（A3/B3 长期员工）、主管与导师区别、运营角色绑定、
已批准请假/未批准请假（默认口径与配置切换）、班次级请假、一人一日一岗、
名额上限、锁定不被移动、离职/停用员工不参与排班。
"""
from __future__ import annotations

import datetime as dt

import pytest

from app.models import AvailabilityRequest, Employee, EmployeePositionSkill
from app.services.config_service import set_setting
from app.services.solver import build_problem, is_eligible, solve

from tests.support import employee_by, plain_long_term, supervisors


def _solve(db, window, days: int = 3, timeout: float = 25):
    return solve(build_problem(db, window[0], days), mode="draft", timeout=timeout)


# --------------------------------------------------------------------------- #
# 资格
# --------------------------------------------------------------------------- #
def test_employee_without_skill_is_never_assigned(db, window):
    """没有任何岗位资格的员工不得被安排（需求 5「没有岗位资格是硬性不可排」）。"""
    emp = plain_long_term(db)
    db.query(EmployeePositionSkill).filter(EmployeePositionSkill.employee_id == emp.id).delete()
    db.commit()

    problem = build_problem(db, window[0], 3)
    assert emp.id in problem.employees, "在职员工应在求解池内"
    assert not problem.employees[emp.id].skills

    result = _solve(db, window)
    assert result.status == "done"
    assert all(a["employee_id"] != emp.id for a in result.assignments)


def test_skill_marked_not_allowed_is_ineligible(db, window, positions):
    """资格记录 allowed=False 视为不具资格（需求 5「允许/禁止」）。"""
    emp = plain_long_term(db)
    skill = (
        db.query(EmployeePositionSkill)
        .filter(EmployeePositionSkill.employee_id == emp.id, EmployeePositionSkill.position_code == "A7")
        .first()
    )
    if skill is None:
        skill = EmployeePositionSkill(employee_id=emp.id, position_code="A7")
        db.add(skill)
    skill.allowed = False
    db.commit()

    problem = build_problem(db, window[0], 1)
    assert not is_eligible(problem.employees[emp.id], problem.positions["A7"], window[0])


# --------------------------------------------------------------------------- #
# 员工类型限制：A3 / B3
# --------------------------------------------------------------------------- #
def test_part_time_cannot_take_a3_even_with_skill(db, window):
    """兼职即使被误加 A3 资格也不能排（需求 4.1 / 12）。"""
    part = db.query(Employee).filter(Employee.employment_type == "part_time").first()
    db.add(EmployeePositionSkill(employee_id=part.id, position_code="A3", priority=1, proficiency=5, allowed=True))
    db.add(EmployeePositionSkill(employee_id=part.id, position_code="B3", priority=1, proficiency=5, allowed=True))
    db.commit()

    problem = build_problem(db, window[0], 3)
    assert not is_eligible(problem.employees[part.id], problem.positions["A3"], window[0])
    assert not is_eligible(problem.employees[part.id], problem.positions["B3"], window[0])

    result = _solve(db, window)
    for a in result.assignments:
        if a["position_code"] in ("A3", "B3"):
            assert problem.employees[a["employee_id"]].employment_type == "long_term"


def test_intern_cannot_take_b3(db, window):
    intern = db.query(Employee).filter(Employee.employment_type == "intern").first()
    db.add(EmployeePositionSkill(employee_id=intern.id, position_code="B3", priority=1, proficiency=5, allowed=True))
    db.commit()

    problem = build_problem(db, window[0], 1)
    assert not is_eligible(problem.employees[intern.id], problem.positions["B3"], window[0])


def test_supervisor_is_eligible_for_a3(db, window):
    """主管属长期员工身份，可承担 A3（需求 4.1「主管可排」）。"""
    sup = supervisors(db)[0]
    problem = build_problem(db, window[0], 1)
    info = problem.employees[sup.id]
    assert info.employment_type == "long_term"
    assert is_eligible(info, problem.positions["A3"], window[0])


# --------------------------------------------------------------------------- #
# 主管 / 导师
# --------------------------------------------------------------------------- #
def test_mentor_is_not_supervisor(db, window):
    """陈嘉欣为导师，不得承担运营角色（需求 2 / 12）。"""
    mentor = db.query(Employee).filter(Employee.is_mentor.is_(True)).first()
    assert mentor is not None and mentor.is_supervisor is False

    result = _solve(db, window)
    roles = [a for a in result.assignments if a["role_code"]]
    assert roles, "应产生主管运营角色"
    for a in roles:
        assert db.get(Employee, a["employee_id"]).is_supervisor is True
        assert a["employee_id"] != mentor.id


def test_operational_role_bound_to_position(db, window):
    """运营角色必须落在绑定岗位：B2=当值、A2=早班辅助、B1=晚班辅助（需求 6）。"""
    result = _solve(db, window)
    mapping = {"duty_supervisor": "B2", "early_support": "A2", "late_support": "B1"}
    seen = set()
    for a in result.assignments:
        if a["role_code"]:
            assert a["position_code"] == mapping[a["role_code"]]
            seen.add(a["role_code"])
    assert seen, "至少应产生一个运营角色"


def test_each_day_has_duty_supervisor(db, window):
    """每天必须有当值主管（B2），需求文档 6「晚班当值主管」为必备角色。"""
    result = _solve(db, window)
    duty_days = {a["work_date"] for a in result.assignments if a["role_code"] == "duty_supervisor"}
    assert duty_days == set(window)


# --------------------------------------------------------------------------- #
# 请假
# --------------------------------------------------------------------------- #
def _add_leave(db, emp_id, start, end, status="approved", shift=None):
    db.add(
        AvailabilityRequest(
            employee_id=emp_id,
            request_type="leave",
            start_date=start,
            end_date=end,
            shift=shift,
            reason="测试请假",
            status=status,
            reviewer="测试主管" if status != "pending" else "",
        )
    )
    db.commit()


def test_approved_leave_is_hard_constraint(db, window):
    emp = plain_long_term(db)
    _add_leave(db, emp.id, window[0], window[-1])

    result = _solve(db, window)
    assert all(a["employee_id"] != emp.id for a in result.assignments)


def test_pending_leave_is_soft_by_default(db, window):
    """未批准申请默认仅作偏好，不阻止排班（需求 17.9 默认口径）。"""
    emp = db.query(Employee).filter(Employee.employment_type == "part_time").first()
    _add_leave(db, emp.id, window[0], window[0], status="pending")

    problem = build_problem(db, window[0], 1)
    assert window[0] not in problem.employees[emp.id].leaves


def test_pending_leave_blocks_when_configured(db, window):
    """把 pending_leave_policy 打开后，未批准申请即成为硬约束（配置可切换）。"""
    emp = db.query(Employee).filter(Employee.employment_type == "part_time").first()
    _add_leave(db, emp.id, window[0], window[0], status="pending")
    set_setting(db, "pending_leave_policy", {"treat_pending_as_unavailable": True})
    db.commit()

    problem = build_problem(db, window[0], 1)
    assert window[0] in problem.employees[emp.id].leaves

    result = solve(problem, mode="draft", timeout=20)
    assert all(not (a["employee_id"] == emp.id and a["work_date"] == window[0]) for a in result.assignments)


def test_shift_scoped_leave_blocks_only_that_shift(db, window):
    """班次级请假只影响该班次，另一班次仍可排（需求 5「特殊时间不可用」）。"""
    emp = plain_long_term(db)
    _add_leave(db, emp.id, window[0], window[0], shift="early")

    problem = build_problem(db, window[0], 1)
    assert (window[0], "early") in problem.employees[emp.id].leave_shifts
    assert window[0] not in problem.employees[emp.id].leaves

    result = solve(problem, mode="draft", timeout=20)
    same_day = [a for a in result.assignments if a["employee_id"] == emp.id and a["work_date"] == window[0]]
    assert all(a["shift"] != "early" for a in same_day)


# --------------------------------------------------------------------------- #
# 一人一岗 / 名额 / 锁定 / 停用
# --------------------------------------------------------------------------- #
def test_one_position_per_employee_per_day(db, window):
    result = _solve(db, window)
    seen: dict[tuple[dt.date, int], str] = {}
    for a in result.assignments:
        key = (a["work_date"], a["employee_id"])
        assert key not in seen, f"{key} 被安排了多个岗位（{seen.get(key)} + {a['position_code']}）"
        seen[key] = a["position_code"]


def test_assignment_never_exceeds_target(db, window):
    """每个岗位实际人数不得超过名额（需求 12 硬约束第一条）。"""
    problem = build_problem(db, window[0], 3)
    result = _solve(db, window)
    counts: dict[tuple[dt.date, str], int] = {}
    for a in result.assignments:
        counts[(a["work_date"], a["position_code"])] = counts.get((a["work_date"], a["position_code"]), 0) + 1
    for key, n in counts.items():
        target = problem.demands.get(key, (0, 0))[0]
        assert n <= target, f"{key} 排了 {n} 人，超过名额 {target}"


def test_locked_assignment_is_preserved(db, window):
    """锁定安排重新求解时不得移动（需求 9 / 12）。"""
    first = _solve(db, window)
    picked = next(a for a in first.assignments if a["position_code"] == "A7")

    from tests.support import make_published_version  # 局部导入，避免循环

    version = make_published_version(
        db,
        start=window[0],
        days=3,
        assignments=[{**a, "is_locked": a["work_date"] == picked["work_date"] and a["employee_id"] == picked["employee_id"]} for a in first.assignments],
        name="锁定测试草稿",
    )
    # 该夹具生成的是 published，需要改成 draft 才能被 build_problem 当作锁定来源
    version.status = "draft"
    db.commit()

    second = _solve(db, window)
    kept = [
        a for a in second.assignments
        if a["work_date"] == picked["work_date"] and a["employee_id"] == picked["employee_id"]
    ]
    assert kept, "锁定员工当天应仍有安排"
    assert kept[0]["position_code"] == "A7", "锁定安排被移动到其他岗位"
    assert kept[0]["is_locked"] is True


def test_inactive_employee_is_excluded(db, window):
    emp = plain_long_term(db)
    emp.is_active = False
    db.commit()

    problem = build_problem(db, window[0], 1)
    assert emp.id not in problem.employees
    result = solve(problem, mode="draft", timeout=20)
    assert all(a["employee_id"] != emp.id for a in result.assignments)


@pytest.mark.xfail(
    reason="max_days_per_week 仅存储未进入求解约束（已知缺口，见验收报告 R-03）",
    strict=False,
)
def test_max_days_per_week_is_enforced(db, window):
    """员工每周最多上班天数：字段已建模，但求解器尚未使用。"""
    emp = plain_long_term(db)
    emp.max_days_per_week = 1
    db.commit()

    problem = build_problem(db, window[0], 3)
    assert problem.employees[emp.id].max_days_per_week == 1, "字段应被加载进求解池"

    result = _solve(db, window)
    days_worked = len({a["work_date"] for a in result.assignments if a["employee_id"] == emp.id})
    assert days_worked <= 1
