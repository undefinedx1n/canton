"""数据库引擎与会话。SQLite 本地开箱即用，生产切换 PostgreSQL 仅需改 DATABASE_URL。"""
from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


connect_args = {}
if settings.database_url.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

engine = create_engine(
    settings.database_url,
    connect_args=connect_args,
    pool_pre_ping=True,
    future=True,
)

if settings.database_url.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def _sqlite_pragma(dbapi_connection, _record):  # pragma: no cover - sqllite 专用
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# 启动时补充新增列：create_all 只建缺失的表，不会给已有表加列。
# 这里只做「缺了就加」的幂等操作，避免为了加一个可空列而强依赖 alembic 与线上库的版本对齐。
_ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    # (表名, 列名, DDL 片段)
    ("employees", "external_user_id", "VARCHAR(64)"),
)


def ensure_schema() -> list[str]:
    """补齐已上线库缺少的可空列，返回本次新增的列名列表。"""
    from sqlalchemy import inspect, text

    added: list[str] = []
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table, column, ddl in _ADDED_COLUMNS:
            if table not in tables:
                continue
            existing = {c["name"] for c in inspector.get_columns(table)}
            if column in existing:
                continue
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
            added.append(f"{table}.{column}")
    return added
