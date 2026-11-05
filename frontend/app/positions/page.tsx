"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import AppShell from "@/components/AppShell";
import { Badge, Card, Modal, SectionTitle, Spinner, useToast } from "@/components/ui";
import { get, post, put, TYPE_LABEL } from "@/lib/api";
import type { DemandTemplate, OperationalRole, Position } from "@/lib/types";

const DAY_TYPES = [
  { code: "weekday", label: "普通工作日" },
  { code: "weekend", label: "周末" },
  { code: "holiday", label: "法定节假日" },
  { code: "peak", label: "旺季/特殊日" },
];

export default function PositionsPage() {
  const toast = useToast();
  const [tab, setTab] = useState<"positions" | "templates" | "roles">("positions");
  const [positions, setPositions] = useState<Position[]>([]);
  const [roles, setRoles] = useState<OperationalRole[]>([]);
  const [templates, setTemplates] = useState<DemandTemplate[]>([]);
  const [editing, setEditing] = useState<Position | null>(null);
  const [tplDraft, setTplDraft] = useState<Record<string, { target: number; min: number }>>({});
  const [saving, setSaving] = useState(false);
  const [loading, setLoading] = useState(true);
  const [types, setTypes] = useState<any[]>([]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [p, r, t, et] = await Promise.all([
        get<Position[]>("/api/positions"),
        get<OperationalRole[]>("/api/operational-roles"),
        get<DemandTemplate[]>("/api/demand-templates"),
        get<any[]>("/api/employee-types"),
      ]);
      setPositions(p);
      setRoles(r);
      setTemplates(t);
      setTypes(et);
      const map: Record<string, { target: number; min: number }> = {};
      p.forEach((pos) =>
        DAY_TYPES.forEach((d) => {
          const row = t.find((x) => x.day_type === d.code && x.position_code === pos.code);
          map[`${d.code}:${pos.code}`] = { target: row?.target_count ?? 0, min: row?.min_count ?? 0 };
        })
      );
      setTplDraft(map);
    } catch (e: any) {
      toast.push(e?.message || "加载失败", "error");
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    load();
  }, [load]);

  const totals = useMemo(() => {
    const out: Record<string, { early: number; late: number }> = {};
    DAY_TYPES.forEach((d) => {
      let early = 0;
      let late = 0;
      positions.forEach((p) => {
        const v = tplDraft[`${d.code}:${p.code}`]?.target || 0;
        if (p.shift === "early") early += v;
        else late += v;
      });
      out[d.code] = { early, late };
    });
    return out;
  }, [tplDraft, positions]);

  async function saveTemplates() {
    setSaving(true);
    try {
      const items: any[] = [];
      DAY_TYPES.forEach((d) =>
        positions.forEach((p) => {
          const v = tplDraft[`${d.code}:${p.code}`];
          items.push({ day_type: d.code, position_code: p.code, target_count: v?.target || 0, min_count: v?.min || v?.target || 0 });
        })
      );
      await put("/api/demand-templates", items);
      toast.push("需求模板已保存", "ok");
      await load();
    } catch (e: any) {
      toast.push(e?.message || "保存失败", "error");
    } finally {
      setSaving(false);
    }
  }

  async function savePosition() {
    if (!editing) return;
    setSaving(true);
    try {
      const body = { ...editing };
      delete (body as any).id;
      if (editing.id) await put(`/api/positions/${editing.id}`, body);
      else await post("/api/positions", body);
      toast.push("已保存", "ok");
      setEditing(null);
      await load();
    } catch (e: any) {
      toast.push(e?.message || "保存失败", "error");
    } finally {
      setSaving(false);
    }
  }

  async function updateRole(r: OperationalRole, counts: boolean) {
    try {
      await put(`/api/operational-roles/${r.id}`, { ...r, counts_extra_slot: counts });
      await load();
      toast.push("运营角色配置已更新", "ok");
    } catch (e: any) {
      toast.push(e?.message || "更新失败", "error");
    }
  }

  return (
    <AppShell>
      <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold">岗位、需求模板与运营角色</h1>
          <p className="mt-0.5 text-xs text-slate-500">
            岗位代码即数据语义：班次、部门、区域、资格限制、默认人数全部可配置，不依赖单元格颜色。
          </p>
        </div>
        <div className="flex gap-2">
          {tab === "positions" ? (
            <button className="btn btn-primary" onClick={() => setEditing({} as Position)}>
              新增岗位
            </button>
          ) : null}
          {tab === "templates" ? (
            <button className="btn btn-primary" disabled={saving} onClick={saveTemplates}>
              {saving ? "保存中…" : "保存模板"}
            </button>
          ) : null}
        </div>
      </div>

      <div className="mb-3 flex w-fit gap-1 rounded-lg border border-line bg-white p-1 text-[13px]">
        {[
          { k: "positions", t: "岗位配置" },
          { k: "templates", t: "需求模板" },
          { k: "roles", t: "主管运营角色" },
        ].map((x) => (
          <button
            key={x.k}
            className={`rounded-md px-3 py-1.5 ${tab === x.k ? "bg-brand-50 font-medium text-brand-700" : "text-slate-500 hover:bg-slate-50"}`}
            onClick={() => setTab(x.k as any)}
          >
            {x.t}
          </button>
        ))}
      </div>

      {loading ? (
        <Card>
          <Spinner />
        </Card>
      ) : tab === "positions" ? (
        <Card pad={false} className="overflow-hidden">
          <div className="table-scroll scroll-thin">
          <table className="table">
            <thead>
              <tr>
                <th className="col-pin">代码</th>
                <th>岗位名称</th>
                <th>部门 / 区域</th>
                <th>班次</th>
                <th>资格限制</th>
                <th>主管要求</th>
                <th>状态</th>
                <th className="text-right">操作</th>
              </tr>
            </thead>
            <tbody>
              {positions.map((p) => (
                <tr key={p.id}>
                  <td className="col-pin">
                    <span className="inline-flex min-w-8 items-center justify-center rounded bg-slate-800 px-1.5 py-0.5 text-[11px] font-bold text-white">{p.code}</span>
                  </td>
                  <td className="min-w-[150px] font-medium text-slate-800">{p.name}</td>
                  <td className="text-slate-600">
                    {p.department === "sales" ? "销售" : "摄影"} · {p.area}
                  </td>
                  <td>
                    <span className={`chip ${p.shift === "early" ? "border-blue-200 bg-early text-blue-700" : "border-rose-200 bg-late text-rose-700"}`}>
                      {p.shift === "early" ? "早 09:00–17:00" : "晚 13:30–22:30"}
                    </span>
                  </td>
                  <td className="text-[12px] text-slate-600">
                    {p.allowed_employment_types.length ? p.allowed_employment_types.map((t) => TYPE_LABEL[t] || t).join("、") : "不限"}
                  </td>
                  <td className="text-[12px] text-slate-600">
                    {p.min_supervisors > 0 ? `至少 ${p.min_supervisors} 名` : "—"}
                    {p.supervisor_role_code ? <Badge tone="violet" className="ml-1">{p.supervisor_role_code}</Badge> : null}
                  </td>
                  <td>{p.is_active ? <Badge tone="green">启用</Badge> : <Badge tone="slate">停用</Badge>}</td>
                  <td className="text-right">
                    <button className="btn btn-sm" onClick={() => setEditing(JSON.parse(JSON.stringify(p)))}>
                      编辑
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
        </Card>
      ) : tab === "templates" ? (
        <Card pad={false} className="overflow-hidden">
          <div className="border-b border-line px-4 py-3">
            <SectionTitle
              title="需求模板（目标 / 最低人数）"
              desc="每格填「目标」，最低人数默认等于目标；如需独立最低值可在「每日需求」逐日覆盖。"
            />
          </div>
          <div className="overflow-auto scroll-thin">
            <table className="table">
              <thead>
                <tr>
                  <th>岗位</th>
                  <th>班次</th>
                  {DAY_TYPES.map((d) => (
                    <th key={d.code} className="text-center">
                      {d.label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {positions.map((p) => (
                  <tr key={p.code}>
                    <td>
                      <span className="font-medium">{p.code}</span>
                      <span className="ml-1 text-[11px] text-slate-500">{p.name}</span>
                    </td>
                    <td>{p.shift === "early" ? <Badge tone="blue">早</Badge> : <Badge tone="rose">晚</Badge>}</td>
                    {DAY_TYPES.map((d) => (
                      <td key={d.code} className="text-center">
                        <input
                          type="number"
                          min={0}
                          className="input !w-16 !py-1 text-center"
                          value={tplDraft[`${d.code}:${p.code}`]?.target ?? 0}
                          onChange={(e) =>
                            setTplDraft({
                              ...tplDraft,
                              [`${d.code}:${p.code}`]: { target: Number(e.target.value), min: Number(e.target.value) },
                            })
                          }
                        />
                      </td>
                    ))}
                  </tr>
                ))}
                <tr className="bg-slate-50 font-medium">
                  <td colSpan={2}>合计（早/晚）</td>
                  {DAY_TYPES.map((d) => (
                    <td key={d.code} className="text-center tabular-nums text-slate-700">
                      {totals[d.code].early} / {totals[d.code].late}
                    </td>
                  ))}
                </tr>
              </tbody>
            </table>
          </div>
        </Card>
      ) : (
        <div className="grid gap-3 md:grid-cols-3">
          {roles.map((r) => (
            <Card key={r.id}>
              <div className="flex items-center justify-between">
                <div>
                  <div className="text-sm font-semibold text-slate-900">{r.name}</div>
                  <div className="mt-0.5 font-mono text-[11px] text-slate-400">{r.code}</div>
                </div>
                <Badge tone="violet">{r.shift === "early" ? "早班" : "晚班"}</Badge>
              </div>
              <p className="mt-2 min-h-[40px] text-[12px] leading-5 text-slate-600">{r.description}</p>
              <div className="mt-2 space-y-1 rounded-lg bg-slate-50 px-2.5 py-2 text-[11px] text-slate-600">
                <div className="flex justify-between">
                  <span>绑定岗位</span>
                  <b>{r.bound_position_code || "—"}</b>
                </div>
                <div className="flex justify-between">
                  <span>额外占用名额</span>
                  <label className="flex items-center gap-1">
                    <input type="checkbox" checked={r.counts_extra_slot} onChange={(e) => updateRole(r, e.target.checked)} />
                    {r.counts_extra_slot ? "是" : "否（仅标记）"}
                  </label>
                </div>
              </div>
            </Card>
          ))}
          <Card className="bg-slate-50/60 md:col-span-3">
            <p className="text-[12px] leading-6 text-slate-600">
              <b>口径说明：</b>运营角色默认作为岗位分配上的标记，不额外占用人数名额，避免重复计数。
              若业务确认「当值主管」属于独立编制（例如 108 层晚班 4 人之外另加 1 名当值），可在此开启「额外占用名额」，
              求解时会额外增加对应岗位的名额。该口径为需求文档 17.3 待确认项，已做成配置。
            </p>
          </Card>
        </div>
      )}

      <Modal
        open={!!editing}
        onClose={() => setEditing(null)}
        title={editing?.id ? `编辑岗位 · ${editing.code}` : "新增岗位"}
        footer={
          <>
            <button className="btn" onClick={() => setEditing(null)}>
              取消
            </button>
            <button className="btn btn-primary" disabled={saving} onClick={savePosition}>
              保存
            </button>
          </>
        }
      >
        {editing ? (
          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <label className="label">岗位代码 *</label>
              <input className="input" value={editing.code || ""} onChange={(e) => setEditing({ ...editing, code: e.target.value.toUpperCase() })} />
            </div>
            <div>
              <label className="label">岗位名称 *</label>
              <input className="input" value={editing.name || ""} onChange={(e) => setEditing({ ...editing, name: e.target.value })} />
            </div>
            <div>
              <label className="label">部门</label>
              <select className="input" value={editing.department} onChange={(e) => setEditing({ ...editing, department: e.target.value as any })}>
                <option value="sales">销售</option>
                <option value="photography">摄影</option>
              </select>
            </div>
            <div>
              <label className="label">区域</label>
              <input className="input" value={editing.area || ""} onChange={(e) => setEditing({ ...editing, area: e.target.value })} />
            </div>
            <div>
              <label className="label">班次</label>
              <select className="input" value={editing.shift} onChange={(e) => setEditing({ ...editing, shift: e.target.value as any })}>
                <option value="early">早班 09:00–17:00</option>
                <option value="late">晚班 13:30–22:30</option>
              </select>
            </div>
            <div>
              <label className="label">排序</label>
              <input type="number" className="input" value={editing.sort_order ?? 0} onChange={(e) => setEditing({ ...editing, sort_order: Number(e.target.value) })} />
            </div>
            <div>
              <label className="label">至少需要主管人数</label>
              <input type="number" min={0} className="input" value={editing.min_supervisors ?? 0} onChange={(e) => setEditing({ ...editing, min_supervisors: Number(e.target.value) })} />
            </div>
            <div>
              <label className="label">绑定的运营角色代码</label>
              <select className="input" value={editing.supervisor_role_code || ""} onChange={(e) => setEditing({ ...editing, supervisor_role_code: e.target.value || null })}>
                <option value="">无</option>
                {roles.map((r) => (
                  <option key={r.id} value={r.code}>
                    {r.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="sm:col-span-2">
              <label className="label">允许的员工类型（不选=不限）</label>
              <div className="flex flex-wrap gap-3 rounded-lg border border-line px-3 py-2">
                {types.map((t) => (
                  <label key={t.code} className="flex items-center gap-1.5 text-[12px] text-slate-700">
                    <input
                      type="checkbox"
                      checked={(editing.allowed_employment_types || []).includes(t.code)}
                      onChange={(e) => {
                        const cur = new Set(editing.allowed_employment_types || []);
                        if (e.target.checked) cur.add(t.code);
                        else cur.delete(t.code);
                        setEditing({ ...editing, allowed_employment_types: [...cur] });
                      }}
                    />
                    {t.name}
                  </label>
                ))}
              </div>
              <p className="mt-1 text-[11px] text-slate-400">例：A3/B3 仅勾选「长期员工」，兼职与实习生即使有资格也不会被排入。</p>
            </div>
            <div className="sm:col-span-2">
              <label className="label">备注</label>
              <textarea className="input h-16 resize-none" value={editing.notes || ""} onChange={(e) => setEditing({ ...editing, notes: e.target.value })} />
            </div>
          </div>
        ) : null}
      </Modal>
    </AppShell>
  );
}
