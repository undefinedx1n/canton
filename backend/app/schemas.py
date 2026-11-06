"""Pydantic 输入输出模型。"""
from __future__ import annotations

import datetime as dt
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ORMBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --------------------------------- 认证 ------------------------------------ #
class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    display_name: str = ""
    role: str = "supervisor"


class UserOut(ORMBase):
    id: int
    username: str
    display_name: str
    role: str
    is_active: bool


# --------------------------------- 员工 ------------------------------------ #
class SkillIn(BaseModel):
    position_code: str
    priority: int = 3
    proficiency: int = 3
    allowed: bool = True
    note: str = ""


class SkillOut(ORMBase):
    position_code: str
    priority: int
    proficiency: int
    allowed: bool
    note: str


class EmployeeIn(BaseModel):
    name: str
    employee_no: str | None = None
    # 钉钉用户ID（导出考勤排班表用，可空）
    external_user_id: str | None = None
    employment_type: str = "long_term"
    job_title: str = "普通员工"
    is_supervisor: bool = False
    is_mentor: bool = False
    is_active: bool = True
    shift_preference: Literal["any", "early", "late"] = "any"
    phone: str | None = None
    hire_date: dt.date | None = None
    max_days_per_week: int | None = None
    notes: str = ""
    skills: list[SkillIn] = Field(default_factory=list)


class EmployeeOut(ORMBase):
    id: int
    employee_no: str | None
    external_user_id: str | None = None
    name: str
    employment_type: str
    job_title: str
    is_supervisor: bool
    is_mentor: bool
    is_active: bool
    shift_preference: str
    phone: str | None
    hire_date: dt.date | None
    max_days_per_week: int | None
    notes: str
    skills: list[SkillOut] = Field(default_factory=list)


# --------------------------------- 岗位 ------------------------------------ #
class PositionIn(BaseModel):
    code: str
    name: str
    department: Literal["sales", "photography"] = "sales"
    area: str = "1F"
    shift: Literal["early", "late"] = "early"
    start_time: str = "09:00"
    end_time: str = "17:00"
    allowed_employment_types: list[str] = Field(default_factory=list)
    min_supervisors: int = 0
    supervisor_role_code: str | None = None
    allow_concurrent: bool = False
    is_active: bool = True
    sort_order: int = 0
    color: str = "#64748b"
    notes: str = ""


class PositionOut(ORMBase):
    id: int
    code: str
    name: str
    department: str
    area: str
    shift: str
    start_time: str
    end_time: str
    allowed_employment_types: list[str]
    min_supervisors: int
    supervisor_role_code: str | None
    allow_concurrent: bool
    is_active: bool
    sort_order: int
    color: str
    notes: str


class OperationalRoleIn(BaseModel):
    code: str
    name: str
    shift: Literal["early", "late"] = "late"
    bound_position_code: str | None = None
    counts_extra_slot: bool = False
    is_active: bool = True
    description: str = ""
    color: str = "#7c3aed"


class OperationalRoleOut(ORMBase):
    id: int
    code: str
    name: str
    shift: str
    bound_position_code: str | None
    counts_extra_slot: bool
    is_active: bool
    description: str
    color: str


# ------------------------------- 需求模板 ---------------------------------- #
class DemandTemplateIn(BaseModel):
    day_type: str
    position_code: str
    target_count: int = 0
    min_count: int = 0


class DemandTemplateOut(ORMBase):
    id: int
    day_type: str
    position_code: str
    target_count: int
    min_count: int


class DailyDemandIn(BaseModel):
    work_date: dt.date
    position_code: str
    target_count: int = 0
    min_count: int = 0
    note: str = ""


class DailyDemandOut(ORMBase):
    work_date: dt.date
    position_code: str
    target_count: int
    min_count: int
    note: str


class CalendarDayIn(BaseModel):
    work_date: dt.date
    day_type: str = "weekday"
    is_holiday: bool = False
    is_peak: bool = False
    label: str = ""
    expected_traffic: str = "normal"
    note: str = ""


class CalendarDayOut(ORMBase):
    work_date: dt.date
    day_type: str
    is_holiday: bool
    is_peak: bool
    label: str
    expected_traffic: str
    note: str


# ------------------------------- 请假申请 ---------------------------------- #
class AvailabilityIn(BaseModel):
    employee_id: int
    request_type: Literal["leave", "rest", "position", "unavailable"] = "leave"
    start_date: dt.date
    end_date: dt.date
    shift: Literal["early", "late"] | None = None
    target_position_code: str | None = None
    reason: str = ""


class AvailabilityReview(BaseModel):
    status: Literal["approved", "rejected", "pending"]
    reviewer: str = ""


class AvailabilityOut(ORMBase):
    id: int
    employee_id: int
    request_type: str
    start_date: dt.date
    end_date: dt.date
    shift: str | None
    target_position_code: str | None
    reason: str
    status: str
    reviewer: str
    reviewed_at: dt.datetime | None
    employee: EmployeeOut | None = None


# ------------------------------- 搭班偏好 ---------------------------------- #
class PairingIn(BaseModel):
    employee_a_id: int
    employee_b_id: int
    preference_type: Literal["prefer", "avoid"] = "prefer"
    scope: Literal["same_shift", "same_position", "same_day"] = "same_shift"
    weight: float = 1.0
    start_date: dt.date | None = None
    end_date: dt.date | None = None
    note: str = ""
    is_active: bool = True


class PairingOut(ORMBase):
    id: int
    employee_a_id: int
    employee_b_id: int
    preference_type: str
    scope: str
    weight: float
    start_date: dt.date | None
    end_date: dt.date | None
    note: str
    is_active: bool


# --------------------------------- 排班 ------------------------------------ #
class GenerateRequest(BaseModel):
    start_date: dt.date
    days: int = 3
    # 是否沿用最近草稿中被手动锁定的安排
    keep_locks: bool = True
    # 生成模式：strict 缺岗即失败；draft 允许带缺口草稿（配置决定默认值）
    mode: Literal["strict", "draft"] = "draft"
    version_name: str = ""


class AssignmentOut(ORMBase):
    id: int
    work_date: dt.date
    employee_id: int
    position_code: str
    shift: str
    role_code: str | None
    is_locked: bool
    source: str
    note: str
    employee_name: str = ""
    employee_type: str = ""
    is_supervisor: bool = False


class AssignmentIn(BaseModel):
    work_date: dt.date
    employee_id: int
    position_code: str
    shift: Literal["early", "late"]
    role_code: str | None = None
    is_locked: bool = False
    note: str = ""


class AssignmentPatch(BaseModel):
    position_code: str | None = None
    shift: Literal["early", "late"] | None = None
    role_code: str | None = None
    is_locked: bool | None = None
    note: str | None = None


class SwapIn(BaseModel):
    """同一天两名员工互换岗位/班次（调班）。"""

    work_date: dt.date
    employee_a: int
    employee_b: int


class VersionOut(ORMBase):
    id: int
    name: str
    start_date: dt.date
    end_date: dt.date
    status: str
    version_no: int
    parent_version_id: int | None
    published_at: dt.datetime | None
    published_by: str
    change_note: str
    created_by: str
    created_at: dt.datetime


class VersionDetailOut(VersionOut):
    assignments: list[AssignmentOut] = Field(default_factory=list)
    coverage: list[dict[str, Any]] = Field(default_factory=list)
    violations: list[dict[str, Any]] = Field(default_factory=list)


class PublishRequest(BaseModel):
    change_note: str = ""


class RunOut(ORMBase):
    id: int
    start_date: dt.date
    end_date: dt.date
    status: str
    solver: str
    objective_value: float | None
    objective_breakdown: dict[str, Any]
    diagnostics: dict[str, Any]
    message: str
    duration_ms: int
    created_by: str
    created_at: dt.datetime


class SettingIn(BaseModel):
    key: str
    value: dict[str, Any]
    description: str = ""


class AuditOut(ORMBase):
    id: int
    actor: str
    action: str
    entity: str
    entity_id: str
    summary: str
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    created_at: dt.datetime
