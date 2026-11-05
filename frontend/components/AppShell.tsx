"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { clearSession, getUser, getToken, get } from "@/lib/api";
import { Spinner, ToastProvider } from "./ui";

const NAV = [
  { href: "/", label: "排班总览", icon: "▤" },
  { href: "/generate/", label: "自动排班", icon: "✦" },
  { href: "/edit/", label: "人工调整", icon: "✎" },
  { href: "/employees/", label: "员工档案", icon: "☰" },
  { href: "/positions/", label: "岗位与需求", icon: "▦" },
  { href: "/demands/", label: "每日需求", icon: "◷" },
  { href: "/requests/", label: "请假审批", icon: "✓" },
  { href: "/stats/", label: "统计报表", icon: "◔" },
  { href: "/versions/", label: "版本历史", icon: "⟲" },
  { href: "/settings/", label: "系统配置", icon: "⚙" },
];

export default function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [ready, setReady] = useState(false);
  const [user, setUser] = useState<{ name: string; role: string } | null>(null);
  const [pending, setPending] = useState(0);
  const [meta, setMeta] = useState<any>(null);
  const [mobileOpen, setMobileOpen] = useState(false);

  useEffect(() => {
    if (!getToken()) {
      router.replace("/login/");
      return;
    }
    setUser(getUser());
    setReady(true);
    get("/api/stats/dashboard")
      .then((d: any) => setPending(d?.pending_requests ?? 0))
      .catch(() => {});
    get("/api/meta")
      .then(setMeta)
      .catch(() => {});
  }, [router]);

  if (!ready) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Spinner label="正在校验登录状态…" />
      </div>
    );
  }

  const nav = (
    <nav className="flex flex-col gap-0.5">
      {NAV.map((item) => {
        const active = pathname === item.href || (item.href !== "/" && pathname.startsWith(item.href));
        return (
          <Link key={item.href} href={item.href} className={`nav-item ${active ? "nav-item-active" : ""}`} onClick={() => setMobileOpen(false)}>
            <span className="w-4 text-center text-xs opacity-70">{item.icon}</span>
            <span>{item.label}</span>
            {item.href === "/requests/" && pending > 0 ? (
              <span className="ml-auto rounded-full bg-rose-100 px-1.5 text-[10px] font-semibold text-rose-600">{pending}</span>
            ) : null}
          </Link>
        );
      })}
    </nav>
  );

  return (
    <ToastProvider>
      <div className="flex min-h-screen">
        {/* 侧边栏 */}
        <aside className="sticky top-0 hidden h-screen w-[196px] shrink-0 flex-col border-r border-line bg-white px-3 py-4 lg:flex">
          <div className="mb-4 flex items-center gap-2 px-1">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand-600 text-sm font-bold text-white">排</div>
            <div className="leading-tight">
              <div className="text-[13px] font-semibold text-slate-900">排班系统</div>
              <div className="text-[10px] text-slate-400">销售 · 摄影团队</div>
            </div>
          </div>
          {nav}
          <div className="mt-auto space-y-2 px-1 pt-4 text-[11px] text-slate-400">
            <div className="rounded-lg bg-slate-50 px-2 py-2 leading-5">
              求解器：{meta?.solver === "cp-sat" ? "OR-Tools CP-SAT" : meta ? "启发式回退" : "—"}
            </div>
            <button
              className="btn btn-ghost btn-sm w-full justify-start"
              onClick={() => {
                clearSession();
                router.replace("/login/");
              }}
            >
              退出登录
            </button>
          </div>
        </aside>

        {/* 主区域 */}
        <div className="min-w-0 flex-1">
          <header className="sticky top-0 z-30 flex items-center gap-3 border-b border-line bg-white/90 px-4 py-2.5 backdrop-blur">
            <button className="btn btn-ghost btn-sm lg:hidden" onClick={() => setMobileOpen((v) => !v)}>
              ☰
            </button>
            <div className="text-[13px] font-medium text-slate-700">
              {NAV.find((n) => n.href === pathname)?.label ?? "排班系统"}
            </div>
            <div className="ml-auto flex items-center gap-2 text-xs text-slate-500">
              <span className="hidden sm:inline rounded-md bg-slate-100 px-2 py-1">
                {user?.name || "管理员"} · {user?.role === "admin" ? "管理员" : "主管"}
              </span>
            </div>
          </header>
          {mobileOpen ? (
            <>
              <div
                className="fixed inset-0 top-[45px] z-40 bg-slate-900/25 backdrop-blur-[1px] lg:hidden"
                onClick={() => setMobileOpen(false)}
                aria-hidden
              />
              <div className="fixed inset-x-0 top-[45px] z-40 max-h-[calc(100vh-45px)] overflow-y-auto border-b border-line bg-white px-3 pb-3 pt-2 shadow-lg lg:hidden">
                {nav}
                <div className="mt-2 flex items-center justify-between border-t border-line px-1 pt-2 text-[11px] text-slate-400">
                  <span>
                    {user?.name || "管理员"} · {user?.role === "admin" ? "管理员" : "主管"}
                  </span>
                  <button
                    className="btn btn-ghost btn-sm"
                    onClick={() => {
                      clearSession();
                      router.replace("/login/");
                    }}
                  >
                    退出登录
                  </button>
                </div>
              </div>
            </>
          ) : null}
          <main className="mx-auto max-w-[1500px] px-3 py-3 sm:px-6 sm:py-4">{children}</main>
        </div>
      </div>
    </ToastProvider>
  );
}
