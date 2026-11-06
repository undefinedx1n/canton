"""FastAPI 应用入口。"""
from __future__ import annotations

import datetime as dt

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from .config import BASE_DIR, settings
from .db import Base, SessionLocal, engine, ensure_schema
from .models import User
from .routers import audit, auth, availability, demands, employees, excel, positions, scheduler, settings_router, stats
from .security import hash_password
from .services.config_service import ensure_defaults

app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    description="游乐园销售与摄影团队排班自动化系统（MVP）",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for module in (auth, employees, positions, demands, availability, scheduler, stats, settings_router, excel, audit):
    app.include_router(module.router)


@app.on_event("startup")
def on_startup() -> None:
    Base.metadata.create_all(bind=engine)
    ensure_schema()
    db: Session = SessionLocal()
    try:
        ensure_defaults(db)
        if db.query(User).count() == 0:
            db.add(
                User(
                    username=settings.bootstrap_admin_username,
                    password_hash=hash_password(settings.bootstrap_admin_password),
                    display_name="系统管理员",
                    role="admin",
                )
            )
            db.commit()
    finally:
        db.close()


@app.get("/api/health")
def health():
    return {"status": "ok", "app": settings.app_name, "time": dt.datetime.now().isoformat(), "solver": _solver_name()}


def _solver_name() -> str:
    from .services.solver import ORTOOLS_AVAILABLE

    return "cp-sat" if ORTOOLS_AVAILABLE else "heuristic"


@app.get("/api/meta")
def meta():
    """前端启动时读取的元信息（版本、口径说明）。"""
    return {
        "app_name": settings.app_name,
        "solver": _solver_name(),
        "allow_gap_draft": settings.allow_gap_draft,
        "notes": {
            "headcount": "当日实际被安排上班的人数，每人每日只计一次；休息/未排班不计入。",
            "role": "运营角色是排班记录上的标记，默认不额外占用岗位名额（可在系统配置中调整）。",
            "rest": "长期员工月休目标为配置项，周期口径见系统配置。",
        },
    }


# 可选：若前端已构建为静态站点（frontend/out），由同一端口托管，便于单机演示/部署
_static_dir = BASE_DIR / "frontend" / "out"
if _static_dir.exists() and (_static_dir / "index.html").exists():
    app.mount("/", StaticFiles(directory=str(_static_dir), html=True), name="frontend")
else:
    @app.get("/")
    def root():
        return JSONResponse(
            {
                "app": settings.app_name,
                "docs": "/docs",
                "hint": "前端开发模式请运行 frontend 的 npm run dev；或将 frontend 构建产物放到 frontend/out 由本服务托管。",
            }
        )
