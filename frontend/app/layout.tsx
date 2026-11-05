import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "游乐园排班系统",
  description: "销售与摄影团队排班自动化：自动生成、人工微调、覆盖检查与公平统计",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
