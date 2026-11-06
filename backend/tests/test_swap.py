"""调班：同一天两名员工互换岗位/班次。

逐个改两格时中间态会短暂超编/缺人，对使用者来说要操作两次也容易漏，
所以提供原子互换接口：一次事务内换完再统一校验。
"""
from __future__ import annotations

import datetime as dt

from app.models import Employee, ScheduleAssignment, ScheduleVersion


def _draft(db, start, assignments):
    version = ScheduleVersion(
        name="调班测试",
        start_date=start,
        end_date=start,
        status="draft",
        version_no=1,
        created_by="测试",
        change_note="",
    )
    db.add(version)
    db.flush()
    for a in assignments:
        db.add(
            ScheduleAssignment(
                version_id=version.id,
                work_date=a["work_date"],
                employee_id=a["employee_id"],
                position_code=a["position_code"],
                shift=a["shift"],
                role_code=a.get("role_code"),
                source="solver",
            )
        )
    db.commit()
    db.refresh(version)
    return version


def test_swap_exchanges_position_and_shift(client, db, start):
    people = db.query(Employee).filter(Employee.is_active.is_(True)).order_by(Employee.id).limit(2).all()
    a, b = people[0], people[1]
    version = _draft(
        db,
        start,
        [
            {"work_date": start, "employee_id": a.id, "position_code": "A2", "shift": "early"},
            {"work_date": start, "employee_id": b.id, "position_code": "B3", "shift": "late"},
        ],
    )

    resp = client.post(
        f"/api/schedule/versions/{version.id}/swap",
        json={"work_date": str(start), "employee_a": a.id, "employee_b": b.id},
    )
    assert resp.status_code == 200, resp.text

    db.expire_all()
    rows = {
        r.employee_id: r
        for r in db.query(ScheduleAssignment).filter(ScheduleAssignment.version_id == version.id).all()
    }
    assert rows[a.id].position_code == "B3" and rows[a.id].shift == "late"
    assert rows[b.id].position_code == "A2" and rows[b.id].shift == "early"
    # 互换结果视为人工调整，后续重新优化时不能被挪走
    assert rows[a.id].source == "manual" and rows[b.id].source == "manual"


def test_swap_requires_both_scheduled(client, db, start):
    people = db.query(Employee).filter(Employee.is_active.is_(True)).order_by(Employee.id).limit(2).all()
    a, b = people[0], people[1]
    version = _draft(db, start, [{"work_date": start, "employee_id": a.id, "position_code": "A2", "shift": "early"}])

    resp = client.post(
        f"/api/schedule/versions/{version.id}/swap",
        json={"work_date": str(start), "employee_a": a.id, "employee_b": b.id},
    )
    assert resp.status_code == 400
    assert "当天都必须已有排班" in resp.json()["detail"]


def test_swap_rejects_same_employee(client, db, start):
    people = db.query(Employee).filter(Employee.is_active.is_(True)).order_by(Employee.id).limit(1).all()
    a = people[0]
    version = _draft(db, start, [{"work_date": start, "employee_id": a.id, "position_code": "A2", "shift": "early"}])

    resp = client.post(
        f"/api/schedule/versions/{version.id}/swap",
        json={"work_date": str(start), "employee_a": a.id, "employee_b": a.id},
    )
    assert resp.status_code == 400
    assert "不同的员工" in resp.json()["detail"]


def test_swap_rolls_back_when_it_breaks_rules(client, db, start):
    """把没有该岗位资格的人换到需要资格的岗位，应整体回滚，不留半截状态。"""
    people = db.query(Employee).filter(Employee.is_active.is_(True)).order_by(Employee.id).limit(2).all()
    a, b = people[0], people[1]
    # 只给陈嘉欣式场景：清掉 b 的所有岗位资格，交换后 b 站在 A2 上必然违规
    from app.models import EmployeePositionSkill

    db.query(EmployeePositionSkill).filter(EmployeePositionSkill.employee_id == b.id).delete()
    db.commit()
    version = _draft(
        db,
        start,
        [
            {"work_date": start, "employee_id": a.id, "position_code": "A2", "shift": "early"},
            {"work_date": start, "employee_id": b.id, "position_code": "B3", "shift": "late"},
        ],
    )

    resp = client.post(
        f"/api/schedule/versions/{version.id}/swap",
        json={"work_date": str(start), "employee_a": a.id, "employee_b": b.id},
    )
    assert resp.status_code == 409, resp.text
    db.expire_all()
    rows = {r.employee_id: r for r in db.query(ScheduleAssignment).filter(ScheduleAssignment.version_id == version.id).all()}
    assert rows[a.id].position_code == "A2"
    assert rows[b.id].position_code == "B3"
