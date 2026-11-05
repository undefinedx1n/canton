"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { post, setSession, getToken } from "@/lib/api";
import { useEffect } from "react";

export default function LoginPage() {
  const router = useRouter();
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("admin123456");
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (getToken()) router.replace("/");
  }, [router]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr("");
    setLoading(true);
    try {
      const res = await post<{ access_token: string; display_name: string; role: string }>("/api/auth/login", {
        username,
        password,
      });
      setSession(res.access_token, res.display_name, res.role);
      router.replace("/");
    } catch (e: any) {
      setErr(e?.message || "登录失败");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-[radial-gradient(circle_at_20%_20%,#eef4ff,transparent_55%),radial-gradient(circle_at_80%_10%,#f0fdfa,transparent_45%)] px-4">
      <div className="w-full max-w-[880px] overflow-hidden rounded-2xl border border-line bg-white shadow-card md:grid md:grid-cols-[1.1fr_1fr]">
        <div className="hidden flex-col justify-between bg-ink px-8 py-9 text-white md:flex">
          <div>
            <div className="mb-6 flex h-10 w-10 items-center justify-center rounded-xl bg-brand-500 text-base font-bold">排</div>
            <h1 className="text-[22px] font-semibold leading-8">
              游乐园销售与摄影团队
              <br />
              排班自动化系统
            </h1>
            <p className="mt-3 text-[13px] leading-6 text-slate-300">
              按客流与岗位需求自动生成未来三天预排班，主管审核微调后发布。
              系统负责岗位覆盖检查、资格校验、主管当值公平与修改留痕。
            </p>
          </div>
          <ul className="space-y-2 text-[12px] text-slate-400">
            <li>· OR-Tools CP-SAT 约束求解，硬规则不可违反</li>
            <li>· 主管当值 / 周末 / 节假日公平可量化查看</li>
            <li>· 每次调整即时校验，发布后生成不可变版本</li>
          </ul>
        </div>

        <div className="px-7 py-9">
          <h2 className="text-lg font-semibold text-slate-900">主管登录</h2>
          <p className="mt-1 text-xs text-slate-500">首期仅开放主管/管理员账号，员工查看权限后续迭代。</p>
          <form className="mt-6 space-y-4" onSubmit={submit}>
            <div>
              <label className="label">账号</label>
              <input className="input" value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" />
            </div>
            <div>
              <label className="label">密码</label>
              <input
                className="input"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
              />
            </div>
            {err ? <div className="rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-600">{err}</div> : null}
            <button className="btn btn-primary w-full py-2" disabled={loading}>
              {loading ? "登录中…" : "登录"}
            </button>
          </form>
          <div className="mt-6 rounded-lg border border-dashed border-line bg-slate-50 px-3 py-2.5 text-[11px] leading-5 text-slate-500">
            演示账号：<b>admin / admin123456</b>（管理员）、<b>supervisor / supervisor123</b>（主管）
            <br />
            生产部署请通过 .env 修改默认密码并启用 HTTPS。
          </div>
        </div>
      </div>
    </div>
  );
}
