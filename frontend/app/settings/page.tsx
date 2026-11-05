"use client";

import { useCallback, useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import { Badge, Card, SectionTitle, Spinner, useToast } from "@/components/ui";
import { get, post, put } from "@/lib/api";
import type { SettingRow } from "@/lib/types";

const FIELD_LABELS: Record<string, string> = {
  period_type: "周期类型",
  rest_days: "每月休息天数",
  cycle_start_day: "周期起始日",
  apply_to_formal_only: "仅适用于长期员工",
  holiday_work_compensated: "节假日上班补休",
  compare_window_days: "对比窗口（天）",
  counts_extra_slot: "额外占用岗位名额",
  allow_supervisor_dual_position: "允许主管同日兼普通岗位",
  allow_gap_draft: "允许生成带缺口草稿",
  default_mode: "默认生成模式",
  treat_pending_as_unavailable: "未批准申请阻止排班",
  counts_part_time_as_normal: "兼职计入普通员工",
  counts_intern_as_normal: "实习生计入普通员工",
  days: "默认天数",
  note: "备注说明",
};

const TITLES: Record<string, { title: string; hint: string }> = {
  rest_rule: { title: "休息周期口径", hint: "需求文档 17.1 待确认项：自然月 / 滚动 28 天 / 自定义周期，均在此配置。" },
  fairness_period: { title: "主管公平统计周期", hint: "需求文档 17.2：当值天数、周末与节假日的均衡统计窗口。" },
  role_slot_rule: { title: "运营角色与岗位名额", hint: "需求文档 17.3：运营角色是否额外占用名额、主管能否同日兼普通岗位。" },
  gap_policy: { title: "缺口处理策略", hint: "需求文档 17.5：人数不足时是否允许生成带缺口的草稿。" },
  pending_leave_policy: { title: "未批准申请的处理", hint: "需求文档 17.9：默认仅已批准作为硬约束。" },
  normal_staff_definition: { title: "普通员工口径", hint: "需求文档 17.4：销售岗位「普通员工」是否计入兼职/实习生。" },
  solver_weights: { title: "优化权重", hint: "需求文档 17.8：软约束权重，数值越大越优先满足。" },
  department_labels: { title: "部门显示名称", hint: "仅影响界面文案。" },
  schedule_window_days: { title: "默认排班窗口", hint: "默认生成天数（天）。" },
};

const WEIGHT_LABELS: Record<string, string> = {
  coverage_target: "达到目标人数",
  gap_penalty: "缺口惩罚（硬性优先）",
  duty_fairness: "当值总次数公平",
  weekend_fairness: "周末当值公平",
  holiday_fairness: "节假日当值公平",
  peak_fairness: "旺季当值公平",
  rest_balance: "长期员工休息目标",
  shift_preference: "班次偏好",
  position_priority: "岗位优先级",
  pairing_avoid: "搭班避让",
  pairing_prefer: "搭班正向偏好",
  proficiency: "旺季能力匹配",
  stability: "相对历史稳定",
  workload_balance: "工作量均衡",
  normal_staff_definition: "",
};

/** 账号安全：修改当前登录账号的密码（后端 /api/auth/change-password）。 */
function PasswordCard() {
  const toast = useToast();
  const [oldPwd, setOldPwd] = useState("");
  const [newPwd, setNewPwd] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (newPwd.length < 8) {
      toast.push("新密码至少 8 位", "error");
      return;
    }
    if (newPwd !== confirm) {
      toast.push("两次输入的新密码不一致", "error");
      return;
    }
    setBusy(true);
    try {
      await post(
        `/api/auth/change-password?old_password=${encodeURIComponent(oldPwd)}&new_password=${encodeURIComponent(newPwd)}`
      );
      toast.push("密码已修改，下次登录请使用新密码", "ok");
      setOldPwd("");
      setNewPwd("");
      setConfirm("");
    } catch (err: any) {
      toast.push(err?.message || "修改失败", "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <SectionTitle title="账号与安全" desc="修改当前登录账号的密码。改动会记入审计日志。" />
      <form className="grid gap-2 sm:grid-cols-3" onSubmit={submit}>
        <input
          className="input"
          type="password"
          autoComplete="current-password"
          placeholder="当前密码"
          value={oldPwd}
          onChange={(e) => setOldPwd(e.target.value)}
        />
        <input
          className="input"
          type="password"
          autoComplete="new-password"
          placeholder="新密码（至少 8 位）"
          value={newPwd}
          onChange={(e) => setNewPwd(e.target.value)}
        />
        <input
          className="input"
          type="password"
          autoComplete="new-password"
          placeholder="再次输入新密码"
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
        />
        <div className="sm:col-span-3">
          <button className="btn btn-primary btn-sm" type="submit" disabled={busy || !oldPwd || !newPwd}>
            {busy ? "提交中…" : "修改密码"}
          </button>
        </div>
      </form>
    </Card>
  );
}

export default function SettingsPage() {
  const toast = useToast();
  const [rows, setRows] = useState<SettingRow[]>([]);
  const [drafts, setDrafts] = useState<Record<string, any>>({});
  const [jsonMode, setJsonMode] = useState<Record<string, boolean>>({});
  const [jsonText, setJsonText] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await get<SettingRow[]>("/api/settings");
      setRows(data);
      const map: Record<string, any> = {};
      const jt: Record<string, string> = {};
      data.forEach((r) => {
        map[r.key] = JSON.parse(JSON.stringify(r.value));
        jt[r.key] = JSON.stringify(r.value, null, 2);
      });
      setDrafts(map);
      setJsonText(jt);
    } catch (e: any) {
      toast.push(e?.message || "加载失败", "error");
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    load();
  }, [load]);

  async function save(key: string) {
    setSaving(key);
    try {
      let value = drafts[key];
      if (jsonMode[key]) value = JSON.parse(jsonText[key]);
      await put(`/api/settings/${key}`, { key, value, description: rows.find((r) => r.key === key)?.description || "" });
      toast.push("配置已保存，下次生成排班时生效", "ok");
      await load();
    } catch (e: any) {
      toast.push(e?.message || "保存失败（JSON 格式是否正确？）", "error");
    } finally {
      setSaving(null);
    }
  }

  function setField(key: string, field: string, value: any) {
    setDrafts((prev) => ({ ...prev, [key]: { ...prev[key], [field]: value } }));
    setJsonText((prev) => ({ ...prev, [key]: JSON.stringify({ ...drafts[key], [field]: value }, null, 2) }));
  }

  return (
    <AppShell>
      <div className="mb-3">
        <h1 className="text-lg font-semibold">系统配置</h1>
        <p className="mt-0.5 text-xs text-slate-500">
          需求文档中标注为「待确认」的业务规则全部做成可调整配置，不写死为不可变规则。修改后重新生成排班即生效。
        </p>
      </div>

      <div className="mb-3">
        <PasswordCard />
      </div>

      {loading ? (
        <Card>
          <Spinner />
        </Card>
      ) : (
        <div className="grid gap-3 lg:grid-cols-2">
          {rows.map((row) => {
            const meta = TITLES[row.key] || { title: row.key, hint: row.description };
            const value = drafts[row.key] || {};
            const isWeights = row.key === "solver_weights";
            return (
              <Card key={row.key} className={isWeights ? "lg:col-span-2" : ""}>
                <SectionTitle
                  title={
                    <span className="flex items-center gap-2">
                      {meta.title}
                      <Badge tone="slate">{row.key}</Badge>
                    </span>
                  }
                  desc={meta.hint}
                  right={
                    <div className="flex items-center gap-2">
                      <button
                        className="btn btn-sm btn-ghost"
                        onClick={() => {
                          setJsonMode({ ...jsonMode, [row.key]: !jsonMode[row.key] });
                          setJsonText({ ...jsonText, [row.key]: JSON.stringify(drafts[row.key], null, 2) });
                        }}
                      >
                        {jsonMode[row.key] ? "表单" : "JSON"}
                      </button>
                      <button className="btn btn-sm btn-primary" disabled={saving === row.key} onClick={() => save(row.key)}>
                        {saving === row.key ? "保存中…" : "保存"}
                      </button>
                    </div>
                  }
                />
                {jsonMode[row.key] ? (
                  <textarea
                    className="input h-40 font-mono !text-[11px]"
                    value={jsonText[row.key]}
                    onChange={(e) => setJsonText({ ...jsonText, [row.key]: e.target.value })}
                  />
                ) : isWeights ? (
                  <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                    {Object.entries(value).map(([k, v]) => (
                      <div key={k} className="rounded-lg border border-line px-2.5 py-2">
                        <div className="flex items-center justify-between text-[11px] text-slate-600">
                          <span>{WEIGHT_LABELS[k] || k}</span>
                          <span className="tabular-nums font-medium text-slate-900">{v as any}</span>
                        </div>
                        <input
                          type="range"
                          min={0}
                          max={120}
                          step={1}
                          className="mt-1.5 w-full accent-brand-600"
                          value={Number(v)}
                          onChange={(e) => setField(row.key, k, Number(e.target.value))}
                        />
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="space-y-2">
                    {Object.entries(value).map(([k, v]) => (
                      <div key={k} className="flex items-center gap-3 rounded-lg px-1 py-1 hover:bg-slate-50">
                        <div className="min-w-0 flex-1">
                          <div className="truncate text-[12px] text-slate-700">{FIELD_LABELS[k] || k}</div>
                          <div className="truncate font-mono text-[10px] text-slate-400">{k}</div>
                        </div>
                        <div className="w-[44%] max-w-[210px] shrink-0">
                          {typeof v === "boolean" ? (
                            <label className="flex items-center justify-end gap-1.5 text-[12px] text-slate-700">
                              <input type="checkbox" checked={v} onChange={(e) => setField(row.key, k, e.target.checked)} />
                              {v ? "启用" : "关闭"}
                            </label>
                          ) : typeof v === "number" ? (
                            <input
                              type="number"
                              className="input w-full text-right"
                              value={v}
                              onChange={(e) => setField(row.key, k, Number(e.target.value))}
                            />
                          ) : (
                            <input className="input w-full" value={String(v)} onChange={(e) => setField(row.key, k, e.target.value)} />
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </Card>
            );
          })}
          <Card className="bg-slate-50/60 lg:col-span-2">
            <SectionTitle title="部署与安全提示" desc="生产部署前请完成以下检查。" />
            <ul className="grid gap-1 text-[11px] leading-5 text-slate-600 md:grid-cols-2">
              <li>· 通过 .env 设置 JWT_SECRET 与初始管理员密码，不要提交到版本库。</li>
              <li>· 生产使用 PostgreSQL，仅暴露 HTTPS 入口，数据库端口不对公网开放。</li>
              <li>· 定期备份数据库并演练恢复；发布前检查数据库迁移。</li>
              <li>· 首期不发送微信群消息，发布后由主管手动通知或导出 Excel。</li>
            </ul>
          </Card>
        </div>
      )}
    </AppShell>
  );
}
