"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import AppShell from "@/components/AppShell";
import { Badge, Card, EmptyState, Modal, SectionTitle, Spinner, Stat, StatusBadge, useToast } from "@/components/ui";
import { get, post, todayISO, weekdayCN, fmtDate, ROLE_LABEL } from "@/lib/api";
import { downloadFile } from "@/lib/download";
import type { VersionDetail, VersionStats } from "@/lib/types";

type DayGroup = {
  date: string;
  day_type: string;
  is_peak: boolean;
  is_holiday: boolean;
  label: string;
  total: number;
  early: number;
  late: number;
  positions: {
    code: string;
    name: string;
    area: string;
    shift: string;
    target: number;
    assigned: { id: number; name: string; role: string | null; is_supervisor: boolean; locked: boolean; type: string }[];
    shortage: number;
  }[];
  duty: { name: string; role_name: string }[];
};

const DAYTYPE_TONE: Record<string, any> = {
  weekday: "slate",
  weekend: "blue",
  holiday: "rose",
  peak: "amber",
  special: "violet",
};
const DAYTYPE_CN: Record<string, string> = {
  weekday: "工作日",
  weekend: "周末",
  holiday: "节假日",
  peak: "旺季",
  special: "特殊日",
};

export default function OverviewPage() {
  const toast = useToast();
  const [version, setVersion] = useState<VersionDetail | null>(null);
  const [stats, setStats] = useState<VersionStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [publishing, setPublishing] = useState(false);
  const [publishOpen, setPublishOpen] = useState(false);
  const [note, setNote] = useState("");
  const [conflict, setConflict] = useState<any[] | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const v = await get<VersionDetail>(`/api/schedule/current?start=${todayISO()}`);
      setVersion(v);
      const s = await get<VersionStats>(`/api/stats/versions/${v.id}`);
      setStats(s);
    } catch (e: any) {
      setError(e?.message || "加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const groups: DayGroup[] = useMemo(() => {
    if (!version || !stats) return [];
    return stats.days.map((d) => {
      const rows = d.positions
        .filter((p) => p.target > 0 || p.assigned > 0)
        .map((p) => ({
          code: p.position_code,
          name: p.position_name,
          area: p.area,
          shift: p.shift,
          target: p.target,
          shortage: p.shortage,
          assigned: version.assignments
            .filter((a) => a.work_date === d.work_date && a.position_code === p.position_code)
            .map((a) => ({
              id: a.id,
              name: a.employee_name,
              role: a.role_code,
              is_supervisor: a.is_supervisor,
              locked: a.is_locked,
              type: a.employee_type,
            })),
        }));
      return {
        date: d.work_date,
        day_type: d.day_type,
        is_peak: d.is_peak,
        is_holiday: d.is_holiday,
        label: d.label,
        total: d.total_people,
        early: d.early_count,
        late: d.late_count,
        positions: rows,
        duty: d.supervisors_on_duty.map((s) => ({ name: s.name, role_name: ROLE_LABEL[s.role_code] || s.role_name })),
      };
    });
  }, [version, stats]);

  const errorCount = version?.violations.filter((v) => v.level === "error").length ?? 0;
  const warnCount = version?.violations.filter((v) => v.level === "warn").length ?? 0;

  async function publish() {
    if (!version) return;
    setPublishing(true);
    try {
      await post(`/api/schedule/versions/${version.id}/publish?allow_gap=true`, { change_note: note || "主管确认发布" });
      toast.push("已发布，生成本次发布的不可变版本快照", "ok");
      setPublishOpen(false);
      setNote("");
      await load();
    } catch (e: any) {
      if (e?.status === 409) {
        setConflict(e.detail?.violations || []);
        setPublishOpen(false);
      } else {
        toast.push(e?.message || "发布失败", "error");
      }
    } finally {
      setPublishing(false);
    }
  }

  return (
    <AppShell>
      <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-xl font-semibold text-slate-900">
              {version ? version.name : "排班总览"}
            </h1>
            {version ? <StatusBadge status={version.status} /> : null}
            {version ? <Badge tone="slate">v{version.version_no}</Badge> : null}
          </div>
          <p className="mt-1 text-xs text-slate-500">
            {version ? `${version.start_date} ~ ${version.end_date}` : ""}
            {version?.published_at ? `　发布于 ${new Date(version.published_at).toLocaleString("zh-CN")}` : "　未发布"}
            {"　"}颜色仅辅助阅读，身份与角色以字段为准
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Link className="btn" href="/generate/">
            重新生成
          </Link>
          <Link className="btn" href={`/edit/?id=${version?.id ?? ""}`}>
            人工调整
          </Link>
          <button className="btn" onClick={() => version && downloadFile(`/api/export/versions/${version.id}.xlsx`, "排班表.xlsx").catch((e) => toast.push(e.message, "error"))}>
            导出 Excel
          </button>
          <button className="btn btn-primary" disabled={!version || version.status === "published"} onClick={() => setPublishOpen(true)}>
            发布班表
          </button>
        </div>
      </div>

      {loading ? (
        <Card>
          <Spinner label="正在加载排班…" />
        </Card>
      ) : error ? (
        <EmptyState
          title="还没有排班数据"
          desc={error + "　请先到「自动排班」生成未来三天的预排班。"}
          action={
            <Link className="btn btn-primary mt-2" href="/generate/">
              去生成排班
            </Link>
          }
        />
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <Stat
              label="本窗口安排人次"
              value={stats?.summary.total_assignments ?? 0}
              hint={`${stats?.summary.dates ?? 0} 天 · 平均 ${stats?.summary.avg_per_day ?? 0} 人次/天`}
            />
            <Stat
              label="岗位覆盖率"
              value={`${stats?.summary.coverage_rate ?? 0}%`}
              tone={(stats?.summary.coverage_rate ?? 100) >= 99 ? "good" : "warn"}
              hint={`目标 ${stats?.summary.total_needed ?? 0} 人次 · 缺口 ${stats?.summary.total_gap ?? 0}`}
            />
            <Stat
              label="涉及员工"
              value={stats?.summary.people ?? 0}
              hint="每人每日只计一次，运营角色不重复计数"
            />
            <Stat
              label="校验结果"
              value={errorCount > 0 ? `${errorCount} 个错误` : "通过"}
              tone={errorCount ? "bad" : warnCount ? "warn" : "good"}
              hint={errorCount ? "发布前必须处理" : warnCount ? `${warnCount} 条提醒` : "无违规"}
            />
          </div>

          {conflict ? (
            <Card className="mt-3 border-rose-200 bg-rose-50/60">
              <SectionTitle
                title="发布被阻止：存在违规排班"
                desc="系统不会无提示地保存或发布违规排班，请先修正下列问题。"
                right={
                  <button className="btn btn-sm" onClick={() => setConflict(null)}>
                    收起
                  </button>
                }
              />
              <ul className="space-y-1 text-xs text-rose-700">
                {conflict.slice(0, 12).map((v, i) => (
                  <li key={i}>· {v.message}</li>
                ))}
              </ul>
            </Card>
          ) : null}

          <div className="mt-3 grid gap-3 xl:grid-cols-3">
            {groups.map((g) => (
              <Card key={g.date} pad={false} className="overflow-hidden">
                <div className="flex items-center justify-between border-b border-line bg-slate-50/70 px-4 py-2.5">
                  <div>
                    <div className="flex items-center gap-1.5">
                      <span className="text-sm font-semibold text-slate-900">{fmtDate(g.date)}</span>
                      <span className="text-xs text-slate-500">{weekdayCN(g.date)}</span>
                      <Badge tone={DAYTYPE_TONE[g.day_type] || "slate"}>{DAYTYPE_CN[g.day_type] || g.day_type}</Badge>
                      {g.label ? <span className="text-[11px] text-amber-600">{g.label}</span> : null}
                    </div>
                    <div className="mt-0.5 text-[11px] text-slate-500">
                      上班 {g.total} 人 · 早 {g.early} / 晚 {g.late}
                    </div>
                  </div>
                  <div className="text-right text-[11px] text-slate-500">
                    {g.duty.length ? (
                      <>
                        <div className="font-medium text-rose-600">{g.duty[0]?.name}</div>
                        <div>当值主管</div>
                      </>
                    ) : (
                      <span className="text-rose-500">未指定当值</span>
                    )}
                  </div>
                </div>

                <div className="space-y-2.5 px-3 py-3">
                  {["early", "late"].map((shift) => {
                    const list = g.positions.filter((p) => p.shift === shift);
                    if (!list.length) return null;
                    return (
                      <div key={shift}>
                        <div className="mb-1 flex items-center gap-1.5 px-1">
                          <span className={`h-1.5 w-1.5 rounded-full ${shift === "early" ? "bg-blue-400" : "bg-rose-400"}`} />
                          <span className="text-[11px] font-semibold text-slate-500">{shift === "early" ? "早班 09:00–17:00" : "晚班 13:30–22:30"}</span>
                        </div>
                        <div className="space-y-1">
                          {list.map((p) => (
                            <div key={p.code} className="flex items-start gap-2 rounded-lg px-1 py-1 hover:bg-slate-50">
                              <div className="w-[104px] shrink-0">
                                <div className="flex items-center gap-1">
                                  <span
                                    className="inline-flex h-4 min-w-7 items-center justify-center rounded px-1 text-[10px] font-bold text-white"
                                    style={{ background: p.code.startsWith("A") ? "#2f7ef0" : p.code.startsWith("B") ? "#e11d48" : "#7c3aed" }}
                                  >
                                    {p.code}
                                  </span>
                                  {p.shortage > 0 ? <span className="text-[10px] font-semibold text-rose-600">缺{p.shortage}</span> : null}
                                </div>
                                <div className="mt-0.5 truncate text-[10px] leading-3 text-slate-400" title={p.name}>
                                  {p.area} · {p.assigned.length}/{p.target}
                                </div>
                              </div>
                              <div className="flex flex-wrap gap-1">
                                {p.assigned.length ? (
                                  p.assigned.map((a) => (
                                    <span
                                      key={a.id}
                                      className={`chip ${
                                        a.role
                                          ? "border-rose-300 bg-rose-50 text-rose-700"
                                          : a.is_supervisor
                                          ? "border-amber-200 bg-amber-50 text-amber-700"
                                          : shift === "early"
                                          ? "border-blue-200 bg-blue-50/70 text-blue-700"
                                          : "border-slate-200 bg-slate-50 text-slate-700"
                                      }`}
                                      title={`${a.name}${a.role ? " · " + (ROLE_LABEL[a.role] || a.role) : ""}${a.locked ? " · 已锁定" : ""}`}
                                    >
                                      {a.locked ? "🔒" : ""}
                                      {a.name}
                                      {a.role ? <span className="text-[9px] opacity-80">·{ROLE_LABEL[a.role]}</span> : null}
                                    </span>
                                  ))
                                ) : (
                                  <span className="chip border-dashed border-rose-300 bg-rose-50/60 text-rose-500">空缺</span>
                                )}
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </Card>
            ))}
          </div>

          {version && version.violations.length ? (
            <Card className="mt-3">
              <SectionTitle title="校验提醒" desc="人工调整后即时校验：错误项会阻止发布，提醒项不阻止但需确认。" />
              <div className="space-y-1">
                {version.violations.slice(0, 20).map((v, i) => (
                  <div key={i} className="flex items-start gap-2 text-xs">
                    <Badge tone={v.level === "error" ? "rose" : "amber"}>{v.level === "error" ? "错误" : "提醒"}</Badge>
                    <span className="text-slate-600">{v.message}</span>
                  </div>
                ))}
              </div>
            </Card>
          ) : null}

          <Card className="mt-3 bg-slate-50/60">
            <SectionTitle title="统计口径说明" desc="界面与导出报表采用同一口径，避免人数重复计数。" />
            <div className="grid gap-2 text-[11px] leading-5 text-slate-500 md:grid-cols-2">
              <div>· 当天上班人数 = 当日实际被安排至少一个岗位的员工去重数；休息与未排班不计入。</div>
              <div>· 同一员工同日即使承担运营角色也只计 1 人；岗位覆盖按岗位名额单独统计。</div>
              <div>· 运营角色（当值/辅助主管）默认视为岗位标记，不额外占用人数名额，可在系统配置中调整。</div>
              <div>· 班次颜色、岗位颜色仅辅助阅读，业务判断以员工资料字段与岗位配置为准。</div>
            </div>
          </Card>
        </>
      )}

      <Modal
        open={publishOpen}
        onClose={() => setPublishOpen(false)}
        title="发布班表"
        footer={
          <>
            <button className="btn" onClick={() => setPublishOpen(false)}>
              取消
            </button>
            <button className="btn btn-primary" disabled={publishing} onClick={publish}>
              {publishing ? "发布中…" : "确认发布"}
            </button>
          </>
        }
      >
        <p className="text-xs leading-6 text-slate-600">
          发布会生成不可变版本快照，之后如需修改将自动创建新版本并记录差异。
          首期仍需主管手动通知微信群，可先导出 Excel 便于查看与打印。
        </p>
        {stats && stats.summary.total_gap > 0 ? (
          <div className="mt-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-[11px] leading-5 text-amber-800">
            注意：当前版本仍有 <b>{stats.summary.total_gap}</b> 人次缺口（低于最低人数）。发布后缺口会保留在版本记录中并计入统计，缺口原因请先与主管确认。
          </div>
        ) : null}
        <div className="mt-3">
          <label className="label">变更说明（可选）</label>
          <textarea className="input h-20 resize-none" value={note} onChange={(e) => setNote(e.target.value)} placeholder="例如：周末客流上调，B2 增加 1 人" />
        </div>
      </Modal>
    </AppShell>
  );
}
