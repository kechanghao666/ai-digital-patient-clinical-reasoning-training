// Browser probe for the teacher observability + de-identified export panels.
// Starts web/e2e/teacher_panel_stack.py (local accounts, simulator mode, temporary data, one
// training + one trusted turn) and verifies: has data, no data, endpoint failure + retry recovery,
// and export preview + download. No real model call.
import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
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
  await obs.getByRole("button", { name: "刷新", exact: true }).click();
  await refreshed;
  await obs.locator(".faint", { hasText: "记录回合数" }).waitFor({ timeout: 15000 });
  ok("刷新后观测面板仍显示数据", true);
  await obs.getByText(/已达预算上限/).waitFor();
  let releaseStats;
  let statsArrived;
  const heldStats = new Promise(resolve => { releaseStats = resolve; });
  const arrivedStats = new Promise(resolve => { statsArrived = resolve; });
  await context.route("**/api/teacher/observability?*", async route => {
    const response = await route.fetch();
    statsArrived();
    await heldStats;
    await route.fulfill({ response });
  });
  await page.getByLabel("观测病例筛选").fill("NO-SUCH-CASE");
  await arrivedStats;
  await obs.getByText("统计加载中…").waitFor();
  ok("切换观测范围等待期间隐藏旧统计及预算", await obs.getByText(/已达预算上限/).count() === 0
    && await obs.getByText("记录回合数", { exact: true }).count() === 0);
  releaseStats();
  await obs.getByText(/筛选范围内暂无/).waitFor();
  await obs.getByText(/已达预算上限/).waitFor();
  ok("病例筛选不消除自有班级预算告警", true);
  await context.unroute("**/api/teacher/observability?*");
  await page.getByLabel("观测病例筛选").fill("");
  await obs.getByText("记录回合数", { exact: true }).waitFor();
  await page.getByLabel("观测结束日期").fill("2020-01-01");
  await obs.getByText(/筛选范围内暂无/).waitFor();
  await obs.getByText(/已达预算上限/).waitFor();
  ok("历史日期筛选不消除今日预算告警", true);
  await page.getByLabel("观测结束日期").fill("");
  await obs.getByText("记录回合数", { exact: true }).waitFor();
  await context.route("**/api/teacher/observability", async route => {
    const response = await route.fetch();
    const body = await response.json();
    body.budget.turns_today = null;
    body.budget.exceeded = null;
    await route.fulfill({ response, json: body });
  });
  await obs.getByRole("button", { name: "刷新", exact: true }).click();
  await obs.getByText(/预算状态未知/).waitFor();
  ok("未知预算不显示未达上限", await obs.getByText(/未达上限/).count() === 0);
  await context.unroute("**/api/teacher/observability");
  await obs.getByRole("button", { name: "刷新", exact: true }).click();
  await obs.getByText(/已达预算上限/).waitFor();

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
  await obs.getByRole("button", { name: "刷新", exact: true }).click();
  await obs.getByText("统计暂不可用").waitFor({ timeout: 15000 });
  ok("接口故障显示统计暂不可用", true);
  ok("接口故障隐藏旧预算", await obs.getByText(/已达预算上限|未达上限/).count() === 0);
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

  // ---- M3/M11: groups, task templates, batch assignment and cross-class copy
  await page.goto(stack.base + "/#/teacher/classes");
  await page.locator("tr", { has: page.getByText("面板班", { exact: true }) }).getByRole("button", { name: "管理" }).click();
  await page.getByRole("heading", { name: "面板班", exact: true }).waitFor();
  await page.getByRole("button", { name: "学生名单" }).click();
  await page.getByLabel("分组名称").fill("第一组");
  await page.getByRole("button", { name: "新建分组" }).click();
  await page.getByText("第一组（0人）").waitFor();
  ok("可创建分组", true);
  await page.getByLabel("面板学生 的分组").selectOption({ label: "第一组" });
  await page.getByText("第一组（1人）").waitFor();
  ok("学生可加入分组", true);
  await page.getByRole("button", { name: "训练任务" }).click();
  await page.getByLabel("病例").selectOption("CASE-001");
  await page.getByRole("button", { name: "布置", exact: true }).click();
  await page.locator("th", { hasText: "CASE-001" }).waitFor();
  await page.getByLabel("病例").selectOption("CASE-001");
  page.once("dialog", (dialog) => dialog.accept("阑尾炎问诊模板"));
  await page.getByRole("button", { name: "保存为模板" }).click();
  await page.getByRole("cell", { name: /阑尾炎问诊模板/ }).waitFor();
  ok("可从布置表单保存任务模板", true);
  const tplPanel = heading("任务模板（可布置到多个班级）");
  await tplPanel.locator("tr", { hasText: "阑尾炎问诊模板" }).getByRole("button", { name: "布置到多班" }).click();
  await tplPanel.getByLabel(/面板班二/).check();
  await tplPanel.getByRole("button", { name: /按模板布置（1 个班）/ }).click();
  await page.getByText(/按模板布置：新建 1 项/).waitFor();
  ok("模板可批量布置到其他自有班级", true);
  const copyPanel = heading("复制本班任务到其他班");
  await copyPanel.getByLabel(/面板班二/).check();
  await copyPanel.getByRole("button", { name: /复制（1 个班）/ }).click();
  await page.getByText(/复制到其他班：跳过已有 1 项/).waitFor();
  ok("跨班复制对已有同病例任务的班级幂等跳过", true);
  await page.getByLabel("病例").selectOption("CASE-003");
  await page.getByLabel("任务分组").selectOption({ label: "第一组（1人）" });
  await page.getByRole("button", { name: "布置", exact: true }).click();
  const groupedHeader = page.locator("th", { hasText: "CASE-003" });
  await groupedHeader.getByText(/分组 第一组/).waitFor();
  ok("可按分组布置任务并显示完成分母", /完成 0\/1/.test(await groupedHeader.innerText()));
  await page.locator("tr", { hasText: "面板学生二" }).getByText("—", { exact: true }).waitFor();
  ok("组外学生在分组任务列显示不适用", true);
  const [matrixDownload] = await Promise.all([
    page.waitForEvent("download"), page.getByRole("button", { name: "导出完成情况" }).click(),
  ]);
  const matrix = await readFile(await matrixDownload.path(), "utf8");
  ok("CSV 明示当前分组并区分组外学生", matrix.includes("当前分组") && matrix.includes("CASE-003")
    && matrix.split(/\r?\n/).some(row => row.includes("面板学生二") && row.includes("不适用")));
  await page.screenshot({ path: path.join(OUT, "class-templates-groups.png"), fullPage: true });
  await page.getByRole("button", { name: "学生名单" }).click();
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByText("第一组（1人）").getByRole("button", { name: "删除" }).click();
  await page.getByText(/该分组仍有进行中的任务/).waitFor();
  ok("有进行中任务的分组不能删除", true);
  await page.getByRole("button", { name: "训练任务" }).click();
  page.once("dialog", (dialog) => dialog.accept());
  await page.locator("th", { hasText: "CASE-003" }).getByRole("button", { name: "撤回" }).click();
  await page.locator("th", { hasText: "CASE-003" }).waitFor({ state: "detached" });
  await page.getByRole("button", { name: "学生名单" }).click();
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByText("第一组（1人）").getByRole("button", { name: "删除" }).click();
  await page.getByText("分组已删除").waitFor();
  ok("撤回任务后分组可删除", true);

  // ---- M3/M11b: semesters, courses, class linking and cross-term course reuse
  await page.goto(stack.base + "/#/teacher/classes");
  await page.getByLabel("学期名称").fill("2026 春季学期");
  await page.getByRole("button", { name: "新建学期" }).click();
  await page.getByText(/2026 春季学期（0门课程/).waitFor();
  ok("可创建学期", true);
  await page.getByLabel("课程名称").fill("临床诊断学");
  await page.getByLabel("课程所属学期").selectOption({ label: "2026 春季学期" });
  await page.getByRole("button", { name: "新建课程" }).click();
  await page.locator("tr", { has: page.getByText("临床诊断学", { exact: true }) }).getByText("未关联").waitFor();
  ok("可创建课程并归属学期", true);
  const linkCourse = async (className) => {
    await page.locator("tr", { has: page.getByText(className, { exact: true }) }).getByRole("button", { name: "管理" }).click();
    await page.getByRole("heading", { name: className, exact: true }).waitFor();
    await page.getByLabel("关联课程").selectOption({ label: "临床诊断学（2026 春季学期）" });
    await page.getByText("已关联课程").waitFor();
    // In-app navigation, like a real user; page.goto with a fragment-only change does not
    // reliably re-run the list load in this back-and-forth sequence.
    await page.getByRole("button", { name: "返回" }).click();
    await page.getByRole("heading", { name: "班级与任务" }).waitFor();
  };
  await linkCourse("面板班");
  await linkCourse("面板班二");
  const courseRow = page.locator("tr", { has: page.getByText("临床诊断学", { exact: true }) });
  await courseRow.getByText(/面板班（/).waitFor();
  ok("班级可关联到课程并回显", true);
  await courseRow.getByRole("button", { name: "跨学期复用" }).click();
  await page.getByLabel("课程复用源班级").selectOption({ index: 1 }); // 面板班（课程内按创建时间排序）
  await page.getByLabel(/面板班二/).check();
  await page.getByRole("button", { name: /复用（1 个班）/ }).click();
  await page.getByText(/课程复用：跳过已有 1 项/).waitFor();
  ok("同一课程复用对已有同病例任务幂等跳过", true);
  page.once("dialog", (dialog) => dialog.accept());
  await courseRow.getByRole("button", { name: "删除", exact: true }).click();
  await page.getByText(/该课程仍关联班级/).waitFor();
  ok("仍关联班级的课程不能删除", true);
  await page.getByLabel("学期名称").fill("2026 秋季学期");
  await page.getByRole("button", { name: "新建学期" }).click();
  await page.getByLabel("课程名称").fill("临床诊断学秋季班");
  await page.getByLabel("课程所属学期").selectOption({ label: "2026 秋季学期" });
  await page.getByRole("button", { name: "新建课程" }).click();
  const fallRow = page.locator("tr", { has: page.getByText("临床诊断学秋季班", { exact: true }) });
  await fallRow.getByText("未关联").waitFor();
  await page.getByLabel("班级名称", { exact: true }).fill("秋季新班");
  await page.getByRole("button", { name: "新建班级" }).click();
  await page.getByLabel("关联课程").selectOption({ label: "临床诊断学秋季班（2026 秋季学期）" });
  await page.getByText("已关联课程").waitFor();
  await page.getByRole("button", { name: "返回" }).click();
  await fallRow.getByText("秋季新班（0人/0任务）").waitFor();
  for (const expected of ["新建 1 项", "跳过已有 1 项"]) {
    await courseRow.getByRole("button", { name: "跨学期复用" }).click();
    await page.getByLabel("课程复用源班级").selectOption({ label: "面板班（1个进行中任务）" });
    await page.getByLabel("目标课程与学期").selectOption({ label: "临床诊断学秋季班（2026 秋季学期）" });
    await page.getByLabel("秋季新班", { exact: true }).check();
    await page.getByRole("button", { name: "复用（1 个班）" }).click();
    await page.getByText(`课程复用：${expected}`, { exact: false }).waitFor();
    await fallRow.getByText("秋季新班（0人/1任务）").waitFor();
    ok(`春季课程复用到秋季空班：${expected}`, true);
  }
  await page.screenshot({ path: path.join(OUT, "course-semesters.png"), fullPage: true });

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
const mainJsSha256 = createHash("sha256").update(await readFile(path.join(ROOT, "web/dist/assets/main.js"))).digest("hex");
writeFileSync(path.join(OUT, "run.json"), JSON.stringify({ checks, passed, real_model_calls: 0, main_js_sha256: mainJsSha256 }, null, 2), "utf-8");
console.log(passed ? "ALL CHECKS PASSED" : "REGRESSION DETECTED", "->", path.join(OUT, "run.json"));
process.exit(passed ? 0 : 1);
