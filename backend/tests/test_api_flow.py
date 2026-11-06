"""接口链路测试：人工调整 → 锁定 → 重排 → 发布 → 修订 → 对比 → 导出 → 留痕。

全部走 TestClient + 临时数据库，不接触 data/schedule.db，也不调用外部服务。
"""
from __future__ import annotations

import datetime as dt
import io

import pytest

from app.models import AuditLog, Employee, ScheduleVersion

from tests.support import force_demand, plain_long_term


def _generate(client, day, days: int = 1, mode: str = "draft"):
    resp = client.post("/api/schedule/generate", json={"start_date": day.isoformat(), "days": days, "mode": mode})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _assignment(client, version_id, day, emp_id):
    detail = client.get(f"/api/schedule/versions/{version_id}").json()
    return next(a for a in detail["assignments"] if a["work_date"] == day.isoformat() and a["employee_id"] == emp_id)


# --------------------------------------------------------------------------- #
# 鉴权
# --------------------------------------------------------------------------- #
def test_requires_authentication(client):
    client.headers.pop("Authorization", None)
    assert client.get("/api/employees").status_code == 401


def test_wrong_password_rejected(client):
    client.headers.pop("Authorization", None)
    resp = client.post("/api/auth/login", json={"username": "admin", "password": "wrong-password"})
    assert resp.status_code == 401


def test_me_returns_current_user(client):
    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["username"] == "admin"


# --------------------------------------------------------------------------- #
# 生成 / 草稿
# --------------------------------------------------------------------------- #
def test_generate_creates_draft_visible_as_current(client, window):
    body = _generate(client, window[0], 3)
    version = body["version"]
    assert version["status"] == "draft"
    assert version["assignments"]

    current = client.get(f"/api/schedule/current?start={window[0].isoformat()}").json()
    assert current["id"] == version["id"]


def test_generate_archives_previous_draft(client, window):
    first = _generate(client, window[0], 1)["version"]
    second = _generate(client, window[0], 1)["version"]
    assert first["id"] != second["id"]
    old = client.get(f"/api/schedule/versions/{first['id']}").json()
    assert old["status"] == "archived", "同日期旧草稿应被归档，保证当前草稿唯一"


# --------------------------------------------------------------------------- #
# 人工调整
# --------------------------------------------------------------------------- #
def test_manual_patch_rejects_violation_without_force(client, window):
    version = _generate(client, window[0], 1)["version"]
    row = version["assignments"][0]
    employees = client.get("/api/employees").json()
    intern = next(e for e in employees if e["employment_type"] == "intern")

    resp = client.patch(
        f"/api/schedule/versions/{version['id']}/assignments/{row['id']}",
        json={"employee_id": intern["id"], "position_code": "A3", "shift": "early"},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["violations"]


def test_manual_patch_with_force_saves_and_logs(client, db, window):
    """主管确认后可强制保存，但必须留下违规明细与审计记录（需求 9.6 / 15）。"""
    version = _generate(client, window[0], 1)["version"]
    row = version["assignments"][0]
    employees = client.get("/api/employees").json()
    intern = next(e for e in employees if e["employment_type"] == "intern")

    resp = client.patch(
        f"/api/schedule/versions/{version['id']}/assignments/{row['id']}?force=true",
        json={"employee_id": intern["id"], "position_code": "A3"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["violations"], "强制保存也必须回报违规项"
    assert body["forked"] is False

    logs = client.get("/api/audit?action=manual_update").json()
    assert logs, "人工调整必须写审计日志"
    assert logs[0]["before"] is not None and logs[0]["after"] is not None


def test_manual_add_rejects_duplicate_employee_day(client, window):
    version = _generate(client, window[0], 1)["version"]
    row = version["assignments"][0]
    resp = client.post(
        f"/api/schedule/versions/{version['id']}/assignments",
        json={
            "work_date": row["work_date"],
            "employee_id": row["employee_id"],
            "position_code": row["position_code"],
            "shift": row["shift"],
            "role_code": row["role_code"],
            "is_locked": False,
            "note": "重复添加",
        },
    )
    assert resp.status_code == 400


def test_lock_then_reoptimize_keeps_locked_row(client, window):
    """锁定后再优化，锁定行不得被移动（需求 9）。"""
    version = _generate(client, window[0], 3)["version"]
    row = next(a for a in version["assignments"] if a["position_code"] == "A7")

    locked = client.patch(
        f"/api/schedule/versions/{version['id']}/assignments/{row['id']}",
        json={"is_locked": True},
    )
    assert locked.status_code == 200, locked.text
    vid = locked.json()["version"]["id"]

    re = client.post(f"/api/schedule/versions/{vid}/reoptimize")
    assert re.status_code == 200, re.text
    after = re.json()["version"] if "version" in re.json() else re.json()
    kept = [
        a for a in after["assignments"]
        if a["work_date"] == row["work_date"] and a["employee_id"] == row["employee_id"]
    ]
    assert kept and kept[0]["position_code"] == "A7"
    assert kept[0]["is_locked"] is True


# --------------------------------------------------------------------------- #
# 发布 / 版本
# --------------------------------------------------------------------------- #
def test_publish_then_edit_forks_new_draft(client, db, window):
    """已发布版本不可直接改：编辑会自动派生新草稿并保留父版本（需求 9.7）。"""
    version = _generate(client, window[0], 1)["version"]
    published = client.post(f"/api/schedule/versions/{version['id']}/publish", json={"change_note": "首次发布"})
    assert published.status_code == 200, published.text
    assert published.json()["status"] == "published"

    row = published.json()["assignments"][0]
    other = next(
        a for a in published.json()["assignments"]
        if a["position_code"] != row["position_code"]
    ) if len(published.json()["assignments"]) > 1 else None
    target_code = "A7" if row["position_code"] != "A7" else "A8"

    resp = client.patch(
        f"/api/schedule/versions/{version['id']}/assignments/{row['id']}?force=true",
        json={"position_code": target_code},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["forked"] is True, "修改已发布版本应派生新草稿"
    new_version = body["version"]
    assert new_version["id"] != version["id"]
    assert new_version["parent_version_id"] == version["id"]
    assert new_version["status"] == "draft"

    # 原已发布版本保持原样
    original = client.get(f"/api/schedule/versions/{version['id']}").json()
    assert original["status"] == "published"
    assert all(a["position_code"] != target_code or a["id"] != row["id"] for a in original["assignments"])


def test_published_version_cannot_be_deleted(client, window):
    version = _generate(client, window[0], 1)["version"]
    client.post(f"/api/schedule/versions/{version['id']}/publish", json={"change_note": ""})
    resp = client.delete(f"/api/schedule/versions/{version['id']}")
    assert resp.status_code == 400


def test_publish_twice_is_rejected(client, window):
    version = _generate(client, window[0], 1)["version"]
    assert client.post(f"/api/schedule/versions/{version['id']}/publish", json={"change_note": ""}).status_code == 200
    again = client.post(f"/api/schedule/versions/{version['id']}/publish", json={"change_note": ""})
    assert again.status_code == 400


def test_compare_versions_reports_changes(client, window):
    version = _generate(client, window[0], 1)["version"]
    client.post(f"/api/schedule/versions/{version['id']}/publish", json={"change_note": "基线"})
    row = client.get(f"/api/schedule/versions/{version['id']}").json()["assignments"][0]
    target_code = "A7" if row["position_code"] != "A7" else "A8"
    forked = client.patch(
        f"/api/schedule/versions/{version['id']}/assignments/{row['id']}?force=true",
        json={"position_code": target_code},
    ).json()["version"]

    cmp = client.get(f"/api/schedule/versions/{version['id']}/compare/{forked['id']}")
    assert cmp.status_code == 200
    data = cmp.json()
    assert data["changes"], "两版之间应存在差异"
    assert data["summary"]["changed"] + data["summary"]["added"] + data["summary"]["removed"] >= 1
    changed = [c for c in data["changes"] if c["employee_id"] == row["employee_id"]]
    assert changed and changed[0]["after_position"] == target_code


def test_validate_endpoint_returns_issues(client, db, window):
    version = _generate(client, window[0], 1)["version"]
    resp = client.get(f"/api/schedule/versions/{version['id']}/validate")
    assert resp.status_code == 200
    issues = resp.json()["violations"]
    assert isinstance(issues, list)
    assert all("level" in i and "message" in i for i in issues)


# --------------------------------------------------------------------------- #
# Excel
# --------------------------------------------------------------------------- #
def test_excel_export_contains_sheets_and_rows(client, window):
    version = _generate(client, window[0], 3)["version"]
    resp = client.get(f"/api/export/versions/{version['id']}.xlsx")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/vnd.openxmlformats")
    assert "filename*=UTF-8''" in resp.headers["content-disposition"]

    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(resp.content))
    assert {"人员排班", "排班表", "岗位覆盖", "每日统计", "员工明细", "主管公平", "版本信息"} <= set(wb.sheetnames)

    ws = wb["排班表"]
    # 排班表是"员工 × 日期"的打印网格：第 4 行为表头，之后每位在职员工一行，末尾附每日汇总
    header = [ws.cell(row=4, column=i).value for i in range(1, 4)]
    assert header == ["姓名", "员工类型", "职务"]

    assigned_count = len({a["employee_id"] for a in version["assignments"]})
    body_rows = [r for r in range(5, ws.max_row + 1) if ws.cell(row=r, column=2).value in ("长期员工", "兼职", "实习生", "其他")]
    assert len(body_rows) == assigned_count, f"网格应有 {assigned_count} 名员工，实际 {len(body_rows)} 行"

    filled = sum(
        1
        for r in body_rows
        for c in range(4, ws.max_column + 1)
        if ws.cell(row=r, column=c).value
    )
    assert filled == len(version["assignments"]), f"网格中 {filled} 个班次，应为 {len(version['assignments'])}"

    summary = [ws.cell(row=r, column=1).value for r in range(ws.max_row - 2, ws.max_row + 1)]
    assert "当日上班人数" in summary, "应包含每日人数汇总行"

    fair = wb["主管公平"]
    assert fair.max_row >= 2, "主管公平页应包含数据行"


def test_employee_template_export(client):
    resp = client.get("/api/export/employees-template.xlsx")
    assert resp.status_code == 200
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(resp.content))
    assert wb.sheetnames == ["员工导入模板"]


def test_import_preview_rejects_non_excel(client):
    resp = client.post(
        "/api/import/employees/preview",
        files={"file": ("employees.csv", b"name,type\nfoo,long_term\n", "text/csv")},
    )
    assert resp.status_code == 400


# --------------------------------------------------------------------------- #
# 留痕
# --------------------------------------------------------------------------- #
def test_audit_covers_generate_and_publish(client, db, window):
    version = _generate(client, window[0], 1)["version"]
    client.post(f"/api/schedule/versions/{version['id']}/publish", json={"change_note": "验收发布"})

    actions = {log["action"] for log in client.get("/api/audit?limit=200").json()}
    assert {"generate", "publish"} <= actions
    assert db.query(AuditLog).count() >= 2
