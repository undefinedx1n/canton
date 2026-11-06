"""人员 × 日期矩阵：休息人员必须出现，且与线下 Excel 表格同构。

诉求来源：主管原来用 Excel 手工排班（姓名 × 日期，V 表示休息），希望能直接
看到"谁今天休息"，并且导出的表能贴回原来的工作簿继续用。
"""
from __future__ import annotations

import datetime as dt
import io

from openpyxl import load_workbook

from app.models import AvailabilityRequest, Employee
from tests.support import make_published_version


def _people(db) -> list[Employee]:
    return db.query(Employee).filter(Employee.is_active.is_(True)).order_by(Employee.id).all()


def _version(db, start, assignments, days=2):
    return make_published_version(db, start=start, days=days, assignments=assignments)


def test_matrix_includes_rest_people(client, db, start):
    people = _people(db)
    d1 = start
    version = _version(
        db,
        start,
        [
            {"work_date": d1, "employee_id": people[0].id, "position_code": "A3", "shift": "early"},
            {"work_date": d1, "employee_id": people[1].id, "position_code": "B3", "shift": "late", "role_code": "duty_supervisor"},
        ],
    )

    data = client.get(f"/api/schedule/versions/{version.id}/matrix").json()

    assert data["summary"]["employees"] == len(people)
    assert len(data["rows"]) == len(people)
    # 矩阵里必须能找回"没排班的人"，而不是只显示被排上的人
    rest_person = next(r for r in data["rows"] if r["employee_id"] == people[2].id)
    assert rest_person["work_days"] == 0
    assert rest_person["rest_days"] == 2
    assert rest_person["cells"][d1.isoformat()]["kind"] == "rest"

    day1 = next(d for d in data["days"] if d["date"] == d1.isoformat())
    assert day1["work_count"] == 2
    assert day1["rest_count"] == len(people) - 2

    duty_cell = next(r for r in data["rows"] if r["employee_id"] == people[1].id)["cells"][d1.isoformat()]
    assert duty_cell["role_code"] == "duty_supervisor"


def test_matrix_shows_approved_leave_instead_of_rest(client, db, start):
    people = _people(db)
    d1 = start
    d2 = start + dt.timedelta(days=1)
    db.add(
        AvailabilityRequest(
            employee_id=people[3].id,
            request_type="leave",
            start_date=d2,
            end_date=d2,
            shift=None,
            reason="家事",
            status="approved",
        )
    )
    db.commit()

    version = _version(db, start, [{"work_date": d1, "employee_id": people[0].id, "position_code": "A3", "shift": "early"}])
    data = client.get(f"/api/schedule/versions/{version.id}/matrix").json()

    row = next(r for r in data["rows"] if r["employee_id"] == people[3].id)
    assert row["cells"][d2.isoformat()]["kind"] == "leave"
    assert row["leave_days"] == 1
    assert row["rest_days"] == 1  # 只有 d1 算休息
    day2 = next(d for d in data["days"] if d["date"] == d2.isoformat())
    # 种子数据里还有别的请假记录，这里只校验"我们这条被计入"
    assert day2["leave_count"] >= 1
    assert day2["rest_count"] + day2["leave_count"] + day2["work_count"] == len(data["rows"])


def test_matrix_is_sorted_supervisors_first(client, db, start):
    version = _version(db, start, [{"work_date": start, "employee_id": _people(db)[0].id, "position_code": "A3", "shift": "early"}])
    rows = client.get(f"/api/schedule/versions/{version.id}/matrix").json()["rows"]
    flags = [r["is_supervisor"] for r in rows]
    # 主管连续排在最前，中间不出现"先普通后主管"的穿插
    assert flags == sorted(flags, reverse=True)


def test_export_has_matrix_sheet_with_rest_marks(client, db, start):
    people = _people(db)
    version = _version(
        db,
        start,
        [
            {"work_date": start, "employee_id": people[0].id, "position_code": "A3", "shift": "early"},
            {"work_date": start, "employee_id": people[1].id, "position_code": "B2", "shift": "late", "role_code": "duty_supervisor"},
        ],
    )

    resp = client.get(f"/api/export/versions/{version.id}.xlsx")
    assert resp.status_code == 200, resp.text

    wb = load_workbook(io.BytesIO(resp.content))
    assert wb.sheetnames[0] == "人员排班"
    ws = wb["人员排班"]
    # 两行表头 + 每人一行
    assert ws.cell(row=1, column=2).value.date() == start
    assert ws.cell(row=2, column=1).value.startswith("星期")

    labels = {ws.cell(row=r, column=2).value for r in range(3, 3 + len(people))}
    assert "V" in labels  # 未排班的人导出为 V

    duty_row = 3 + next(i for i, p in enumerate(people) if p.id == people[1].id)
    duty_cell = ws.cell(row=duty_row, column=2)
    assert duty_cell.value == "B2"
    assert duty_cell.font.color.rgb.endswith("C00000")
