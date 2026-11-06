"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import { Badge, Card, Modal, SectionTitle, Spinner, StatusBadge, useToast } from "@/components/ui";
import { get, post, del } from "@/lib/api";
import { downloadFile } from "@/lib/download";
import type { Version } from "@/lib/types";

export default function VersionsPage() {
  const toast = useToast();
  const [rows, setRows] = useState<Version[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("");
  const [selected, setSelected] = useState<number[]>([]);
  const [compare, setCompare] = useState<any>(null);
  const [audits, setAudits] = useState<any[]>([]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [v, a] = await Promise.all([
        get<Version[]>(`/api/schedule/versions${filter ? `?status=${filter}` : ""}`),
        get<any[]>("/api/audit?limit=40"),
      ]);
      setRows(v);
      setAudits(a);
    } catch (e: any) {
      toast.push(e?.message || "加载失败", "error");
    } finally {
      setLoading(false);
    }
  }, [filter, toast]);

  useEffect(() => {
    load();
  }, [load]);

  function toggle(id: number) {
    setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : s.length >= 2 ? [s[1], id] : [...s, id]));
  }

  async function runCompare() {
    if (selected.length !== 2) {
      toast.push("请选择两个版本进行对比", "error");
      return;
    }
    try {
      const res = await get(`/api/schedule/versions/${selected[0]}/compare/${selected[1]}`);
      setCompare(res);
    } catch (e: any) {
      toast.push(e?.message || "对比失败", "error");
    }
  }

  return (
    <AppShell>
      <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold">发布历史与版本对比</h1>
          <p className="mt-0.5 text-xs text-slate-500">发布后版本不可变；修改已发布班表会自动派生新版本并记录差异与操作留痕。</p>
        </div>
        <div className="flex items-center gap-2">
          <select className="input max-w-[140px]" value={filter} onChange={(e) => setFilter(e.target.value)}>
            <option value="">全部状态</option>
            <option value="draft">草稿</option>
            <option value="published">已发布</option>
            <option value="archived">已归档</option>
          </select>
          <button className="btn" disabled={selected.length !== 2} onClick={runCompare}>
            对比所选（{selected.length}/2）
          </button>
        </div>
      </div>

      <div className="grid gap-3 xl:grid-cols-[1fr_380px]">
        <Card pad={false} className="overflow-hidden">
          {loading ? (
            <div className="p-4">
              <Spinner />
            </div>
          ) : (
            <div className="table-scroll overflow-auto scroll-thin">
              <table className="table">
                <thead>
                  <tr>
                    <th className="w-8"></th>
                    <th>版本</th>
                    <th>日期范围</th>
                    <th>状态</th>
                    <th>发布</th>
                    <th>说明</th>
                    <th className="text-right">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((v) => (
                    <tr key={v.id}>
                      <td>
                        <input type="checkbox" checked={selected.includes(v.id)} onChange={() => toggle(v.id)} />
                      </td>
                      <td>
                        <div className="font-medium text-slate-800">{v.name}</div>
                        <div className="text-[10px] text-slate-400">
                          v{v.version_no} · #{v.id}
                          {v.parent_version_id ? ` · 源自 #${v.parent_version_id}` : ""}
                        </div>
                      </td>
                      <td className="whitespace-nowrap text-[12px] text-slate-600">
                        {v.start_date} ~ {v.end_date}
                      </td>
                      <td>
                        <StatusBadge status={v.status} />
                      </td>
                      <td className="text-[11px] text-slate-500">
                        {v.published_at ? (
                          <>
                            {v.published_by}
                            <br />
                            {new Date(v.published_at).toLocaleString("zh-CN", { hour12: false })}
                          </>
                        ) : (
                          "—"
                        )}
                      </td>
                      <td className="max-w-[220px] text-[11px] text-slate-500" title={v.change_note}>
                        <div className="truncate">{v.change_note || "—"}</div>
                        <div className="text-[10px] text-slate-400">创建：{v.created_by}</div>
                      </td>
                      <td className="text-right">
                        <div className="flex justify-end gap-1">
                          <Link className="btn btn-sm" href={`/edit/?id=${v.id}`}>
                            查看/调整
                          </Link>
                          <button
                            className="btn btn-sm"
                            onClick={() => downloadFile(`/api/export/versions/${v.id}.xlsx`, "排班表.xlsx").catch((e) => toast.push(e.message, "error"))}
                          >
                            导出
                          </button>
                          <button
                            className="btn btn-sm"
                            title="按钉钉考勤排班表格式导出，用于上交人力"
                            onClick={() =>
                              downloadFile(
                                `/api/export/versions/${v.id}/dingtalk.xlsx`,
                                `柯达广州塔排班表${v.start_date}-${v.end_date}.xlsx`,
                              ).catch((e) => toast.push(e.message, "error"))
                            }
                          >
                            导出钉钉表
                          </button>
                          {v.status === "draft" ? (
                            <button
                              className="btn btn-sm btn-danger"
                              onClick={async () => {
                                if (!confirm("删除该草稿版本？")) return;
                                await del(`/api/schedule/versions/${v.id}`);
                                await load();
                              }}
                            >
                              删除
                            </button>
                          ) : null}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        <Card pad={false}>
          <div className="border-b border-line px-4 py-3">
            <SectionTitle title="操作留痕" desc="关键排班操作与配置修改的审计日志（最近 40 条）。" />
          </div>
          <div className="max-h-[calc(100vh-240px)] space-y-2 overflow-auto scroll-thin px-3 py-3">
            {audits.map((a) => (
              <div key={a.id} className="rounded-lg border border-line px-2.5 py-2">
                <div className="flex items-center justify-between">
                  <Badge tone={a.action === "publish" ? "green" : a.action.includes("delete") ? "rose" : "slate"}>{a.action}</Badge>
                  <span className="text-[10px] text-slate-400">{new Date(a.created_at).toLocaleString("zh-CN", { hour12: false })}</span>
                </div>
                <div className="mt-1 text-[11px] leading-4 text-slate-600">{a.summary}</div>
                <div className="mt-0.5 text-[10px] text-slate-400">
                  {a.actor} · {a.entity}
                  {a.entity_id ? `#${a.entity_id}` : ""}
                </div>
              </div>
            ))}
            {!audits.length ? <div className="text-center text-xs text-slate-400">暂无日志</div> : null}
          </div>
        </Card>
      </div>

      <Modal open={!!compare} onClose={() => setCompare(null)} title="版本差异对比" width="max-w-4xl">
        {compare ? (
          <div>
            <div className="mb-3 flex flex-wrap gap-2 text-[12px]">
              <Badge tone="slate">左：{compare.left.name} v{compare.left.version_no}</Badge>
              <Badge tone="blue">右：{compare.right.name} v{compare.right.version_no}</Badge>
              <Badge tone="green">新增 {compare.summary.added}</Badge>
              <Badge tone="rose">移除 {compare.summary.removed}</Badge>
              <Badge tone="amber">变更 {compare.summary.changed}</Badge>
            </div>
            <div className="max-h-[55vh] overflow-auto scroll-thin rounded-lg border border-line">
              <table className="table">
                <thead>
                  <tr>
                    <th>日期</th>
                    <th>员工</th>
                    <th>变化</th>
                    <th>左</th>
                    <th>右</th>
                  </tr>
                </thead>
                <tbody>
                  {compare.changes.map((c: any, i: number) => (
                    <tr key={i}>
                      <td className="whitespace-nowrap">{c.work_date}</td>
                      <td>{c.employee_name}</td>
                      <td>
                        <Badge tone={c.type === "added" ? "green" : c.type === "removed" ? "rose" : "amber"}>
                          {c.type === "added" ? "新增" : c.type === "removed" ? "移除" : "变更"}
                        </Badge>
                      </td>
                      <td className="text-slate-500">{c.before_position || "—"}</td>
                      <td className="text-slate-800">{c.after_position || "—"}</td>
                    </tr>
                  ))}
                  {!compare.changes.length ? (
                    <tr>
                      <td colSpan={5} className="py-6 text-center text-xs text-slate-400">
                        两个版本内容一致
                      </td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
            </div>
          </div>
        ) : null}
      </Modal>
    </AppShell>
  );
}
