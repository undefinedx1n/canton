"use client";

import { useCallback, useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import { Badge, Card, Modal, SectionTitle, Spinner, StatusBadge, useToast } from "@/components/ui";
import { get, patch, post, del, todayISO } from "@/lib/api";
import type { AvailabilityRequest, Employee } from "@/lib/types";

const TYPE_CN: Record<string, string> = { leave: "请假", rest: "休息申请", position: "岗位申请", unavailable: "不可用时间" };

export default function RequestsPage() {
  const toast = useToast();
  const [rows, setRows] = useState<AvailabilityRequest[]>([]);
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [status, setStatus] = useState("");
  const [loading, setLoading] = useState(true);
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({
    employee_id: 0,
    request_type: "leave",
    start_date: todayISO(),
    end_date: todayISO(),
    shift: "",
    reason: "",
  });
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [r, e] = await Promise.all([
        get<AvailabilityRequest[]>(`/api/availability-requests${status ? `?status=${status}` : ""}`),
        get<Employee[]>("/api/employees?include_inactive=false"),
      ]);
      setRows(r);
      setEmployees(e);
    } catch (e: any) {
      toast.push(e?.message || "加载失败", "error");
    } finally {
      setLoading(false);
    }
  }, [status, toast]);

  useEffect(() => {
    load();
  }, [load]);

  async function review(id: number, next: "approved" | "rejected") {
    try {
      await patch(`/api/availability-requests/${id}/review`, { status: next, reviewer: "" });
      toast.push(next === "approved" ? "已批准，将作为排班硬约束" : "已拒绝", "ok");
      await load();
    } catch (e: any) {
      toast.push(e?.message || "操作失败", "error");
    }
  }

  async function submit() {
    if (!form.employee_id) {
      toast.push("请选择员工", "error");
      return;
    }
    setSaving(true);
    try {
      await post("/api/availability-requests", {
        ...form,
        shift: form.shift || null,
      });
      toast.push("已提交申请", "ok");
      setOpen(false);
      await load();
    } catch (e: any) {
      toast.push(e?.message || "提交失败", "error");
    } finally {
      setSaving(false);
    }
  }

  const nameOf = (id: number) => employees.find((e) => e.id === id)?.name || rows.find((r) => r.employee_id === id)?.employee?.name || `#${id}`;
  const pendingCount = rows.filter((r) => r.status === "pending").length;

  return (
    <AppShell>
      <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold">请假与特殊申请</h1>
          <p className="mt-0.5 text-xs text-slate-500">
            首期由主管录入/确认，员工门户后续迭代。已批准的不可用时间属于硬约束，未批准默认仅作偏好（可在系统配置调整）。
          </p>
        </div>
        <div className="flex items-center gap-2">
          <select className="input max-w-[140px]" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">全部状态</option>
            <option value="pending">待审批</option>
            <option value="approved">已批准</option>
            <option value="rejected">已拒绝</option>
          </select>
          <button className="btn btn-primary" onClick={() => setOpen(true)}>
            录入申请
          </button>
        </div>
      </div>

      {pendingCount > 0 ? (
        <Card className="mb-3 border-amber-200 bg-amber-50/50 text-xs text-amber-800">
          当前有 <b>{pendingCount}</b> 条待审批申请。批准前不会阻止排班（默认口径），请尽快处理以免影响已生成班表。
        </Card>
      ) : null}

      <Card pad={false} className="overflow-hidden">
        {loading ? (
          <div className="p-4">
            <Spinner />
          </div>
        ) : rows.length ? (
          <div className="table-scroll scroll-thin">
          <table className="table">
            <thead>
              <tr>
                <th className="col-pin">员工</th>
                <th>类型</th>
                <th>日期</th>
                <th>班次</th>
                <th>原因</th>
                <th>状态</th>
                <th>审批人</th>
                <th className="text-right">操作</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id}>
                  <td className="col-pin font-medium text-slate-800">{nameOf(r.employee_id)}</td>
                  <td>
                    <Badge tone={r.request_type === "leave" ? "rose" : "slate"}>{TYPE_CN[r.request_type] || r.request_type}</Badge>
                  </td>
                  <td className="tabular-nums text-slate-600">
                    {r.start_date === r.end_date ? r.start_date : `${r.start_date} ~ ${r.end_date}`}
                  </td>
                  <td className="text-slate-600">{r.shift ? (r.shift === "early" ? "早班" : "晚班") : "全天"}</td>
                  <td className="max-w-[240px] truncate text-slate-600" title={r.reason}>
                    {r.reason || "—"}
                  </td>
                  <td>
                    <StatusBadge status={r.status} />
                  </td>
                  <td className="text-slate-500">{r.reviewer || "—"}</td>
                  <td className="text-right">
                    <div className="flex justify-end gap-1">
                      {r.status !== "approved" ? (
                        <button className="btn btn-sm" onClick={() => review(r.id, "approved")}>
                          批准
                        </button>
                      ) : null}
                      {r.status !== "rejected" ? (
                        <button className="btn btn-sm" onClick={() => review(r.id, "rejected")}>
                          拒绝
                        </button>
                      ) : null}
                      <button
                        className="btn btn-sm btn-danger"
                        onClick={async () => {
                          if (!confirm("删除该申请记录？")) return;
                          await del(`/api/availability-requests/${r.id}`);
                          await load();
                        }}
                      >
                        删除
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
        ) : (
          <div className="p-6 text-center text-sm text-slate-400">暂无申请记录</div>
        )}
      </Card>

      <Modal
        open={open}
        onClose={() => setOpen(false)}
        title="录入请假 / 不可用申请"
        footer={
          <>
            <button className="btn" onClick={() => setOpen(false)}>
              取消
            </button>
            <button className="btn btn-primary" disabled={saving} onClick={submit}>
              提交
            </button>
          </>
        }
      >
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="sm:col-span-2">
            <label className="label">员工</label>
            <select className="input" value={form.employee_id} onChange={(e) => setForm({ ...form, employee_id: Number(e.target.value) })}>
              <option value={0}>请选择</option>
              {employees.map((e) => (
                <option key={e.id} value={e.id}>
                  {e.name}（{e.job_title}）
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="label">类型</label>
            <select className="input" value={form.request_type} onChange={(e) => setForm({ ...form, request_type: e.target.value })}>
              <option value="leave">请假</option>
              <option value="rest">休息申请</option>
              <option value="position">岗位申请</option>
              <option value="unavailable">特定时段不可用</option>
            </select>
          </div>
          <div>
            <label className="label">班次（可选）</label>
            <select className="input" value={form.shift} onChange={(e) => setForm({ ...form, shift: e.target.value })}>
              <option value="">全天</option>
              <option value="early">仅早班</option>
              <option value="late">仅晚班</option>
            </select>
          </div>
          <div>
            <label className="label">开始日期</label>
            <input type="date" className="input" value={form.start_date} onChange={(e) => setForm({ ...form, start_date: e.target.value })} />
          </div>
          <div>
            <label className="label">结束日期</label>
            <input type="date" className="input" value={form.end_date} onChange={(e) => setForm({ ...form, end_date: e.target.value })} />
          </div>
          <div className="sm:col-span-2">
            <label className="label">原因</label>
            <textarea className="input h-20 resize-none" value={form.reason} onChange={(e) => setForm({ ...form, reason: e.target.value })} />
          </div>
        </div>
      </Modal>
    </AppShell>
  );
}
