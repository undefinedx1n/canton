"use client";

import { useMemo, useState } from "react";
import { fmtDate, TYPE_LABEL, weekdayCN } from "@/lib/api";
import type { MatrixCell, MatrixData } from "@/lib/types";
import { Card, EmptyState } from "@/components/ui";

function cellTitle(cell: MatrixCell, name: string, date: string): string {
  if (cell.kind === "work") {
    const parts = [name, fmtDate(date), `${cell.position_code} ${cell.position_name || ""}`.trim()];
    if (cell.role_name) parts.push(cell.role_name);
    parts.push(cell.shift === "early" ? "早班" : "晚班");
    parts.push(cell.source === "manual" ? "人工预排" : "自动排班");
    if (cell.is_locked) parts.push("已锁定");
    return parts.join(" · ");
  }
  if (cell.kind === "leave") return `${name} · ${fmtDate(date)} · 请假（${cell.range || ""}）${cell.reason ? " · " + cell.reason : ""}`;
  if (cell.kind === "inactive") return `${name} · 已停用`;
  return `${name} · ${fmtDate(date)} · 休息`;
}

function Cell({ cell, name, date }: { cell: MatrixCell | undefined; name: string; date: string }) {
  if (!cell) return <span className="text-slate-300">-</span>;
  if (cell.kind === "work") {
    const duty = cell.role_code === "duty_supervisor";
    const support = !!cell.role_code && !duty;
    return (
      <span
        title={cellTitle(cell, name, date)}
        className={`inline-block tabular-nums ${duty ? "font-bold text-rose-600" : support ? "font-bold text-violet-600" : "text-slate-700"}`}
      >
        {cell.position_code}
      </span>
    );
  }
  if (cell.kind === "leave") {
    return (
      <span title={cellTitle(cell, name, date)} className="font-medium text-amber-600">
        假
      </span>
    );
  }
  if (cell.kind === "inactive") {
    return (
      <span title={cellTitle(cell, name, date)} className="text-slate-300">
        -
      </span>
    );
  }
  return (
    <span title={cellTitle(cell, name, date)} className="text-slate-300">
      V
    </span>
  );
}

export default function MatrixBoard({ data }: { data: MatrixData | null }) {
  const [keyword, setKeyword] = useState("");
  const [onlySupervisors, setOnlySupervisors] = useState(false);

  const rows = useMemo(() => {
    if (!data) return [];
    const kw = keyword.trim();
    return data.rows.filter((r) => (!onlySupervisors || r.is_supervisor) && (!kw || r.name.includes(kw)));
  }, [data, keyword, onlySupervisors]);

  if (!data || !data.days.length) return <EmptyState title="暂无排班" desc="先生成预排班，这里会按姓名 × 日期展示。" />;

  const today = new Date();
  const todayISO = `${today.getFullYear()}-${`${today.getMonth() + 1}`.padStart(2, "0")}-${`${today.getDate()}`.padStart(2, "0")}`;

  return (
    <Card pad={false} className="mt-3 overflow-hidden">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-line px-3 py-2">
        <div className="text-[13px] font-semibold text-slate-900">人员排班表</div>
        <div className="text-[11px] text-slate-500">
          {data.summary.employees} 人 · {data.summary.dates} 天 · 平均上班 {data.summary.avg_work_days} 天 · 每日平均休息{" "}
          {data.summary.avg_rest_per_day} 人
        </div>
        <div className="ml-auto flex items-center gap-2">
          <input
            className="input h-8 w-32 text-xs"
            placeholder="搜索姓名"
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
          />
          <label className="flex cursor-pointer items-center gap-1 text-[11px] text-slate-600">
            <input type="checkbox" checked={onlySupervisors} onChange={(e) => setOnlySupervisors(e.target.checked)} />
            仅看主管
          </label>
        </div>
      </div>

      <div className="max-h-[72vh] overflow-auto">
        <table className="min-w-full border-collapse text-[11px]">
          <thead>
            <tr>
              <th className="sticky left-0 top-0 z-30 h-[28px] min-w-[92px] border border-slate-200 bg-white px-2 text-left font-semibold text-rose-600">
                日期
              </th>
              {data.days.map((d) => (
                <th
                  key={d.date}
                  title={`${d.date} 星期${weekdayCN(d.date).slice(1)}${d.label ? " · " + d.label : ""}`}
                  className={`sticky top-0 z-20 h-[28px] min-w-[54px] whitespace-nowrap border border-slate-200 px-1 font-semibold ${
                    d.date === todayISO ? "bg-amber-50 text-rose-600" : "bg-white text-rose-600"
                  }`}
                >
                  {d.date.slice(5)}
                </th>
              ))}
            </tr>
            <tr>
              <th className="sticky left-0 top-[28px] z-30 h-[24px] border border-slate-200 bg-white px-2 text-left text-[10px] font-medium text-slate-500">
                星期＼姓名
              </th>
              {data.days.map((d) => (
                <th
                  key={d.date}
                  className={`sticky top-[28px] z-20 h-[24px] whitespace-nowrap border border-slate-200 px-1 text-[10px] font-normal ${
                    d.date === todayISO ? "bg-amber-50 text-slate-600" : "bg-white text-slate-500"
                  }`}
                >
                  星期{weekdayCN(d.date).slice(1)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.employee_id} className="group">
                <td
                  title={`${r.name}${r.job_title ? " · " + r.job_title : ""} · ${TYPE_LABEL[r.employment_type] || r.employment_type} · 上班 ${r.work_days} 天 / 休息 ${r.rest_days} 天${r.duty_count ? ` / 当值 ${r.duty_count} 次` : ""}`}
                  className={`sticky left-0 z-10 whitespace-nowrap border border-slate-200 px-2 py-1 text-left font-semibold ${
                    r.is_supervisor ? "bg-[#FCE4D6] text-slate-900" : "bg-white text-slate-800 group-hover:bg-slate-50"
                  }`}
                >
                  {r.name}
                  {!r.is_active ? <span className="ml-1 text-[9px] font-normal text-slate-400">停用</span> : null}
                </td>
                {data.days.map((d) => (
                  <td
                    key={d.date}
                    className={`border border-slate-200 px-0.5 py-1 text-center ${d.date === todayISO ? "bg-amber-50/40" : ""}`}
                  >
                    <Cell cell={r.cells[d.date]} name={r.name} date={d.date} />
                  </td>
                ))}
              </tr>
            ))}
            {!rows.length ? (
              <tr>
                <td colSpan={data.days.length + 1} className="border border-slate-200 px-3 py-6 text-center text-xs text-slate-400">
                  没有匹配的人员
                </td>
              </tr>
            ) : null}
          </tbody>
          <tfoot>
            <tr>
              <td className="sticky left-0 z-10 border border-slate-200 bg-slate-50 px-2 py-1 text-left text-[10px] font-semibold text-slate-600">
                上班人数
              </td>
              {data.days.map((d) => (
                <td key={d.date} className="border border-slate-200 bg-slate-50 px-1 py-1 text-center tabular-nums text-slate-700">
                  {d.work_count}
                </td>
              ))}
            </tr>
            <tr>
              <td className="sticky left-0 z-10 border border-slate-200 bg-slate-50 px-2 py-1 text-left text-[10px] font-semibold text-slate-500">
                休息人数
              </td>
              {data.days.map((d) => (
                <td key={d.date} className="border border-slate-200 bg-slate-50 px-1 py-1 text-center tabular-nums text-slate-500">
                  {d.rest_count}
                </td>
              ))}
            </tr>
            {data.summary.total_leave > 0 ? (
              <tr>
                <td className="sticky left-0 z-10 border border-slate-200 bg-slate-50 px-2 py-1 text-left text-[10px] font-semibold text-amber-600">
                  请假人数
                </td>
                {data.days.map((d) => (
                  <td key={d.date} className="border border-slate-200 bg-slate-50 px-1 py-1 text-center tabular-nums text-amber-600">
                    {d.leave_count || ""}
                  </td>
                ))}
              </tr>
            ) : null}
          </tfoot>
        </table>
      </div>

      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-line px-3 py-2 text-[10px] text-slate-500">
        <span className="font-medium text-rose-600">B2 当值主管</span>
        <span className="font-medium text-violet-600">A2 / B1 辅助主管</span>
        <span className="text-slate-700">其他岗位代码</span>
        <span className="text-slate-400">V 休息</span>
        <span className="text-amber-600">假 已批准请假</span>
        <span className="ml-auto">与导出的「人员排班」工作表同构，可直接贴回原 Excel</span>
      </div>
    </Card>
  );
}
