"""钉钉考勤排班表导出：格子必须是人力模板认识的那 18 个班次名。

诉求来源：每次排班后要导出「柯达广州塔排班表.xls」上交人力，导入钉钉考勤后台。
模板要求：行1 标题、行2 提示、行3 排班信息（整行原样）、行4 表头、行5+ 每人一格一个班次名或「休」。
"""
from __future__ import annotations

import datetime as dt
import io
from urllib.parse import unquote

from openpyxl import load_workbook

from app.models import Employee, ScheduleAssignment, ScheduleVersion
from app.services.config_service import DEFAULT_SETTINGS


def _draft(db, start, days, assignments, name="导出测试"):
    version = ScheduleVersion(
        name=name,
        start_date=start,
        end_date=start + dt.timedelta(days=days - 1),
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
                shift=a.get("shift") or "early",
                role_code=a.get("role_code"),
                source="manual" if a.get("manual") else "solver",
            )
        )
    db.commit()
    db.refresh(version)
    return version


def _sheet(client, version_id):
    resp = client.get(f"/api/export/versions/{version_id}/dingtalk.xlsx")
    assert resp.status_code == 200, resp.text
    return load_workbook(io.BytesIO(resp.content)).active, resp


def test_export_layout_matches_template(client, db, start):
    people = db.query(Employee).filter(Employee.is_active.is_(True)).order_by(Employee.id).limit(2).all()
    d1 = start
    version = _draft(
        db,
        start,
        2,
        [
            {"work_date": d1, "employee_id": people[0].id, "position_code": "A2", "shift": "early"},
            {"work_date": d1, "employee_id": people[1].id, "position_code": "B2", "shift": "late", "role_code": "duty_supervisor"},
        ],
    )

    ws, _ = _sheet(client, version.id)

    # 前三行是钉钉识别用的固定文案
    assert ws.cell(row=1, column=1).value == "柯达广州塔排班表"
    assert "严格按" in ws.cell(row=2, column=1).value
    assert ws.cell(row=3, column=1).value == DEFAULT_SETTINGS["dingtalk_export"][0]["shift_info"]

    # 表头：部门/用户ID/工号/姓名 + 日期（含星期，换行）
    heads = [ws.cell(row=4, column=c).value for c in range(1, 5)]
    assert heads == ["部门", "用户ID", "工号", "姓名"]
    assert ws.cell(row=4, column=5).value == f"{d1.year}/{d1.month}/{d1.day}\n周{'一二三四五六日'[d1.weekday()]}"

    # 员工行：部门固定，姓名对得上
    names = [ws.cell(row=r, column=4).value for r in range(5, 5 + len(people))]
    assert names == [p.name for p in people]


def test_export_uses_dingtalk_shift_tokens_not_position_codes(client, db, start):
    people = db.query(Employee).filter(Employee.is_active.is_(True)).order_by(Employee.id).limit(3).all()
    d1, d2 = start, start + dt.timedelta(days=1)
    version = _draft(
        db,
        start,
        2,
        [
            {"work_date": d1, "employee_id": people[0].id, "position_code": "A2", "shift": "early"},   # A108柜台
            {"work_date": d1, "employee_id": people[1].id, "position_code": "B3", "shift": "late"},    # B107销售
            {"work_date": d1, "employee_id": people[2].id, "position_code": "AK", "shift": "early"},   # A绿背景
            {"work_date": d2, "employee_id": people[0].id, "position_code": "B8", "shift": "late"},    # B108玻璃屋
        ],
    )

    ws, _ = _sheet(client, version.id)
    by_name = {ws.cell(row=r, column=4).value: r for r in range(5, 5 + 50) if ws.cell(row=r, column=4).value}

    assert ws.cell(row=by_name[people[0].name], column=5).value == "A108柜台"
    assert ws.cell(row=by_name[people[1].name], column=5).value == "B107销售"
    assert ws.cell(row=by_name[people[2].name], column=5).value == "A绿背景"
    assert ws.cell(row=by_name[people[0].name], column=6).value == "B108玻璃屋"
    # 未排班当天 = 休（不是空、也不是岗位代码）
    assert ws.cell(row=by_name[people[1].name], column=6).value == "休"
    assert ws.cell(row=by_name[people[2].name], column=6).value == "休"


def test_export_marks_leave_as_rest_token(client, db, start):
    from app.models import AvailabilityRequest

    people = db.query(Employee).filter(Employee.is_active.is_(True)).order_by(Employee.id).limit(2).all()
    d1 = start
    db.add(
        AvailabilityRequest(
            employee_id=people[0].id,
            request_type="leave",
            start_date=d1,
            end_date=d1,
            reason="年假",
            status="approved",
        )
    )
    db.commit()
    # 即使存在历史排班行，批准请假也必须在班表里显示为「休」
    version = _draft(db, start, 1, [{"work_date": d1, "employee_id": people[0].id, "position_code": "A2", "shift": "early"}])

    ws, _ = _sheet(client, version.id)
    by_name = {ws.cell(row=r, column=4).value: r for r in range(5, 5 + 50) if ws.cell(row=r, column=4).value}
    assert ws.cell(row=by_name[people[0].name], column=5).value == "休"


def test_export_warns_when_dingtalk_id_missing(client, db, start):
    people = db.query(Employee).filter(Employee.is_active.is_(True)).order_by(Employee.id).limit(2).all()
    for p in people:
        p.external_user_id = None
    db.commit()
    version = _draft(db, start, 1, [{"work_date": start, "employee_id": people[0].id, "position_code": "A2", "shift": "early"}])

    ws, resp = _sheet(client, version.id)
    warnings = unquote(resp.headers.get("X-Export-Warnings", ""))
    assert "缺钉钉用户ID" in warnings

    # 填了 ID 的人必须出现在「用户ID」列
    people[1].external_user_id = "01551052440126367057"
    db.commit()
    ws2, _ = _sheet(client, version.id)
    by_name = {ws2.cell(row=r, column=4).value: r for r in range(5, 5 + 50) if ws2.cell(row=r, column=4).value}
    assert ws2.cell(row=by_name[people[1].name], column=2).value == "01551052440126367057"


def test_external_ids_backfill_by_name(client, db):
    people = db.query(Employee).filter(Employee.is_active.is_(True)).order_by(Employee.id).limit(3).all()
    payload = [
        {"name": people[0].name, "external_user_id": "ID-0001"},
        {"name": people[1].name, "external_user_id": "ID-0002"},
        {"name": "系统里没有的人", "external_user_id": "ID-9999"},
    ]
    resp = client.post("/api/employees/external-ids", json=payload)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["updated"] == 2
    assert body["missing"] == ["系统里没有的人"]

    db.expire_all()
    assert db.get(Employee, people[0].id).external_user_id == "ID-0001"
    assert db.get(Employee, people[1].id).external_user_id == "ID-0002"
    assert db.get(Employee, people[2].id).external_user_id is None
