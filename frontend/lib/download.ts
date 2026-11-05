"use client";

import { apiBase, getToken } from "./api";

export async function downloadFile(path: string, fallbackName: string) {
  const token = getToken();
  const res = await fetch(`${apiBase()}${path}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) {
    let msg = `下载失败（${res.status}）`;
    try {
      const data = await res.json();
      msg = typeof data.detail === "string" ? data.detail : msg;
    } catch {}
    throw new Error(msg);
  }
  const blob = await res.blob();
  const cd = res.headers.get("Content-Disposition") || "";
  let name = fallbackName;
  const star = /filename\*=UTF-8''([^;]+)/i.exec(cd);
  const plain = /filename="?([^";]+)"?/i.exec(cd);
  if (star) name = decodeURIComponent(star[1]);
  else if (plain) name = plain[1];
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}
