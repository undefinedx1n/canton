"""求解器核心回归测试（需求文档 12 / 15 验收标准）。

时间窗统一由 `window` 夹具提供（工作日模板 + 清空逐日覆盖），
保证任何一天运行结果一致；用例内 `START` 可当作普通变量使用。
"""
from __future__ import annotations

import datetime as dt

import pytest

from app.models import (
    AvailabilityRequest,
    CalendarDay,
    DailyDemand,
    Employee,
    EmployeePositionSkill,
    ScheduleAssignment,
    ScheduleVersion,
)
from app.services.solver import build_problem, is_eligible, solve
from app.services.validation import validate_assignments

from tests.support import force_demand, plain_long_term


@pytest.fixture()
def START(window):
    return window[0]


def test_a3_b3_only_long_term(db, START):
    """非长期员工即使误加 A3 资格也不能排 A3/B3（需求 12 硬约束）。"""
    intern = db.query(Employee).filter(Employee.employment_type == "intern").first()
    db.add(EmployeePositionSkill(employee_id=intern.id, position_code="A3", priority=1, proficiency=5, allowed=True))
    db.add(EmployeePositionSkill(employee_id=intern.id, position_code="B3", priority=1, proficiency=5, allowed=True))
    db.commit()

    problem = build_problem(db, START, 3)
    assert not is_eligible(problem.employees[intern.id], problem.positions["A3"], START)

    result = solve(problem, mode="draft", timeout=20)
    assert result.status == "done"
    for a in result.assignments:
        if a["position_code"] in ("A3", "B3"):
            assert problem.employees[a["employee_id"]].employment_type == "long_term"


def test_mentor_is_not_supervisor(db, START):
    """导师不自动视为主管，不能承担运营角色（需求 2 / 12）。"""
    mentor = db.query(Employee).filter(Employee.is_mentor.is_(True)).first()
    assert mentor.is_supervisor is False

    result = solve(build_problem(db, START, 2), mode="draft", timeout=20)
    roles = [a for a in result.assignments if a["role_code"]]
    assert roles, "应产生主管运营角色"
    for a in roles:
        emp = db.get(Employee, a["employee_id"])
        assert emp.is_supervisor is True
        assert emp.id != mentor.id


def test_supervisor_role_requires_supervisor_position(db, START):
    """运营角色必须落在绑定岗位（B2=当值、A2=早班辅助、B1=晚班辅助）。"""
    result = solve(build_problem(db, START, 2), mode="draft", timeout=20)
    mapping = {"duty_supervisor": "B2", "early_support": "A2", "late_support": "B1"}
    for a in result.assignments:
        if a["role_code"]:
            assert a["position_code"] == mapping[a["role_code"]]


def test_approved_leave_is_hard_constraint(db, START):
    emp = plain_long_term(db)
    db.add(
        AvailabilityRequest(
            employee_id=emp.id,
            request_type="leave",
            start_date=START,
            end_date=START + dt.timedelta(days=2),
            reason="病假",
            status="approved",
            reviewer="主管",
        )
    )
    db.commit()
    result = solve(build_problem(db, START, 3), mode="draft", timeout=20)
    assert all(a["employee_id"] != emp.id for a in result.assignments)


def test_pending_leave_not_blocking_by_default(db, START):
    """未批准申请默认不作为硬约束（需求 17.9 默认口径，可在系统配置改为阻止）。"""
    emp = db.query(Employee).filter(Employee.employment_type == "part_time").first()
    db.add(
        AvailabilityRequest(
            employee_id=emp.id, request_type="leave", start_date=START, end_date=START, reason="事假", status="pending",
        )
    )
    db.commit()
    problem = build_problem(db, START, 1)
    assert START not in problem.employees[emp.id].leaves


def test_one_position_per_employee_per_day(db, START):
    result = solve(build_problem(db, START, 3), mode="draft", timeout=20)
    seen = {}
    for a in result.assignments:
        key = (a["work_date"], a["employee_id"])
        assert key not in seen, f"{key} 被安排了多个岗位"
        seen[key] = a["position_code"]


def test_gap_detected_and_reported(db, START):
    """需求明显超出合格人员时，必须列出缺口而不是安排不合格人员（需求 4.3）。"""
    force_demand(db, [START], {"AK": 30})

    result = solve(build_problem(db, START, 1), mode="draft", timeout=20)
    gaps = [g for g in result.diagnostics["gaps"] if g["work_date"] == START.isoformat() and g["position_code"] == "AK"]
    assert gaps, "应报告 AK 岗位缺口"
    assert gaps[0]["shortage"] > 0
    assert gaps[0]["reason"]
    assert result.diagnostics["total_gap"] > 0
    # strict 模式应判为不可行
    strict = solve(build_problem(db, START, 1), mode="strict", timeout=20)
    assert strict.status == "infeasible"


def test_locked_assignment_is_preserved(db, START):
    """重新求解时锁定安排不得被移动（需求 9）。"""
    first = solve(build_problem(db, START, 2), mode="draft", timeout=20)
    picked = next(a for a in first.assignments if a["position_code"] == "A7")
    version = ScheduleVersion(
        name="测试草稿", start_date=START, end_date=START + dt.timedelta(days=1), status="draft", version_no=1, created_by="test",
    )
    db.add(version)
    db.flush()
    for a in first.assignments:
        db.add(
            ScheduleAssignment(
                version_id=version.id,
                work_date=a["work_date"],
                employee_id=a["employee_id"],
                position_code=a["position_code"],
                shift=a["shift"],
                role_code=a["role_code"],
                is_locked=(a["work_date"] == picked["work_date"] and a["employee_id"] == picked["employee_id"]),
                source="manual",
            )
        )
    db.commit()

    second = solve(build_problem(db, START, 2), mode="draft", timeout=20)
    kept = [
        a for a in second.assignments
        if a["work_date"] == picked["work_date"] and a["employee_id"] == picked["employee_id"]
    ]
    assert kept and kept[0]["position_code"] == "A7", "锁定安排被移动了"


def test_validation_catches_unqualified_and_duplicate(db, START):
    emp_part = db.query(Employee).filter(Employee.employment_type == "part_time").first()
    normal = plain_long_term(db)
    issues = validate_assignments(
        db,
        [START],
        [
            {"work_date": START, "employee_id": emp_part.id, "position_code": "A3", "shift": "early", "role_code": None},
            {"work_date": START, "employee_id": emp_part.id, "position_code": "B1", "shift": "late", "role_code": None},
            {"work_date": START, "employee_id": normal.id, "position_code": "B2", "shift": "late", "role_code": "duty_supervisor"},
        ],
    )
    codes = {i["code"] for i in issues}
    assert "DUPLICATE_ASSIGNMENT" in codes
    assert "TYPE_RESTRICTED" in codes or "NO_SKILL" in codes
    assert "ROLE_NOT_SUPERVISOR" in codes


def test_coverage_full_for_demo_demand(db, START):
    """默认需求模板在示例人力下应能全部覆盖（工作日口径）。"""
    result = solve(build_problem(db, START, 3), mode="draft", timeout=25)
    assert result.diagnostics["total_gap"] == 0, result.diagnostics["gaps"]
    assert result.diagnostics["duplicate_assignments"] == []


def test_objective_breakdown_covers_soft_goals(db, START):
    """结果页需要展示各软目标满足情况，而不是只给一个总分（需求 12）。"""
    result = solve(build_problem(db, START, 2), mode="draft", timeout=20)
    assert result.objective_value >= 0
    for key in ("gap", "duty_fairness", "shift_preference", "pairing_avoid", "pairing_prefer", "workload_balance"):
        assert key in result.objective_breakdown
        assert result.objective_breakdown[key] >= 0


def test_api_generate_and_publish(client, window):
    resp = client.post("/api/schedule/generate", json={"start_date": window[0].isoformat(), "days": 3, "mode": "draft"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    version = body["version"]
    assert version["status"] == "draft"
    assert version["assignments"]

    published = client.post(f"/api/schedule/versions/{version['id']}/publish", json={"change_note": "测试发布"})
    assert published.status_code == 200, published.text
    assert published.json()["status"] == "published"

    stats = client.get(f"/api/stats/versions/{version['id']}")
    assert stats.status_code == 200
    data = stats.json()
    assert data["summary"]["total_assignments"] == len(version["assignments"])
    assert data["days"][0]["total_people"] > 0
    assert all("deviation" in s for s in data["supervisors"])


def test_api_publish_blocks_invalid_schedule(client, window):
    gen = client.post("/api/schedule/generate", json={"start_date": window[0].isoformat(), "days": 1, "mode": "draft"}).json()
    version = gen["version"]
    emp = client.get("/api/employees").json()
    assigned_ids = {a["employee_id"] for a in version["assignments"] if a["work_date"] == window[0].isoformat()}
    part = next(e for e in emp if e["employment_type"] == "part_time" and e["id"] not in assigned_ids)
    add = client.post(
        f"/api/schedule/versions/{version['id']}/assignments?force=true",
        json={
            "work_date": window[0].isoformat(),
            "employee_id": part["id"],
            "position_code": "A3",
            "shift": "early",
            "role_code": None,
            "is_locked": False,
            "note": "违规测试",
        },
    )
    assert add.status_code == 200, add.text
    resp = client.post(f"/api/schedule/versions/{version['id']}/publish", json={"change_note": ""})
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert any(v["code"] in ("TYPE_RESTRICTED", "NO_SKILL") for v in detail["violations"])


def test_api_manual_patch_returns_409_without_force(client, window):
    gen = client.post("/api/schedule/generate", json={"start_date": window[0].isoformat(), "days": 1, "mode": "draft"}).json()
    version = gen["version"]
    first = version["assignments"][0]
    resp = client.patch(
        f"/api/schedule/versions/{version['id']}/assignments/{first['id']}",
        json={"position_code": "A3"},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["violations"]
