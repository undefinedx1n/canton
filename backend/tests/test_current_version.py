"""首页「当前班表」取值规则测试。

场景来源：部署验收时发现 11/5 发布 11/6–11/8 的班表后，首页仍显示「还没有排班数据」。
排班是提前一天生成的，所以未指定日期时必须回退到最近的未来版本。
"""
from __future__ import annotations

import datetime as dt

from app.models import ScheduleVersion


def _add_version(db, name: str, start: dt.date, end: dt.date, status: str = "published") -> ScheduleVersion:
    version = ScheduleVersion(name=name, start_date=start, end_date=end, status=status, version_no=1)
    db.add(version)
    db.commit()
    db.refresh(version)
    return version


def test_current_returns_version_covering_today(client, db):
    today = dt.date.today()
    version = _add_version(db, "本周班表", today - dt.timedelta(days=1), today + dt.timedelta(days=1))

    resp = client.get("/api/schedule/current")
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == version.id


def test_current_falls_back_to_next_future_version(client, db):
    """当天没有班表时，首页展示最近的未来班表（提前排班的真实用法）。"""
    today = dt.date.today()
    version = _add_version(db, "明天起的三天班表", today + dt.timedelta(days=1), today + dt.timedelta(days=3))

    resp = client.get("/api/schedule/current")
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == version.id


def test_current_prefers_earliest_future_version(client, db):
    today = dt.date.today()
    near = _add_version(db, "近的", today + dt.timedelta(days=1), today + dt.timedelta(days=2))
    _add_version(db, "远的", today + dt.timedelta(days=8), today + dt.timedelta(days=10))

    resp = client.get("/api/schedule/current")
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == near.id


def test_current_falls_back_to_latest_history(client, db):
    """没有任何当前/未来班表时，展示最近一期历史班表而不是 404。"""
    today = dt.date.today()
    version = _add_version(db, "上周班表", today - dt.timedelta(days=7), today - dt.timedelta(days=5))

    resp = client.get("/api/schedule/current")
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == version.id


def test_current_with_explicit_start_keeps_strict_semantics(client, db):
    """显式查询某天的班表时不做回退，没有就明确 404。"""
    today = dt.date.today()
    _add_version(db, "明天起", today + dt.timedelta(days=1), today + dt.timedelta(days=2))

    resp = client.get("/api/schedule/current", params={"start": today.isoformat()})
    assert resp.status_code == 404, resp.text


def test_current_404_without_any_version(client):
    resp = client.get("/api/schedule/current")
    assert resp.status_code == 404
    assert "暂无排班版本" in resp.json()["detail"]
