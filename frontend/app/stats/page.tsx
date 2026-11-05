"use client";

import { useCallback, useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import { Badge, Card, EmptyState, SectionTitle, Spinner, Stat, StatusBadge, useToast } from "@/components/ui";
import { get, todayISO, fmtDate, weekdayCN, TYPE_LABEL } from "@/lib/api";
import type { VersionDetail, VersionStats } from "@/lib/types";

const TABS = [
  { k: "days", t: "每日统计" },
  { k: "employees", t: "员工明细" },
  { k: "supervisors", t: "主管公平" },
];

export default function StatsPage() {
  const toast = useToast();
  const [tab, setTab] = useState("days");
  const [version, setVersion] = useState<VersionDetail | null>(null);
  const [stats, setStats] = useState<VersionStats | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const v = await get<VersionDetail>(`/api/schedule/current?start=${todayISO()}`);
      setVersion(v);
      setStats(await get<VersionStats>(`/api/stats/versions/${v.id}`));
    } catch (e: any) {
      toast.push(e?.message || "加载失败", "error");
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    load();
  }, [load]);

  if (loading) {
    return (
      <AppShell>
        <Card>
          <Spinner label="统计计算中…" />
        </Card>
      </AppShell>
    );
  }
  if (!stats || !version) {
    return (
      <AppShell>
        <EmptyState title="暂无可统计的排班" desc="请先生成预排班。" />
      </AppShell>
    );
  }

  const maxDev = Math.max(1, ...stats.supervisors.map((s) => Math.abs(s.deviation)));
  const employees = [...stats.employees].sort((a, b) => (b.is_supervisor ? 1 : 0) - (a.is_supervisor ? 1 : 0) || b.work_days - a.work_days);

  return (
    <AppShell>
      <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-lg font-semibold">统计报表</h1>
            <StatusBadge status={version.status} />
            <Badge tone="slate">v{version.version_no}</Badge>
          </div>
          <p className="mt-0.5 text-xs text-slate-500">
            {version.start_date} ~ {version.end_date}　周期 {stats.period.start} ~ {stats.period.end}（{stats.period.days} 天，月休目标 {stats.period.target_rest} 天）
          </p>
        </div>
        <div className="flex gap-2">
          <button className="btn" onClick={load}>
            刷新
          </button>
        </div>
      </div>

      <div className="mb-3 grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="安排人次" value={stats.summary.total_assignments} hint={`${stats.summary.dates} 天，日均 ${stats.summary.avg_per_day}`} />
        <Stat
          label="岗位覆盖率"
          value={`${stats.summary.coverage_rate}%`}
          tone={stats.summary.coverage_rate >= 99 ? "good" : "warn"}
          hint={`目标 ${stats.summary.total_needed} 人次`}
        />
        <Stat label="缺口人次" value={stats.summary.total_gap} tone={stats.summary.total_gap ? "bad" : "good"} hint="低于最低人数的人次" />
        <Stat label="主管人数" value={stats.supervisors.length} hint="当值公平统计对象" />
      </div>

      <div className="mb-3 flex w-fit gap-1 rounded-lg border border-line bg-white p-1 text-[13px]">
        {TABS.map((t) => (
          <button
            key={t.k}
            className={`rounded-md px-3 py-1.5 ${tab === t.k ? "bg-brand-50 font-medium text-brand-700" : "text-slate-500 hover:bg-slate-50"}`}
            onClick={() => setTab(t.k)}
          >
            {t.t}
          </button>
        ))}
      </div>

      {tab === "days" ? (
        <div className="space-y-3">
          {stats.days.map((d) => (
            <Card key={d.work_date} pad={false} className="overflow-hidden">
              <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line bg-slate-50/70 px-4 py-2.5">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-semibold">{fmtDate(d.work_date)}</span>
                  <span className="text-xs text-slate-500">{weekdayCN(d.work_date)}</span>
                  <Badge tone={d.is_peak ? "amber" : d.day_type === "weekend" ? "blue" : "slate"}>
                    {d.is_peak ? "旺季" : d.day_type === "weekend" ? "周末" : d.day_type === "holiday" ? "节假日" : "工作日"}
                  </Badge>
                  {d.label ? <span className="text-[11px] text-amber-600">{d.label}</span> : null}
                </div>
                <div className="flex flex-wrap gap-3 text-[11px] text-slate-600">
                  <span>
                    上班 <b className="text-slate-900">{d.total_people}</b> 人
                  </span>
                  <span>
                    早 <b>{d.early_count}</b> / 晚 <b>{d.late_count}</b>
                  </span>
                  <span>
                    销售 <b>{d.sales_count}</b> / 摄影 <b>{d.photography_count}</b>
                  </span>
                  {Object.entries(d.by_type).map(([k, v]) => (
                    <span key={k} className="text-slate-500">
                      {k} {v}
                    </span>
                  ))}
                </div>
              </div>
              <div className="table-scroll overflow-auto scroll-thin">
                <table className="table table-compact">
                  <thead>
                    <tr>
                      <th className="min-w-[150px]">岗位</th>
                      <th className="hidden sm:table-cell">区域</th>
                      <th className="hidden sm:table-cell">班次</th>
                      <th className="text-center">目标</th>
                      <th className="text-center">最低</th>
                      <th className="text-center">已排</th>
                      <th className="text-center">缺口</th>
                      <th className="text-center">主管数</th>
                    </tr>
                  </thead>
                  <tbody>
                    {d.positions.map((p) => (
                      <tr key={p.position_code}>
                        <td>
                          <span className="font-medium">{p.position_code}</span>
                          <span className="ml-1 text-[11px] text-slate-500">{p.position_name}</span>
                        </td>
                        <td className="hidden text-slate-600 sm:table-cell">{p.area}</td>
                        <td className="hidden sm:table-cell">{p.shift === "early" ? <Badge tone="blue">早</Badge> : <Badge tone="rose">晚</Badge>}</td>
                        <td className="text-center tabular-nums">{p.target}</td>
                        <td className="text-center tabular-nums">{p.min}</td>
                        <td className="text-center tabular-nums font-medium">{p.assigned}</td>
                        <td className={`text-center tabular-nums ${p.shortage ? "font-semibold text-rose-600" : "text-slate-400"}`}>{p.shortage || "—"}</td>
                        <td className="text-center tabular-nums">{p.supervisors}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          ))}
        </div>
      ) : tab === "employees" ? (
        <Card pad={false} className="overflow-hidden">
          <div className="border-b border-line px-4 py-3">
            <SectionTitle
              title="员工工作与休息统计"
              desc={`周期已休为「周期天数 − 周期内工作天数」；应休目标来自系统配置（当前 ${stats.period.target_rest} 天），仅对长期员工生效。`}
            />
          </div>
          <div className="table-scroll overflow-auto scroll-thin" style={{ maxHeight: "calc(100vh - 300px)" }}>
            <table className="table">
              <thead>
                <tr>
                  <th>姓名</th>
                  <th>类型</th>
                  <th className="text-center">工作天数</th>
                  <th className="text-center">早班</th>
                  <th className="text-center">晚班</th>
                  <th className="text-center">窗口内休息</th>
                  <th className="text-center">周期已休</th>
                  <th className="text-center">应休</th>
                  <th className="text-center">差额</th>
                  <th>岗位分布</th>
                </tr>
              </thead>
              <tbody>
                {employees.map((e) => (
                  <tr key={e.employee_id}>
                    <td>
                      <div className="flex items-center gap-1">
                        {e.is_supervisor ? <span className="text-amber-500">★</span> : null}
                        <span className="font-medium text-slate-800">{e.name}</span>
                        {e.is_mentor ? <Badge tone="violet">导师</Badge> : null}
                      </div>
                    </td>
                    <td className="text-slate-600">{e.employment_type_name || TYPE_LABEL[e.employment_type]}</td>
                    <td className="text-center tabular-nums font-medium">{e.work_days}</td>
                    <td className="text-center tabular-nums">{e.early_count}</td>
                    <td className="text-center tabular-nums">{e.late_count}</td>
                    <td className="text-center tabular-nums">{e.rest_days_in_window}</td>
                    <td className="text-center tabular-nums">{e.period_rest_days}</td>
                    <td className="text-center tabular-nums text-slate-400">{e.period_target_rest ?? "—"}</td>
                    <td className={`text-center tabular-nums ${e.rest_gap == null ? "text-slate-300" : e.rest_gap < 0 ? "font-semibold text-rose-600" : "text-emerald-600"}`}>
                      {e.rest_gap == null ? "—" : e.rest_gap > 0 ? `+${e.rest_gap}` : e.rest_gap}
                    </td>
                    <td className="max-w-[240px] text-[11px] text-slate-500">
                      {Object.entries(e.positions)
                        .map(([k, v]) => `${k}×${v}`)
                        .join("、") || "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      ) : (
        <div className="grid gap-3 lg:grid-cols-[1fr_380px]">
          <Card pad={false} className="overflow-hidden">
            <div className="border-b border-line px-4 py-3">
              <SectionTitle
                title="主管当值公平"
                desc="总当值、周末、节假日、旺季分别统计；偏差为与主管团队平均值的差。公平为软目标，不违反请假与资格等硬约束。"
              />
            </div>
            <table className="table">
              <thead>
                <tr>
                  <th>主管</th>
                  <th className="text-center">本窗口当值</th>
                  <th className="text-center">周期当值</th>
                  <th className="text-center">周末（窗口/周期）</th>
                  <th className="text-center">节假日</th>
                  <th className="text-center">旺季</th>
                  <th className="text-center">偏差</th>
                </tr>
              </thead>
              <tbody>
                {stats.supervisors.map((s) => (
                  <tr key={s.employee_id}>
                    <td className="font-medium text-slate-800">{s.name}</td>
                    <td className="text-center tabular-nums">{s.window_duty}</td>
                    <td className="text-center tabular-nums font-medium">{s.period_duty}</td>
                    <td className="text-center tabular-nums">
                      {s.window_weekend} / {s.period_weekend}
                    </td>
                    <td className="text-center tabular-nums">{s.period_holiday}</td>
                    <td className="text-center tabular-nums">{s.period_peak}</td>
                    <td className={`text-center tabular-nums ${Math.abs(s.deviation) > 1 ? "font-semibold text-amber-600" : "text-slate-500"}`}>
                      {s.deviation > 0 ? `+${s.deviation}` : s.deviation}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="border-t border-line px-4 py-2 text-[11px] text-slate-500">
              周期口径：{stats.period.start} ~ {stats.period.end}，包含已发布历史与本版本。
            </div>
          </Card>

          <Card>
            <SectionTitle title="当值次数分布" desc="偏差越小越接近团队平均。" />
            <div className="space-y-2">
              {stats.supervisors.map((s) => (
                <div key={s.employee_id}>
                  <div className="flex items-center justify-between text-[11px]">
                    <span className="text-slate-600">{s.name}</span>
                    <span className="tabular-nums text-slate-500">{s.period_duty} 次</span>
                  </div>
                  <div className="mt-0.5 h-2 overflow-hidden rounded-full bg-slate-100">
                    <div
                      className={s.deviation > 0 ? "h-full rounded-full bg-rose-400" : "h-full rounded-full bg-brand-400"}
                      style={{ width: `${Math.max(6, (Math.abs(s.deviation) / maxDev) * 100)}%` }}
                    />
                  </div>
                </div>
              ))}
            </div>
            <p className="mt-3 rounded-lg bg-slate-50 px-3 py-2 text-[11px] leading-5 text-slate-500">
              下一个排班周期会参考本周期历史，优先让当值较少的主管承担晚班当值，并单独平衡周末与节假日。
            </p>
          </Card>
        </div>
      )}
    </AppShell>
  );
}
