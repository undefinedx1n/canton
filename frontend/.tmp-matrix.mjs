import { chromium } from "playwright-core";

const BASE = process.env.BASE_URL || "https://canton.1gavin.online";
const PASS = process.env.ADMIN_PASS || "";
const OUT = process.env.OUT_DIR || "D:/tmp-shots";

const browser = await chromium.launch({
  executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe",
  args: ["--host-resolver-rules=MAP canton.1gavin.online 8.217.198.163", "--ignore-certificate-errors"],
});
const ctx = await browser.newContext({ viewport: { width: 1500, height: 950 }, ignoreHTTPSErrors: true });
const page = await ctx.newPage();
const errors = [];
page.on("console", (m) => {
  if (m.type() === "error") errors.push(m.text());
});
page.on("pageerror", (e) => errors.push(String(e)));

await page.goto(`${BASE}/login/`, { waitUntil: "networkidle" });
const inputs = page.locator("form input");
await inputs.nth(0).fill("admin");
await inputs.nth(1).fill(PASS);
await page.locator("form button").click();
await page.waitForURL((u) => !u.pathname.includes("/login"), { timeout: 20000 });

await page.goto(`${BASE}/?view=table`, { waitUntil: "networkidle" });
await page.waitForSelector("table", { timeout: 20000 });
await page.waitForTimeout(800);

const info = await page.evaluate(() => {
  const t = document.querySelector("table");
  const rows = [...t.querySelectorAll("tbody tr")];
  const first = rows[0];
  const bottom = [...t.querySelectorAll("tfoot tr")].map((tr) => [...tr.children].slice(0, 4).map((c) => c.textContent.trim()));
  return {
    header: [...t.querySelectorAll("thead tr")].map((tr) => [...tr.children].slice(0, 5).map((c) => c.textContent.trim())),
    rowCount: rows.length,
    firstRow: first ? [...first.children].slice(0, 5).map((c) => c.textContent.trim()) : [],
    foot: bottom,
    legend: document.body.innerText.includes("V 休息"),
  };
});
console.log(JSON.stringify(info, null, 2));

await page.screenshot({ path: `${OUT}/matrix-top.png` });
const box = await page.locator("table").boundingBox();
if (box) {
  await page.setViewportSize({ width: 1500, height: Math.min(2400, Math.max(950, Math.ceil(box.height) + 220)) });
  await page.waitForTimeout(400);
  await page.screenshot({ path: `${OUT}/matrix-full.png`, fullPage: true });
}
console.log("console errors:", errors.slice(0, 5));
await browser.close();
