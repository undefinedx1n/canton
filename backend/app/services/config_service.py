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
            # 连休上限：同一员工不得连续休息超过该天数（硬约束，人工锁定/请假豁免）
            "max_consecutive_rest_days": 3,
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
            # 三个主管角色（B2 当值 / A2 早辅 / B1 晚辅）缺失视为严重违规：
            # 权重高于普通缺口，保证"能排就一定排"，但仍是软约束以便不可行时降级而不是整体失败
            "role_gap": 2000,
            # 当值（B2）均衡权重高于岗位轮换/班次连续性：30 天窗口下若权重偏低，
            # 新加的连续性约束会把时间预算吃光，出现 [4,4,5,5,6,6] 这种偏差 2 次的分布。
            "duty_fairness": 90,
            "weekend_fairness": 30,
            "holiday_fairness": 22,
            "peak_fairness": 18,
            "fairness_excess": 130,
            "rest_balance": 14,
            "shift_preference": 9,
            "position_priority": 7,
            "pairing_avoid": 40,
            "pairing_prefer": 10,
            "proficiency": 6,
            "stability": 4,
            "workload_balance": 8,
            # 岗位轮换：重点岗位连续两天同岗高惩罚；其他岗位默认不做（避免 30 天窗口搜索空间爆炸）
            "rotation_consecutive_focus": 55,
            "rotation_consecutive_other": 0,
            # 重点岗位在周期内的人均承担次数均衡（超出人均上限才罚）
            "rotation_balance": 70,
            # 早班辅助 A2 / 晚班辅助 B1 在主管之间的承担次数均衡
            "support_fairness": 40,
            # 每人早/晚班次数差（优先级低于班次连续性，作为跨周期纠偏用）
            "shift_balance": 8,
            # 早/晚次数差允许的容差（天），超过后按超标天数重罚，避免整月只站一个班次
            "shift_balance_tolerance": 3,
            "shift_balance_excess": 55,
            # 班次连续性：相邻两天早晚班不同 → 换班次代价；必须明显高于 rotation_consecutive_focus，
            # 才能让"轮换岗位"发生在同一班次内，而不是连带把班次也换掉
            "shift_switch": 90,
            # 晚班次日直接转早班（中间没有休息）的额外加罚 → "晚转早尽量休息后转"
            "shift_switch_late_to_early": 110,
            # 连休上限降级为软约束时的违规代价（仅在人力密度不足、硬约束无解时启用）
            "max_rest_run": 250,
        },
        "软约束权重（需求文档 17.8 建议默认值）",
    ),
    "rotation_rule": (
        {
            # 需要"平均轮换、尽量不连续"的重点岗位（销售 A3/B3、摄影 B8、三个主管位）
            "focus_positions": ["A3", "B3", "B8", "A2", "B1", "B2"],
            "avoid_consecutive": True,
            "balance_within_period": True,
            "note": "重点岗位连续同岗权重 55，其他岗位 6；轮换均衡按公平周期统计",
        },
        "岗位轮换规则（销售/摄影重点位与主管位优先均衡）",
    ),
    "department_labels": (
        {"sales": "销售", "photography": "摄影"},
        "部门显示名称",
    ),
    "schedule_window_days": ({"days": 3}, "默认排班窗口长度（天）"),
    "solve_chunk_days": (
        {"days": 7, "note": "长窗口（>该天数）按块滚动求解，跨块继承历史与昨日岗位；0 表示一次性求解"},
        "求解分块长度（天）",
    ),
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
