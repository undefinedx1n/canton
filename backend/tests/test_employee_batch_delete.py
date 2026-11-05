"""员工批量停用 / 批量删除的行为约束。

核心不变量：**已经进入过排班版本的员工不允许物理删除**，否则已发布班表会指向
不存在的员工，历史无法追溯；这类员工自动降级为停用并在结果里说明原因。
"""
from __future__ import annotations

import datetime as dt

from app.models import (
    AuditLog,
    AvailabilityRequest,
    Employee,
    EmployeePositionSkill,
    PairingPreference,
    ScheduleAssignment,
)
from tests.support import make_published_version


def _make(db, name: str, no: str, **kw) -> Employee:
    emp = Employee(name=name, employee_no=no, employment_type=kw.pop("employment_type", "long_term"), **kw)
    db.add(emp)
    db.commit()
    db.refresh(emp)
    return emp


def test_batch_deactivate_marks_inactive_and_keeps_records(client, db):
    a = _make(db, "批量停用甲", "BD001")
    b = _make(db, "批量停用乙", "BD002")

    resp = client.post("/api/employees/batch-delete", json={"employee_ids": [a.id, b.id], "mode": "deactivate"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["deactivated"] == 2
    assert body["purged"] == 0

    db.expire_all()
    assert db.get(Employee, a.id).is_active is False
    assert db.get(Employee, b.id).is_active is False
    # 停用不删除，记录仍在
    assert db.get(Employee, a.id) is not None


def test_batch_purge_removes_employee_without_schedule_history(client, db):
    emp = _make(db, "待彻底删除", "BD010")
    db.add(EmployeePositionSkill(employee_id=emp.id, position_code="B2", priority=3, proficiency=3, allowed=True))
    db.commit()

    resp = client.post("/api/employees/batch-delete", json={"employee_ids": [emp.id], "mode": "purge"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["purged"] == 1
    assert body["deactivated"] == 0

    db.expire_all()
    assert db.get(Employee, emp.id) is None
    # 关联资格一并清理，不留孤儿行
    assert db.query(EmployeePositionSkill).filter(EmployeePositionSkill.employee_id == emp.id).count() == 0


def test_purge_refuses_employee_with_schedule_history(client, db):
    emp = _make(db, "有班表的主管", "BD020", is_supervisor=True)
    make_published_version(
        db,
        start=dt.date(2026, 3, 2),
        days=1,
        assignments=[{"work_date": dt.date(2026, 3, 2), "employee_id": emp.id, "position_code": "B2", "role_code": "duty_supervisor"}],
    )

    resp = client.post("/api/employees/batch-delete", json={"employee_ids": [emp.id], "mode": "purge"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["purged"] == 0
    assert body["deactivated"] == 1, "有排班历史应降级为停用"
    assert any("排班记录" in s["reason"] for s in body["skipped"])

    db.expire_all()
    still = db.get(Employee, emp.id)
    assert still is not None, "有排班历史的员工不能被物理删除"
    assert still.is_active is False
    # 历史班表引用完好
    assert db.query(ScheduleAssignment).filter(ScheduleAssignment.employee_id == emp.id).count() == 1


def test_purge_cleans_leave_and_pairing_references(client, db):
    a = _make(db, "删除甲", "BD030")
    b = _make(db, "删除乙", "BD031")
    db.add(AvailabilityRequest(employee_id=a.id, start_date=dt.date(2026, 5, 1), end_date=dt.date(2026, 5, 2), request_type="leave", status="approved"))
    db.add(PairingPreference(employee_a_id=a.id, employee_b_id=b.id, preference_type="prefer", scope="same_shift", weight=1.0))
    db.commit()

    resp = client.post("/api/employees/batch-delete", json={"employee_ids": [a.id], "mode": "purge"})
    assert resp.status_code == 200
    assert resp.json()["purged"] == 1

    db.expire_all()
    assert db.query(AvailabilityRequest).filter(AvailabilityRequest.employee_id == a.id).count() == 0
    assert (
        db.query(PairingPreference)
        .filter((PairingPreference.employee_a_id == a.id) | (PairingPreference.employee_b_id == a.id))
        .count()
        == 0
    )
    assert db.get(Employee, b.id) is not None, "不应误删另一名员工"


def test_batch_delete_reports_missing_and_already_inactive(client, db):
    emp = _make(db, "已停用的人", "BD040", is_active=False)
    resp = client.post("/api/employees/batch-delete", json={"employee_ids": [emp.id, 999999], "mode": "deactivate"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["deactivated"] == 0
    reasons = {s["employee_id"]: s["reason"] for s in body["skipped"]}
    assert "不存在" in reasons[999999]
    assert "已是停用" in reasons[emp.id]


def test_batch_delete_rejects_empty_and_unknown_mode(client, db):
    emp = _make(db, "参数校验", "BD050")
    assert client.post("/api/employees/batch-delete", json={"employee_ids": [], "mode": "deactivate"}).status_code == 400
    assert client.post("/api/employees/batch-delete", json={"employee_ids": [emp.id], "mode": "drop"}).status_code == 400
    db.expire_all()
    assert db.get(Employee, emp.id).is_active is True, "参数非法时不得产生副作用"


def test_batch_delete_is_idempotent_and_audited(client, db):
    a = _make(db, "审计甲", "BD060")
    b = _make(db, "审计乙", "BD061")

    first = client.post("/api/employees/batch-delete", json={"employee_ids": [a.id, b.id], "mode": "purge"})
    assert first.json()["purged"] == 2

    # 重复提交同一批 id：都已不存在，不应报错
    second = client.post("/api/employees/batch-delete", json={"employee_ids": [a.id, b.id], "mode": "purge"})
    assert second.status_code == 200
    assert second.json()["purged"] == 0
    assert len(second.json()["skipped"]) == 2

    db.expire_all()
    logs = db.query(AuditLog).filter(AuditLog.action == "batch_purge").all()
    assert logs, "批量删除必须留审计"
    assert "审计甲" in logs[0].summary
