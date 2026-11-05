// 本地可视化验收：用已安装的 Chrome 无头模式截图关键页面
import { chromium } from "playwright-core";
import fs from "node:fs";

const BASE = process.env.BASE || "http://127.0.0.1:8000";
const OUT = process.env.OUT || "D:\\workbuddy-projects\\park-scheduling\\docs\\shots";
fs.mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch({
  executablePath: "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  headless: true,
  args: ["--no-sandbox", "--disable-gpu"],
});
const page = await browser.newPage({ viewport: { width: 1500, height: 1000 }, deviceScaleFactor: 1 });
const errors = [];
page.on("console", (m) => {
  if (m.type() === "error") errors.push(`console: ${m.text()}`);
});
page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));

async function shot(path, name, wait = 1200) {
  await page.goto(`${BASE}${path}`, { waitUntil: "networkidle" });
  await page.waitForTimeout(wait);
  await page.screenshot({ path: `${OUT}\\${name}.png`, fullPage: true });
  console.log("shot", name, "->", page.url());
}

await shot("/login/", "01-login");
await page.fill('input[autocomplete="username"]', "admin");
await page.fill('input[type="password"]', "admin123456");
await page.click('button:has-text("登录")');
await page.waitForURL(`${BASE}/`, { timeout: 15000 });
await page.waitForTimeout(1800);
await page.screenshot({ path: `${OUT}\\02-overview.png`, fullPage: true });
console.log("shot 02-overview");

for (const [path, name] of [
  ["/generate/", "03-generate"],
  ["/edit/", "04-edit"],
  ["/employees/", "05-employees"],
  ["/positions/", "06-positions"],
  ["/demands/", "07-demands"],
  ["/requests/", "08-requests"],
  ["/stats/", "09-stats"],
  ["/versions/", "10-versions"],
  ["/settings/", "11-settings"],
]) {
  await shot(path, name, 900);
}

console.log("errors:", errors.length ? errors.slice(0, 10) : "none");
await browser.close();
