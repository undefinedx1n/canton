"""系统配置：待确认业务规则全部落成可调整配置（需求文档第 17 节）。"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ..models import AppSetting

DEFAULT_SETTINGS: dict[str, tuple[dict[str, Any], str]] = {
    "rest_rule": (
        {
            "period_type": "calendar_month",  # calendar_month / rolling_28 / cycle
            "rest_days": 8,
            "cycle_start_day": 1,
            "apply_to_formal_only": True,
            "holiday_work_compensated": False,
        },
        "长期员工休息周期口径（需求文档 17.1，待确认，可配置）",
    ),
    "fairness_period": (
        {"period_type": "calendar_month", "cycle_start_day": 1, "compare_window_days": 60},
        "主管当值公平统计周期（需求文档 17.2）",
    ),
    "role_slot_rule": (
        {
            "counts_extra_slot": False,
            "allow_supervisor_dual_position": False,
            "note": "运营角色默认视作岗位分配的标记，不额外占用名额",
        },
        "运营角色与岗位名额关系（需求文档 17.3）",
    ),
    "gap_policy": (
        {"allow_gap_draft": True, "default_mode": "draft"},
        "人数不足时是否允许带缺口草稿（需求文档 17.5）",
    ),
    "pending_leave_policy": (
        {"treat_pending_as_unavailable": False},
        "未批准申请是否阻止排班（需求文档 17.9，默认仅批准后作为硬约束）",
    ),
    "normal_staff_definition": (
        {
            "counts_part_time_as_normal": True,
            "counts_intern_as_normal": False,
            "note": "销售岗位需求中「普通员工」的类型口径（需求文档 17.4）",
        },
        "普通员工类型定义（需求文档 17.4）",
    ),
    "solver_weights": (
        {
            "coverage_target": 60,
            "gap_penalty": 400,
            "duty_fairness": 45,
            "weekend_fairness": 30,
            "holiday_fairness": 22,
            "peak_fairness": 18,
            "rest_balance": 14,
            "shift_preference": 9,
            "position_priority": 7,
            "pairing_avoid": 40,
            "pairing_prefer": 10,
            "proficiency": 6,
            "stability": 4,
            "workload_balance": 8,
        },
        "软约束权重（需求文档 17.8 建议默认值）",
    ),
    "department_labels": (
        {"sales": "销售", "photography": "摄影"},
        "部门显示名称",
    ),
    "schedule_window_days": ({"days": 3}, "默认排班窗口长度（天）"),
}


def _row(db: Session, key: str) -> AppSetting | None:
    return db.get(AppSetting, key)


def ensure_defaults(db: Session) -> None:
    changed = False
    for key, (value, desc) in DEFAULT_SETTINGS.items():
        if db.get(AppSetting, key) is None:
            db.add(AppSetting(key=key, value=value, description=desc))
            changed = True
    if changed:
        db.commit()


def get_setting(db: Session, key: str) -> dict[str, Any]:
    row = _row(db, key)
    if row is None:
        return DEFAULT_SETTINGS.get(key, ({}, ""))[0]
    return row.value or {}


def get_all_settings(db: Session) -> list[dict[str, Any]]:
    ensure_defaults(db)
    rows = db.query(AppSetting).order_by(AppSetting.key).all()
    return [
        {
            "key": r.key,
            "value": r.value,
            "description": r.description,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        }
        for r in rows
    ]


def set_setting(db: Session, key: str, value: dict[str, Any], description: str = "") -> AppSetting:
    row = _row(db, key)
    if row is None:
        row = AppSetting(key=key, value=value, description=description or DEFAULT_SETTINGS.get(key, ({}, ""))[1])
        db.add(row)
    else:
        row.value = value
        if description:
            row.description = description
    db.commit()
    db.refresh(row)
    return row


def weights(db: Session) -> dict[str, float]:
    base = dict(DEFAULT_SETTINGS["solver_weights"][0])
    base.update(get_setting(db, "solver_weights"))
    return {k: float(v) for k, v in base.items()}
