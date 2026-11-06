"use client";

import { useCallback, useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import { Badge, Card, EmptyState, Modal, SectionTitle, Spinner, useToast } from "@/components/ui";
import { apiBase, del, get, post, put, getToken, TYPE_LABEL, SHIFT_LABEL } from "@/lib/api";
import { downloadFile } from "@/lib/download";
import type { Employee, Pairing, Position, Skill } from "@/lib/types";

const EMPTY: Employee = {
  id: 0,
  employee_no: null,
  name: "",
  employment_type: "long_term",
  job_title: "普通员工",
  is_supervisor: false,
  is_mentor: false,
  is_active: true,
  shift_preference: "any",
  phone: "",
  hire_date: null,
  max_days_per_week: null,
  notes: "",
  skills: [],
};

export default function EmployeesPage() {
  const toast = useToast();
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [positions, setPositions] = useState<Position[]>([]);
  const [types, setTypes] = useState<{ code: string; name: string; counts_as_formal: boolean }[]>([]);
  const [loading, setLoading] = useState(true);
  const [q, setQ] = useState("");
  const [typeFilter, setTypeFilter] = useState("");
  const [editing, setEditing] = useState<Employee | null>(null);
  const [saving, setSaving] = useState(false);
  const [tab, setTab] = useState<"list" | "pairing">("list");
  const [pairings, setPairings] = useState<Pairing[]>([]);
  const [importOpen, setImportOpen] = useState(false);
  const [importData, setImportData] = useState<any>(null);
  const [importing, setImporting] = useState(false);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [batchMode, setBatchMode] = useState<"deactivate" | "purge" | null>(null);
  const [batchRunning, setBatchRunning] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [e, p, t, pr] = await Promise.all([
        get<Employee[]>("/api/employees"),
        get<Position[]>("/api/positions"),
        get<any[]>("/api/employee-types"),
        get<Pairing[]>("/api/pairing-preferences"),
      ]);
      setEmployees(e);
      setPositions(p);
      setTypes(t);
      setPairings(pr);
    } catch (e: any) {
      toast.push(e?.message || "加载失败", "error");
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    load();
  }, [load]);

  function toggleSkill(skill: Skill, code: string) {
    if (!editing) return;
    const exists = editing.skills.find((s) => s.position_code === code);
    let next: Skill[];
    if (exists) next = editing.skills.filter((s) => s.position_code !== code);
    else next = [...editing.skills, { position_code: code, priority: 3, proficiency: 3, allowed: true, note: "" }];
    setEditing({ ...editing, skills: next });
  }

  function updateSkill(code: string, field: "priority" | "proficiency" | "allowed", value: any) {
    if (!editing) return;
    setEditing({
      ...editing,
      skills: editing.skills.map((s) => (s.position_code === code ? { ...s, [field]: value } : s)),
    });
  }

  async function save() {
    if (!editing) return;
    if (!editing.name.trim()) {
      toast.push("请填写姓名", "error");
      return;
    }
    setSaving(true);
    try {
      const body = { ...editing };
      delete (body as any).id;
      if (editing.id) await put(`/api/employees/${editing.id}`, body);
      else await post("/api/employees", body);
      toast.push("已保存", "ok");
      setEditing(null);
      await load();
    } catch (e: any) {
      toast.push(e?.message || "保存失败", "error");
    } finally {
      setSaving(false);
    }
  }

  async function deactivate(emp: Employee) {
    if (!confirm(`确定停用 ${emp.name}？停用后不会出现在排班候选中（历史记录保留）。`)) return;
    try {
      await del(`/api/employees/${emp.id}`);
      toast.push("已停用", "ok");
      await load();
    } catch (e: any) {
      toast.push(e?.message || "操作失败", "error");
    }
  }

  function toggleSelect(id: number) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function runBatch() {
    if (!batchMode || selected.size === 0) return;
    setBatchRunning(true);
    try {
      const res = await post<any>("/api/employees/batch-delete", {
        employee_ids: [...selected],
        mode: batchMode,
      });
      const parts = [];
      if (res.deactivated) parts.push(`停用 ${res.deactivated} 人`);
      if (res.purged) parts.push(`删除 ${res.purged} 人`);
      const blocked = (res.skipped || []).filter((s: any) => s.reason.includes("排班记录"));
      toast.push(parts.length ? parts.join("，") : "没有需要变更的员工", parts.length ? "ok" : "info");
      if (blocked.length) {
        toast.push(`${blocked.length} 人因存在排班记录改为停用，可在提示中查看`, "info");
      }
      setSelected(new Set());
      setBatchMode(null);
      await load();
    } catch (e: any) {
      toast.push(e?.message || "批量操作失败", "error");
    } finally {
      setBatchRunning(false);
    }
  }

  async function previewImport(file: File) {
    const fd = new FormData();
    fd.append("file", file);
    const res = await fetch(`${apiBase()}/api/import/employees/preview`, {
      method: "POST",
      headers: { Authorization: `Bearer ${getToken()}` },
      body: fd,
    });
    const data = await res.json();
    if (!res.ok) {
      toast.push(data?.detail || "解析失败", "error");
      return;
    }
    setImportData(data);
  }

  async function commitImport() {
    if (!importData) return;
    setImporting(true);
    try {
      const payload = importData.rows
        .filter((r: any) => !r.exists)
        .map((r: any) => ({
          name: r.name,
          employment_type: r.employment_type,
          job_title: r.job_title,
          is_supervisor: r.is_supervisor,
          is_mentor: false,
          is_active: true,
          shift_preference: "any",
          skills: r.positions.map((code: string) => ({ position_code: code, priority: 3, proficiency: 3, allowed: true, note: "" })),
        }));
      const res = await post<any>("/api/import/employees/commit", payload);
      toast.push(`导入完成：新增 ${res.created} 人，跳过 ${res.skipped.length} 人`, "ok");
      setImportOpen(false);
      setImportData(null);
      await load();
    } catch (e: any) {
      toast.push(e?.message || "导入失败", "error");
    } finally {
      setImporting(false);
    }
  }

  const filtered = employees.filter((e) => {
    if (q && !e.name.includes(q) && !(e.employee_no || "").includes(q)) return false;
    if (typeFilter && e.employment_type !== typeFilter) return false;
    return true;
  });

  const allSelected = filtered.length > 0 && filtered.every((e) => selected.has(e.id));
  const someSelected = filtered.some((e) => selected.has(e.id)) && !allSelected;
  const selectedList = employees.filter((e) => selected.has(e.id));

  function toggleAll() {
    setSelected((prev) => {
      const next = new Set(prev);
      if (allSelected) filtered.forEach((e) => next.delete(e.id));
      else filtered.forEach((e) => next.add(e.id));
      return next;
    });
  }

  return (
    <AppShell>
      <div className="mb-3 flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-lg font-semibold">员工档案与岗位资格</h1>
          <p className="mt-0.5 text-xs text-slate-500">
            资格是硬约束，优先级/熟练度是优化输入；是否主管为独立字段，导师不自动视为主管。
          </p>
        </div>
        <div className="flex gap-2">
          <button className="btn" onClick={() => setImportOpen(true)}>
            导入旧表
          </button>
          <button className="btn" onClick={() => downloadFile("/api/export/employees-template.xlsx", "员工导入模板.xlsx").catch((e) => toast.push(e.message, "error"))}>
            下载模板
          </button>
          <button className="btn btn-primary" onClick={() => setEditing({ ...EMPTY, skills: [] })}>
            新增员工
          </button>
        </div>
      </div>

      <div className="mb-3 flex gap-1 rounded-lg border border-line bg-white p-1 text-[13px] w-fit">
        {[
          { k: "list", t: "员工列表" },
          { k: "pairing", t: "搭班偏好" },
        ].map((t) => (
          <button
            key={t.k}
            className={`rounded-md px-3 py-1.5 ${tab === t.k ? "bg-brand-50 font-medium text-brand-700" : "text-slate-500 hover:bg-slate-50"}`}
            onClick={() => setTab(t.k as any)}
          >
            {t.t}
          </button>
        ))}
      </div>

      {tab === "list" ? (
        <Card pad={false} className="overflow-hidden">
          <div className="flex flex-wrap items-center gap-2 border-b border-line px-3 py-2">
            <input className="input max-w-[200px]" placeholder="搜索姓名 / 工号" value={q} onChange={(e) => setQ(e.target.value)} />
            <select className="input max-w-[140px]" value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)}>
              <option value="">全部类型</option>
              {types.map((t) => (
                <option key={t.code} value={t.code}>
                  {t.name}
                </option>
              ))}
            </select>
            <span className="ml-auto text-[11px] text-slate-400">
              共 {employees.length} 人（主管 {employees.filter((e) => e.is_supervisor).length} 人 · 导师 {employees.filter((e) => e.is_mentor).length} 人）
            </span>
          </div>
          {selected.size > 0 ? (
            <div className="flex flex-wrap items-center gap-2 border-b border-line bg-brand-50/60 px-3 py-2 text-[12px]">
              <span className="font-medium text-brand-800">已选 {selected.size} 人</span>
              <button className="btn btn-sm" onClick={() => setBatchMode("deactivate")}>
                批量停用
              </button>
              <button className="btn btn-sm border-rose-200 text-rose-600 hover:bg-rose-50" onClick={() => setBatchMode("purge")}>
                批量删除
              </button>
              <button className="btn btn-sm btn-ghost" onClick={() => setSelected(new Set())}>
                取消选择
              </button>
              <span className="ml-auto text-[11px] text-slate-500">停用可随时恢复；删除不可撤销</span>
            </div>
          ) : null}
          {loading ? (
            <div className="p-4">
              <Spinner />
            </div>
          ) : (
            <div className="table-scroll overflow-auto scroll-thin" style={{ maxHeight: "calc(100vh - 260px)" }}>
              <table className="table">
                <thead>
                  <tr>
                    <th className="w-10">
                      <input
                        type="checkbox"
                        className="h-3.5 w-3.5 cursor-pointer accent-brand-600"
                        checked={allSelected}
                        ref={(el) => {
                          if (el) el.indeterminate = someSelected;
                        }}
                        onChange={toggleAll}
                        title={allSelected ? "取消全选" : "全选当前筛选结果"}
                        aria-label="全选"
                      />
                    </th>
                    <th>姓名</th>
                    <th>类型</th>
                    <th>职务</th>
                    <th>主管</th>
                    <th>班次偏好</th>
                    <th>岗位资格</th>
                    <th>状态</th>
                    <th className="text-right">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((e) => (
                    <tr key={e.id} className={selected.has(e.id) ? "bg-brand-50/40" : undefined}>
                      <td>
                        <input
                          type="checkbox"
                          className="h-3.5 w-3.5 cursor-pointer accent-brand-600"
                          checked={selected.has(e.id)}
                          onChange={() => toggleSelect(e.id)}
                          aria-label={`选择 ${e.name}`}
                        />
                      </td>
                      <td>
                        <div className="font-medium text-slate-800">{e.name}</div>
                        <div className="text-[10px] text-slate-400">{e.employee_no || "—"}</div>
                      </td>
                      <td className="text-slate-600">{TYPE_LABEL[e.employment_type] || e.employment_type}</td>
                      <td className="text-slate-600">
                        {e.job_title}
                        {e.is_mentor ? <Badge tone="violet" className="ml-1">导师</Badge> : null}
                      </td>
                      <td>{e.is_supervisor ? <Badge tone="rose">主管</Badge> : <span className="text-slate-300">—</span>}</td>
                      <td className="text-slate-600">{SHIFT_LABEL[e.shift_preference]}</td>
                      <td>
                        <div className="flex flex-wrap gap-1">
                          {e.skills.length ? (
                            e.skills.map((s) => (
                              <span key={s.position_code} className="chip border-slate-200 bg-slate-50 text-slate-600">
                                {s.position_code}
                                <span className="text-[9px] text-slate-400">P{s.priority}</span>
                              </span>
                            ))
                          ) : (
                            <span className="text-[11px] text-rose-500">未配置</span>
                          )}
                        </div>
                      </td>
                      <td>{e.is_active ? <Badge tone="green">在职</Badge> : <Badge tone="slate">停用</Badge>}</td>
                      <td className="text-right">
                        <button className="btn btn-sm" onClick={() => setEditing(JSON.parse(JSON.stringify(e)))}>
                          编辑
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      ) : (
        <PairingPanel employees={employees} pairings={pairings} reload={load} />
      )}

      {/* 员工编辑 */}
      <Modal
        open={!!editing}
        onClose={() => setEditing(null)}
        width="max-w-3xl"
        title={editing?.id ? `编辑员工 · ${editing.name}` : "新增员工"}
        footer={
          <>
            <button className="btn" onClick={() => setEditing(null)}>
              取消
            </button>
            {editing?.id ? (
              <button className="btn btn-danger" onClick={() => editing && deactivate(editing).then(() => setEditing(null))}>
                停用
              </button>
            ) : null}
            <button className="btn btn-primary" disabled={saving} onClick={save}>
              {saving ? "保存中…" : "保存"}
            </button>
          </>
        }
      >
        {editing ? (
          <div className="space-y-4">
            <div className="grid gap-3 sm:grid-cols-3">
              <div>
                <label className="label">姓名 *</label>
                <input className="input" value={editing.name} onChange={(e) => setEditing({ ...editing, name: e.target.value })} />
              </div>
              <div>
                <label className="label">工号</label>
                <input className="input" value={editing.employee_no || ""} onChange={(e) => setEditing({ ...editing, employee_no: e.target.value || null })} />
              </div>
              <div>
                <label className="label">钉钉用户ID</label>
                <input
                  className="input"
                  placeholder="导出考勤排班表用"
                  value={editing.external_user_id || ""}
                  onChange={(e) => setEditing({ ...editing, external_user_id: e.target.value || null })}
                />
              </div>
              <div>
                <label className="label">员工类型</label>
                <select className="input" value={editing.employment_type} onChange={(e) => setEditing({ ...editing, employment_type: e.target.value })}>
                  {types.map((t) => (
                    <option key={t.code} value={t.code}>
                      {t.name}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="label">职务</label>
                <input className="input" value={editing.job_title} onChange={(e) => setEditing({ ...editing, job_title: e.target.value })} />
              </div>
              <div>
                <label className="label">班次偏好</label>
                <select className="input" value={editing.shift_preference} onChange={(e) => setEditing({ ...editing, shift_preference: e.target.value as any })}>
                  <option value="any">无偏好</option>
                  <option value="early">偏好早班</option>
                  <option value="late">偏好晚班</option>
                </select>
              </div>
              <div>
                <label className="label">每周最多上班天数</label>
                <input
                  type="number"
                  className="input"
                  value={editing.max_days_per_week ?? ""}
                  placeholder="不限制"
                  onChange={(e) => setEditing({ ...editing, max_days_per_week: e.target.value ? Number(e.target.value) : null })}
                />
              </div>
            </div>
            <div className="flex flex-wrap gap-5 rounded-lg bg-slate-50 px-3 py-2.5">
              <label className="flex items-center gap-2 text-[13px] text-slate-700">
                <input type="checkbox" checked={editing.is_supervisor} onChange={(e) => setEditing({ ...editing, is_supervisor: e.target.checked })} />
                是否主管（可承担当值/辅助主管角色）
              </label>
              <label className="flex items-center gap-2 text-[13px] text-slate-700">
                <input type="checkbox" checked={editing.is_mentor} onChange={(e) => setEditing({ ...editing, is_mentor: e.target.checked })} />
                导师（不自动视为主管）
              </label>
              <label className="flex items-center gap-2 text-[13px] text-slate-700">
                <input type="checkbox" checked={editing.is_active} onChange={(e) => setEditing({ ...editing, is_active: e.target.checked })} />
                在职
              </label>
            </div>

            <div>
              <SectionTitle title="岗位资格" desc="勾选即具备资格（硬约束）；优先级 1 最优，熟练度 1–5 用于旺季优先分配。" />
              <div className="grid gap-1.5 sm:grid-cols-2">
                {positions.map((p) => {
                  const sk = editing.skills.find((s) => s.position_code === p.code);
                  return (
                    <div key={p.code} className={`rounded-lg border px-2.5 py-2 ${sk ? "border-brand-200 bg-brand-50/40" : "border-line"}`}>
                      <label className="flex items-center gap-2">
                        <input type="checkbox" checked={!!sk} onChange={() => toggleSkill(sk as any, p.code)} />
                        <span className="text-[12px] font-medium text-slate-700">{p.code}</span>
                        <span className="truncate text-[11px] text-slate-500">{p.name}</span>
                      </label>
                      {sk ? (
                        <div className="mt-1.5 flex items-center gap-2 pl-5 text-[11px] text-slate-500">
                          <label className="flex items-center gap-1">
                            优先级
                            <select
                              className="rounded border border-line px-1 py-0.5"
                              value={sk.priority}
                              onChange={(e) => updateSkill(p.code, "priority", Number(e.target.value))}
                            >
                              {[1, 2, 3, 4, 5].map((n) => (
                                <option key={n} value={n}>
                                  {n}
                                </option>
                              ))}
                            </select>
                          </label>
                          <label className="flex items-center gap-1">
                            熟练
                            <select
                              className="rounded border border-line px-1 py-0.5"
                              value={sk.proficiency}
                              onChange={(e) => updateSkill(p.code, "proficiency", Number(e.target.value))}
                            >
                              {[1, 2, 3, 4, 5].map((n) => (
                                <option key={n} value={n}>
                                  {n}
                                </option>
                              ))}
                            </select>
                          </label>
                        </div>
                      ) : null}
                    </div>
                  );
                })}
              </div>
            </div>
            <div>
              <label className="label">备注</label>
              <textarea className="input h-16 resize-none" value={editing.notes} onChange={(e) => setEditing({ ...editing, notes: e.target.value })} />
            </div>
          </div>
        ) : null}
      </Modal>

      {/* 批量停用 / 批量删除 */}
      <Modal
        open={batchMode !== null}
        onClose={() => {
          if (!batchRunning) setBatchMode(null);
        }}
        width="max-w-lg"
        title={batchMode === "purge" ? "确认批量删除员工" : "确认批量停用员工"}
        footer={
          <>
            <button className="btn" disabled={batchRunning} onClick={() => setBatchMode(null)}>
              取消
            </button>
            <button
              className={`btn ${batchMode === "purge" ? "btn-danger" : "btn-primary"}`}
              disabled={batchRunning || selected.size === 0}
              onClick={runBatch}
            >
              {batchRunning ? "处理中…" : batchMode === "purge" ? `确认删除 ${selected.size} 人` : `确认停用 ${selected.size} 人`}
            </button>
          </>
        }
      >
        <div className="space-y-3 text-[13px]">
          <p className="text-slate-600">
            {batchMode === "purge"
              ? "将永久删除以下员工的档案、岗位资格、请假申请与搭班偏好，此操作不可撤销。"
              : "以下员工将被置为停用，不再出现在排班候选中；历史排班、请假与偏好全部保留，可随时恢复。"}
          </p>
          <div className="max-h-48 overflow-y-auto rounded-lg border border-line bg-slate-50 px-3 py-2">
            <div className="flex flex-wrap gap-x-3 gap-y-1">
              {selectedList.map((e) => (
                <span key={e.id} className="text-[12px] text-slate-700">
                  {e.name}
                  {e.employee_no ? <span className="text-[10px] text-slate-400">({e.employee_no})</span> : null}
                </span>
              ))}
            </div>
          </div>
          {batchMode === "purge" ? (
            <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-[12px] leading-5 text-amber-800">
              已有排班记录的员工不会被删除，会自动改为停用并在结果中提示 —— 否则已发布的班表会指向不存在的员工。
            </div>
          ) : null}
        </div>
      </Modal>

      {/* 导入 */}
      <Modal
        open={importOpen}
        onClose={() => setImportOpen(false)}
        width="max-w-3xl"
        title="导入员工资料（预览后确认）"
        footer={
          <>
            <button className="btn" onClick={() => setImportOpen(false)}>
              取消
            </button>
            <button className="btn btn-primary" disabled={!importData || importing} onClick={commitImport}>
              {importing ? "导入中…" : `确认导入 ${importData ? importData.summary.new : 0} 人`}
            </button>
          </>
        }
      >
        <div className="space-y-3">
          <div className="rounded-lg border border-dashed border-line px-3 py-3 text-[12px] text-slate-600">
            选择 .xlsx 文件，系统自动识别「姓名 / 员工类型 / 职务 / 是否主管 / 岗位」列，
            <b>先预览映射与校验结果，不会直接覆盖生产数据</b>。
            <input
              type="file"
              accept=".xlsx"
              className="mt-2 block w-full text-xs"
              onChange={(e) => e.target.files?.[0] && previewImport(e.target.files[0])}
            />
          </div>
          {importData ? (
            <>
              <div className="flex gap-2 text-[11px] text-slate-600">
                <Badge tone="blue">共 {importData.summary.total} 行</Badge>
                <Badge tone="green">新增 {importData.summary.new}</Badge>
                <Badge tone="amber">重复 {importData.summary.duplicated}</Badge>
                <Badge tone="rose">主管 {importData.summary.supervisors}</Badge>
              </div>
              {importData.warnings.length ? (
                <ul className="max-h-24 space-y-0.5 overflow-auto text-[11px] text-amber-700">
                  {importData.warnings.slice(0, 20).map((w: string, i: number) => (
                    <li key={i}>· {w}</li>
                  ))}
                </ul>
              ) : null}
              <div className="max-h-64 overflow-auto scroll-thin rounded-lg border border-line">
                <table className="table">
                  <thead>
                    <tr>
                      <th>行</th>
                      <th>姓名</th>
                      <th>类型</th>
                      <th>职务</th>
                      <th>主管</th>
                      <th>岗位</th>
                      <th>状态</th>
                    </tr>
                  </thead>
                  <tbody>
                    {importData.rows.map((r: any) => (
                      <tr key={r.row}>
                        <td className="text-slate-400">{r.row}</td>
                        <td>{r.name}</td>
                        <td>{TYPE_LABEL[r.employment_type] || r.employment_type}</td>
                        <td>{r.job_title}</td>
                        <td>{r.is_supervisor ? "是" : "否"}</td>
                        <td className="max-w-[220px] truncate text-slate-500">{r.positions.join(", ") || "—"}</td>
                        <td>{r.exists ? <Badge tone="amber">已存在</Badge> : <Badge tone="green">新增</Badge>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          ) : null}
        </div>
      </Modal>
    </AppShell>
  );
}

function PairingPanel({ employees, pairings, reload }: { employees: Employee[]; pairings: Pairing[]; reload: () => Promise<void> }) {
  const toast = useToast();
  const [form, setForm] = useState({ employee_a_id: 0, employee_b_id: 0, preference_type: "prefer", scope: "same_shift", weight: 1 });
  const nameOf = (id: number) => employees.find((e) => e.id === id)?.name || `#${id}`;

  async function add() {
    if (!form.employee_a_id || !form.employee_b_id || form.employee_a_id === form.employee_b_id) {
      toast.push("请选择两位不同员工", "error");
      return;
    }
    try {
      await post("/api/pairing-preferences", form);
      toast.push("已添加", "ok");
      await reload();
    } catch (e: any) {
      toast.push(e?.message || "添加失败", "error");
    }
  }

  return (
    <div className="grid gap-3 lg:grid-cols-[360px_1fr]">
      <Card>
        <SectionTitle title="新增搭班偏好" desc="正向偏好加分，负向避让优先满足；不会覆盖资格与请假等硬约束。" />
        <div className="space-y-2.5">
          <div>
            <label className="label">员工 A</label>
            <select className="input" value={form.employee_a_id} onChange={(e) => setForm({ ...form, employee_a_id: Number(e.target.value) })}>
              <option value={0}>请选择</option>
              {employees.map((e) => (
                <option key={e.id} value={e.id}>
                  {e.name}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="label">员工 B</label>
            <select className="input" value={form.employee_b_id} onChange={(e) => setForm({ ...form, employee_b_id: Number(e.target.value) })}>
              <option value={0}>请选择</option>
              {employees.map((e) => (
                <option key={e.id} value={e.id}>
                  {e.name}
                </option>
              ))}
            </select>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="label">类型</label>
              <select className="input" value={form.preference_type} onChange={(e) => setForm({ ...form, preference_type: e.target.value })}>
                <option value="prefer">希望同班</option>
                <option value="avoid">避免同班</option>
              </select>
            </div>
            <div>
              <label className="label">作用范围</label>
              <select className="input" value={form.scope} onChange={(e) => setForm({ ...form, scope: e.target.value })}>
                <option value="same_shift">同班次</option>
                <option value="same_day">同日</option>
                <option value="same_position">同岗位</option>
              </select>
            </div>
          </div>
          <div>
            <label className="label">权重</label>
            <input type="number" step="0.5" className="input" value={form.weight} onChange={(e) => setForm({ ...form, weight: Number(e.target.value) })} />
          </div>
          <button className="btn btn-primary w-full" onClick={add}>
            添加
          </button>
        </div>
      </Card>
      <Card pad={false} className="overflow-hidden">
        <div className="border-b border-line px-4 py-3">
          <h2 className="text-[15px] font-semibold">搭班偏好列表</h2>
          <p className="mt-0.5 text-xs text-slate-500">首期由主管手工维护，不做 AI 推断。</p>
        </div>
        {pairings.length ? (
          <div className="table-scroll scroll-thin">
          <table className="table">
            <thead>
              <tr>
                <th className="col-pin">员工 A</th>
                <th>员工 B</th>
                <th>类型</th>
                <th>范围</th>
                <th>权重</th>
                <th className="text-right">操作</th>
              </tr>
            </thead>
            <tbody>
              {pairings.map((p) => (
                <tr key={p.id}>
                  <td className="col-pin">{nameOf(p.employee_a_id)}</td>
                  <td>{nameOf(p.employee_b_id)}</td>
                  <td>
                    <Badge tone={p.preference_type === "avoid" ? "rose" : "green"}>{p.preference_type === "avoid" ? "避免同班" : "希望同班"}</Badge>
                  </td>
                  <td className="text-slate-500">{{ same_shift: "同班次", same_day: "同日", same_position: "同岗位" }[p.scope] || p.scope}</td>
                  <td className="tabular-nums">{p.weight}</td>
                  <td className="text-right">
                    <button
                      className="btn btn-sm"
                      onClick={async () => {
                        await del(`/api/pairing-preferences/${p.id}`);
                        await reload();
                      }}
                    >
                      删除
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
        ) : (
          <div className="p-4">
            <EmptyState title="暂无搭班偏好" desc="可添加「希望同班」或「避免同班」，求解时按权重优化。" />
          </div>
        )}
      </Card>
    </div>
  );
}
