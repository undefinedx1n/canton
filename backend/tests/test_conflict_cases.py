"""冲突与缺口测试（需求 4.3 / 12 / 15：需求无法满足时明确报缺口，不得用不合格人员凑数）。"""
from __future__ import annotations

import datetime as dt

import pytest

from app.models import AvailabilityRequest, Employee, EmployeePositionSkill, ScheduleVersion
from app.services.solver import build_problem, is_eligible, solve

from tests.support import force_demand, plain_long_term, supervisors


def _gaps_for(result, code: str):
    return [g for g in result.diagnostics["gaps"] if g["position_code"] == code]


# --------------------------------------------------------------------------- #
# 人力不足
# --------------------------------------------------------------------------- #
def test_impossible_demand_reports_gap(db, window):
    """需求远超合格人力时，必须报告缺口并给出原因（不能静默忽略）。"""
    force_demand(db, [window[0]], {"AK": 30})
    result = solve(build_problem(db, window[0], 1), mode="draft", timeout=20)

    gaps = _gaps_for(result, "AK")
    assert gaps, "应报告 AK 岗位缺口"
    g = gaps[0]
    assert g["target"] == 30 and g["assigned"] < 30
    assert g["shortage"] > 0
    assert g["reason"], "缺口必须带原因说明"
    assert g["eligible_total"] < 30
    assert result.diagnostics["total_gap"] > 0


def test_gap_never_filled_with_unqualified_staff(db, window):
    """补缺口不得使用不具备资格的人员（需求 4.3 明确禁止）。"""
    force_demand(db, [window[0]], {"AK": 30, "A3": 5})
    problem = build_problem(db, window[0], 1)
    result = solve(problem, mode="draft", timeout=20)

    for a in result.assignments:
        pos = problem.positions[a["position_code"]]
        emp = problem.employees[a["employee_id"]]
        assert is_eligible(emp, pos, a["work_date"]), f"{emp.name} 无 {pos.code} 资格却被排入"
        if pos.code == "A3":
            assert emp.employment_type == "long_term"


def test_no_eligible_employee_reports_specific_reason(db, window):
    """某个岗位完全无人具备资格时，原因文案应指向"资格缺失"而不是泛泛的"人力不足"。"""
    db.query(EmployeePositionSkill).filter(EmployeePositionSkill.position_code == "A8").delete()
    db.commit()
    force_demand(db, [window[0]], {"A8": 1})

    result = solve(build_problem(db, window[0], 1), mode="draft", timeout=20)
    gaps = _gaps_for(result, "A8")
    assert gaps
    assert gaps[0]["eligible_total"] == 0
    assert "资格" in gaps[0]["reason"]


def test_strict_mode_marks_infeasible(db, window):
    """strict 模式下带缺口即判不可行（需求 12「最低人数不足时…具体模式由产品明确」）。"""
    force_demand(db, [window[0]], {"AK": 30})
    result = solve(build_problem(db, window[0], 1), mode="strict", timeout=20)
    assert result.status == "infeasible"


def test_missing_supervisor_role_is_diagnosed(db, window):
    """全体主管请假时，运营角色无法覆盖，必须报告缺失而不是让普通员工顶上。"""
    for sup in supervisors(db):
        db.add(
            AvailabilityRequest(
                employee_id=sup.id, request_type="leave", start_date=window[0], end_date=window[-1],
                reason="全员培训", status="approved", reviewer="测试主管",
            )
        )
    db.commit()

    result = solve(build_problem(db, window[0], 3), mode="draft", timeout=25)
    assert result.diagnostics["missing_roles"], "应报告运营角色缺失"
    assert all(a["role_code"] is None for a in result.assignments), "无主管时不得伪造运营角色"

    # B2 需要当值主管，缺主管时该岗位不能由普通人占位
    b2 = [a for a in result.assignments if a["position_code"] == "B2"]
    assert b2 == [], "无可用主管时 B2 不应排人（避免伪造满足）"


def test_duplicate_demand_conflict_is_visible_in_coverage(db, window):
    """缺口信息在版本覆盖数据中同样可见（供总览页/报表使用）。"""
    force_demand(db, [window[0]], {"BK": 40})
    result = solve(build_problem(db, window[0], 1), mode="draft", timeout=20)

    version = ScheduleVersion(
        name="缺口草稿", start_date=window[0], end_date=window[0], status="draft", version_no=1, created_by="test",
    )
    db.add(version)
    db.flush()
    from app.models import ScheduleAssignment

    for a in result.assignments:
        db.add(ScheduleAssignment(version_id=version.id, **a))
    db.commit()
    db.refresh(version)

    from app.routers.scheduler import _coverage

    rows = {r["position_code"]: r for r in _coverage(db, version)}
    assert rows["BK"]["shortage"] > 0
    assert rows["BK"]["below_min"] is True


# --------------------------------------------------------------------------- #
# API 层：缺口必须出现在返回体，并阻断无确认发布
# --------------------------------------------------------------------------- #
def test_api_generate_exposes_gaps(client, db, window):
    force_demand(db, [window[0]], {"AK": 30})
    resp = client.post("/api/schedule/generate", json={"start_date": window[0].isoformat(), "days": 1, "mode": "draft"})
    assert resp.status_code == 200, resp.text
    body = resp.json()

    gaps = body["run"]["diagnostics"]["gaps"]
    assert any(g["position_code"] == "AK" for g in gaps)
    cove = [c for c in body["version"]["coverage"] if c["position_code"] == "AK"]
    assert cove and cove[0]["shortage"] > 0


def test_publish_requires_acknowledgement_when_gap(client, db, window):
    """带缺口发布必须显式确认（allow_gap），否则 409 阻断（需求 12 / 17.5）。"""
    force_demand(db, [window[0]], {"AK": 30})
    gen = client.post("/api/schedule/generate", json={"start_date": window[0].isoformat(), "days": 1, "mode": "draft"}).json()
    vid = gen["version"]["id"]

    blocked = client.post(f"/api/schedule/versions/{vid}/publish", json={"change_note": "带缺口"})
    assert blocked.status_code == 409
    codes = {v["code"] for v in blocked.json()["detail"]["violations"]}
    assert "BELOW_MIN" in codes
    assert db.get(ScheduleVersion, vid).status == "draft", "被阻断时不得改变版本状态"

    ok = client.post(f"/api/schedule/versions/{vid}/publish?allow_gap=true", json={"change_note": "已知悉缺口"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "published"


def test_gap_reason_is_human_readable(client, db, window):
    force_demand(db, [window[0]], {"AK": 30})
    gen = client.post("/api/schedule/generate", json={"start_date": window[0].isoformat(), "days": 1, "mode": "draft"}).json()
    gap = next(g for g in gen["run"]["diagnostics"]["gaps"] if g["position_code"] == "AK")
    assert len(gap["reason"]) >= 6
    assert gap["position_name"] and gap["shift"] in ("early", "late")
