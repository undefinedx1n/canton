"use client";

const TOKEN_KEY = "ps_token";
const USER_KEY = "ps_user";

export function apiBase(): string {
  if (process.env.NEXT_PUBLIC_API_BASE) return process.env.NEXT_PUBLIC_API_BASE;
  if (typeof window === "undefined") return "http://127.0.0.1:8000";
  const { protocol, hostname, port } = window.location;
  // 开发模式（next dev 3000）默认连本地 8000；构建产物由后端同端口托管时使用相对路径
  if (port === "3000") return `${protocol}//${hostname}:8000`;
  return "";
}

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}

export function getUser(): { name: string; role: string } | null {
  if (typeof window === "undefined") return null;
  const raw = window.localStorage.getItem(USER_KEY);
  return raw ? JSON.parse(raw) : null;
}

export function setSession(token: string, name: string, role: string) {
  window.localStorage.setItem(TOKEN_KEY, token);
  window.localStorage.setItem(USER_KEY, JSON.stringify({ name, role }));
}

export function clearSession() {
  window.localStorage.removeItem(TOKEN_KEY);
  window.localStorage.removeItem(USER_KEY);
}

export class ApiError extends Error {
  status: number;
  detail: any;
  constructor(status: number, message: string, detail: any) {
    super(message);
    this.status = status;
    this.detail = detail;
  }
}

export async function api<T = any>(path: string, options: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    ...(options.body ? { "Content-Type": "application/json" } : {}),
    ...(options.headers as Record<string, string>),
  };
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const res = await fetch(`${apiBase()}${path}`, { ...options, headers, cache: "no-store" });
  if (res.status === 401) {
    let body: any = null;
    try {
      body = await res.json();
    } catch {
      body = null;
    }
    const serverMsg = typeof body?.detail === "string" ? body.detail : "";
    // 登录接口的 401 是「账号或密码不对」，不是会话过期，必须原样告诉用户
    if (path.endsWith("/auth/login")) {
      throw new ApiError(401, serverMsg || "用户名或密码不正确", body?.detail ?? null);
    }
    clearSession();
    if (typeof window !== "undefined" && !window.location.pathname.startsWith("/login")) {
      window.location.href = "/login/";
    }
    throw new ApiError(401, token ? "登录已过期，请重新登录" : "请先登录", body?.detail ?? null);
  }
  if (!res.ok) {
    let detail: any = null;
    try {
      detail = await res.json();
    } catch {
      detail = null;
    }
    const msg =
      typeof detail?.detail === "string"
        ? detail.detail
        : detail?.detail?.message || `请求失败（${res.status}）`;
    throw new ApiError(res.status, msg, detail?.detail ?? detail);
  }
  if (res.status === 204) return null as T;
  const text = await res.text();
  return text ? (JSON.parse(text) as T) : (null as T);
}

export const get = <T,>(path: string) => api<T>(path);
export const post = <T,>(path: string, body?: any) => api<T>(path, { method: "POST", body: body ? JSON.stringify(body) : undefined });
export const put = <T,>(path: string, body?: any) => api<T>(path, { method: "PUT", body: body ? JSON.stringify(body) : undefined });
export const patch = <T,>(path: string, body?: any) => api<T>(path, { method: "PATCH", body: body ? JSON.stringify(body) : undefined });
export const del = <T,>(path: string) => api<T>(path, { method: "DELETE" });

export function todayISO(offset = 0): string {
  const d = new Date();
  d.setDate(d.getDate() + offset);
  const y = d.getFullYear();
  const m = `${d.getMonth() + 1}`.padStart(2, "0");
  const day = `${d.getDate()}`.padStart(2, "0");
  return `${y}-${m}-${day}`;
}

export function fmtDate(iso: string): string {
  const d = new Date(`${iso}T00:00:00`);
  return `${d.getMonth() + 1}月${d.getDate()}日`;
}

export function weekdayCN(iso: string): string {
  const d = new Date(`${iso}T00:00:00`);
  return "周" + "日一二三四五六"[d.getDay()];
}

export function addDays(iso: string, n: number): string {
  const d = new Date(`${iso}T00:00:00`);
  d.setDate(d.getDate() + n);
  const y = d.getFullYear();
  const m = `${d.getMonth() + 1}`.padStart(2, "0");
  const day = `${d.getDate()}`.padStart(2, "0");
  return `${y}-${m}-${day}`;
}

export const TYPE_LABEL: Record<string, string> = {
  long_term: "长期员工",
  part_time: "兼职",
  intern: "实习生",
  other: "其他",
};

export const SHIFT_LABEL: Record<string, string> = { early: "早班", late: "晚班", any: "无偏好" };

export const ROLE_LABEL: Record<string, string> = {
  duty_supervisor: "当值主管",
  early_support: "早班辅助",
  late_support: "晚班辅助",
};

export const DAYTYPE_LABEL: Record<string, string> = {
  weekday: "工作日",
  weekend: "周末",
  holiday: "节假日",
  peak: "旺季",
  special: "特殊日",
};

export const STATUS_LABEL: Record<string, string> = {
  draft: "草稿",
  published: "已发布",
  archived: "已归档",
  pending: "待审批",
  approved: "已批准",
  rejected: "已拒绝",
  done: "完成",
  infeasible: "不可行",
  error: "错误",
  running: "求解中",
};
