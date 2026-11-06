"""ORM 模型：严格对应需求文档第 11 节的数据模型建议。

设计要点：
- 员工身份（是否主管）是独立布尔字段，颜色/单元格样式只作为导入线索，不参与业务判断。
- 岗位资格（employee_position_skills）是硬约束来源；优先级/熟练度是软优化输入。
- 运营角色（晚班当值主管 / 早班辅助主管 / 晚班辅助主管）以 assignment.role_code 显式记录，
  不依赖颜色；是否额外占用名额由 OperationalRole.counts_extra_slot 配置决定。
- 排班数据按版本（ScheduleVersion）快照，历史不会被覆盖。
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


# --------------------------------------------------------------------------- #
# 账号与权限
# --------------------------------------------------------------------------- #
class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(64), default="")
    # admin = 全部权限；supervisor = 排班与发布
    role: Mapped[str] = mapped_column(String(32), default="supervisor")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)


# --------------------------------------------------------------------------- #
# 员工档案
# --------------------------------------------------------------------------- #
class Employee(Base):
    __tablename__ = "employees"

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_no: Mapped[str | None] = mapped_column(String(32), unique=True, nullable=True)
    # 钉钉成员用户ID，仅用于「排班表导出」对接考勤后台，不参与排班逻辑
    external_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    name: Mapped[str] = mapped_column(String(64), index=True)
    # long_term / part_time / intern / other —— 类型可维护（见 EmployeeTypeOption）
    employment_type: Mapped[str] = mapped_column(String(32), default="long_term", index=True)
    # 职务文本：主管 / 导师 / 普通员工 ...（仅展示）
    job_title: Mapped[str] = mapped_column(String(64), default="普通员工")
    # 关键字段：是否主管，独立于职务与颜色
    is_supervisor: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    is_mentor: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    # any / early / late
    shift_preference: Mapped[str] = mapped_column(String(16), default="any")
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    hire_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    # 排班能力上界，None 表示不限制（需求文档未强制，作为可选约束）
    max_days_per_week: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    skills: Mapped[list[EmployeePositionSkill]] = relationship(
        back_populates="employee", cascade="all, delete-orphan", lazy="selectin"
    )


class EmployeeTypeOption(Base):
    """员工类型可配置，避免把"长期员工/兼职/实习生"写死。"""

    __tablename__ = "employee_types"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(64))
    # 是否计入"月休 8 天"等长期员工规则
    counts_as_formal: Mapped[bool] = mapped_column(Boolean, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class EmployeePositionSkill(Base):
    __tablename__ = "employee_position_skills"
    __table_args__ = (UniqueConstraint("employee_id", "position_code", name="uq_emp_pos"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), index=True)
    position_code: Mapped[str] = mapped_column(String(16), index=True)
    # 数字越小越优先（1 为最优先）
    priority: Mapped[int] = mapped_column(Integer, default=3)
    # 1-5 熟练度，用于旺季高客流岗位优先分配
    proficiency: Mapped[int] = mapped_column(Integer, default=3)
    # False = 明确禁止（优先于资格，作为硬性禁止）
    allowed: Mapped[bool] = mapped_column(Boolean, default=True)
    note: Mapped[str] = mapped_column(Text, default="")

    employee: Mapped[Employee] = relationship(back_populates="skills")


# --------------------------------------------------------------------------- #
# 岗位与运营角色
# --------------------------------------------------------------------------- #
class Position(Base):
    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(16), unique=True, index=True)  # A1 / B2 / AK ...
    name: Mapped[str] = mapped_column(String(64))
    department: Mapped[str] = mapped_column(String(16), default="sales")  # sales / photography
    area: Mapped[str] = mapped_column(String(32), default="1F")  # 1F / 107F / 108F
    shift: Mapped[str] = mapped_column(String(16), default="early")  # early / late
    start_time: Mapped[str] = mapped_column(String(8), default="09:00")
    end_time: Mapped[str] = mapped_column(String(8), default="17:00")
    # 允许的员工类型；空数组表示不限制
    allowed_employment_types: Mapped[list] = mapped_column(JSON, default=list)
    # 该岗位至少需要的主管人数（0 表示不要求）
    min_supervisors: Mapped[int] = mapped_column(Integer, default=0)
    # 若该岗位的一名主管兼任运营角色，绑定角色代码（B2 -> duty_supervisor 等）
    supervisor_role_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # 是否可被跨岗兼任（首期默认 False）
    allow_concurrent: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    color: Mapped[str] = mapped_column(String(16), default="#64748b")
    notes: Mapped[str] = mapped_column(Text, default="")


class OperationalRole(Base):
    """主管运营角色。是否额外占用岗位名额可配置（需求文档 17.3）。"""

    __tablename__ = "operational_roles"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(64))
    shift: Mapped[str] = mapped_column(String(16), default="late")
    bound_position_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    counts_extra_slot: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    description: Mapped[str] = mapped_column(Text, default="")
    color: Mapped[str] = mapped_column(String(16), default="#7c3aed")


# --------------------------------------------------------------------------- #
# 需求模板与每日需求
# --------------------------------------------------------------------------- #
class DemandTemplate(Base):
    """日期类型（普通工作日/周末节假日/旺季特殊日）× 岗位 -> 目标与最低人数。"""

    __tablename__ = "demand_templates"
    __table_args__ = (UniqueConstraint("day_type", "position_code", name="uq_tpl_day_pos"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    day_type: Mapped[str] = mapped_column(String(32), default="weekday", index=True)
    position_code: Mapped[str] = mapped_column(String(16), index=True)
    target_count: Mapped[int] = mapped_column(Integer, default=0)
    min_count: Mapped[int] = mapped_column(Integer, default=0)


class DailyDemand(Base):
    """按日逐岗覆盖值；未设置的岗位回落到模板。"""

    __tablename__ = "daily_demands"
    __table_args__ = (UniqueConstraint("work_date", "position_code", name="uq_day_pos"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    work_date: Mapped[dt.date] = mapped_column(Date, index=True)
    position_code: Mapped[str] = mapped_column(String(16), index=True)
    target_count: Mapped[int] = mapped_column(Integer, default=0)
    min_count: Mapped[int] = mapped_column(Integer, default=0)
    note: Mapped[str] = mapped_column(Text, default="")


class CalendarDay(Base):
    __tablename__ = "calendar_days"

    id: Mapped[int] = mapped_column(primary_key=True)
    work_date: Mapped[dt.date] = mapped_column(Date, unique=True, index=True)
    # weekday / weekend / holiday / peak / special
    day_type: Mapped[str] = mapped_column(String(32), default="weekday", index=True)
    is_holiday: Mapped[bool] = mapped_column(Boolean, default=False)
    is_peak: Mapped[bool] = mapped_column(Boolean, default=False)
    label: Mapped[str] = mapped_column(String(64), default="")
    expected_traffic: Mapped[str] = mapped_column(String(16), default="normal")  # low/normal/high
    note: Mapped[str] = mapped_column(Text, default="")


# --------------------------------------------------------------------------- #
# 请假 / 不可用时间 / 申请
# --------------------------------------------------------------------------- #
class AvailabilityRequest(Base):
    __tablename__ = "availability_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), index=True)
    # leave = 请假；rest = 休息申请；position = 岗位申请；unavailable = 特定时段不可用
    request_type: Mapped[str] = mapped_column(String(32), default="leave")
    start_date: Mapped[dt.date] = mapped_column(Date, index=True)
    end_date: Mapped[dt.date] = mapped_column(Date, index=True)
    # 仅针对某班次生效时可填 early/late，空表示全天
    shift: Mapped[str | None] = mapped_column(String(16), nullable=True)
    target_position_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    # pending / approved / rejected
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    reviewer: Mapped[str] = mapped_column(String(64), default="")
    reviewed_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)

    employee: Mapped[Employee] = relationship(lazy="joined")


# --------------------------------------------------------------------------- #
# 搭班偏好
# --------------------------------------------------------------------------- #
class PairingPreference(Base):
    __tablename__ = "pairing_preferences"

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_a_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), index=True)
    employee_b_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), index=True)
    # prefer = 希望同班；avoid = 避免同班
    preference_type: Mapped[str] = mapped_column(String(16), default="prefer")
    # same_shift = 同班次；same_position = 同岗位；same_day = 同日
    scope: Mapped[str] = mapped_column(String(32), default="same_shift")
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    start_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


# --------------------------------------------------------------------------- #
# 排班运行、版本、分配
# --------------------------------------------------------------------------- #
class ScheduleVersion(Base):
    __tablename__ = "schedule_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    start_date: Mapped[dt.date] = mapped_column(Date, index=True)
    end_date: Mapped[dt.date] = mapped_column(Date, index=True)
    # draft / published / archived
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)
    version_no: Mapped[int] = mapped_column(Integer, default=1)
    parent_version_id: Mapped[int | None] = mapped_column(ForeignKey("schedule_versions.id"), nullable=True)
    published_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    published_by: Mapped[str] = mapped_column(String(64), default="")
    change_note: Mapped[str] = mapped_column(Text, default="")
    run_id: Mapped[int | None] = mapped_column(ForeignKey("schedule_runs.id"), nullable=True)
    created_by: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)

    assignments: Mapped[list[ScheduleAssignment]] = relationship(
        back_populates="version", cascade="all, delete-orphan", lazy="selectin"
    )


class ScheduleRun(Base):
    __tablename__ = "schedule_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    start_date: Mapped[dt.date] = mapped_column(Date)
    end_date: Mapped[dt.date] = mapped_column(Date)
    # pending / running / done / infeasible / error
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    solver: Mapped[str] = mapped_column(String(32), default="cp-sat")
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    objective_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    objective_breakdown: Mapped[dict] = mapped_column(JSON, default=dict)
    diagnostics: Mapped[dict] = mapped_column(JSON, default=dict)
    message: Mapped[str] = mapped_column(Text, default="")
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)


class ScheduleAssignment(Base):
    __tablename__ = "schedule_assignments"
    __table_args__ = (
        UniqueConstraint("version_id", "work_date", "employee_id", name="uq_ver_day_emp"),
        Index("ix_assign_ver_day", "version_id", "work_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("schedule_versions.id", ondelete="CASCADE"), index=True)
    work_date: Mapped[dt.date] = mapped_column(Date, index=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    position_code: Mapped[str] = mapped_column(String(16), index=True)
    shift: Mapped[str] = mapped_column(String(16), default="early")
    # 运营角色代码（duty_supervisor / early_support / late_support），普通排班为 None
    role_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False)
    # auto / manual
    source: Mapped[str] = mapped_column(String(16), default="auto")
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)

    version: Mapped[ScheduleVersion] = relationship(back_populates="assignments")
    employee: Mapped[Employee] = relationship(lazy="joined")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    actor: Mapped[str] = mapped_column(String(64), default="")
    action: Mapped[str] = mapped_column(String(64), index=True)
    entity: Mapped[str] = mapped_column(String(64), default="")
    entity_id: Mapped[str] = mapped_column(String(64), default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    before: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    after: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now, index=True)


class AppSetting(Base):
    """可配置的周期口径、优化权重、运营角色与名额关系等（需求文档 17 待确认项）。"""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)
    description: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now, onupdate=_now)
