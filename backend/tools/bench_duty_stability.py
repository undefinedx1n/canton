"""30 天窗口当值均衡稳定性自检：同一份数据重复求解，看分布是否抖动。

用法：../.venv/Scripts/python.exe tools/bench_duty_stability.py
背景：分块求解有时间预算，权重设置不当会出现"跑三次一次均衡两次偏差 2 次"。
"""
import datetime as dt
import sys
import time
sys.path.insert(0, '.')
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.db import Base
from app.services.config_service import ensure_defaults, get_setting, set_setting
from app.services.seed import seed_all
from app.services.solver import build_problem, solve
from tests.support import set_day
from app.models import Employee

HOLIDAY={7,8,14,15,21}; PEAK={5,6,7,8,12,13,14,15,19,20,21,22}
engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
Base.metadata.create_all(bind=engine)
db = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
ensure_defaults(db); seed_all(db)
start = dt.date(2026,11,2)
dates=[start+dt.timedelta(days=i) for i in range(30)]
for i,d in enumerate(dates):
    set_day(db, d, day_type="holiday" if i in HOLIDAY else ("weekend" if d.weekday()>=5 else "weekday"),
            is_holiday=i in HOLIDAY, is_peak=i in PEAK)
db.commit()
sups=[e for e in db.query(Employee).filter(Employee.is_supervisor.is_(True), Employee.is_active.is_(True)).order_by(Employee.id)]
BASE=dict(get_setting(db,"solver_weights"))
p=None
def run(label, reps=3, ov=None):
    global p
    set_setting(db,"solver_weights",{**BASE,**(ov or {})})
    if p is None: p=build_problem(db,start,30)
    for i in range(reps):
        t=time.time(); res=solve(p,"draft",timeout=60); dur=time.time()-t
        v={s.id:0 for s in sups}
        for a in res.assignments:
            if a.get("role_code")=="duty_supervisor" and a["employee_id"] in v: v[a["employee_id"]]+=1
        vals=sorted(v.values())
        print(f"{label:<26} #{i+1} {dur:5.1f}s duty={vals} spread={max(vals)-min(vals)} sw={(res.objective_breakdown or {}).get('shift_switch',0):.0f}")

run("换班90/110 duty120", 3, {"duty_fairness":120})
run("换班90/110 duty90", 3, {"duty_fairness":90})
