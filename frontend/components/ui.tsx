"use client";

import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

/* ------------------------------- 基础组件 -------------------------------- */
export function Card({ children, className = "", pad = true }: { children: React.ReactNode; className?: string; pad?: boolean }) {
  return <div className={`card ${pad ? "card-pad" : ""} ${className}`}>{children}</div>;
}

export function SectionTitle({
  title,
  desc,
  right,
}: {
  title: React.ReactNode;
  desc?: React.ReactNode;
  right?: React.ReactNode;
}) {
  return (
    <div className="mb-3 flex items-start justify-between gap-3">
      <div>
        <h2 className="text-[15px] font-semibold text-slate-900">{title}</h2>
        {desc ? <p className="mt-0.5 text-xs leading-5 text-slate-500">{desc}</p> : null}
      </div>
      {right ? <div className="flex shrink-0 items-center gap-2">{right}</div> : null}
    </div>
  );
}

export function Stat({
  label,
  value,
  hint,
  tone = "default",
}: {
  label: string;
  value: React.ReactNode;
  hint?: React.ReactNode;
  tone?: "default" | "good" | "warn" | "bad";
}) {
  const toneClass =
    tone === "good" ? "text-emerald-600" : tone === "warn" ? "text-amber-600" : tone === "bad" ? "text-rose-600" : "text-slate-900";
  return (
    <div className="card px-4 py-3">
      <div className="text-xs text-slate-500">{label}</div>
      <div className={`mt-1 text-2xl font-semibold tabular-nums ${toneClass}`}>{value}</div>
      {hint ? <div className="mt-1 text-[11px] leading-4 text-slate-400">{hint}</div> : null}
    </div>
  );
}

export function Badge({
  children,
  tone = "slate",
  className = "",
}: {
  children: React.ReactNode;
  tone?: "slate" | "blue" | "green" | "amber" | "rose" | "violet" | "cyan";
  className?: string;
}) {
  const map: Record<string, string> = {
    slate: "bg-slate-100 text-slate-600",
    blue: "bg-blue-50 text-blue-700",
    green: "bg-emerald-50 text-emerald-700",
    amber: "bg-amber-50 text-amber-700",
    rose: "bg-rose-50 text-rose-700",
    violet: "bg-violet-50 text-violet-700",
    cyan: "bg-cyan-50 text-cyan-700",
  };
  return <span className={`badge ${map[tone]} ${className}`}>{children}</span>;
}

export function Spinner({ label = "加载中" }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 text-xs text-slate-500">
      <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-slate-300 border-t-brand-500" />
      {label}
    </div>
  );
}

export function EmptyState({ title, desc, action }: { title: string; desc?: string; action?: React.ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-line bg-slate-50/60 px-6 py-10 text-center">
      <div className="text-sm font-medium text-slate-600">{title}</div>
      {desc ? <div className="max-w-md text-xs leading-5 text-slate-400">{desc}</div> : null}
      {action}
    </div>
  );
}

export function Modal({
  open,
  onClose,
  title,
  children,
  footer,
  width = "max-w-2xl",
}: {
  open: boolean;
  onClose: () => void;
  title: React.ReactNode;
  children: React.ReactNode;
  footer?: React.ReactNode;
  width?: string;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    if (open) window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-slate-900/30 p-4 py-10 backdrop-blur-[2px]">
      <div className={`w-full ${width} rounded-2xl border border-line bg-white shadow-xl`}>
        <div className="flex items-center justify-between border-b border-line px-5 py-3">
          <h3 className="text-sm font-semibold text-slate-900">{title}</h3>
          <button className="btn btn-ghost btn-sm" onClick={onClose}>
            关闭
          </button>
        </div>
        <div className="max-h-[70vh] overflow-y-auto px-5 py-4">{children}</div>
        {footer ? <div className="flex justify-end gap-2 border-t border-line px-5 py-3">{footer}</div> : null}
      </div>
    </div>
  );
}

/* -------------------------------- Toast --------------------------------- */
type ToastItem = { id: number; message: string; tone: "ok" | "error" | "info" };
const ToastCtx = createContext<{ push: (m: string, t?: ToastItem["tone"]) => void }>({ push: () => {} });

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const push = useCallback((message: string, tone: ToastItem["tone"] = "info") => {
    const id = Date.now() + Math.random();
    setItems((s) => [...s, { id, message, tone }]);
    setTimeout(() => setItems((s) => s.filter((i) => i.id !== id)), 3600);
  }, []);
  const value = useMemo(() => ({ push }), [push]);
  return (
    <ToastCtx.Provider value={value}>
      {children}
      <div className="fixed bottom-5 left-1/2 z-[60] flex -translate-x-1/2 flex-col items-center gap-2">
        {items.map((i) => (
          <div
            key={i.id}
            className={`rounded-lg px-4 py-2 text-[13px] text-white shadow-lg ${
              i.tone === "error" ? "bg-rose-600" : i.tone === "ok" ? "bg-emerald-600" : "bg-slate-800"
            }`}
          >
            {i.message}
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

export function useToast() {
  return useContext(ToastCtx);
}

/* ------------------------------- 表格辅助 -------------------------------- */
export function Th({ children, className = "" }: { children?: React.ReactNode; className?: string }) {
  return <th className={className}>{children}</th>;
}

export function Td({ children, className = "", ...rest }: React.TdHTMLAttributes<HTMLTableCellElement>) {
  return (
    <td className={className} {...rest}>
      {children}
    </td>
  );
}

export function ShiftChip({ shift }: { shift: string }) {
  return shift === "early" ? (
    <span className="chip border-blue-200 bg-early text-blue-700">早</span>
  ) : (
    <span className="chip border-rose-200 bg-late text-rose-700">晚</span>
  );
}

export function StatusBadge({ status }: { status: string }) {
  const map: Record<string, { tone: any; label: string }> = {
    draft: { tone: "amber", label: "草稿" },
    published: { tone: "green", label: "已发布" },
    archived: { tone: "slate", label: "已归档" },
    pending: { tone: "amber", label: "待审批" },
    approved: { tone: "green", label: "已批准" },
    rejected: { tone: "rose", label: "已拒绝" },
    done: { tone: "green", label: "完成" },
    infeasible: { tone: "rose", label: "不可行" },
    error: { tone: "rose", label: "错误" },
  };
  const m = map[status] || { tone: "slate", label: status };
  return <Badge tone={m.tone}>{m.label}</Badge>;
}
