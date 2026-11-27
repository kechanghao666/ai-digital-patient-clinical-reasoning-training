// Browser probe for the teacher observability + de-identified export panels.
// Starts web/e2e/teacher_panel_stack.py (local accounts, simulator mode, temporary data, one
// training + one trusted turn) and verifies: has data, no data, endpoint failure + retry recovery,
// and export preview + download. No real model call.
import { spawn } from "node:child_process";
import { readFile } from "node:fs/promises";
import { existsSync, mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";

const ROOT = process.env.PROJECT_ROOT || path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const OUT = process.env.OUT || mkdtempSync(path.join(tmpdir(), "teacher-panel-"));
const PYTHON = process.env.PYTHON || path.join(ROOT, ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
const CHROME = process.env.CHROME || [
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
  "C:/Program Files/Microsoft/Edge/Application/msedge.exe",
  "C:/Program Files/Google/Chrome/Application/chrome.exe",
  path.join(process.env.LOCALAPPDATA || "", "Google/Chrome/Application/chrome.exe"),
].find(existsSync);
if (process.env.OUT) mkdirSync(OUT);

function startStack() {
  return new Promise((resolve, reject) => {
    const child = spawn(PYTHON, [path.join(ROOT, "web", "e2e", "teacher_panel_stack.py")], {
      cwd: ROOT, env: { ...process.env, PYTHONUNBUFFERED: "1" }, stdio: ["pipe", "pipe", "pipe"],
    });
    let output = "";
    let ready = false;
    const timer = setTimeout(() => { child.kill(); reject(new Error("stack not ready")); }, 90000);
    child.on("error", (error) => { clearTimeout(timer); reject(error); });
    child.stdout.on("data", (chunk) => {
      output += chunk.toString();
      const match = output.match(/READY (\S+) (\S+) (.+)\r?\n/);
      if (match && !ready) { ready = true; clearTimeout(timer); resolve({ child, base: match[1], teacherPw: match[2], data: match[3].trim() }); }
    });
    child.stderr.on("data", (chunk) => { output += chunk.toString(); });
    child.on("exit", (code) => { if (!ready) { clearTimeout(timer); reject(new Error(`stack exited ${code}`)); } });
  });
}

let stack = null;
let browser = null;
let page = null;
const checks = [];
const ok = (name, cond, detail = "") => { checks.push({ name, ok: !!cond, detail }); console.log(cond ? "✓" : "✗", name, detail); };
const heading = (title) => page.locator("section.panel", { has: page.locator(".panel-head h3", { hasText: title }) });

async function login(user, password) {
  await page.goto(stack.base + "/#/");
  await page.evaluate(() => sessionStorage.removeItem("clinic.identity"));
  await page.reload();
  await page.getByLabel("账号").fill(user);
  await page.getByLabel("密码").fill(password);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await page.getByText("学情统计", { exact: true }).first().waitFor({ timeout: 20000 });
  await page.goto(stack.base + "/#/teacher/dashboard");
}

try {
  stack = await startStack();
  browser = await chromium.launch({ executablePath: CHROME, args: ["--no-sandbox"] });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: "zh-CN", acceptDownloads: true });
  page = await context.newPage();
  const pageErrors = [];
  page.on("pageerror", (e) => pageErrors.push(String(e)));

  // ---- has data + refresh + export preview/download
  await login("tpanel_teacher", stack.teacherPw);
  const obs = heading("模型调用观测");
  await obs.locator(".panel-head h3", { hasText: "模型调用观测" }).waitFor({ timeout: 15000 });
  await obs.locator(".faint", { hasText: "记录回合数" }).waitFor({ timeout: 15000 });
  ok("有数据：观测面板显示记录回合数", true);

  const refreshed = page.waitForResponse(r => r.url().endsWith("/api/teacher/observability") && r.status() === 200);
  await page.getByRole("button", { name: "刷新" }).click();
  await refreshed;
  await obs.locator(".faint", { hasText: "记录回合数" }).waitFor({ timeout: 15000 });
  ok("刷新后观测面板仍显示数据", true);

  const exp = heading("数据导出（去标识化）");
  await exp.getByRole("button", { name: "预览导出" }).click();
  await exp.getByText(/已生成 1 条去标识化记录/).waitFor({ timeout: 15000 });
  ok("导出预览显示 1 条去标识化记录", true);
  const [download] = await Promise.all([
    page.waitForEvent("download"),
    exp.getByRole("button", { name: "下载 JSON" }).click(),
  ]);
  const dlPath = await download.path();
  ok("导出下载生成 JSON 文件", !!dlPath);
  const dlText = dlPath ? (await readFile(dlPath, "utf-8")).trim() : "";
  ok("下载内容不含身份/自由文本", !/(13800001111|张三|sid-|req-internal|有高血压)/.test(dlText));
  const downloaded = JSON.parse(dlText);
  await exp.locator("summary").click();
  const preview = JSON.parse(await exp.locator("pre").innerText());
  ok("下载字段与预览相同", downloaded.format === "teaching-deidentified-v1" && JSON.stringify(downloaded.records) === JSON.stringify(preview));
  await page.screenshot({ path: path.join(OUT, "teacher-export.png"), fullPage: true });
  await page.getByLabel("班级", { exact: true }).selectOption({ label: "面板班" });
  await exp.getByRole("button", { name: "预览导出" }).waitFor();
  ok("切换班级清除旧下载", await exp.getByRole("button", { name: "下载 JSON" }).count() === 0);

  let releaseExport;
  let exportArrived;
  const delayed = new Promise(resolve => { releaseExport = resolve; });
  const arrived = new Promise(resolve => { exportArrived = resolve; });
  await context.route("**/api/teacher/export", async route => {
    const response = await route.fetch();
    exportArrived();
    await delayed;
    await route.fulfill({ response });
  });
  await exp.getByRole("button", { name: "预览导出" }).click();
  await arrived;
  await page.getByLabel("班级", { exact: true }).selectOption("");
  const oldResponse = page.waitForResponse(r => r.url().endsWith("/api/teacher/export"));
  releaseExport();
  await oldResponse;
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  ok("旧班级迟到响应不恢复下载", await exp.getByRole("button", { name: "下载 JSON" }).count() === 0);
  await context.unroute("**/api/teacher/export");
  await context.route("**/api/teacher/export", route => route.fulfill({ status: 503, contentType: "application/json", body: '{"detail":"导出暂不可用"}' }));
  await exp.getByRole("button", { name: "预览导出" }).click();
  await exp.getByText("导出暂不可用", { exact: true }).waitFor();
  ok("导出失败不提供下载", await exp.getByRole("button", { name: "下载 JSON" }).count() === 0);
  await context.unroute("**/api/teacher/export");

  // ---- endpoint failure + retry recovery
  await context.route("**/api/teacher/observability", (route) => route.fulfill({ status: 503, contentType: "application/json", body: '{"detail":"统计暂不可用"}' }));
  await page.getByRole("button", { name: "刷新" }).click();
  await obs.getByText("统计暂不可用").waitFor({ timeout: 15000 });
  ok("接口故障显示统计暂不可用", true);
  await context.unroute("**/api/teacher/observability");
  await obs.getByRole("button", { name: "重试" }).click();
  await obs.locator(".faint", { hasText: "记录回合数" }).waitFor({ timeout: 15000 });
  ok("重试后恢复数据", true);

  await context.route("**/api/teacher/dashboard?class_id=*", route => route.fulfill({ status: 503,
    contentType: "application/json", body: '{"detail":"班级统计暂不可用"}' }));
  await page.getByLabel("班级", { exact: true }).selectOption({ label: "面板班" });
  await page.getByRole("alert").filter({ hasText: "班级统计暂不可用" }).waitFor();
  ok("班级统计失败不展示旧班级明细", await page.getByText("tpanel_student", { exact: true }).count() === 0);
  await context.unroute("**/api/teacher/dashboard?class_id=*");
  await page.getByRole("button", { name: "重试", exact: true }).click();
  await page.getByText("tpanel_student", { exact: true }).waitFor();
  ok("班级统计重试恢复", true);

  // ---- synthetic reviewer lifecycle; control commands are fixture stdin, never a web endpoint.
  const control = command => new Promise((resolve, reject) => {
    let output = "";
    const listener = chunk => { output += chunk.toString(); if (output.includes(`CONTROL ${command}`)) { clearTimeout(timer); stack.child.stdout.off("data", listener); resolve(); } };
    const timer = setTimeout(() => { stack.child.stdout.off("data", listener); reject(new Error("fixture control timeout")); }, 10000);
    stack.child.stdout.on("data", listener);
    stack.child.stdin.write(command + "\n");
  });
  await page.goto(stack.base + "/#/teacher/cases");
  await page.getByRole("button").filter({ hasText: "合成资质发布探针" }).click();
  const publishButton = page.getByRole("button", { name: "确认审核并发布", exact: true });
  await publishButton.waitFor();
  await page.getByLabel("审核日期", { exact: true }).fill(await page.getByLabel("审核日期", { exact: true }).getAttribute("max"));
  await page.getByRole("checkbox", { name: "我确认该版本已由上述人员完成真实医学审核" }).check();
  ok("待核验资质不能发布", await publishButton.isDisabled());
  await control("activate-reviewer");
  await page.getByRole("button", { name: "刷新资质" }).click();
  await heading("当前账号的审核资质").getByText(/^有效 · 科室/).waitFor();
  await page.getByRole("checkbox", { name: "我确认该版本已由上述人员完成真实医学审核" }).check();
  ok("匹配有效资质允许确认发布", await publishButton.isEnabled());
  await context.route("**/api/teacher/reviewer-qualifications", route => route.fulfill({ status: 503, contentType: "application/json", body: '{"detail":"合成读取故障"}' }));
  await page.getByRole("button", { name: "刷新资质" }).click();
  await page.getByRole("alert").filter({ hasText: "资质读取失败" }).waitFor();
  ok("读取失败时旧有效资质不能继续发布", await publishButton.isDisabled());
  await context.unroute("**/api/teacher/reviewer-qualifications");
  await page.getByRole("button", { name: "刷新资质" }).click();
  await heading("当前账号的审核资质").getByText(/^有效 · 科室/).waitFor();
  await page.getByRole("checkbox", { name: "我确认该版本已由上述人员完成真实医学审核" }).check();
  const published = page.waitForResponse(r => r.url().endsWith("/versions/1/publish"));
  await publishButton.click();
  ok("合成审核发布请求成功", (await published).status() === 200);
  await page.getByText(/已绑定账号与外部核验登记资质/).waitFor();
  ok("发布审计展示绑定身份", true);
  await page.getByRole("button", { name: "复制为新草稿", exact: true }).click();
  const savedCopy = page.waitForResponse(r => r.request().method() === "PUT" && r.url().includes("/api/teacher/cases/"));
  await page.getByRole("button", { name: "保存为新版本草稿", exact: true }).click();
  ok("已发布病例可复制保存草稿", (await savedCopy).status() === 200);
  await page.getByRole("button", { name: "标记结构校验通过", exact: true }).click();
  await page.getByRole("button", { name: "提交医学审核", exact: true }).click();
  await publishButton.waitFor();
  await page.getByLabel("审核日期", { exact: true }).fill(await page.getByLabel("审核日期", { exact: true }).getAttribute("max"));
  await page.getByRole("checkbox", { name: "我确认该版本已由上述人员完成真实医学审核" }).check();
  await control("revoke-reviewer");
  const denied = page.waitForResponse(r => r.url().endsWith("/versions/1/publish"));
  await publishButton.click();
  ok("页面旧资质不能绕过服务端撤销", (await denied).status() === 403);
  await page.getByRole("button", { name: "刷新资质" }).click();
  await heading("当前账号的审核资质").getByText(/^已撤销 · 科室/).waitFor();
  ok("刷新后撤销状态禁止发布", await publishButton.isDisabled());
  await page.screenshot({ path: path.join(OUT, "reviewer-qualification.png"), fullPage: true });

  // ---- no data (teacher with no class)
  await login("tpanel_empty", stack.teacherPw);
  await heading("模型调用观测").getByText("暂无已记录的可信回合数据").waitFor({ timeout: 15000 });
  ok("无数据：空班级教师显示暂无", true);

  ok("无前端脚本错误", pageErrors.length === 0, pageErrors.slice(0, 2).join(" | "));
} catch (error) {
  ok("流程执行", false, String(error).replace(stack?.teacherPw || /$^/, "[redacted]"));
} finally {
  if (browser) await browser.close().catch(() => {});
  if (stack) {
    const exited = new Promise(resolve => stack.child.once("exit", code => resolve(code)));
    stack.child.stdin.end("stop\n");
    let timer;
    const code = await Promise.race([exited, new Promise(resolve => { timer = setTimeout(() => resolve("timeout"), 15000); })]);
    clearTimeout(timer);
    if (code === "timeout") stack.child.kill();
    ok("服务退出且临时数据清理", code === 0 && !existsSync(stack.data));
  }
}

const passed = checks.length > 0 && checks.every((c) => c.ok);
writeFileSync(path.join(OUT, "run.json"), JSON.stringify({ checks, passed }, null, 2), "utf-8");
console.log(passed ? "ALL CHECKS PASSED" : "REGRESSION DETECTED", "->", path.join(OUT, "run.json"));
process.exit(passed ? 0 : 1);
