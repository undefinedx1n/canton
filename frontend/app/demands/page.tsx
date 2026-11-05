"use client";

import { useCallback, useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import { Badge, Card, SectionTitle, Spinner, useToast } from "@/components/ui";
import { addDays, get, put, todayISO, weekdayCN } from "@/lib/api";
import type { DemandDay } from "@/lib/types";

const DAY_TYPES = [
  { code: "weekday", label: "普通工作日" },
  { code: "weekend", label: "周末" },
  { code: "holiday", label: "节假日" },
  { code: "peak", label: "旺季/特殊日" },
];

export default function DemandsPage() {
  const toast = useToast();
  const [start, setStart] = useState(todayISO());
  const [days, setDays] = useState(3);
  const [data, setData] = useState<DemandDay[]>([]);
  const [draft, setDraft] = useState<Record<string, { target: number; min: number }>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [cal, setCal] = useState<Record<string, any>>({});

  const load = useCallback(async () => {
    setLoading(true);
    const end = addDays(start, days - 1);
    try {
      const [rows, calendar] = await Promise.all([
        get<DemandDay[]>(`/api/daily-demands?start=${start}&end=${end}`),
        get<any[]>(`/api/calendar-days?start=${start}&end=${end}`),
      ]);
      setData(rows);
      const map: Record<string, { target: number; min: number }> = {};
      rows.forEach((d) =>
        d.positions.forEach((p) => {
          map[`${d.work_date}:${p.position_code}`] = { target: p.target_count, min: p.min_count };
        })
      );
      setDraft(map);
      setCal(Object.fromEntries(calendar.map((c) => [c.work_date, c])));
    } catch (e: any) {
      toast.push(e?.message || "加载失败", "error");
    } finally {
      setLoading(false);
    }
  }, [start, days, toast]);

  useEffect(() => {
    load();
  }, [load]);

  async function save() {
    setSaving(true);
    try {
      const items: any[] = [];
      data.forEach((d) =>
        d.positions.forEach((p) => {
          const v = draft[`${d.work_date}:${p.position_code}`];
          if (!v) return;
          if (v.target !== p.target_count || v.min !== p.min_count) {
            items.push({ work_date: d.work_date, position_code: p.position_code, target_count: v.target, min_count: v.min, note: "" });
          }
        })
      );
      if (!items.length) {
        toast.push("没有需要保存的变更");
        setSaving(false);
        return;
      }
      await put("/api/daily-demands", items);
      toast.push(`已保存 ${items.length} 条日需求覆盖`, "ok");
      await load();
    } catch (e: any) {
      toast.push(e?.message || "保存失败", "error");
    } finally {
      setSaving(false);
    }
  }

  async function saveCalendar(date: string, patch: any) {
    try {
      await put(`/api/calendar-days/${date}`, {
        work_date: date,
        day_type: patch.day_type ?? cal[date]?.day_type ?? "weekday",
        is_holiday: patch.is_holiday ?? cal[date]?.is_holiday ?? false,
        is_peak: patch.is_peak ?? cal[date]?.is_peak ?? false,
        label: patch.label ?? cal[date]?.label ?? "",
        expected_traffic: patch.expected_traffic ?? cal[date]?.expected_traffic ?? "normal",
        note: "",
      });
      await load();
      toast.push("日期标记已更新，需求已按模板重算", "ok");
    } catch (e: any) {
      toast.push(e?.message || "更新失败", "error");
    }
  }

  const positions = data[0]?.positions || [];

  return (
    <AppShell>
      <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold">每日需求编辑</h1>
          <p className="mt-0.5 text-xs text-slate-500">
            按日期类型套用模板，并支持逐岗覆盖「目标人数 / 最低人数」。修改后重新生成预排班即可生效。
          </p>
        </div>
        <div className="flex flex-wrap items-end gap-2">
          <div>
            <label className="label">起始日期</label>
            <input type="date" className="input" value={start} onChange={(e) => setStart(e.target.value)} />
          </div>
          <div>
            <label className="label">天数</label>
            <select className="input" value={days} onChange={(e) => setDays(Number(e.target.value))}>
              {[3, 5, 7].map((d) => (
                <option key={d} value={d}>
                  {d} 天
                </option>
              ))}
            </select>
          </div>
          <button className="btn" onClick={load}>
            刷新
          </button>
          <button className="btn btn-primary" disabled={saving} onClick={save}>
            {saving ? "保存中…" : "保存需求"}
          </button>
        </div>
      </div>

      <Card className="mb-3">
        <SectionTitle title="日期类型与客流" desc="日期类型决定套用哪个需求模板；旺季/节假日标签同时用于主管公平统计。" />
        <div className="grid gap-2 md:grid-cols-3 lg:grid-cols-5">
          {data.map((d) => (
            <div key={d.work_date} className="rounded-lg border border-line px-3 py-2">
              <div className="flex items-center justify-between">
                <span className="text-[13px] font-medium text-slate-800">
                  {d.work_date.slice(5)} {weekdayCN(d.work_date)}
                </span>
                {d.is_peak ? <Badge tone="amber">旺季</Badge> : d.is_holiday ? <Badge tone="rose">节假日</Badge> : null}
              </div>
              <select
                className="input mt-1.5 !py-1 !text-[12px]"
                value={cal[d.work_date]?.day_type || d.day_type}
                onChange={(e) => saveCalendar(d.work_date, { day_type: e.target.value })}
              >
                {DAY_TYPES.map((t) => (
                  <option key={t.code} value={t.code}>
                    {t.label}
                  </option>
                ))}
              </select>
              <div className="mt-1.5 flex items-center gap-2 text-[11px] text-slate-500">
                <label className="flex items-center gap-1">
                  <input type="checkbox" checked={cal[d.work_date]?.is_peak || false} onChange={(e) => saveCalendar(d.work_date, { is_peak: e.target.checked })} />
                  旺季
                </label>
                <label className="flex items-center gap-1">
                  <input type="checkbox" checked={cal[d.work_date]?.is_holiday || false} onChange={(e) => saveCalendar(d.work_date, { is_holiday: e.target.checked })} />
                  法定节假日
                </label>
              </div>
              <div className="mt-1 text-[10px] text-slate-400">
                当日总需求 {d.positions.reduce((s, p) => s + (draft[`${d.work_date}:${p.position_code}`]?.target ?? 0), 0)} 人次
              </div>
            </div>
          ))}
        </div>
      </Card>

      {loading ? (
        <Card>
          <Spinner />
        </Card>
      ) : (
        <Card pad={false} className="overflow-hidden">
          <div className="table-scroll overflow-auto scroll-thin">
            <table className="table">
              <thead>
                <tr>
                  <th className="sticky left-0 z-20 bg-slate-50">岗位</th>
                  <th>班次</th>
                  {data.map((d) => (
                    <th key={d.work_date} className="text-center" style={{ minWidth: 120 }}>
                      {d.work_date.slice(5)} {weekdayCN(d.work_date)}
                      <div className="text-[10px] font-normal text-slate-400">{DAY_TYPES.find((t) => t.code === d.day_type)?.label}</div>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {positions.map((p) => (
                  <tr key={p.position_code}>
                    <td className="sticky left-0 z-10 bg-white">
                      <span className="text-[12px] font-semibold">{p.position_code}</span>
                      <span className="ml-1 text-[11px] text-slate-500">{p.position_name.replace(/（.*?）/g, "")}</span>
                      <div className="text-[10px] text-slate-400">
                        {p.department === "sales" ? "销售" : "摄影"} · {p.area}
                      </div>
                    </td>
                    <td>{p.shift === "early" ? <Badge tone="blue">早</Badge> : <Badge tone="rose">晚</Badge>}</td>
                    {data.map((d) => {
                      const k = `${d.work_date}:${p.position_code}`;
                      const orig = d.positions.find((x) => x.position_code === p.position_code);
                      const v = draft[k] || { target: 0, min: 0 };
                      const changed = orig && (v.target !== orig.target_count || v.min !== orig.min_count);
                      return (
                        <td key={k} className={`text-center ${changed ? "bg-amber-50" : ""}`}>
                          <div className="flex items-center justify-center gap-0.5">
                            <input
                              type="number"
                              min={0}
                              className="input !w-12 !py-0.5 text-center"
                              value={v.target}
                              onChange={(e) => setDraft({ ...draft, [k]: { ...v, target: Number(e.target.value), min: Math.min(v.min, Number(e.target.value)) } })}
                            />
                            <span className="text-slate-300">/</span>
                            <input
                              type="number"
                              min={0}
                              className="input !w-12 !py-0.5 text-center"
                              title="最低人数"
                              value={v.min}
                              onChange={(e) => setDraft({ ...draft, [k]: { ...v, min: Number(e.target.value) } })}
                            />
                          </div>
                          {orig?.is_override ? <div className="mt-0.5 text-[9px] text-brand-600">已覆盖</div> : null}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="border-t border-line px-4 py-2 text-[11px] text-slate-500">
            格式：<b>目标人数 / 最低人数</b>。最低人数不足时求解器会报告缺口；若最低人数高于目标，系统会以最低人数为覆盖率基准。
          </div>
        </Card>
      )}
    </AppShell>
  );
}
