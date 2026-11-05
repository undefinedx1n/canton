"""pytest 夹具：独立临时 SQLite 数据库 + FastAPI TestClient。

测试数据全部为 seed.py 生成的**虚构**员工（31 人），不读取任何生产库或真实凭证；
每个测试用 tmp_path 下的独立 SQLite 文件，互不影响，也不触碰 data/schedule.db。
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.services.config_service import ensure_defaults  # noqa: E402
from app.services.seed import seed_all  # noqa: E402
from app.models import DailyDemand, Employee, Position  # noqa: E402

from tests.support import set_day  # noqa: E402

WINDOW_DAYS = 3


@pytest.fixture()
def db(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'test.db').as_posix()}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = SessionLocal()
    ensure_defaults(session)
    seed_all(session)
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def client(db):
    def override():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        resp = c.post("/api/auth/login", json={"username": "admin", "password": "admin123456"})
        assert resp.status_code == 200, resp.text
        token = resp.json()["access_token"]
        c.headers.update({"Authorization": f"Bearer {token}"})
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def start() -> dt.date:
    return dt.date.today()


@pytest.fixture()
def window(db, start):
    """把未来三天统一成"工作日 + 默认需求"，让每个测试的输入完全一致。

    真实日期里周末/节假日的需求模板不同，若不抹平，测试结果会随运行日期漂移，
    无法重复。这里清掉逐日覆盖、并把日历标签固定为 weekday。
    """
    dates = [start + dt.timedelta(days=i) for i in range(WINDOW_DAYS)]
    # 清掉逐日覆盖 -> 回到 weekday 模板；再把日历标签固定为工作日
    db.query(DailyDemand).filter(DailyDemand.work_date.in_(dates)).delete(synchronize_session=False)
    for d in dates:
        set_day(db, d, "weekday")
    db.commit()
    return dates


@pytest.fixture()
def weekend(db, window):
    """三天窗口有 1 天被标记为周末，需求仍按工作日口径，避免人力不足干扰公平性断言。"""
    sat = next(d for d in window if d.weekday() >= 5) if any(d.weekday() >= 5 for d in window) else window[-1]
    set_day(db, sat, "weekend")
    db.commit()
    return sat


@pytest.fixture()
def employees(db):
    return {e.name: e for e in db.query(Employee).all()}


@pytest.fixture()
def positions(db):
    return {p.code: p for p in db.query(Position).all()}
