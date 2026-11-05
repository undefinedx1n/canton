"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import { Badge, Card, SectionTitle, Spinner, StatusBadge, useToast } from "@/components/ui";
import { get, post, todayISO, addDays } from "@/lib/api";
import type { ScheduleRun, VersionDetail } from "@/lib/types";

const OBJECTIVE_LABEL: Record<string, string> = {
  gap: "缺口惩罚",
  coverage: "目标覆盖",
  duty_fairness: "当值公平",
  weekend_fairness: "周末公平",
  holiday_fairness: "节假日公平",
  peak_fairness: "旺季公平",
  rest_balance: "休息目标",
  shift_preference: "班次偏好",
  position_priority: "岗位优先级",
  pairing_avoid: "搭班避让",
  pairing_prefer: "搭班偏好",
  proficiency: "能力匹配",
  workload_balance: "工作量均衡",
};

export default function GeneratePage() {
  const toast = useToast();
  const [start, setStart] = useState(todayISO());
  const [days, setDays] = useState(3);
  const [mode, setMode] = useState<"draft" | "strict">("draft");
  const [busy, setBusy] = useState(false);
  const [seconds, setSeconds] = useState(0);
  const [run, setRun] = useState<ScheduleRun | null>(null);
  const [version, setVersion] = useState<VersionDetail | null>(null);
  const [error, setError] = useState("");
  const [meta, setMeta] = useState<any>(null);
  const [demands, setDemands] = useState<any[]>([]);

  useEffect(() => {
    get("/api/meta").then(setMeta).catch(() => {});
  }, []);

  useEffect(() => {
    get<any[]>(`/api/daily-demands?start=${start}&end=${addDays(start, days - 1)}`)
      .then(setDemands)
      .catch(() => setDemands([]));
  }, [start, days]);

  useEffect(() => {
    if (!busy) return;
    const t = setInterval(() => setSeconds((s) => s + 1), 1000);
    return () => clearInterval(t);
  }, [busy]);

  async function generate() {
    setBusy(true);
    setSeconds(0);
    setError("");
    try {
      const res = await post<{ run: ScheduleRun; version: VersionDetail }>("/api/schedule/generate", {
        start_date: start,
        days,
        mode,
        keep_locks: true,
      });
      setRun(res.run);
      setVersion(res.version);
      toast.push(res.run.message || "生成完成", res.run.status === "done" ? "ok" : "error");
    } catch (e: any) {
      setError(e?.message || "生成失败");
    } finally {
      setBusy(false);
    }
  }

  const gaps = (run?.diagnostics.gaps || []).filter((g: any) => g.shortage > 0);
  const missingRoles = run?.diagnostics.missing_roles || [];
  const breakdown = run?.objective_breakdown || {};
  const maxPenalty = Math.max(1, ...Object.values(breakdown).map((v) => Number(v) || 0));

  return (
    <AppShell>
      <div className="grid gap-3 lg:grid-cols-[340px_1fr]">
        <Card>
          <SectionTitle title="自动排班" desc="按需求模板与硬规则求解，软目标按权重优化。" />
          <div className="space-y-3">
            <div>
              <label className="label">起始日期</label>
              <input type="date" className="input" value={start} onChange={(e) => setStart(e.target.value)} />
            </div>
            <div>
              <label className="label">天数（滚动排未来三天）</label>
              <select className="input" value={days} onChange={(e) => setDays(Number(e.target.value))}>
                {[1, 2, 3, 5, 7].map((d) => (
                  <option key={d} value={d}>
                    {d} 天（{addDays(start, d - 1)} 结束）
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="label">缺口处理</label>
              <div className="space-y-1.5">
                {[
                  { v: "draft", t: "允许带缺口草稿", d: "仍生成可行部分，并列出缺岗原因（推荐）" },
                  { v: "strict", t: "严格模式", d: "任一岗位低于最低人数即标记不可行，不发布" },
                ].map((o) => (
                  <label key={o.v} className={`flex cursor-pointer gap-2 rounded-lg border p-2 ${mode === o.v ? "border-brand-400 bg-brand-50/50" : "border-line"}`}>
                    <input type="radio" className="mt-0.5" checked={mode === o.v} onChange={() => setMode(o.v as any)} />
                    <span>
                      <span className="block text-[13px] font-medium text-slate-800">{o.t}</span>
                      <span className="block text-[11px] leading-4 text-slate-500">{o.d}</span>
                    </span>
                  </label>
                ))}
              </div>
            </div>
            <div className="rounded-lg bg-slate-50 px-3 py-2 text-[11px] leading-5 text-slate-500">
              已锁定的安排不会被移动。求解器：
              <b>{meta?.solver === "cp-sat" ? " OR-Tools CP-SAT" : " 启发式（未安装 OR-Tools）"}</b>
              <br />
              硬约束：资格、A3/B3 仅长期员工、主管角色、请假、锁定、单日单岗。
            </div>
            <button className="btn btn-primary w-full py-2" onClick={generate} disabled={busy}>
              {busy ? `求解中… ${seconds}s` : "生成预排班"}
            </button>
          </div>
        </Card>

        <div className="space-y-3">
          {busy ? (
            <Card>
              <Spinner label="CP-SAT 正在求解（通常 1–5 秒）…" />
              <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-slate-100">
                <div className="h-full animate-pulse rounded-full bg-brand-400" style={{ width: `${Math.min(95, 12 + seconds * 14)}%` }} />
              </div>
            </Card>
          ) : null}

          {error ? <Card className="border-rose-200 bg-rose-50 text-sm text-rose-700">{error}</Card> : null}

          {run && version ? (
            <>
              <Card>
                <SectionTitle
                  title={
                    <span className="flex items-center gap-2">
                      求解结果 <StatusBadge status={run.status} />
                      <Badge tone="slate">{run.solver}</Badge>
                    </span>
                  }
                  desc={`${run.message} · 耗时 ${run.duration_ms}ms · 安排 ${run.diagnostics.assigned_count ?? 0} 人次`}
                  right={
                    <div className="flex gap-2">
                      <Link className="btn btn-sm" href={`/edit/?id=${version.id}`}>
                        人工调整
                      </Link>
                      <Link className="btn btn-sm btn-primary" href="/">
                        查看总览
                      </Link>
                    </div>
                  }
                />
                <div className="grid grid-cols-3 gap-2">
                  <div className="rounded-lg border border-line px-3 py-2">
                    <div className="text-[11px] text-slate-500">缺口人次</div>
                    <div className={`text-lg font-semibold ${(run.diagnostics.total_gap || 0) > 0 ? "text-rose-600" : "text-emerald-600"}`}>
                      {run.diagnostics.total_gap ?? 0}
                    </div>
                  </div>
                  <div className="rounded-lg border border-line px-3 py-2">
                    <div className="text-[11px] text-slate-500">当日人数</div>
                    <div className="text-lg font-semibold text-slate-900">{run.diagnostics.assigned_count ?? 0}</div>
                  </div>
                  <div className="rounded-lg border border-line px-3 py-2">
                    <div className="text-[11px] text-slate-500">重复排班</div>
                    <div className="text-lg font-semibold text-slate-900">{run.diagnostics.duplicate_assignments?.length ?? 0}</div>
                  </div>
                </div>
              </Card>

              {gaps.length || missingRoles.length ? (
                <Card className="border-amber-200 bg-amber-50/40">
                  <SectionTitle title="缺口与不可行原因" desc="系统不会用不合格人员填补缺口，请调整需求或增补人力。" />
                  <div className="space-y-1.5">
                    {gaps.map((g: any, i: number) => (
                      <div key={i} className="rounded-lg border border-amber-200 bg-white px-3 py-2 text-xs">
                        <div className="flex flex-wrap items-center gap-2">
                          <Badge tone="rose">
                            {g.work_date} · {g.position_code}
                          </Badge>
                          <span className="font-medium text-slate-700">{g.position_name}</span>
                          <span className="text-slate-500">
                            已排 {g.assigned} / 目标 {g.target}（最低 {g.min}），缺 {g.shortage}
                          </span>
                          <span className="text-slate-400">
                            合格 {g.eligible_total} 人 · 当日空闲 {g.eligible_idle} 人
                          </span>
                        </div>
                        <div className="mt-1 text-[11px] text-amber-700">{g.reason}</div>
                      </div>
                    ))}
                    {missingRoles.map((r: any, i: number) => (
                      <div key={`r${i}`} className="text-xs text-rose-700">
                        · {r.work_date} {r.position_code}：{r.reason}
                      </div>
                    ))}
                  </div>
                </Card>
              ) : (
                <Card className="border-emerald-200 bg-emerald-50/40 text-xs text-emerald-700">
                  全部岗位达到目标人数，无缺口。
                </Card>
              )}

              <Card>
                <SectionTitle title="目标函数分解" desc="数值为加权后的惩罚值，越小越好；用于解释本次排班的取舍。" />
                <div className="space-y-1.5">
                  {Object.entries(breakdown)
                    .filter(([, v]) => Number(v) > 0)
                    .sort((a, b) => Number(b[1]) - Number(a[1]))
                    .map(([k, v]) => (
                      <div key={k} className="flex items-center gap-2">
                        <div className="w-24 shrink-0 text-[11px] text-slate-500">{OBJECTIVE_LABEL[k] || k}</div>
                        <div className="h-2 flex-1 overflow-hidden rounded-full bg-slate-100">
                          <div className="h-full rounded-full bg-brand-400" style={{ width: `${(Number(v) / maxPenalty) * 100}%` }} />
                        </div>
                        <div className="w-12 text-right text-[11px] tabular-nums text-slate-500">{Math.round(Number(v))}</div>
                      </div>
                    ))}
                  {Object.values(breakdown).every((v) => !Number(v)) ? (
                    <div className="text-xs text-slate-400">本次无软约束惩罚（全部达到理想状态）。</div>
                  ) : null}
                </div>
              </Card>
            </>
          ) : null}

          {!busy ? (
            <Card pad={false}>
              <div className="border-b border-line px-4 py-3">
                <SectionTitle
                  title="需求预览"
                  desc="本次将被求解的岗位人数（来自需求模板与逐日覆盖），可在「每日需求」调整。"
                  right={
                    <Link className="btn btn-sm" href="/demands/">
                      去调整
                    </Link>
                  }
                />
              </div>
              <div className="divide-y divide-slate-100">
                {demands.map((d) => {
                  const total = d.positions.reduce((s: number, p: any) => s + p.target_count, 0);
                  const early = d.positions.filter((p: any) => p.shift === "early").reduce((s: number, p: any) => s + p.target_count, 0);
                  return (
                    <div key={d.work_date} className="flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-2.5 text-[12px]">
                      <div className="w-[112px] shrink-0 font-medium text-slate-800">{d.work_date.slice(5)}</div>
                      <Badge tone={d.is_peak ? "amber" : d.day_type === "weekend" ? "blue" : "slate"}>
                        {d.is_peak ? "旺季" : d.day_type === "weekend" ? "周末" : d.day_type === "holiday" ? "节假日" : "工作日"}
                      </Badge>
                      <span className="text-slate-600">
                        目标 <b className="text-slate-900">{total}</b> 人次 · 早 {early} / 晚 {total - early}
                      </span>
                      <span className="text-[11px] text-slate-400">
                        {d.positions
                          .filter((p: any) => p.target_count > 0)
                          .map((p: any) => `${p.position_code}${p.target_count}`)
                          .join(" ")}
                      </span>
                    </div>
                  );
                })}
                {!demands.length ? <div className="px-4 py-6 text-center text-xs text-slate-400">暂无需求数据</div> : null}
              </div>
            </Card>
          ) : null}

          {!busy && !run ? (
            <Card className="bg-slate-50/60">
              <SectionTitle title="生成前检查" desc="以下规则来自系统配置，可在「系统配置」中调整。" />
              <ul className="space-y-1.5 text-xs leading-5 text-slate-600">
                <li>· 需求模板按日期类型（工作日 / 周末 / 节假日 / 旺季）套用，可在「每日需求」逐日覆盖。</li>
                <li>· A3/B3 仅长期员工或合资格主管可排；运营角色仅主管可承担，导师不计为主管。</li>
                <li>· 已批准请假为硬约束；未批准申请默认仅作为偏好（可配置为阻止）。</li>
                <li>· 长期员工月休目标为配置项，求解时以软约束方式尽量满足。</li>
              </ul>
            </Card>
          ) : null}
        </div>
      </div>
    </AppShell>
  );
}
