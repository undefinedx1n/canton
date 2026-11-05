"""统计与报表口径测试（需求 10 / 15）。

关键口径：当天上班人数 = 当日实际被安排的员工去重数；运营角色不重复计数；
岗位覆盖按岗位名额单独统计；长期员工才有周期应休目标。
"""
from __future__ import annotations

import datetime as dt

import pytest

from app.models import ScheduleVersion
from app.services.stats import version_stats

from tests.support import force_demand, plain_long_term, supervisors


def _make_version(client, db, window, days=3):
    body = client.post(
        "/api/schedule/generate",
        json={"start_date": window[0].isoformat(), "days": days, "mode": "draft"},
    ).json()
    return db.get(ScheduleVersion, body["version"]["id"])


# --------------------------------------------------------------------------- #
# 每日人数口径
# --------------------------------------------------------------------------- #
def test_total_people_counts_each_employee_once(client, db, window):
    version = _make_version(client, db, window)
    stats = version_stats(db, version)

    for day in stats["days"]:
        d = dt.date.fromisoformat(day["work_date"])
        rows = [a for a in version.assignments if a.work_date == d]
        assert day["total_people"] == len({a.employee_id for a in rows})
        # 每人每日只有一个班次，因此早晚班人数之和应等于总人数
        assert day["early_count"] + day["late_count"] == day["total_people"]


def test_role_holder_not_double_counted(client, db, window):
    """承担运营角色的员工只计 1 人（需求 10 末条）。"""
    version = _make_version(client, db, window)
    stats = version_stats(db, version)
    for day in stats["days"]:
        with_role = {s["employee_id"] for s in day["supervisors_on_duty"]}
        assert with_role
        assert len(with_role) <= day["total_people"]


def test_sales_and_photography_split(client, db, window):
    version = _make_version(client, db, window)
    stats = version_stats(db, version)
    for day in stats["days"]:
        assert day["sales_count"] + day["photography_count"] == day["total_people"]


def test_coverage_rows_sum_to_assignments(client, db, window):
    version = _make_version(client, db, window)
    stats = version_stats(db, version)
    for day in stats["days"]:
        d = dt.date.fromisoformat(day["work_date"])
        total_assigned = sum(p["assigned"] for p in day["positions"])
        assert total_assigned == len([a for a in version.assignments if a.work_date == d])


def test_no_shortage_when_fully_staffed(client, db, window):
    version = _make_version(client, db, window)
    stats = version_stats(db, version)
    assert stats["summary"]["total_assignments"] == len(version.assignments)
    for day in stats["days"]:
        assert all(p["shortage"] == 0 for p in day["positions"]), day["positions"]


# --------------------------------------------------------------------------- #
# 员工维度
# --------------------------------------------------------------------------- #
def test_employee_rows_are_consistent(client, db, window):
    version = _make_version(client, db, window)
    stats = version_stats(db, version)
    window_len = len(window)

    for row in stats["employees"]:
        assert row["work_days"] + row["rest_days_in_window"] == window_len
        assert row["early_count"] + row["late_count"] == row["work_days"]
        assert row["position_total"] == row["work_days"]


def test_rest_target_only_for_formal(client, db, window):
    """周期应休目标只对长期员工生效（需求 7「长期员工每月休息 8 天」）。"""
    version = _make_version(client, db, window)
    stats = version_stats(db, version)

    part_time = [r for r in stats["employees"] if r["employment_type"] == "part_time"]
    formal = [r for r in stats["employees"] if r["employment_type"] == "long_term"]
    assert part_time and formal
    assert all(r["period_target_rest"] is None for r in part_time)
    assert all(r["period_target_rest"] is not None for r in formal)
    assert formal[0]["period_rest_days"] >= 0


# --------------------------------------------------------------------------- #
# 主管公平报表
# --------------------------------------------------------------------------- #
def test_supervisor_fairness_fields(client, db, window):
    version = _make_version(client, db, window)
    stats = version_stats(db, version)

    sups = stats["supervisors"]
    assert len(sups) == len(supervisors(db))
    for row in sups:
        for key in ("name", "window_duty", "period_duty", "window_weekend", "window_holiday", "window_peak", "deviation"):
            assert key in row, f"主管公平缺少字段 {key}"
        assert row["window_duty"] >= 0

    duty_total = sum(r["window_duty"] for r in sups)
    assert duty_total == len(window), "每天 1 名当值主管"
    avg = sum(r["period_duty"] for r in sups) / len(sups)
    for row in sups:
        assert abs(row["deviation"] - (row["period_duty"] - avg)) <= 0.01


def test_holiday_duty_reported_separately(client, db, window):
    from tests.support import set_day

    holiday = window[1]
    set_day(db, holiday, "holiday", is_holiday=True, label="验收节假日")
    db.commit()

    version = _make_version(client, db, window)
    stats = version_stats(db, version)
    day = next(d for d in stats["days"] if d["work_date"] == holiday.isoformat())
    assert day["is_holiday"] is True
    assert sum(r["window_holiday"] for r in stats["supervisors"]) == 1


# --------------------------------------------------------------------------- #
# 接口层
# --------------------------------------------------------------------------- #
def test_api_stats_endpoints(client, db, window):
    version = _make_version(client, db, window)

    per_version = client.get(f"/api/stats/versions/{version.id}")
    assert per_version.status_code == 200
    data = per_version.json()
    assert data["summary"]["total_assignments"] == len(version.assignments)

    dashboard = client.get("/api/stats/dashboard")
    assert dashboard.status_code == 200
    assert "pending_requests" in dashboard.json()

    daily = client.get(
        "/api/stats/daily",
        params={"start": window[0].isoformat(), "end": window[-1].isoformat()},
    )
    assert daily.status_code == 200
    payload = daily.json()
    assert isinstance(payload.get("days"), list)


def test_stats_matches_database_after_gap(client, db, window):
    """带缺口时统计必须如实反映，不粉饰为满员。"""
    force_demand(db, [window[0]], {"AK": 40})
    body = client.post(
        "/api/schedule/generate",
        json={"start_date": window[0].isoformat(), "days": 1, "mode": "draft"},
    ).json()
    version = db.get(ScheduleVersion, body["version"]["id"])
    stats = version_stats(db, version)

    ak = next(p for p in stats["days"][0]["positions"] if p["position_code"] == "AK")
    assert ak["target"] == 40
    assert ak["shortage"] == 40 - ak["assigned"] > 0
    assert stats["summary"]["total_gap"] > 0
