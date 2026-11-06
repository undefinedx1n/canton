"""钉钉考勤「排班表」导出。

对接人力上交的既有模板（柯达广州塔排班表.xls）：标题 / 提示 / 排班信息 / 表头 / 员工 × 日期，
格子里放**班次名**（如 `A108柜台`）或 `休`，未排班留空。

设计约束：
- 只读数据（版本、排班、员工、已批准请假），**不参与求解**，不改动任何排班逻辑。
- 岗位 → 班次名的词表放在系统配置里（`dingtalk_export`），岗位改名/新增只改配置不改代码。
- 输出 .xlsx；钉钉导入只认格子里的文本与「排班信息」整行，样式仅影响观感。
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from sqlalchemy.orm import Session

from ..models import AvailabilityRequest, Employee, ScheduleVersion
from .config_service import DEFAULT_SETTINGS, get_setting

SHEET_NAME = "排班表"
WEEKDAY_CN = "一二三四五六日"

TITLE_FONT = Font(name="新宋体", size=20, bold=True)
NOTICE_FONT = Font(name="新宋体", size=14, bold=True)
INFO_FONT = Font(name="新宋体", size=14, bold=True)
HEAD_FONT = Font(name="宋体", size=12, bold=True)
BODY_FONT = Font(name="宋体", size=12)
WARN_FONT = Font(name="宋体", size=12, color="B45309")

HEAD_FILL = PatternFill("solid", fgColor="DDEBF7")
WARN_FILL = PatternFill("solid", fgColor="FFF2CC")
THIN = Side(style="thin", color="BFBFBF")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
LEFT_WRAP = Alignment(horizontal="left", vertical="center", wrap_text=True)

COL_WIDTHS = (13.0, 13.0, 8.83, 11.33)  # 部门 / 用户ID / 工号 / 姓名
DATE_COL_WIDTH = 13.66
ROW_HEIGHT = 20.0


def _cfg(db: Session) -> dict[str, Any]:
    """合并默认配置与库里保存的配置（允许只存部分字段）。"""
    base = dict(DEFAULT_SETTINGS["dingtalk_export"][0])
    stored = get_setting(db, "dingtalk_export") or {}
    if not isinstance(stored, dict):
        return base
    merged = {**base, **stored}
    tokens = dict(base.get("position_tokens") or {})
    tokens.update(stored.get("position_tokens") or {})
    merged["position_tokens"] = tokens
    return merged


def _day_cell_text(
    *,
    has_assignment: bool,
    token: str | None,
    on_leave: bool,
    active: bool,
    cfg: dict[str, Any],
) -> str:
    if not active:
        return ""
    if on_leave:
        return str(cfg.get("leave_token") or cfg.get("rest_token") or "休")
    if has_assignment:
        return token or ""
    return str(cfg.get("rest_token") or "休")


def build_workbook(db: Session, version: ScheduleVersion) -> tuple[Workbook, list[str]]:
    """生成排班表工作簿，返回 (workbook, 警告信息列表)。"""
    cfg = _cfg(db)
    position_tokens: dict[str, str] = dict(cfg.get("position_tokens") or {})
    rest_token = str(cfg.get("rest_token") or "休")
    warnings: list[str] = []

    dates = [version.start_date + dt.timedelta(days=i) for i in range((version.end_date - version.start_date).days + 1)]
    by_key = {(a.work_date, a.employee_id): a for a in version.assignments}

    # 已批准的整天请假/休息 → 该日写 rest_token
    leave_days: set[tuple[dt.date, int]] = set()
    reqs = (
        db.query(AvailabilityRequest)
        .filter(
            AvailabilityRequest.status == "approved",
            AvailabilityRequest.request_type.in_(["leave", "rest", "unavailable"]),
        )
        .all()
    )
    for r in reqs:
        if r.shift:  # 只影响某班次，不整天空白
            continue
        cur = max(r.start_date, dates[0])
        end = min(r.end_date, dates[-1])
        while cur <= end:
            leave_days.add((cur, r.employee_id))
            cur += dt.timedelta(days=1)

    employees = db.query(Employee).filter(Employee.is_active.is_(True)).all()
    employees.sort(key=lambda e: (not e.is_supervisor, e.employment_type, e.id))

    wb = Workbook()
    ws = wb.active
    ws.title = SHEET_NAME
    ncols = 4 + len(dates)

    # 行 1：标题
    ws.cell(row=1, column=1, value=str(cfg.get("title") or "柯达广州塔排班表"))
    ws.cell(row=1, column=1).font = TITLE_FONT
    ws.cell(row=1, column=1).alignment = LEFT_WRAP
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=min(16, ncols))
    ws.row_dimensions[1].height = 30

    # 行 2：提示
    ws.cell(row=2, column=1, value=str(cfg.get("notice") or "注意：时间必须严格按照现有格式，否则无法识别到钉钉考勤后台"))
    ws.cell(row=2, column=1).font = NOTICE_FONT
    ws.cell(row=2, column=1).alignment = LEFT_WRAP
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=ncols)
    ws.row_dimensions[2].height = 24

    # 行 3：排班信息（整行原样保留，钉钉按这一行识别班次）
    ws.cell(row=3, column=1, value=str(cfg.get("shift_info") or ""))
    ws.cell(row=3, column=1).font = INFO_FONT
    ws.cell(row=3, column=1).alignment = LEFT_WRAP
    ws.merge_cells(start_row=3, start_column=1, end_row=3, end_column=ncols)
    ws.row_dimensions[3].height = 72 if len(dates) > 12 else 48

    # 行 4：表头
    headers = ["部门", "用户ID", "工号", "姓名"]
    for i, h in enumerate(headers, start=1):
        cell = ws.cell(row=4, column=i, value=h)
        cell.font = HEAD_FONT
        cell.fill = HEAD_FILL
        cell.alignment = CENTER
        cell.border = BORDER
    for j, d in enumerate(dates, start=5):
        cell = ws.cell(row=4, column=j, value=f"{d.year}/{d.month}/{d.day}\n周{WEEKDAY_CN[d.weekday()]}")
        cell.font = BODY_FONT
        cell.fill = HEAD_FILL
        cell.alignment = CENTER
        cell.border = BORDER
    ws.row_dimensions[4].height = 32

    department = str(cfg.get("department") or "柯达广州塔")
    missing_ids: list[str] = []
    unmapped: set[str] = set()

    r = 5
    for emp in employees:
        ws.cell(row=r, column=1, value=department)
        ws.cell(row=r, column=2, value=emp.external_user_id or "")
        ws.cell(row=r, column=3, value=emp.employee_no or "")
        ws.cell(row=r, column=4, value=emp.name)
        for c in range(1, 5):
            cell = ws.cell(row=r, column=c)
            cell.font = BODY_FONT
            cell.alignment = CENTER
            cell.border = BORDER
        if not emp.external_user_id:
            missing_ids.append(emp.name)
            ws.cell(row=r, column=2).fill = WARN_FILL

        for j, d in enumerate(dates, start=5):
            a = by_key.get((d, emp.id))
            token = None
            if a:
                token = position_tokens.get(a.position_code)
                if token is None:
                    unmapped.add(a.position_code)
                    token = a.position_code
            text = _day_cell_text(
                has_assignment=bool(a),
                token=token,
                on_leave=(d, emp.id) in leave_days,
                active=True,
                cfg={"rest_token": rest_token, "leave_token": cfg.get("leave_token")},
            )
            cell = ws.cell(row=r, column=j, value=text)
            cell.font = BODY_FONT
            cell.alignment = CENTER
            cell.border = BORDER
        ws.row_dimensions[r].height = ROW_HEIGHT
        r += 1

    # 列宽
    for i, w in enumerate(COL_WIDTHS, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    for j in range(5, ncols + 1):
        ws.column_dimensions[get_column_letter(j)].width = DATE_COL_WIDTH
    ws.freeze_panes = ws.cell(row=5, column=5)

    if missing_ids:
        warnings.append(f"{len(missing_ids)} 人缺钉钉用户ID（导入会失败）：" + "、".join(missing_ids[:10]))
    if unmapped:
        warnings.append("以下岗位没有配置钉钉班次名，已按岗位代码原样输出：" + "、".join(sorted(unmapped)))
    return wb, warnings


def filename_for(version: ScheduleVersion) -> str:
    return f"柯达广州塔排班表{version.start_date:%Y%m%d}-{version.end_date:%Y%m%d}.xlsx"
