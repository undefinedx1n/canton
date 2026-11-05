// 移动端/桌面端 UI 巡检：截图 + 横向溢出检测
// 用法: node scripts/ui-audit.mjs [mobile|desktop|all]
import { chromium } from "playwright-core";
import fs from "node:fs";

const BASE = "http://127.0.0.1:8000";
const OUT = "D:\\workbuddy-projects\\park-scheduling\\docs\\shots";
const mode = process.argv[2] || "mobile";

const PAGES = [
  ["/", "overview"],
  ["/generate/", "generate"],
  ["/edit/", "edit"],
  ["/employees/", "employees"],
  ["/positions/", "positions"],
  ["/demands/", "demands"],
  ["/requests/", "requests"],
  ["/stats/", "stats"],
  ["/versions/", "versions"],
  ["/settings/", "settings"],
];

const VIEWPORTS = {
  mobile: { width: 390, height: 844, tag: "m" },
  tablet: { width: 820, height: 1180, tag: "t" },
  desktop: { width: 1440, height: 900, tag: "d" },
};

const targets = mode === "all" ? ["mobile", "tablet", "desktop"] : [mode];

const browser = await chromium.launch({
  executablePath: "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  headless: true,
  args: ["--no-sandbox", "--disable-gpu"],
});

const report = [];

for (const t of targets) {
  const vp = VIEWPORTS[t];
  const ctx = await browser.newContext({ viewport: { width: vp.width, height: vp.height }, deviceScaleFactor: 2, isMobile: t === "mobile", hasTouch: t === "mobile" });
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));

  await page.goto(`${BASE}/login/`, { waitUntil: "networkidle" });
  await page.fill('input[autocomplete="username"]', "admin");
  await page.fill('input[type="password"]', "admin123456");
  await page.click('button:has-text("登录")');
  await page.waitForURL(`${BASE}/`, { timeout: 20000 });

  for (const [path, name] of PAGES) {
    await page.goto(`${BASE}${path}`, { waitUntil: "networkidle" });
    await page.waitForTimeout(900);
    const m = await page.evaluate(() => {
      const de = document.documentElement;
      const overflow = de.scrollWidth - de.clientWidth;
      // 找到导致溢出的元素（最右侧超出视口且宽度大于视口 60% 的块）
      const bad = [];
      const vw = de.clientWidth;
      document.querySelectorAll("main *").forEach((el) => {
        const r = el.getBoundingClientRect();
        if (r.width > vw * 0.98 && r.right > vw + 2) {
          const tag = el.tagName.toLowerCase();
          const cls = (el.className || "").toString().slice(0, 70);
          bad.push(`${tag}.${cls}`);
        }
      });
      // 检测被裁切的文本节点
      const clipped = [];
      document.querySelectorAll("main *").forEach((el) => {
        if (el.children.length === 0 && el.textContent && el.textContent.trim()) {
          if (el.scrollWidth > el.clientWidth + 2 && getComputedStyle(el).overflow !== "visible") {
            clipped.push(el.textContent.trim().slice(0, 24));
          }
        }
      });
      return { overflow, bad: [...new Set(bad)].slice(0, 4), clipped: [...new Set(clipped)].slice(0, 4), vw };
    });
    report.push({ viewport: t, page: name, ...m, errors: errors.length });
    await page.screenshot({ path: `${OUT}\\${vp.tag}-${name}.png`, fullPage: true });
  }
  await ctx.close();
}

await browser.close();
fs.writeFileSync(`${OUT}\\ui-audit-${mode}.json`, JSON.stringify(report, null, 2));
for (const r of report) {
  const flag = r.overflow > 1 ? "OVERFLOW" : "ok";
  console.log(`${r.viewport.padEnd(8)} ${r.page.padEnd(10)} ${flag.padEnd(9)} +${r.overflow}px  ${r.bad.join(" | ")}${r.clipped.length ? "  clipped:" + r.clipped.join(",") : ""}`);
}
