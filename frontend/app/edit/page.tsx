"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import AppShell from "@/components/AppShell";
import { Badge, Card, EmptyState, Modal, SectionTitle, Spinner, StatusBadge, useToast } from "@/components/ui";
import { TYPE_LABEL, ROLE_LABEL, del, get, patch, post, weekdayCN, fmtDate } from "@/lib/api";
import type { CoverageRow, Employee, Position, VersionDetail, Violation } from "@/lib/types";

type Cell = { position: string; role: string | null; locked: boolean };

function keyOf(date: string, empId: number) {
  return `${date}:${empId}`;
}

function EditInner() {
  const toast = useToast();
  const params = useSearchParams();
  const [versionId, setVersionId] = useState<number | null>(params.get("id") ? Number(params.get("id")) : null);
  const [version, setVersion] = useState<VersionDetail | null>(null);
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [positions, setPositions] = useState<Position[]>([]);
  const [leaves, setLeaves] = useState<Record<string, any[]>>({});
  const [draft, setDraft] = useState<Record<string, Cell>>({});
  const [original, setOriginal] = useState<Record<string, Cell>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [tableFilter, setTableFilter] = useState("");
  const [violations, setViolations] = useState<Violation[]>([]);
  const [pendingConflict, setPendingConflict] = useState<{ violations: Violation[]; apply: () => Promise<void> } | null>(null);
  const [showOnlyAssigned, setShowOnlyAssigned] = useState(false);
  const [swapOpen, setSwapOpen] = useState(false);
  const [swapDate, setSwapDate] = useState("");
  const [swapA, setSwapA] = useState<number | "">("");
  const [swapB, setSwapB] = useState<number | "">("");
  const [swapping, setSwapping] = useState(false);

  async function doSwap() {
    if (!version || !swapDate || swapA === "" || swapB === "") {
      toast.push("请选择日期和两名员工", "error");
      return;
    }
    if (swapA === swapB) {
      toast.push("请选择两名不同的员工", "error");
      return;
    }
    setSwapping(true);
    try {
      const res = await post<{ version: VersionDetail }>(`/api/schedule/versions/${version.id}/swap`, {
        work_date: swapDate,
        employee_a: swapA,
        employee_b: swapB,
      });
      await loadVersion(res.version?.id ?? version.id);
      setSwapOpen(false);
      setSwapA("");
      setSwapB("");
      toast.push("已互换两人当天的岗位与班次", "ok");
    } catch (e: any) {
      toast.push(e?.message || "互换失败", "error");
    } finally {
      setSwapping(false);
    }
  }

  const loadVersion = useCallback(async (id: number | null) => {
    const url = id ? `/api/schedule/versions/${id}` : `/api/schedule/current`;
    const v = await get<VersionDetail>(url);
    setVersion(v);
    setVersionId(v.id);
    const map: Record<string, Cell> = {};
    v.assignments.forEach((a) => {
      map[keyOf(a.work_date, a.employee_id)] = { position: a.position_code, role: a.role_code, locked: a.is_locked };
    });
    setDraft(map);
    setOriginal(JSON.parse(JSON.stringify(map)));
    setViolations(v.violations);
    return v;
  }, []);

  useEffect(() => {
    (async () => {
      setLoading(true);
      try {
        const [emps, pos, v] = await Promise.all([
          get<Employee[]>("/api/employees?include_inactive=false"),
          get<Position[]>("/api/positions?include_inactive=false"),
          loadVersion(versionId),
        ]);
        setEmployees(emps);
        setPositions(pos);
        const lv = await get<Record<string, any[]>>(
          `/api/availability-requests/calendar?start=${v.start_date}&end=${v.end_date}`
        );
        setLeaves(lv || {});
      } catch (e: any) {
        toast.push(e?.message || "加载失败", "error");
      } finally {
        setLoading(false);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const dates = useMemo(() => {
    if (!version) return [];
    const out: string[] = [];
    const s = new Date(`${version.start_date}T00:00:00`);
    const e = new Date(`${version.end_date}T00:00:00`);
    for (let d = new Date(s); d <= e; d.setDate(d.getDate() + 1)) {
      out.push(`${d.getFullYear()}-${`${d.getMonth() + 1}`.padStart(2, "0")}-${`${d.getDate()}`.padStart(2, "0")}`);
    }
    return out;
  }, [version]);

  const eligible = useCallback(
    (emp: Employee) => {
      const codes = new Set(emp.skills.filter((s) => s.allowed).map((s) => s.position_code));
      return positions.filter((p) => codes.has(p.code) && (!p.allowed_employment_types?.length || p.allowed_employment_types.includes(emp.employment_type)));
    },
    [positions]
  );

  const onLeave = useCallback(
    (empId: number, date: string) => {
      const list = leaves[String(empId)] || [];
      return list.some((l) => date >= l.start && date <= l.end && !l.shift);
    },
    [leaves]
  );

  const dirty = useMemo(() => {
    const keys = new Set([...Object.keys(draft), ...Object.keys(original)]);
    return [...keys].filter((k) => (draft[k]?.position || "") !== (original[k]?.position || ""));
  }, [draft, original]);

  const liveCoverage = useMemo(() => {
    if (!version) return [] as { date: string; code: string; name: string; target: number; min: number; assigned: number }[];
    const base = new Map<string, CoverageRow>();
    version.coverage.forEach((c) => base.set(`${c.work_date}:${c.position_code}`, c));
    const counts: Record<string, number> = {};
    Object.entries(draft).forEach(([k, v]) => {
      if (!v.position) return;
      const date = k.split(":")[0];
      const ck = `${date}:${v.position}`;
      counts[ck] = (counts[ck] || 0) + 1;
    });
    const rows: { date: string; code: string; name: string; target: number; min: number; assigned: number }[] = [];
    dates.forEach((d) => {
      positions.forEach((p) => {
        const c = base.get(`${d}:${p.code}`);
        const assigned = counts[`${d}:${p.code}`] || 0;
        const target = c?.target ?? 0;
        const min = c?.min ?? 0;
        if (target === 0 && assigned === 0) return;
        rows.push({ date: d, code: p.code, name: p.name, target, min, assigned });
      });
    });
    return rows;
  }, [version, draft, dates, positions]);

  const liveGaps = liveCoverage.filter((r) => r.assigned < r.min);

  const filledToday = useMemo(() => {
    const map: Record<string, number> = {};
    Object.entries(draft).forEach(([k, v]) => {
      if (!v.position) return;
      const date = k.split(":")[0];
      const ck = `${date}:${v.position}`;
      map[ck] = (map[ck] || 0) + 1;
    });
    return map;
  }, [draft]);

  const targetOf = useCallback(
    (date: string, code: string) => liveCoverage.find((c) => c.date === date && c.code === code)?.target ?? 0,
    [liveCoverage]
  );

  function setCell(date: string, emp: Employee, positionCode: string) {
    const k = keyOf(date, emp.id);
    const pos = positions.find((p) => p.code === positionCode);
    setDraft((prev) => ({
      ...prev,
      [k]: {
        position: positionCode,
        role: positionCode ? pos?.supervisor_role_code ?? null : null,
        locked: prev[k]?.locked ?? false,
      },
    }));
  }

  function toggleLock(date: string, empId: number) {
    const k = keyOf(date, empId);
    setDraft((prev) => {
      const cur = prev[k];
      if (!cur?.position) return prev;
      return { ...prev, [k]: { ...cur, locked: !cur.locked } };
    });
  }

  async function applyChanges(force = false) {
    if (!version) return;
    setSaving(true);
    const errs: Violation[] = [];
    let currentVersionId = version.id;
    try {
      for (const k of dirty) {
        const [date, empIdStr] = k.split(":");
        const empId = Number(empIdStr);
        const before = original[k];
        const after = draft[k];
        // 同一员工同日若原本有安排而被清空
        if (before?.position && !after?.position) {
          const target = version.assignments.find((a) => a.work_date === date && a.employee_id === empId);
          if (target) {
            const res = await del<any>(`/api/schedule/versions/${currentVersionId}/assignments/${target.id}`);
            currentVersionId = res.version.id;
          }
        } else if (!before?.position && after?.position) {
          const res = await post<any>(`/api/schedule/versions/${currentVersionId}/assignments?force=${force}`, {
            work_date: date,
            employee_id: empId,
            position_code: after.position,
            shift: positions.find((p) => p.code === after.position)?.shift || "early",
            role_code: after.role,
            is_locked: after.locked,
            note: "人工调整",
          });
          currentVersionId = res.version.id;
        } else if (before?.position && after?.position && before.position !== after.position) {
          const target = version.assignments.find((a) => a.work_date === date && a.employee_id === empId);
          if (target) {
            const res = await patch<any>(`/api/schedule/versions/${currentVersionId}/assignments/${target.id}?force=${force}`, {
              position_code: after.position,
              role_code: after.role,
            });
            currentVersionId = res.version.id;
          }
        }
      }
      await loadVersion(currentVersionId);
      toast.push(force ? "已强制保存（存在违规，请尽快修正）" : "更改已保存并通过校验", force ? "error" : "ok");
    } catch (e: any) {
      if (e?.status === 409) {
        errs.push(...(e.detail?.violations || []));
        setPendingConflict({
          violations: errs,
          apply: async () => {
            setPendingConflict(null);
            await applyChanges(true);
          },
        });
      } else {
        toast.push(e?.message || "保存失败", "error");
      }
    } finally {
      setSaving(false);
    }
  }

  async function publish() {
    if (!version) return;
    try {
      await post(`/api/schedule/versions/${version.id}/publish?allow_gap=true`, { change_note: "主管确认发布" });
      toast.push("已发布", "ok");
      await loadVersion(version.id);
    } catch (e: any) {
      if (e?.status === 409) {
        setPendingConflict({ violations: e.detail?.violations || [], apply: async () => setPendingConflict(null) });
      } else toast.push(e?.message || "发布失败", "error");
    }
  }

  async function reoptimize() {
    if (!version) return;
    setSaving(true);
    try {
      const res = await post<any>(`/api/schedule/versions/${version.id}/reoptimize`);
      await loadVersion(res.version.id);
      toast.push(`已在保留锁定安排的前提下重新优化（缺口 ${res.run.diagnostics?.total_gap ?? 0}）`, "ok");
    } catch (e: any) {
      toast.push(e?.message || "重新优化失败", "error");
    } finally {
      setSaving(false);
    }
  }

  const visibleEmployees = employees.filter((e) => {
    if (tableFilter && !e.name.includes(tableFilter)) return false;
    if (showOnlyAssigned && !dates.some((d) => draft[keyOf(d, e.id)]?.position)) return false;
    return true;
  });

  if (loading) {
    return (
      <Card>
        <Spinner label="加载排班数据…" />
      </Card>
    );
  }
  if (!version) {
    return <EmptyState title="没有可编辑的排班" desc="请先到「自动排班」生成预排班。" action={<Link href="/generate/" className="btn btn-primary mt-2">去生成</Link>} />;
  }

  return (
    <>
      <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-lg font-semibold">{version.name}</h1>
            <StatusBadge status={version.status} />
            {dirty.length ? <Badge tone="amber">{dirty.length} 处未保存</Badge> : <Badge tone="green">已同步</Badge>}
          </div>
          <p className="mt-0.5 text-xs text-slate-500">
            下拉选择即可调整；调整后即时校验，违规不会静默保存。🔒 表示锁定，重新优化时不会被移动。
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button className="btn" onClick={() => { setDraft(JSON.parse(JSON.stringify(original))); toast.push("已撤销未保存的更改"); }}>
            撤销更改
          </button>
          <button className="btn" onClick={() => setSwapOpen(true)}>
            调班互换
          </button>
          <button className="btn" disabled={saving} onClick={reoptimize}>
            锁定后重新优化
          </button>
          <button className="btn btn-primary" disabled={!dirty.length || saving} onClick={() => applyChanges(false)}>
            {saving ? "保存中…" : "保存更改"}
          </button>
          <button className="btn" disabled={version.status === "published"} onClick={publish}>
            发布
          </button>
        </div>
      </div>

      <div className="grid gap-3 xl:grid-cols-[1fr_300px]">
        <Card pad={false} className="overflow-hidden">
          <div className="flex flex-wrap items-center gap-2 border-b border-line px-3 py-2">
            <input className="input max-w-[180px]" placeholder="搜索员工姓名" value={tableFilter} onChange={(e) => setTableFilter(e.target.value)} />
            <label className="flex items-center gap-1.5 text-xs text-slate-600">
              <input type="checkbox" checked={showOnlyAssigned} onChange={(e) => setShowOnlyAssigned(e.target.checked)} />
              只看已排班
            </label>
            <span className="ml-auto text-[11px] text-slate-400">
              {visibleEmployees.length} 人 · {dates.length} 天
            </span>
          </div>
          <div className="flex items-center gap-1 border-b border-line bg-slate-50/70 px-3 py-1 text-[11px] text-slate-500 sm:hidden">
            <span>← 左右滑动查看三天 →</span>
            <span className="ml-auto text-slate-400">姓名列已固定</span>
          </div>
          <div className="overflow-auto scroll-thin" style={{ maxHeight: "calc(100vh - 240px)" }}>
            <table className="table">
              <thead>
                <tr>
                  <th className="sticky left-0 z-20 bg-slate-50" style={{ minWidth: 132 }}>员工</th>
                  {dates.map((d) => (
                    <th key={d} className="day-col text-center">
                      {fmtDate(d)} {weekdayCN(d)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {visibleEmployees.map((emp) => (
                  <tr key={emp.id}>
                    <td className="sticky left-0 z-10 bg-white">
                      <div className="flex items-center gap-1">
                        {emp.is_supervisor ? <span className="text-amber-500">★</span> : null}
                        <span className={`text-[13px] ${emp.is_supervisor ? "font-semibold text-slate-900" : "text-slate-700"}`}>{emp.name}</span>
                      </div>
                      <div className="text-[10px] text-slate-400">
                        {TYPE_LABEL[emp.employment_type] || emp.employment_type}
                        {emp.is_mentor ? " · 导师" : ""}
                        {emp.shift_preference !== "any" ? ` · 偏${emp.shift_preference === "early" ? "早" : "晚"}` : ""}
                      </div>
                    </td>
                    {dates.map((d) => {
                      const k = keyOf(d, emp.id);
                      const cell = draft[k];
                      const opts = eligible(emp);
                      const leave = onLeave(emp.id, d);
                      const pos = positions.find((p) => p.code === cell?.position);
                      return (
                        <td key={k} className={leave ? "bg-rose-50/40" : ""}>
                          <div className="flex items-center gap-1">
                            <select
                              className={`input !py-1 !text-[12px] ${pos ? (pos.shift === "early" ? "!bg-blue-50/70" : "!bg-rose-50/70") : ""}`}
                              value={cell?.position || ""}
                              onChange={(e) => setCell(d, emp, e.target.value)}
                              disabled={leave}
                            >
                              <option value="">— 休 —</option>
                              {opts.map((p) => {
                                const used = filledToday[`${d}:${p.code}`] || 0;
                                const target = targetOf(d, p.code);
                                const full = target > 0 && used >= target && cell?.position !== p.code;
                                return (
                                  <option key={p.code} value={p.code}>
                                    {p.code} {p.name.replace(/（.*?）/g, "")}
                                    {full ? "（已满）" : ""}
                                  </option>
                                );
                              })}
                              {cell?.position && !opts.some((p) => p.code === cell.position) ? (
                                <option value={cell.position}>{cell.position}（无资格）</option>
                              ) : null}
                            </select>
                            <button
                              className={`shrink-0 rounded px-1 text-[11px] ${cell?.locked ? "text-amber-600" : "text-slate-300 hover:text-slate-500"}`}
                              title={cell?.locked ? "解锁" : "锁定此安排"}
                              onClick={() => toggleLock(d, emp.id)}
                              disabled={!cell?.position}
                            >
                              🔒
                            </button>
                          </div>
                          {leave ? <div className="mt-0.5 text-[10px] text-rose-500">已批准请假</div> : null}
                          {cell?.role ? <div className="mt-0.5 text-[10px] text-violet-600">{ROLE_LABEL[cell.role] || cell.role}</div> : null}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>

        <div className="space-y-3">
          <Card>
            <SectionTitle title="实时缺口" desc="基于当前未保存的编辑即时计算。" />
            {liveGaps.length ? (
              <div className="space-y-1.5">
                {liveGaps.map((g, i) => (
                  <div key={i} className="rounded-lg border border-rose-200 bg-rose-50/60 px-2.5 py-1.5 text-[11px]">
                    <div className="font-medium text-rose-700">
                      {fmtDate(g.date)} · {g.code}
                    </div>
                    <div className="text-rose-600">
                      {g.name}：{g.assigned}/{g.min} 人（目标 {g.target}）
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="rounded-lg bg-emerald-50 px-2.5 py-2 text-[11px] text-emerald-700">所有岗位达到最低人数。</div>
            )}
            {liveCoverage.filter((c) => c.assigned > c.target).length ? (
              <div className="mt-2 space-y-1.5">
                <div className="text-[11px] font-medium text-amber-700">超编（发布前必须调整）</div>
                {liveCoverage
                  .filter((c) => c.assigned > c.target)
                  .map((c, i) => (
                    <div key={i} className="rounded-lg border border-amber-200 bg-amber-50 px-2.5 py-1.5 text-[11px] text-amber-800">
                      {fmtDate(c.date)} · {c.code}：{c.assigned}/{c.target} 人
                    </div>
                  ))}
              </div>
            ) : null}
          </Card>

          <Card>
            <SectionTitle title="校验问题" desc="错误会阻止发布；提醒可确认后继续。" />
            {violations.length ? (
              <div className="max-h-72 space-y-1.5 overflow-auto scroll-thin pr-1">
                {violations.map((v, i) => (
                  <div key={i} className="flex items-start gap-1.5 text-[11px] leading-4">
                    <Badge tone={v.level === "error" ? "rose" : "amber"}>{v.level === "error" ? "错" : "提"}</Badge>
                    <span className="text-slate-600">{v.message}</span>
                  </div>
                ))}
              </div>
            ) : (
              <div className="rounded-lg bg-emerald-50 px-2.5 py-2 text-[11px] text-emerald-700">当前版本无校验问题。</div>
            )}
          </Card>

          <Card>
            <SectionTitle title="岗位覆盖" desc="目标 / 已排（含未保存编辑）。" />
            <div className="max-h-[360px] space-y-1 overflow-auto scroll-thin pr-1">
              {liveCoverage.map((c, i) => (
                <div key={i} className="flex items-center justify-between gap-2 border-b border-slate-50 py-0.5 text-[11px] last:border-0">
                  <span className="text-slate-500">
                    {fmtDate(c.date)} {c.code}
                  </span>
                  <span className={`tabular-nums ${c.assigned < c.min ? "font-semibold text-rose-600" : c.assigned >= c.target ? "text-emerald-600" : "text-amber-600"}`}>
                    {c.assigned}/{c.target}
                  </span>
                </div>
              ))}
            </div>
            <div className="mt-2 text-[10px] text-slate-400">共 {liveCoverage.length} 个岗位-日期组合，可滚动查看。</div>
          </Card>
        </div>
      </div>

      <Modal
        open={swapOpen}
        onClose={() => setSwapOpen(false)}
        title="调班互换"
        footer={
          <>
            <button className="btn" onClick={() => setSwapOpen(false)}>
              取消
            </button>
            <button className="btn btn-primary" disabled={swapping} onClick={doSwap}>
              {swapping ? "互换中…" : "确认互换"}
            </button>
          </>
        }
      >
        <p className="text-xs text-slate-600">
          同一天两名员工互换岗位与班次（岗位变了班次跟着变）。互换后记为人工调整，重新优化时不会被挪走。
        </p>
        <div className="mt-3 grid gap-2">
          <label className="text-xs text-slate-600">
            日期
            <select className="input mt-1" value={swapDate} onChange={(e) => setSwapDate(e.target.value)}>
              <option value="">请选择日期</option>
              {dates.map((d) => (
                <option key={d} value={d}>
                  {fmtDate(d)} {weekdayCN(d)}
                </option>
              ))}
            </select>
          </label>
          <label className="text-xs text-slate-600">
            员工 A
            <select className="input mt-1" value={swapA} onChange={(e) => setSwapA(e.target.value ? Number(e.target.value) : "")}>
              <option value="">请选择员工</option>
              {employees.map((emp) => (
                <option key={emp.id} value={emp.id}>
                  {emp.name}
                  {swapDate && draft[keyOf(swapDate, emp.id)] ? `（${draft[keyOf(swapDate, emp.id)].position}）` : "（当天无排班）"}
                </option>
              ))}
            </select>
          </label>
          <label className="text-xs text-slate-600">
            员工 B
            <select className="input mt-1" value={swapB} onChange={(e) => setSwapB(e.target.value ? Number(e.target.value) : "")}>
              <option value="">请选择员工</option>
              {employees.map((emp) => (
                <option key={emp.id} value={emp.id}>
                  {emp.name}
                  {swapDate && draft[keyOf(swapDate, emp.id)] ? `（${draft[keyOf(swapDate, emp.id)].position}）` : "（当天无排班）"}
                </option>
              ))}
            </select>
          </label>
          <p className="text-[11px] text-slate-400">只有当天两人都已有排班才能互换；缺的那天请先在表格里补上。</p>
        </div>
      </Modal>

      <Modal
        open={!!pendingConflict}
        onClose={() => setPendingConflict(null)}
        title="存在违规排班"
        footer={
          <>
            <button className="btn" onClick={() => setPendingConflict(null)}>
              返回修改
            </button>
            <button className="btn btn-danger" onClick={() => pendingConflict?.apply()}>
              仍然强制保存
            </button>
          </>
        }
      >
        <p className="text-xs text-slate-600">
          系统检测到以下违规。强制保存后班表将带有错误标记，且无法发布，直到问题被修正：
        </p>
        <ul className="mt-2 space-y-1 text-[12px] text-rose-700">
          {pendingConflict?.violations.slice(0, 15).map((v, i) => (
            <li key={i}>· {v.message}</li>
          ))}
        </ul>
      </Modal>
    </>
  );
}

export default function EditPage() {
  return (
    <AppShell>
      <Suspense fallback={<Card><Spinner /></Card>}>
        <EditInner />
      </Suspense>
    </AppShell>
  );
}
