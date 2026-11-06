from __future__ import annotations

import datetime as dt
import io
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_current_user, write_audit
from ..models import Employee, EmployeePositionSkill, EmployeeTypeOption, OperationalRole, Position, ScheduleVersion, User
from ..schemas import EmployeeIn
from ..services.matrix import CELL_LEAVE, CELL_REST, CELL_WORK, WEEKDAY_CN, version_matrix
from ..services.dingtalk_export import build_workbook as build_dingtalk_workbook
from ..services.dingtalk_export import filename_for as dingtalk_filename
from ..services.stats import version_stats
from ..services.validation import validate_assignments

router = APIRouter(prefix="/api", tags=["excel"], dependencies=[Depends(get_current_user)])

THIN = Side(style="thin", color="D1D5DB")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
HEADER_FILL = PatternFill("solid", fgColor="0F172A")
HEADER_FONT = Font(color="FFFFFF", bold=True, size=11)
EARLY_FILL = PatternFill("solid", fgColor="DBEAFE")
LATE_FILL = PatternFill("solid", fgColor="FFE4E6")
SUP_FONT = Font(bold=True, color="9F1239")
CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)


def _style_header(ws, row: int, ncols: int):
    for c in range(1, ncols + 1):
        cell = ws.cell(row=row, column=c)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = CENTER
        cell.border = BORDER


MATRIX_DATE_FONT = Font(bold=True, color="C00000", size=10)
MATRIX_DUTY_FONT = Font(bold=True, color="C00000", size=10)
MATRIX_SUPPORT_FONT = Font(bold=True, color="7030A0", size=10)
MATRIX_NORMAL_FONT = Font(color="1F2937", size=10)
MATRIX_REST_FONT = Font(color="9CA3AF", size=10)
MATRIX_LEAVE_FONT = Font(color="B45309", size=10)
SUPERVISOR_ROW_FILL = PatternFill("solid", fgColor="FCE4D6")
REST_ROW_FILL = PatternFill("solid", fgColor="F3F4F6")


def _write_matrix_sheet(ws, matrix: dict) -> None:
    """按线下习惯写「姓名 × 日期」表：B2 红、A2/B1 紫、V 表示休息。

    保持与主管原来手工维护的 Excel 完全相同的行列结构，方便直接把导出的表
    贴回原工作簿，或从原工作簿导入。
    """
    days = matrix["days"]
    people = matrix["rows"]

    ws.cell(row=1, column=1, value="日期").font = MATRIX_DATE_FONT
    ws.cell(row=2, column=1, value="星期＼姓名").font = Font(bold=True, size=10)
    for c in (ws.cell(row=1, column=1), ws.cell(row=2, column=1)):
        c.alignment = CENTER
        c.border = BORDER

    for j, day in enumerate(days, start=2):
        c1 = ws.cell(row=1, column=j, value=dt.date.fromisoformat(day["date"]))
        c1.number_format = "yyyy-mm-dd"
        c1.font = MATRIX_DATE_FONT
        c1.alignment = CENTER
        c1.border = BORDER
        c2 = ws.cell(row=2, column=j, value=f"星期{WEEKDAY_CN[dt.date.fromisoformat(day['date']).weekday()]}")
        c2.font = Font(size=9, color="374151")
        c2.alignment = CENTER
        c2.border = BORDER

    r = 3
    for person in people:
        name_cell = ws.cell(row=r, column=1, value=person["name"])
        name_cell.font = Font(bold=True, size=10)
        name_cell.alignment = Alignment(horizontal="left", vertical="center")
        name_cell.border = BORDER
        if person["is_supervisor"]:
            name_cell.fill = SUPERVISOR_ROW_FILL

        for j, day in enumerate(days, start=2):
            cell_data = person["cells"][day["date"]]
            cell = ws.cell(row=r, column=j)
            kind = cell_data["kind"]
            if kind == CELL_WORK:
                cell.value = cell_data["position_code"]
                role = cell_data.get("role_code")
                if role == "duty_supervisor":
                    cell.font = MATRIX_DUTY_FONT
                elif role:
                    cell.font = MATRIX_SUPPORT_FONT
                else:
                    cell.font = MATRIX_NORMAL_FONT
            elif kind == CELL_LEAVE:
                cell.value = "假"
                cell.font = MATRIX_LEAVE_FONT
                cell.fill = PatternFill("solid", fgColor="FFF7ED")
            elif kind == CELL_REST:
                cell.value = "V"
                cell.font = MATRIX_REST_FONT
                if person["is_supervisor"]:
                    cell.fill = SUPERVISOR_ROW_FILL
            else:
                cell.value = "-"
                cell.font = MATRIX_REST_FONT
                cell.fill = REST_ROW_FILL
            cell.alignment = CENTER
            cell.border = BORDER
        r += 1

    # 每日人数：上班 / 休息，方便主管核对人力
    r += 1
    for label, key, font in (("上班人数", "work_count", Font(bold=True, size=9)), ("休息人数", "rest_count", Font(size=9, color="6B7280"))):
        ws.cell(row=r, column=1, value=label).font = font
        ws.cell(row=r, column=1).border = BORDER
        for j, day in enumerate(days, start=2):
            cell = ws.cell(row=r, column=j, value=day[key])
            cell.font = font
            cell.alignment = CENTER
            cell.border = BORDER
        r += 1

    note_row = r + 1
    ws.cell(
        row=note_row,
        column=1,
        value="说明：B2=当值主管（红）；A2/B1=早/晚班辅助主管（紫）；V=休息；假=已批准请假；其余为岗位代码。",
    ).font = Font(italic=True, color="6B7280", size=9)

    ws.column_dimensions["A"].width = 12
    for j in range(2, 2 + len(days)):
        ws.column_dimensions[get_column_letter(j)].width = 11.5
    ws.freeze_panes = "B3"
    ws.sheet_view.zoomScale = 90


@router.get("/export/versions/{version_id}.xlsx")
def export_version(version_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    version = db.get(ScheduleVersion, version_id)
    if not version:
        raise HTTPException(404, "版本不存在")
    emps = {e.id: e for e in db.query(Employee).all()}
    positions = {p.code: p for p in db.query(Position).all()}
    roles = {r.code: r for r in db.query(OperationalRole).all()}
    types = {t.code: t.name for t in db.query(EmployeeTypeOption).all()}
    stats = version_stats(db, version)

    dates = [version.start_date + dt.timedelta(days=i) for i in range((version.end_date - version.start_date).days + 1)]
    by_key = {(a.work_date, a.employee_id): a for a in version.assignments}

    wb = Workbook()
    ws0 = wb.active
    ws0.title = "人员排班"
    _write_matrix_sheet(ws0, version_matrix(db, version))

    ws = wb.create_sheet("排班表")

    ws.cell(row=1, column=1, value=f"{version.name}  [{version.status}]")
    ws.cell(row=1, column=1).font = Font(bold=True, size=14)
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=3 + len(dates))
    ws.cell(row=2, column=1, value=f"{version.start_date} ~ {version.end_date}　导出时间 {dt.datetime.now():%Y-%m-%d %H:%M}")
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=3 + len(dates))
    ws.cell(row=2, column=1).font = Font(color="6B7280", size=10)

    header_row = 4
    headers = ["姓名", "员工类型", "职务"] + [f"{d:%m-%d}\n{'一二三四五六日'[d.weekday()]}" for d in dates]
    for i, h in enumerate(headers, start=1):
        ws.cell(row=header_row, column=i, value=h)
    _style_header(ws, header_row, len(headers))

    emp_rows = sorted(
        {a.employee_id for a in version.assignments},
        key=lambda eid: (not emps[eid].is_supervisor, emps[eid].employment_type, emps[eid].name) if eid in emps else (True, "", ""),
    )
    r = header_row + 1
    for eid in emp_rows:
        emp = emps.get(eid)
        if not emp:
            continue
        ws.cell(row=r, column=1, value=emp.name)
        ws.cell(row=r, column=2, value=types.get(emp.employment_type, emp.employment_type))
        ws.cell(row=r, column=3, value=emp.job_title)
        if emp.is_supervisor:
            ws.cell(row=r, column=1).font = SUP_FONT
        for j, d in enumerate(dates, start=4):
            a = by_key.get((d, eid))
            cell = ws.cell(row=r, column=j, value="")
            if a:
                pos = positions.get(a.position_code)
                label = a.position_code
                if a.role_code and a.role_code in roles:
                    label = f"{a.position_code}\n{roles[a.role_code].name.replace('主管', '')}"
                cell.value = label
                cell.fill = EARLY_FILL if a.shift == "early" else LATE_FILL
            cell.alignment = CENTER
            cell.border = BORDER
        for c in range(1, 4):
            ws.cell(row=r, column=c).border = BORDER
            ws.cell(row=r, column=c).alignment = Alignment(vertical="center")
        r += 1

    # 每日人数汇总
    r += 1
    ws.cell(row=r, column=1, value="当日上班人数").font = Font(bold=True)
    ws.cell(row=r, column=2, value="早班 / 晚班").font = Font(bold=True)
    ws.cell(row=r, column=3, value="销售 / 摄影").font = Font(bold=True)
    for j, day in enumerate(stats["days"], start=4):
        cell = ws.cell(row=r, column=j, value=f"{day['total_people']}\n{day['early_count']}/{day['late_count']}")
        cell.alignment = CENTER
        cell.border = BORDER
    ws.cell(row=r + 1, column=1, value="说明：每人每日只计一次；运营角色为岗位标记，不额外重复计数。").font = Font(italic=True, color="6B7280", size=9)

    ws.column_dimensions["A"].width = 12
    ws.column_dimensions["B"].width = 11
    ws.column_dimensions["C"].width = 11
    for j in range(4, 4 + len(dates)):
        ws.column_dimensions[get_column_letter(j)].width = 13

    # 岗位覆盖
    ws2 = wb.create_sheet("岗位覆盖")
    headers2 = ["日期", "岗位", "岗位名称", "部门", "区域", "班次", "目标", "最低", "已排", "缺口", "主管人数"]
    for i, h in enumerate(headers2, start=1):
        ws2.cell(row=1, column=i, value=h)
    _style_header(ws2, 1, len(headers2))
    r = 2
    for row in sorted(stats["days"], key=lambda x: x["work_date"]):
        for p in row["positions"]:
            values = [row["work_date"], p["position_code"], p["position_name"], p["department"], p["area"], p["shift"], p["target"], p["min"], p["assigned"], p["shortage"], p["supervisors"]]
            for i, v in enumerate(values, start=1):
                cell = ws2.cell(row=r, column=i, value=v)
                cell.border = BORDER
                cell.alignment = CENTER if i > 1 else Alignment()
            if p["shortage"] > 0:
                ws2.cell(row=r, column=10).fill = PatternFill("solid", fgColor="FEE2E2")
                ws2.cell(row=r, column=10).font = Font(color="B91C1C", bold=True)
            r += 1
    for i, w in enumerate([12, 10, 26, 10, 8, 8, 7, 7, 7, 7, 9], start=1):
        ws2.column_dimensions[get_column_letter(i)].width = w

    # 每日统计
    ws3 = wb.create_sheet("每日统计")
    headers3 = ["日期", "星期", "日期类型", "总人数", "早班", "晚班", "销售", "摄影"] + [t["name"] for t in _type_list(db)]
    for i, h in enumerate(headers3, start=1):
        ws3.cell(row=1, column=i, value=h)
    _style_header(ws3, 1, len(headers3))
    for ri, day in enumerate(stats["days"], start=2):
        values = [day["work_date"], day["weekday"], day["day_type"], day["total_people"], day["early_count"], day["late_count"], day["sales_count"], day["photography_count"]]
        values += [day["by_type"].get(t["name"], 0) for t in _type_list(db)]
        for i, v in enumerate(values, start=1):
            cell = ws3.cell(row=ri, column=i, value=v)
            cell.border = BORDER
            cell.alignment = CENTER

    # 员工明细
    ws4 = wb.create_sheet("员工明细")
    headers4 = ["姓名", "类型", "职务", "是否主管", "工作天数", "早班次数", "晚班次数", "窗口内休息", "周期已休", "周期应休", "休息差", "岗位分布"]
    for i, h in enumerate(headers4, start=1):
        ws4.cell(row=1, column=i, value=h)
    _style_header(ws4, 1, len(headers4))
    for ri, e in enumerate(stats["employees"], start=2):
        values = [
            e["name"], e["employment_type_name"], e["job_title"], "是" if e["is_supervisor"] else "否",
            e["work_days"], e["early_count"], e["late_count"], e["rest_days_in_window"],
            e["period_rest_days"], e["period_target_rest"] if e["period_target_rest"] is not None else "-",
            e["rest_gap"] if e["rest_gap"] is not None else "-",
            "、".join(f"{k}×{v}" for k, v in e["positions"].items()),
        ]
        for i, v in enumerate(values, start=1):
            cell = ws4.cell(row=ri, column=i, value=v)
            cell.border = BORDER
            cell.alignment = CENTER if i not in (1, 12) else Alignment(vertical="center", wrap_text=True)
    for i, w in enumerate([12, 10, 10, 9, 9, 9, 9, 10, 9, 9, 8, 40], start=1):
        ws4.column_dimensions[get_column_letter(i)].width = w

    # 主管公平
    ws5 = wb.create_sheet("主管公平")
    headers5 = ["主管", "本窗口当值", "周期当值", "本窗口周末", "周期周末", "节假日当值", "旺季当值", "与均值偏差"]
    for i, h in enumerate(headers5, start=1):
        ws5.cell(row=1, column=i, value=h)
    _style_header(ws5, 1, len(headers5))
    for ri, s in enumerate(stats["supervisors"], start=2):
        values = [s["name"], s["window_duty"], s["period_duty"], s["window_weekend"], s["period_weekend"], s["period_holiday"], s["period_peak"], s["deviation"]]
        for i, v in enumerate(values, start=1):
            cell = ws5.cell(row=ri, column=i, value=v)
            cell.border = BORDER
            cell.alignment = CENTER
    for i, w in enumerate([12, 12, 10, 12, 10, 12, 12, 12], start=1):
        ws5.column_dimensions[get_column_letter(i)].width = w

    # 版本信息
    ws6 = wb.create_sheet("版本信息")
    info = [
        ("版本名称", version.name),
        ("版本号", f"v{version.version_no}"),
        ("状态", version.status),
        ("日期范围", f"{version.start_date} ~ {version.end_date}"),
        ("创建人", version.created_by),
        ("发布人", version.published_by),
        ("发布时间", version.published_at.strftime("%Y-%m-%d %H:%M") if version.published_at else "-"),
        ("变更说明", version.change_note),
        ("统计口径", "人数按当日实际被安排上班的员工去重统计；休息/未排班不计入；运营角色为岗位标记不重复计数。"),
        ("导出人", user.display_name or user.username),
    ]
    for ri, (k, v) in enumerate(info, start=1):
        ws6.cell(row=ri, column=1, value=k).font = Font(bold=True)
        ws6.cell(row=ri, column=2, value=v)
    ws6.column_dimensions["A"].width = 14
    ws6.column_dimensions["B"].width = 70

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    filename = f"排班表_{version.start_date}_{version.end_date}_v{version.version_no}.xlsx"
    write_audit(db, user.username, "export", "schedule_version", str(version.id), f"导出 Excel：{filename}")
    db.commit()
    quoted = quote(filename)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quoted}"},
    )


@router.get("/export/versions/{version_id}/dingtalk.xlsx")
def export_dingtalk(version_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """按人力上交模板（钉钉考勤排班表）导出：格子=班次名/休，未排班留空。"""
    version = db.get(ScheduleVersion, version_id)
    if not version:
        raise HTTPException(404, "版本不存在")
    wb, warnings = build_dingtalk_workbook(db, version)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    filename = dingtalk_filename(version)
    write_audit(
        db, user.username, "export_dingtalk", "schedule_version", str(version.id),
        f"导出钉钉排班表：{filename}" + (f"（{'；'.join(warnings)}）" if warnings else ""),
    )
    db.commit()
    quoted = quote(filename)
    headers = {"Content-Disposition": f"attachment; filename*=UTF-8''{quoted}"}
    if warnings:
        # 前端读取该头做提示（缺用户ID/岗位未配置班次名）
        headers["X-Export-Warnings"] = quote("；".join(warnings))[:900]
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers,
    )


def _type_list(db: Session):
    rows = db.query(EmployeeTypeOption).order_by(EmployeeTypeOption.sort_order).all()
    return [{"code": r.code, "name": r.name} for r in rows]


@router.get("/export/employees-template.xlsx")
def export_employee_template():
    wb = Workbook()
    ws = wb.active
    ws.title = "员工导入模板"
    headers = ["姓名", "员工类型(long_term/part_time/intern)", "职务", "是否主管(是/否)", "岗位(逗号分隔)"]
    for i, h in enumerate(headers, start=1):
        ws.cell(row=1, column=i, value=h)
    _style_header(ws, 1, len(headers))
    sample = ["示例员工", "long_term", "普通员工", "否", "A1,B1,A7"]
    for i, v in enumerate(sample, start=1):
        ws.cell(row=2, column=i, value=v)
    for i, w in enumerate([14, 32, 14, 16, 24], start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=employees-template.xlsx"},
    )


@router.post("/import/employees/preview")
async def preview_import(file: UploadFile = File(...), db: Session = Depends(get_db)):
    if not file.filename or not file.filename.lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(400, "请上传 .xlsx 文件")
    content = await file.read()
    try:
        wb = load_workbook(io.BytesIO(content), data_only=True)
    except Exception as exc:
        raise HTTPException(400, f"无法读取 Excel：{exc}") from exc
    ws = wb.worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        raise HTTPException(400, "工作表为空")
    header_idx = None
    for i, row in enumerate(rows[:10]):
        if row and any(str(c).strip() in ("姓名", "员工姓名", "name") for c in row if c is not None):
            header_idx = i
            break
    if header_idx is None:
        raise HTTPException(400, "未找到「姓名」列，请先下载导入模板")
    header = [str(c).strip() if c is not None else "" for c in rows[header_idx]]
    col = {name: idx for idx, name in enumerate(header)}

    def pick(row, *names):
        for n in names:
            for key, idx in col.items():
                if n in key and idx < len(row):
                    val = row[idx]
                    if val not in (None, ""):
                        return str(val).strip()
        return ""

    existing = {e.name for e in db.query(Employee).all()}
    valid_positions = {p.code for p in db.query(Position).all()}
    preview = []
    warnings = []
    for i, row in enumerate(rows[header_idx + 1:], start=header_idx + 2):
        name = pick(row, "姓名", "name")
        if not name:
            continue
        raw_type = pick(row, "类型", "type").lower()
        type_map = {"长期": "long_term", "长期员工": "long_term", "兼职": "part_time", "实习": "intern", "实习生": "intern"}
        etype = raw_type if raw_type in ("long_term", "part_time", "intern", "other") else type_map.get(raw_type, "long_term")
        sup_raw = pick(row, "主管", "supervisor")
        is_sup = sup_raw in ("是", "Y", "y", "true", "True", "1", "主管")
        title = pick(row, "职务", "岗位名称", "title") or ("主管" if is_sup else "普通员工")
        pos_raw = pick(row, "岗位", "position", "技能")
        codes = [c.strip() for c in pos_raw.replace("，", ",").replace("、", ",").split(",") if c.strip()]
        unknown = [c for c in codes if c not in valid_positions]
        if unknown:
            warnings.append(f"第 {i} 行岗位代码未识别：{', '.join(unknown)}（将忽略，可导入后补录）")
        if name in existing:
            warnings.append(f"第 {i} 行「{name}」已存在，导入时将被跳过")
        preview.append(
            {
                "row": i,
                "name": name,
                "employment_type": etype,
                "job_title": title,
                "is_supervisor": is_sup,
                "positions": [c for c in codes if c in valid_positions],
                "exists": name in existing,
            }
        )
    return {
        "headers": header,
        "rows": preview,
        "warnings": warnings,
        "summary": {
            "total": len(preview),
            "new": sum(1 for r in preview if not r["exists"]),
            "duplicated": sum(1 for r in preview if r["exists"]),
            "supervisors": sum(1 for r in preview if r["is_supervisor"]),
        },
        "note": "预览不会写入数据库；确认后调用 /api/import/employees/commit 才有变更，变更会记录审计日志。",
    }


@router.post("/import/employees/commit")
def commit_import(payload: list[EmployeeIn], db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    existing = {e.name for e in db.query(Employee).all()}
    created = 0
    skipped = []
    for item in payload:
        if item.name in existing:
            skipped.append(item.name)
            continue
        emp = Employee(**item.model_dump(exclude={"skills"}))
        db.add(emp)
        db.flush()
        for s in item.skills:
            db.add(EmployeePositionSkill(employee_id=emp.id, **s.model_dump()))
        created += 1
    write_audit(db, user.username, "import", "employee", "bulk", f"导入员工 {created} 人，跳过 {len(skipped)} 人", after={"created": created, "skipped": skipped})
    db.commit()
    return {"created": created, "skipped": skipped}
