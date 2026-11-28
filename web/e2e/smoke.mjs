// End-to-end run of the real teaching workflow against a running API in local account mode.
// Teacher creates a class, imports a student, assigns a case; the student signs in with the issued
// password, sets a new one, completes the assignment; the teacher reviews progress and scores.
// Isolated default entry: python web/e2e/demo.py. Direct runs require an explicit target and credentials.
import { chromium } from "playwright-core";
import { randomBytes } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";

for (const key of ["BASE", "TEACHER", "TEACHER_PW", "CHROME", "OUT"]) {
  if (!process.env[key]) throw new Error(`Missing ${key}; use python web/e2e/demo.py for an isolated simulator run`);
}
if (!existsSync(process.env.CHROME)) throw new Error("CHROME must identify an installed browser");
const BASE = process.env.BASE;
const OUT = process.env.OUT || "shots";
const TEACHER = process.env.TEACHER || "teacher";
const STUDENT = process.env.E2E_STUDENT || `e2e${Date.now().toString().slice(-6)}`;
const NEW_PW = randomBytes(24).toString("base64url");
const RECORD = process.env.RECORD_DEMO === "1";
mkdirSync(OUT, { recursive: true });
const publicCase = process.env.DEMO_CASE || "CASE-001";
if (!/^CASE-00[1-5]$/.test(publicCase)) throw new Error("Unknown demo case");
const traceFile = new URL(`../../docs/evidence/comprehensive-review-20261127/SIM-0${publicCase.at(-1)}-trace.json`, import.meta.url);
const trace = JSON.parse(readFileSync(traceFile, "utf8"));
const scenario = publicCase === "CASE-001" ? null : trace.result.state;
const log = [];
const sessionEvidence = {};
const errors = [];
const present = async (name, delay = 15000) => {
  await page.locator("#demo-caption").evaluate((element, text) => { element.textContent = text; },
    `本地规则模拟器 · 合成数据 · 未经医学审核　|　${name}`);
  await page.waitForTimeout(delay);
};
const step = async (name, ok = true, detail = "") => {
  log.push({ name, ok, detail });
  console.log(ok ? "✓" : "✗", name, detail);
  if (!ok) throw new Error(name);
  if (RECORD) await present(name);
};

const browser = await chromium.launch({ executablePath: process.env.CHROME, args: ["--no-sandbox"] });
const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: "zh-CN",
  ...(RECORD ? { recordVideo: { dir: OUT, size: { width: 1440, height: 900 } } } : {}),
});
if (RECORD) await context.addInitScript(() => {
  document.addEventListener("DOMContentLoaded", () => {
    const style = document.createElement("style");
    style.textContent = ".msg-bar td:nth-child(3){font-size:0!important}.msg-bar td:nth-child(3)::after{content:'已隐藏初始密码';font-size:14px}#demo-caption{position:fixed;bottom:0;left:0;right:0;z-index:2147483647;padding:12px 20px;background:#10233d;color:white;font:18px sans-serif;pointer-events:none}body{padding-bottom:52px}";
    document.head.append(style);
    const caption = document.createElement("div");
    caption.id = "demo-caption";
    caption.textContent = "本地规则模拟器 · 合成数据 · 未经医学审核　|　教学模拟环境，非真实患者，不用于真实诊疗";
    document.body.append(caption);
  });
});
const page = await context.newPage();
// Optional endpoints (no feedback / no adjudication yet) answer 404 by design.
page.on("console", (m) => { if (m.type() === "error" && !/status of 404/.test(m.text())) errors.push(m.text()); });
page.on("pageerror", (e) => errors.push(String(e)));
page.on("dialog", (d) => d.accept());
const shot = (name, full = false) => page.screenshot({ path: `${OUT}/${name}.png`, fullPage: full });
const panel = (title) => page.locator("section.panel", { has: page.locator(".panel-head h3", { hasText: title }) });
const settle = () => page.waitForTimeout(350);

async function login(user, password) {
  await page.goto(BASE + "/#/");
  await page.getByLabel("账号").fill(user);
  await page.getByLabel("密码").fill(password);
  await page.getByRole("button", { name: "登录", exact: true }).click();
}
async function logout() {
  await page.getByRole("button", { name: "退出" }).click();
  await page.getByRole("heading", { name: "登录" }).waitFor();
}

try {
  await page.goto(BASE + "/");
  await page.getByRole("heading", { name: "登录" }).waitFor();
  await shot("01-login");
  await step("登录页与合规提示", await page.getByText("教学模拟环境，非真实患者", { exact: false }).first().isVisible());

  // ---- Teacher: class, roster, assignment
  await login(TEACHER, process.env.TEACHER_PW);
  await page.getByRole("heading", { name: "班级与任务" }).waitFor();
  await page.getByLabel("班级名称").fill("临床医学2024级1班（演示）");
  await page.getByRole("button", { name: "新建班级" }).click();
  await page.getByRole("button", { name: "学生名单" }).waitFor();
  await page.getByRole("button", { name: "学生名单" }).click();
  await page.getByLabel("学生名单").fill(`${STUDENT},张同学`);
  await page.getByRole("button", { name: "导入", exact: true }).click();
  const issuedRow = page.locator(".msg-bar tr", { hasText: STUDENT });
  await issuedRow.waitFor();
  const tempPassword = (await issuedRow.locator("td").nth(2).innerText()).trim();
  if (RECORD) {
    if (await issuedRow.locator("td").nth(2).evaluate((element) => getComputedStyle(element).fontSize) !== "0px") {
      throw new Error("Recording requires hidden temporary passwords");
    }
  }
  await step("教师建班并导入学生，获得一次性初始密码", tempPassword.length === 16);
  await page.getByRole("button", { name: "关闭" }).click();
  await issuedRow.waitFor({ state: "hidden" });
  await shot("02-teacher-roster");
  await page.getByRole("button", { name: "训练任务" }).click();
  const option = await page.locator("select option", { hasText: publicCase }).first().getAttribute("value");
  await panel("布置任务").locator("select").selectOption(option);
  await page.getByLabel("截止日期", { exact: true }).fill("2026-12-31");
  await page.getByRole("button", { name: "布置" }).click();
  await page.locator("th", { hasText: publicCase }).waitFor();
  await step("布置训练任务");
  await logout();

  // ---- Student: forced password change, assignment, full clinical loop
  await login(STUDENT, tempPassword);
  await page.getByRole("heading", { name: "设置新密码" }).waitFor();
  await shot("03-force-password");
  await page.getByLabel("当前密码").fill(tempPassword);
  await page.getByLabel(/^新密码/).fill(NEW_PW);
  await page.getByLabel("确认新密码").fill(NEW_PW);
  await page.getByRole("button", { name: "修改密码" }).click();
  await page.getByRole("heading", { name: "我的任务" }).waitFor();
  await shot("04-student-tasks");
  await step("首次登录强制改密后进入我的任务");
  const [detail] = await Promise.all([
    page.waitForResponse(r => r.request().method() === "GET" && /\/api\/sessions\/[^/?]+$/.test(r.url()) && r.ok()),
    page.getByRole("button", { name: "开始" }).click(),
  ]);
  const opened = await detail.json();
  if (opened.case.public_id !== publicCase || opened.case.version !== trace.versions.case_version) throw new Error("病例版本与冻结轨迹不符");
  Object.assign(sessionEvidence, { public_id: publicCase, case_id: trace.versions.case_id, version: opened.case.version, session_id: opened.state.session_id });
  await page.getByLabel("问诊内容").waitFor();
  const questions = scenario ? scenario.events.filter(e => e.action === "ask").map(e => e.intent) : ["您好，我是今天接诊的医生，请问哪里不舒服？", "什么时候开始的？", "具体哪里疼？", "怎么个疼法？", "会跑到别的地方吗？",
    "有没有出汗？", "以前有什么病，有没有过敏？", "抽烟喝酒吗？", "家里人有类似的病吗？", "我是不是心梗？"];
  for (const q of questions) {
    await page.getByLabel("问诊内容").fill(q);
    await page.getByRole("button", { name: "发送" }).click();
    await page.locator(".turn.doctor", { hasText: q }).waitFor();
    await page.waitForFunction((n) => document.querySelectorAll(".turn.patient .text").length >= n, questions.indexOf(q) + 1);
  }
  const answers = await page.locator(".turn.patient .text").allInnerTexts();
  const fallbacks = answers.filter((a) => a.includes("换一种方式询问")).length;
  await step(`问诊 ${questions.length} 轮全部得到回答`, answers.length === questions.length && fallbacks === 0, `兜底 ${fallbacks}`);
  const catalogSize = await page.locator(".cat-row").count();
  await step("检查从全院目录选择，不预先给出答案", catalogSize >= 40, `目录 ${catalogSize} 项`);
  // An unrelated, costly exam is ordered on purpose to exercise the stewardship deduction.
  const exams = scenario ? scenario.orders.map(o => opened.options.exams.find(e => e.exam_id === o.catalog_id).name) : ["心电图", "肌钙蛋白", "腹部CT"];
  for (const exam of exams) {
    await page.getByLabel("检索检查").fill(exam);
    const row = page.locator(".cat-row").filter({ has: page.locator(".cat-name", { hasText: new RegExp(`^${exam}$`) }) });
    await row.getByRole("button", { name: "开单" }).click();
    await row.locator(".done").waitFor();
  }
  await page.getByLabel("检索检查").fill("");
  let remaining = scenario ? scenario.events.find(e => e.action === "wait").result.advanced_minutes : 60;
  for (const minutes of [60, 30, 15, 10, 5]) {
    if (remaining < minutes) continue;
    await page.getByLabel("等待分钟").selectOption(String(minutes));
    await Promise.all([page.waitForResponse(r => r.url().endsWith("/action") && r.ok()),
      page.getByRole("button", { name: "推进时间" }).click()]);
    remaining -= minutes;
  }
  if (remaining) throw new Error("演练等待时长不在可选范围");
  await page.locator(".order .result").nth(exams.length - 1).waitFor();
  await settle();
  await shot("05-training-intake");
  const results = await page.locator(".order .result").allInnerTexts();
  if (RECORD) await panel(/^检查结果/).scrollIntoViewIfNeeded();
  if (scenario) {
    for (const order of scenario.orders) {
      const row = page.locator(".order").filter({ has: page.getByText(order.exam_name, { exact: true }) });
      if ((await row.locator(".result").innerText()) !== order.result) throw new Error(`检查回报与冻结轨迹不符：${order.exam_name}`);
    }
    await step("本病例的检查均已回报");
  } else {
    const neutral = await page.locator(".order", { hasText: "腹部CT" }).locator(".result").innerText();
    await step("开单、推进时间、结果回报；无关检查只返回中性结果", neutral.includes("未见明显异常"));
  }

  await page.getByRole("tab", { name: "病历与诊断" }).click();
  await page.getByRole("button", { name: "插入模板" }).click();
  await page.getByLabel("病历记录").fill(scenario ? [...answers, ...results].join("\n") : "主诉：突发胸痛2小时。\n现病史：休息时突发胸骨后压榨样疼痛，向左肩及左上肢放射，伴大汗、恶心。\n既往史：高血压10年，糖尿病5年，吸烟20年。\n辅助检查：心电图II/III/aVF导联ST段抬高；肌钙蛋白升高。\n初步诊断：急性下壁ST段抬高型心肌梗死。");
  await panel("病历记录").getByRole("button", { name: "保存" }).click();
  await settle();
  if (scenario) {
    const candidate = scenario.submissions.differential.find(d => d.supporting_evidence.length && d.opposing_evidence.length);
    const label = opened.options.differentials.find(d => d.candidate_id === candidate.candidate_id).label;
    await page.getByLabel(label, { exact: true }).check();
    const card = page.locator(".ddx", { hasText: label });
    await card.getByLabel("支持点", { exact: true }).fill(candidate.supporting_evidence.join("；"));
    await card.getByLabel("不支持点", { exact: true }).fill(candidate.opposing_evidence.join("；"));
    for (const id of candidate.evidence_event_ids) {
      const event = scenario.events.find(e => e.event_id === id);
      if (event.action === "ask") {
        await card.getByLabel(`问：${event.intent}`).check();
      } else {
        const cited = [...candidate.supporting_evidence, ...candidate.opposing_evidence];
        const exam = event.result.exam_results.find(result => cited.some(text => result.result.includes(text)));
        if (!exam) throw new Error("冻结的鉴别依据无法定位到检查回报");
        await card.getByLabel(new RegExp(`回报：.*${exam.name}`)).check();
      }
    }
  } else {
    await page.getByLabel("主动脉夹层").check();
    const card = page.locator(".ddx", { hasText: "主动脉夹层" });
    await card.getByLabel("支持点", { exact: true }).fill("休息时突然发作");
    await card.getByLabel("不支持点", { exact: true }).fill("后背没有撕裂一样的疼");
    await card.getByLabel(/#2 问/).check();
    await card.getByLabel(/#5 问/).check();
  }
  await panel("鉴别诊断").getByRole("button", { name: "提交" }).click();
  await settle();
  await page.getByLabel("主要诊断").fill(scenario ? scenario.submissions.diagnosis : "急性ST段抬高型心肌梗死");
  await panel(/^诊断$/).getByRole("button", { name: "提交" }).click();
  await settle();
  await shot("06-training-reasoning");
  if (RECORD) await panel("病历记录").scrollIntoViewIfNeeded();
  await step("病历、鉴别与诊断提交", (await page.locator(".panel-head .faint", { hasText: /未保存|未提交/ }).count()) === 0);

  await page.getByRole("tab", { name: "处置与提交" }).click();
  await page.getByLabel(scenario ? scenario.submissions.treatment.treatment_name : /再灌注/).first().check();
  await page.getByLabel(/确认执行所选处置/).check();
  await page.getByRole("button", { name: "执行", exact: true }).click();
  await settle();
  await shot("07-training-treatment");
  await page.getByRole("button", { name: "提交并评分" }).click();
  await panel("临床思维评分").waitFor();
  const [feedbackResponse] = await Promise.all([
    page.waitForResponse(r => r.url() === `${detail.url()}/feedback` && r.request().method() === "POST" && r.ok()),
    page.getByRole("button", { name: "生成反馈" }).click(),
  ]);
  const feedback = await feedbackResponse.json();
  const snapshot = await page.request.get(detail.url(), { headers: await detail.request().allHeaders() });
  const { state } = await snapshot.json();
  const stages = new Set(feedback.items.map(item => item.stage));
  const expectedStages = ["病史采集", "检查选择", "鉴别诊断", "诊断", "治疗"];
  if (state.session_id !== sessionEvidence.session_id || !state.sealed || !state.case_notes || !expectedStages.every(s => stages.has(s)) || stages.size !== 5 || feedback.source !== "local_rules") throw new Error("同次封存训练或五环节反馈不完整");
  Object.assign(sessionEvidence, { seal_seq: state.seal_seq, feedback_source: feedback.source, feedback_stages: [...stages],
    events: state.events.map(e => ({ seq: e.seq, action: e.action })) });
  await page.locator("td", { hasText: "病史采集" }).first().waitFor();
  await settle();
  await shot("08-review", true);
  if (RECORD) await panel("临床思维评分").scrollIntoViewIfNeeded();
  if (scenario) await step("同次封存训练的评分与五环节反馈可见", await panel("分环节反馈").locator("tbody tr").count() === 5);
  else await step("提交后评分、扣分说明与反馈；多余检查被指出", await page.locator("td", { hasText: "开立了与本病例无关的检查" }).first().isVisible());
  if (RECORD) {
    for (const title of ["扣分点与改进建议", "分环节反馈", "操作回放"]) {
      await panel(title).scrollIntoViewIfNeeded();
      await present(title, 8000);
    }
  }
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(BASE + "/#/tasks");
  await settle();
  await shot("09-tasks-390", true);
  await page.setViewportSize({ width: 1440, height: 900 });
  await logout();

  // ---- Teacher: progress, statistics, score review, case management
  await login(TEACHER, process.env.TEACHER_PW);
  await page.getByRole("heading", { name: "班级与任务" }).waitFor();
  await page.getByRole("button", { name: "管理" }).first().click();
  await page.locator("td .status", { hasText: "%" }).first().waitFor();
  await shot("10-teacher-class-progress");
  await step("班级完成情况显示学生成绩");
  await page.getByRole("button", { name: "本班学情" }).click();
  await page.getByRole("heading", { name: "学情统计" }).waitFor();
  await settle();
  await shot("11-teacher-statistics", true);
  await page.getByRole("button", { name: "评分复核" }).first().click();
  await page.locator(".ddx").first().waitFor();
  await shot("12-teacher-score-review");
  if (RECORD) await page.locator(".ddx").first().scrollIntoViewIfNeeded();
  await step("从学情进入评分复核并自动载入");
  if (RECORD) {
    await page.getByRole("button", { name: /定位证据/ }).first().click();
    await present("教师定位评分依据，查看封存前事件；不改写原分", 8000);
  }
  await page.goto(BASE + "/#/teacher/cases");
  await page.getByRole("button").filter({ has: page.getByText(`${publicCase} v${opened.case.version}`, { exact: true }) }).click();
  await page.getByText("症状与体征").first().waitFor();
  await shot("13-teacher-cases");
  await step("病例管理可打开病例");
} catch (e) {
  process.exitCode = 1;
  log.push({ name: "流程中断", ok: false, detail: String(e).split("\n")[0] });
  console.log("✗ 流程中断", String(e).split("\n")[0]);
  await shot("zz-failure", true).catch(() => {});
} finally {
  log.push({ name: "无前端脚本错误", ok: errors.length === 0, detail: errors.slice(0, 3).join(" | ") });
  if (errors.length) process.exitCode = 1;
  console.log(errors.length ? "✗" : "✓", "无前端脚本错误", errors.slice(0, 3).join(" | "));
  writeFileSync(`${OUT}/run.json`, JSON.stringify({ base: BASE, session: sessionEvidence, steps: log, console_errors: errors }, null, 2));
  await context.close();
  if (RECORD) {
    await page.video().saveAs(`${OUT}/demo-simulator.webm`);
    await page.video().delete();
  }
  await browser.close();
}
