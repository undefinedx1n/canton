"""接口冒烟测试：逐个打通 MVP 所需端点，发现 5xx 立即失败。"""
from __future__ import annotations

import datetime as dt
import io
import json
import sys

import httpx

BASE = "http://127.0.0.1:8000"
client = httpx.Client(base_url=BASE, timeout=60)
failures: list[str] = []


def call(method: str, path: str, expected=(200,), **kwargs):
    resp = client.request(method, path, **kwargs)
    ok = resp.status_code in expected
    mark = "OK " if ok else "FAIL"
    print(f"{mark} {method:5s} {path:55s} {resp.status_code}")
    if not ok:
        failures.append(f"{method} {path} -> {resp.status_code} {resp.text[:300]}")
    return resp


r = call("POST", "/api/auth/login", json={"username": "admin", "password": "admin123456"})
token = r.json()["access_token"]
client.headers["Authorization"] = f"Bearer {token}"

call("GET", "/api/health")
call("GET", "/api/meta")
call("GET", "/api/auth/me")
call("GET", "/api/stats/dashboard")

emps = call("GET", "/api/employees").json()
positions = call("GET", "/api/positions").json()
roles = call("GET", "/api/operational-roles").json()
call("GET", "/api/employee-types")
call("GET", "/api/demand-templates")
call("GET", "/api/pairing-preferences")

today = dt.date.today().isoformat()
end = (dt.date.today() + dt.timedelta(days=2)).isoformat()

call("GET", f"/api/calendar-days?start={today}&end={end}")
call("PUT", f"/api/calendar-days/{end}", json={"work_date": end, "day_type": "weekend", "is_holiday": False, "is_peak": True, "label": "冒烟测试", "expected_traffic": "high", "note": ""})
demands = call("GET", f"/api/daily-demands?start={today}&end={end}").json()
first = demands[0]["positions"][0]
call("PUT", "/api/daily-demands", json=[{"work_date": demands[0]["work_date"], "position_code": first["position_code"], "target_count": first["target_count"] + 1, "min_count": first["min_count"], "note": "冒烟"}])
call("DELETE", f"/api/daily-demands?work_date={demands[0]['work_date']}&position_code={first['position_code']}")

# 请假
emp = emps[-1]
req = call("POST", "/api/availability-requests", json={"employee_id": emp["id"], "request_type": "leave", "start_date": today, "end_date": today, "shift": None, "reason": "冒烟测试", "target_position_code": None})
rid = req.json()["id"]
call("PATCH", f"/api/availability-requests/{rid}/review", json={"status": "approved", "reviewer": ""})
call("GET", f"/api/availability-requests/calendar?start={today}&end={end}")
call("DELETE", f"/api/availability-requests/{rid}")

# 员工增删改
new_emp = {
    "name": "冒烟测试员工", "employee_no": f"T{int(dt.datetime.now().timestamp())}", "employment_type": "part_time", "job_title": "兼职",
    "is_supervisor": False, "is_mentor": False, "is_active": True, "shift_preference": "early",
    "phone": None, "hire_date": None, "max_days_per_week": None, "notes": "冒烟",
    "skills": [{"position_code": "A1", "priority": 2, "proficiency": 3, "allowed": True, "note": ""}],
}
created = call("POST", "/api/employees", json=new_emp).json()
new_emp["job_title"] = "兼职（已更新）"
call("PUT", f"/api/employees/{created['id']}", json=new_emp)
call("PATCH", f"/api/employees/{created['id']}/skills", json=[{"position_code": "A1", "priority": 1, "proficiency": 4, "allowed": True, "note": ""}])
call("DELETE", f"/api/employees/{created['id']}")

# 搭班
pr = call("POST", "/api/pairing-preferences", json={"employee_a_id": emps[0]["id"], "employee_b_id": emps[1]["id"], "preference_type": "prefer", "scope": "same_shift", "weight": 1.0, "start_date": None, "end_date": None, "note": "冒烟", "is_active": True}).json()
call("DELETE", f"/api/pairing-preferences/{pr['id']}")

# 配置
settings = call("GET", "/api/settings").json()
w = next(s for s in settings if s["key"] == "solver_weights")
call("PUT", "/api/settings/solver_weights", json={"key": "solver_weights", "value": w["value"], "description": ""})

# 排班流程
gen = call("POST", "/api/schedule/generate", json={"start_date": today, "days": 3, "mode": "draft", "keep_locks": True, "version_name": "冒烟预排班"}).json()
vid = gen["version"]["id"]
print("   generated:", len(gen["version"]["assignments"]), "assignments, gap", gen["run"]["diagnostics"].get("total_gap"))
call("GET", f"/api/schedule/versions/{vid}")
call("GET", f"/api/schedule/versions/{vid}/validate")
call("GET", f"/api/schedule/current?start={today}")
call("GET", "/api/schedule/versions")

# 人工调整：锁定一条 + 修改另一条
a1 = gen["version"]["assignments"][0]
call("PATCH", f"/api/schedule/versions/{vid}/assignments/{a1['id']}?force=true", json={"is_locked": True})
call("POST", f"/api/schedule/versions/{vid}/reoptimize")

# 版本对比
call("POST", f"/api/schedule/versions/{vid}/publish?allow_gap=true", json={"change_note": "冒烟发布"})
versions = call("GET", "/api/schedule/versions").json()
if len(versions) >= 2:
    call("GET", f"/api/schedule/versions/{versions[0]['id']}/compare/{versions[1]['id']}")

call("GET", f"/api/stats/versions/{vid}")
call("GET", f"/api/stats/daily?start={today}&end={end}")

# Excel
xlsx = call("GET", f"/api/export/versions/{vid}.xlsx")
print("   xlsx bytes:", len(xlsx.content))
tpl = call("GET", "/api/export/employees-template.xlsx")
files = {"file": ("t.xlsx", tpl.content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
call("POST", "/api/import/employees/preview", files=files)

call("GET", "/api/audit?limit=20")

print()
if failures:
    print(f"{len(failures)} FAILURES")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("ALL API CHECKS PASSED")
