"""示例数据初始化（演示用，不代表真实员工资料；需求文档要求不把示例写成硬规则）。"""
from __future__ import annotations

import datetime as dt
import random

from sqlalchemy.orm import Session

from ..models import (
    AppSetting,
    AvailabilityRequest,
    CalendarDay,
    DailyDemand,
    DemandTemplate,
    Employee,
    EmployeePositionSkill,
    EmployeeTypeOption,
    OperationalRole,
    PairingPreference,
    Position,
    ScheduleAssignment,
    ScheduleRun,
    ScheduleVersion,
    User,
)
from ..security import hash_password
from .config_service import ensure_defaults
from .solver import build_problem, solve

SUPERVISORS = ["陈志强", "林晓雯", "黄伟杰", "周美玲", "郑家豪", "吴嘉慧"]
MENTOR = "陈嘉欣"
LONG_TERM = [
    "李俊杰", "王思颖", "张文博", "刘雅婷", "何嘉伟", "黄晓彤", "梁俊宇", "罗嘉怡",
    "谢明轩", "冯诗敏", "邓子豪", "蔡雅文", "许志明", "曾婉婷", "彭浩然", "苏静怡",
]
PART_TIME = ["潘小雪", "卢家俊", "姚可欣", "石浩宇", "温嘉敏"]
INTERNS = ["蒋雨桐", "崔子谦", "范晓彤"]

POSITIONS = [
    # code, name, dept, area, shift, allowed_types, min_supervisors, role_code
    ("A1", "一楼销售柜台（早班）", "sales", "1F", "early", [], 0, None),
    ("B1", "一楼销售柜台（晚班）", "sales", "1F", "late", [], 1, "late_support"),
    ("A2", "108 层销售柜台（早班）", "sales", "108F", "early", [], 1, "early_support"),
    ("B2", "108 层销售柜台（晚班）", "sales", "108F", "late", [], 1, "duty_supervisor"),
    ("A3", "107 层销售柜台（早班）", "sales", "107F", "early", ["long_term"], 0, None),
    ("B3", "107 层销售柜台（晚班）", "sales", "107F", "late", ["long_term"], 0, None),
    ("A7", "107 层玻璃屋摄影（早班）", "photography", "107F", "early", [], 0, None),
    ("B7", "107 层玻璃屋摄影（晚班）", "photography", "107F", "late", [], 0, None),
    ("A8", "108 层玻璃屋摄影（早班）", "photography", "108F", "early", [], 0, None),
    ("B8", "108 层玻璃屋摄影（晚班）", "photography", "108F", "late", [], 0, None),
    ("AK", "一楼绿幕摄影（早班）", "photography", "1F", "early", [], 0, None),
    ("BK", "一楼绿幕摄影（晚班）", "photography", "1F", "late", [], 0, None),
]

# 默认需求模板（需求文档 4.1 / 4.2）
TEMPLATES = {
    "weekday": {"A1": 1, "B1": 2, "A2": 3, "B2": 4, "A3": 1, "B3": 1, "A7": 1, "B7": 1, "A8": 1, "B8": 1, "AK": 1, "BK": 2},
    "weekend": {"A1": 2, "B1": 3, "A2": 3, "B2": 4, "A3": 1, "B3": 1, "A7": 2, "B7": 2, "A8": 2, "B8": 2, "AK": 2, "BK": 3},
    "holiday": {"A1": 2, "B1": 3, "A2": 4, "B2": 5, "A3": 2, "B3": 2, "A7": 2, "B7": 2, "A8": 2, "B8": 2, "AK": 2, "BK": 3},
    "peak": {"A1": 2, "B1": 3, "A2": 4, "B2": 5, "A3": 2, "B3": 2, "A7": 2, "B7": 2, "A8": 2, "B8": 2, "AK": 2, "BK": 3},
}

ROLES = [
    ("duty_supervisor", "晚班当值主管", "late", "B2", False, "负责当天运营，必须是主管且属于晚班"),
    ("early_support", "早班辅助主管", "early", "A2", False, "早班运营负责人，17:00 交班给晚班当值主管"),
    ("late_support", "晚班辅助主管", "late", "B1", False, "晚间高客流运营支持"),
]


def _calendar_for(db: Session, start: dt.date, end: dt.date) -> None:
    cur = start
    while cur <= end:
        exists = db.query(CalendarDay).filter(CalendarDay.work_date == cur).first()
        if not exists:
            is_weekend = cur.weekday() >= 5
            db.add(
                CalendarDay(
                    work_date=cur,
                    day_type="weekend" if is_weekend else "weekday",
                    is_holiday=False,
                    is_peak=is_weekend,
                    label="周末高峰" if is_weekend else "",
                    expected_traffic="high" if is_weekend else "normal",
                )
            )
        cur += dt.timedelta(days=1)
    db.commit()


def seed_all(db: Session, force: bool = False) -> dict:
    ensure_defaults(db)
    if db.query(Employee).count() > 0 and not force:
        return {"seeded": False, "message": "已存在员工数据，跳过初始化"}

    if force:
        for model in (
            ScheduleAssignment, ScheduleVersion, ScheduleRun, PairingPreference, AvailabilityRequest,
            DailyDemand, DemandTemplate, EmployeePositionSkill, Employee, Position, OperationalRole,
            CalendarDay, AppSetting, EmployeeTypeOption, User,
        ):
            db.query(model).delete()
        db.commit()

    # 账号
    if db.query(User).count() == 0:
        db.add(User(username="admin", password_hash=hash_password("admin123456"), display_name="系统管理员", role="admin"))
        db.add(User(username="supervisor", password_hash=hash_password("supervisor123"), display_name="值班主管", role="supervisor"))
        db.commit()

    # 员工类型
    for i, (code, name, formal) in enumerate(
        [("long_term", "长期员工", True), ("part_time", "兼职", False), ("intern", "实习生", False), ("other", "其他", False)]
    ):
        if not db.query(EmployeeTypeOption).filter(EmployeeTypeOption.code == code).first():
            db.add(EmployeeTypeOption(code=code, name=name, counts_as_formal=formal, sort_order=i))

    # 运营角色
    for code, name, shift, bound, extra, desc in ROLES:
        if not db.query(OperationalRole).filter(OperationalRole.code == code).first():
            db.add(
                OperationalRole(
                    code=code, name=name, shift=shift, bound_position_code=bound,
                    counts_extra_slot=extra, description=desc,
                    color={"duty_supervisor": "#dc2626", "early_support": "#7c3aed", "late_support": "#7c3aed"}[code],
                )
            )

    # 岗位
    colors = {
        "A1": "#0ea5e9", "B1": "#0284c7", "A2": "#6366f1", "B2": "#dc2626",
        "A3": "#0891b2", "B3": "#0e7490", "A7": "#f59e0b", "B7": "#d97706",
        "A8": "#10b981", "B8": "#059669", "AK": "#8b5cf6", "BK": "#6d28d9",
    }
    for i, (code, name, dept, area, shift, allowed, min_sup, role) in enumerate(POSITIONS):
        if not db.query(Position).filter(Position.code == code).first():
            db.add(
                Position(
                    code=code, name=name, department=dept, area=area, shift=shift,
                    start_time="09:00" if shift == "early" else "13:30",
                    end_time="17:00" if shift == "early" else "22:30",
                    allowed_employment_types=allowed, min_supervisors=min_sup,
                    supervisor_role_code=role, is_active=True, sort_order=i * 10,
                    color=colors.get(code, "#64748b"),
                )
            )

    # 需求模板
    for day_type, mapping in TEMPLATES.items():
        for code, count in mapping.items():
            if not db.query(DemandTemplate).filter(DemandTemplate.day_type == day_type, DemandTemplate.position_code == code).first():
                db.add(DemandTemplate(day_type=day_type, position_code=code, target_count=count, min_count=count))

    # 员工
    rng = random.Random(20261105)
    today = dt.date.today()
    employees: list[Employee] = []

    def add_employee(name: str, etype: str, is_sup: bool, title: str, is_mentor: bool = False, pref: str = "any"):
        e = Employee(
            name=name,
            employee_no=f"E{len(employees) + 1:03d}",
            employment_type=etype,
            job_title=title,
            is_supervisor=is_sup,
            is_mentor=is_mentor,
            is_active=True,
            shift_preference=pref,
            hire_date=today - dt.timedelta(days=rng.randint(120, 1800)),
            notes="",
        )
        db.add(e)
        employees.append(e)
        return e

    for name in SUPERVISORS:
        add_employee(name, "long_term", True, "主管", False, "late")
    add_employee(MENTOR, "long_term", False, "导师", True, "any")
    for name in LONG_TERM:
        add_employee(name, "long_term", False, "普通员工", False, rng.choice(["early", "any"]))
    for name in PART_TIME:
        add_employee(name, "part_time", False, "兼职", False, "early")
    for name in INTERNS:
        add_employee(name, "intern", False, "实习生", False, "early")
    db.flush()

    all_codes = [p[0] for p in POSITIONS]
    sales_codes = ["A1", "B1", "A2", "B2", "A3", "B3"]
    photo_codes = ["A7", "B7", "A8", "B8", "AK", "BK"]

    granted: set[tuple[int, str]] = set()

    def grant(emp: Employee, codes: list[str], base_priority: int = 2, prof: int = 4):
        for i, code in enumerate(codes):
            if (emp.id, code) in granted:
                continue
            granted.add((emp.id, code))
            db.add(
                EmployeePositionSkill(
                    employee_id=emp.id, position_code=code,
                    priority=min(5, base_priority + i // 2),
                    proficiency=max(1, min(5, prof - i // 3)),
                    allowed=True,
                )
            )

    sup_count = len(SUPERVISORS)
    for e in employees[:sup_count]:
        grant(e, sales_codes + photo_codes, base_priority=2, prof=5)
        # 主管也能承担 A3/B3（长期员工身份）
    mentor = employees[sup_count]
    grant(mentor, sales_codes + ["A7", "B7"], base_priority=2, prof=5)

    regular = employees[sup_count + 1: sup_count + 1 + len(LONG_TERM)]
    for i, e in enumerate(regular):
        pair = [sales_codes[(i * 2) % len(sales_codes)], sales_codes[(i * 2 + 1) % len(sales_codes)]]
        codes = pair + [photo_codes[i % 6], photo_codes[(i + 3) % 6]]
        grant(e, list(dict.fromkeys(codes)), base_priority=2, prof=rng.randint(3, 5))
        # 保证 A3/B3 有稳定长期员工覆盖
        if i % 2 == 0:
            grant(e, ["A3"], base_priority=2, prof=4)
        if i % 3 == 0:
            grant(e, ["B3"], base_priority=2, prof=4)

    for e in employees[sup_count + 1 + len(LONG_TERM): sup_count + 1 + len(LONG_TERM) + len(PART_TIME)]:
        grant(e, ["A1", "B1", "AK", "BK", "A7", "A8"], base_priority=1, prof=3)
    for e in employees[sup_count + 1 + len(LONG_TERM) + len(PART_TIME):]:
        grant(e, ["A1", "B1", "AK", "BK", "A8"], base_priority=1, prof=2)

    db.commit()

    # 日历：今天前 16 天 ~ 后 45 天
    _calendar_for(db, today - dt.timedelta(days=16), today + dt.timedelta(days=45))

    # 演示：把下个周六标为旺季/节假日，演示标签与统计口径（可自由修改）
    for offset in range(1, 46):
        d = today + dt.timedelta(days=offset)
        if d.weekday() == 5:
            row = db.query(CalendarDay).filter(CalendarDay.work_date == d).first()
            row.is_peak = True
            row.expected_traffic = "high"
            row.label = row.label or "周六高峰"
            break
    db.commit()

    # 请假申请
    leaves = [
        (employees[sup_count + 3].id, today + dt.timedelta(days=1), today + dt.timedelta(days=1), "leave", "事假", "approved"),
        (employees[sup_count + 8].id, today + dt.timedelta(days=2), today + dt.timedelta(days=3), "leave", "病假", "approved"),
        (employees[sup_count + 2].id, today + dt.timedelta(days=2), today + dt.timedelta(days=2), "rest", "调休申请", "pending"),
    ]
    for eid, start, end, rtype, reason, status in leaves:
        db.add(
            AvailabilityRequest(
                employee_id=eid, request_type=rtype, start_date=start, end_date=end,
                reason=reason, status=status,
                reviewer="值班主管" if status != "pending" else "",
                reviewed_at=dt.datetime.now() if status != "pending" else None,
            )
        )
    db.commit()

    # 搭班偏好（正向 1 组、负向 1 组，仅演示）
    db.add(PairingPreference(employee_a_id=employees[0].id, employee_b_id=employees[sup_count + 1].id, preference_type="prefer", scope="same_shift", weight=1.5, note="希望同班带新人"))
    db.add(PairingPreference(employee_a_id=employees[sup_count + 2].id, employee_b_id=employees[sup_count + 4].id, preference_type="avoid", scope="same_day", weight=1.0, note="避免同日搭班"))
    db.commit()

    summary = {"employees": len(employees), "positions": len(POSITIONS), "templates": len(TEMPLATES)}
    return {"seeded": True, "message": "示例数据已创建", **summary}


def seed_history_and_draft(db: Session, days_history: int = 14) -> dict:
    """生成一批已发布的历史排班（用于公平/休息统计）与未来三天草稿。"""
    today = dt.date.today()
    out: dict = {}

    if db.query(ScheduleVersion).count() == 0:
        start = today - dt.timedelta(days=days_history)
        problem = build_problem(db, start, days_history)
        result = solve(problem, mode="draft", timeout=25)
        version = ScheduleVersion(
            name=f"历史排班 {start:%Y-%m-%d} ~ {(start + dt.timedelta(days=days_history - 1)):%Y-%m-%d}",
            start_date=start,
            end_date=start + dt.timedelta(days=days_history - 1),
            status="published",
            version_no=1,
            published_at=dt.datetime.now() - dt.timedelta(days=1),
            published_by="系统初始化",
            created_by="系统初始化",
            change_note="初始化历史数据（演示）",
        )
        db.add(version)
        db.flush()
        for a in result.assignments:
            db.add(ScheduleAssignment(version_id=version.id, **a))
        db.commit()
        out["history"] = {"version_id": version.id, "assignments": len(result.assignments), "status": result.status}

    start = today
    existing = (
        db.query(ScheduleVersion)
        .filter(ScheduleVersion.status == "draft", ScheduleVersion.start_date == start)
        .first()
    )
    if not existing:
        problem = build_problem(db, start, 3)
        result = solve(problem, mode="draft", timeout=20)
        run = ScheduleRun(
            start_date=start, end_date=start + dt.timedelta(days=2), status=result.status,
            solver=result.solver, objective_value=result.objective_value,
            objective_breakdown=result.objective_breakdown, diagnostics=result.diagnostics,
            message=result.message, duration_ms=result.duration_ms, created_by="系统初始化",
            params={"mode": "draft"},
        )
        db.add(run)
        db.flush()
        version = ScheduleVersion(
            name=f"预排班 {start:%Y-%m-%d} ~ {(start + dt.timedelta(days=2)):%Y-%m-%d}",
            start_date=start, end_date=start + dt.timedelta(days=2), status="draft",
            version_no=1, run_id=run.id, created_by="系统初始化",
            change_note="系统初始化自动生成",
        )
        db.add(version)
        db.flush()
        for a in result.assignments:
            db.add(ScheduleAssignment(version_id=version.id, **a))
        db.commit()
        out["draft"] = {"version_id": version.id, "assignments": len(result.assignments), "status": result.status, "gap": result.diagnostics.get("total_gap")}
    return out
