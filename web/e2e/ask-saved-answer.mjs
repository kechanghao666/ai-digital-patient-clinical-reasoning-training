// Behavior regression for displaying the server-saved ask event before the refresh GET finishes.
//
// Zero real model calls: the driver starts web/e2e/ask_stack.py, which uses temporary databases,
// the synthetic CASE-001 teaching case, and a loopback fake Nexent + trusted MCP stack.
// Usage: OUT=local-only/ask-saved-answer-after node web/e2e/ask-saved-answer.mjs
// Optional env: PROJECT_ROOT, PYTHON, CHROME, OUT. Assertions use event order, never latency targets.
import { spawn } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";

const ROOT = process.env.PROJECT_ROOT || path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const OUT = process.env.OUT || mkdtempSync(path.join(tmpdir(), "ask-saved-evidence-"));
const PYTHON = process.env.PYTHON || path.join(ROOT, ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
const CHROME = process.env.CHROME || [
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
  "C:/Program Files/Google/Chrome/Application/chrome.exe",
  path.join(process.env.LOCALAPPDATA || "", "Google/Chrome/Application/chrome.exe"),
].find(existsSync);
const OWNER = "demo-ask-saved-answer";
const PATIENT = ".turn.patient .text:not(.faint)";
const DOCTOR = ".turn.doctor";
const REFRESH_NOTICE = "回答已保存并显示，但训练记录重新读取失败。请重新读取。";
if (process.env.OUT) mkdirSync(OUT); // Require a fresh output directory; never overwrite evidence.

const evidence = {
  base_commit: process.env.BASE_COMMIT || "7295952",
  model_policy: "zero real model calls; loopback fake Nexent and trusted MCP only",
  case_id: "CASE-001 (synthetic teaching case)",
  checks: [], scenarios: {}, page_errors: [],
};
const check = (scenario, name, ok, detail = "") => {
  const row = { scenario, name, ok: !!ok, detail };
  evidence.checks.push(row);
  console.log(`${ok ? "OK  " : "FAIL"} [${scenario}] ${name}${detail ? `  ${typeof detail === "string" ? detail : JSON.stringify(detail)}` : ""}`);
  return !!ok;
};
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
};
const settle = (page) => page.evaluate(() => new Promise((resolve) => {
  requestAnimationFrame(() => requestAnimationFrame(resolve));
}));

function startStack() {
  return new Promise((resolve, reject) => {
    const child = spawn(PYTHON, [path.join(ROOT, "web", "e2e", "ask_stack.py")], {
      cwd: ROOT, env: { ...process.env, PYTHONUNBUFFERED: "1" }, stdio: ["ignore", "pipe", "pipe"],
    });
    let output = "";
    let ready = false;
    const timer = setTimeout(() => { child.kill(); reject(new Error(`stack not ready\n${output}`)); }, 90000);
    child.stdout.on("data", (chunk) => {
      output += chunk.toString();
      const match = output.match(/READY (\S+) (\S+)/);
      if (match && !ready) {
        ready = true;
        clearTimeout(timer);
        resolve({ child, api: match[1], control: match[2] });
      }
    });
    child.stderr.on("data", (chunk) => { output += chunk.toString(); });
    child.on("exit", (code) => {
      if (!ready) { clearTimeout(timer); reject(new Error(`stack exited ${code}\n${output}`)); }
    });
  });
}

const stack = await startStack();
let browser;
try {
  browser = await chromium.launch({ executablePath: CHROME, args: ["--no-sandbox"] });
} catch (error) {
  stack.child.kill();
  throw error;
}

async function api(method, route, body) {
  const response = await fetch(stack.api + route, {
    method,
    headers: { "Content-Type": "application/json", "X-Owner-Id": OWNER },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await response.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  return { status: response.status, data };
}
const setControl = async (patch) => {
  const response = await fetch(stack.control + "/__control", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(patch),
  });
  return response.json();
};
const controlStatus = async () => (await fetch(stack.control + "/__control")).json();
const createSession = async () => {
  const result = await api("POST", "/api/sessions?case_id=CASE-001");
  if (result.status !== 200 && result.status !== 201) throw new Error(`create session failed: ${JSON.stringify(result)}`);
  return result.data.session_id;
};
const detail = async (sid) => (await api("GET", `/api/sessions/${sid}`)).data;

async function newPage() {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: "zh-CN" });
  await context.addInitScript((owner) => {
    sessionStorage.setItem("clinic.identity", JSON.stringify({
      mode: "demo", ownerId: owner,
      user: { id: owner, username: "demo", display_name: "合成学生", role: "student" },
    }));
  }, OWNER);
  const page = await context.newPage();
  page.on("pageerror", (error) => evidence.page_errors.push(String(error)));
  return { context, page };
}
const openTraining = async (page, sid) => {
  await page.goto(stack.api + `/#/training/${sid}`);
  await page.getByLabel("问诊内容").waitFor();
  await page.waitForFunction((id) => document.body.innerText.includes(id.slice(0, 8)), sid);
};
const patientTexts = (page) => page.locator(PATIENT).allInnerTexts();
const doctorTexts = (page) => page.locator(DOCTOR).allInnerTexts();
const postResponse = (page, sid) => page.waitForResponse((response) =>
  response.request().method() === "POST" && new URL(response.url()).pathname === `/api/sessions/${sid}/ask`);
const refreshResponse = (page, sid) => page.waitForResponse((response) =>
  response.request().method() === "GET" && new URL(response.url()).pathname === `/api/sessions/${sid}`);
const clickSend = async (page, question) => {
  if (question !== undefined) await page.getByLabel("问诊内容").fill(question);
  await page.getByRole("button", { name: "发送", exact: true }).click();
};
const waitSendEnabled = (page) => page.waitForFunction(() => {
  const button = [...document.querySelectorAll("button")].find((item) => item.textContent.trim() === "发送");
  return !!button && !button.disabled;
});
const turnMatches = async (page, question, answer) => {
  const doctors = await doctorTexts(page);
  const patients = await patientTexts(page);
  return doctors.length === 1 && patients.length === 1 && doctors[0].includes(question) && patients[0].trim() === answer.trim();
};

async function holdRefresh(page, sid, fail = false) {
  const seen = deferred();
  const release = deferred();
  let used = false;
  await page.route(`**/api/sessions/${sid}`, async (route) => {
    if (used || route.request().method() !== "GET") { await route.continue(); return; }
    used = true;
    seen.resolve();
    await release.promise;
    if (fail) {
      await route.fulfill({ status: 500, contentType: "application/json", body: JSON.stringify({ detail: "synthetic refresh failure" }) });
    } else {
      await route.continue();
    }
  });
  return { seen: seen.promise, release: release.resolve };
}

async function scenario(name, fn) {
  evidence.scenarios[name] = { sequence: [] };
  try {
    await fn(evidence.scenarios[name].sequence);
  } catch (error) {
    check(name, "场景执行完成", false, String(error).split("\n")[0]);
  }
}

await scenario("refresh-gate", async (sequence) => {
  const sid = await createSession();
  const { context, page } = await newPage();
  try {
    await openTraining(page, sid);
    const monitorBefore = await page.locator(".readouts").innerText();
    const coverageBefore = await page.locator(".coverage").innerText();
    const gate = await holdRefresh(page, sid);
    const waitingPost = postResponse(page, sid);
    await clickSend(page, "有高血压吗");
    const response = await waitingPost;
    const posted = await response.json();
    sequence.push("POST response body read");
    await gate.seen;
    sequence.push("refresh GET held");
    await page.waitForFunction(() => document.querySelector('[aria-label="问诊内容"]').value === "");
    const visibleBeforeRelease = await turnMatches(page, "有高血压吗", posted.event.result.answer);
    sequence.push({ mark: "DOM inspected before GET release", matches_post_event: visibleBeforeRelease });
    check("refresh-gate", "POST 已保存且响应读取成功", response.status() === 200 && posted.event.action === "ask", posted.event.event_id);
    check("refresh-gate", "GET 放行前展示与 POST event 一致的问答", visibleBeforeRelease, { posted_event: posted.event });
    check("refresh-gate", "即时展示不提前改变监护与问诊结构状态",
      (await page.locator(".readouts").innerText()) === monitorBefore && (await page.locator(".coverage").innerText()) === coverageBefore);
    await page.screenshot({ path: path.join(OUT, "01-before-get-release.png"), fullPage: true });
    const waitingRefresh = refreshResponse(page, sid);
    gate.release();
    sequence.push("refresh GET released");
    const refreshed = await waitingRefresh;
    await refreshed.finished();
    await settle(page);
    sequence.push("refresh response body read");
    check("refresh-gate", "刷新成功后问答不重复", await turnMatches(page, "有高血压吗", posted.event.result.answer));
    const saved = await detail(sid);
    check("refresh-gate", "服务端仅保存一个本轮事件", saved.state.events.length === 1 && saved.state.events[0].event_id === posted.event.event_id);
  } finally { await context.close(); }
});

await scenario("refresh-failure", async (sequence) => {
  const sid = await createSession();
  const { context, page } = await newPage();
  const askRequests = [];
  page.on("request", (request) => { if (request.method() === "POST" && request.url().endsWith(`/api/sessions/${sid}/ask`)) askRequests.push(request); });
  try {
    await openTraining(page, sid);
    const gate = await holdRefresh(page, sid, true);
    const waitingPost = postResponse(page, sid);
    await clickSend(page, "有糖尿病吗");
    const posted = await (await waitingPost).json();
    await gate.seen;
    await page.waitForFunction(() => document.querySelector('[aria-label="问诊内容"]').value === "");
    check("refresh-failure", "刷新失败前已显示服务端确认的回答", await turnMatches(page, "有糖尿病吗", posted.event.result.answer));
    const waitingRefresh = refreshResponse(page, sid);
    gate.release();
    const failedRefresh = await waitingRefresh;
    sequence.push("refresh GET released as 500");
    const warning = page.locator(".msg-bar.warn");
    await warning.waitFor();
    const warningText = (await warning.innerText()).trim();
    check("refresh-failure", "刷新返回失败", failedRefresh.status() === 500);
    check("refresh-failure", "只提示重新读取且不要求重发", warningText.includes(REFRESH_NOTICE) && !warningText.includes("原样重试"), warningText);
    check("refresh-failure", "刷新失败后已确认回答仍保留", await turnMatches(page, "有糖尿病吗", posted.event.result.answer));
    const waitingReread = refreshResponse(page, sid);
    const postCount = askRequests.length;
    await page.getByRole("button", { name: "重新读取", exact: true }).click();
    const reread = await waitingReread;
    await warning.waitFor({ state: "hidden" });
    check("refresh-failure", "重新读取只发 GET 且刷新后不重复",
      reread.status() === 200 && askRequests.length === postCount && await turnMatches(page, "有糖尿病吗", posted.event.result.answer));
    await page.screenshot({ path: path.join(OUT, "02-refresh-reread.png"), fullPage: true });
  } finally { await context.close(); }
});

await scenario("post-failure", async (sequence) => {
  const sid = await createSession();
  const { context, page } = await newPage();
  try {
    await openTraining(page, sid);
    await setControl({ hang: 4 });
    const waitingPost = postResponse(page, sid);
    await clickSend(page, "有高血压吗");
    const response = await waitingPost;
    sequence.push(`POST returned ${response.status()}`);
    await page.locator(".msg-bar.warn").waitFor({ timeout: 30000 });
    await waitSendEnabled(page);
    const saved = await detail(sid);
    check("post-failure", "POST 失败保留原输入", (await page.getByLabel("问诊内容").inputValue()) === "有高血压吗");
    check("post-failure", "POST 失败页面无新增问答", (await patientTexts(page)).length === 0 && (await doctorTexts(page)).length === 0);
    check("post-failure", "POST 失败服务端无新增事件", response.status() >= 400 && saved.state.events.length === 0);
    await page.screenshot({ path: path.join(OUT, "03-post-failure.png"), fullPage: true });
  } finally {
    await setControl({ hang: 0 });
    await context.close();
  }
});

await scenario("request-replay", async (sequence) => {
  const sid = await createSession();
  const { context, page } = await newPage();
  const requestIds = [];
  page.on("request", (request) => {
    if (request.method() === "POST" && request.url().endsWith(`/api/sessions/${sid}/ask`)) {
      try { requestIds.push(request.postDataJSON().request_id); } catch { requestIds.push(null); }
    }
  });
  try {
    await openTraining(page, sid);
    let dropped = false;
    let firstSaved;
    await page.route(`**/api/sessions/${sid}/ask`, async (route) => {
      if (dropped) { await route.continue(); return; }
      dropped = true;
      const upstream = await context.request.fetch(route.request().url(), {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Owner-Id": OWNER },
        data: route.request().postData(),
      });
      firstSaved = await upstream.json();
      sequence.push("first POST saved on server; browser response aborted");
      await route.abort("failed");
    });
    await clickSend(page, "有高血压吗");
    await page.locator(".msg-bar.warn").waitFor();
    await waitSendEnabled(page);
    check("request-replay", "响应丢失后保留输入且尚未展示未确认文本",
      (await page.getByLabel("问诊内容").inputValue()) === "有高血压吗" && (await patientTexts(page)).length === 0);
    check("request-replay", "首次请求其实已在服务端保存一次", (await detail(sid)).state.events.length === 1);
    const callsBeforeReplay = (await controlStatus()).calls;
    const gate = await holdRefresh(page, sid);
    const waitingPost = postResponse(page, sid);
    await clickSend(page);
    const replayed = await (await waitingPost).json();
    await gate.seen;
    await page.waitForFunction(() => document.querySelector('[aria-label="问诊内容"]').value === "");
    check("request-replay", "重放响应在 GET 放行前显示既有事件", await turnMatches(page, "有高血压吗", replayed.event.result.answer));
    check("request-replay", "重放复用请求编号与事件", replayed.timing.replayed === true && replayed.event.event_id === firstSaved.event.event_id && requestIds.length === 2 && requestIds[0] === requestIds[1], requestIds);
    const waitingRefresh = refreshResponse(page, sid);
    gate.release();
    await waitingRefresh;
    await settle(page);
    check("request-replay", "重放及刷新后 DOM 不重复", await turnMatches(page, "有高血压吗", replayed.event.result.answer));
    check("request-replay", "重放及刷新后服务端不重复", (await detail(sid)).state.events.length === 1);
    check("request-replay", "重放未再次调用本机病人替身", (await controlStatus()).calls === callsBeforeReplay);
  } finally { await context.close(); }
});

await scenario("session-switch", async (sequence) => {
  const sidA = await createSession();
  const sidB = await createSession();
  const { context, page } = await newPage();
  let watchLateARefresh = false;
  let lateARefresh = false;
  page.on("request", (request) => {
    if (watchLateARefresh && request.method() === "GET" && request.url().endsWith(`/api/sessions/${sidA}`)) lateARefresh = true;
  });
  try {
    await openTraining(page, sidA);
    const upstreamReady = deferred();
    const release = deferred();
    let postedA;
    await page.route(`**/api/sessions/${sidA}/ask`, async (route) => {
      const upstream = await context.request.fetch(route.request().url(), {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Owner-Id": OWNER },
        data: route.request().postData(),
      });
      const body = await upstream.body();
      postedA = JSON.parse(body.toString());
      upstreamReady.resolve();
      sequence.push("training A POST saved; response held");
      await release.promise;
      await route.fulfill({ status: upstream.status(), headers: upstream.headers(), body });
    });
    const waitingPost = postResponse(page, sidA);
    await clickSend(page, "有高血压吗");
    await upstreamReady.promise;
    await page.evaluate((sid) => { window.location.hash = `#/training/${sid}`; }, sidB);
    await page.waitForFunction((id) => document.body.innerText.includes(id.slice(0, 8)), sidB);
    await page.getByLabel("问诊内容").fill("B 训练尚未发送的草稿");
    watchLateARefresh = true;
    release.resolve();
    await waitingPost;
    await settle(page);
    const doctors = (await doctorTexts(page)).join("\n");
    const patients = (await patientTexts(page)).join("\n");
    check("session-switch", "A 的迟到回答不进入 B 页面",
      !doctors.includes("有高血压吗") && !patients.includes(postedA.event.result.answer) && (await patientTexts(page)).length === 0);
    check("session-switch", "A 的迟到完成不清空 B 的草稿", (await page.getByLabel("问诊内容").inputValue()) === "B 训练尚未发送的草稿");
    check("session-switch", "A/B 服务端写入归属正确", (await detail(sidA)).state.events.length === 1 && (await detail(sidB)).state.events.length === 0);
    check("session-switch", "迟到 A 完成不再发起会覆盖 B 的 A 刷新", lateARefresh === false);
    await page.screenshot({ path: path.join(OUT, "04-session-switch.png"), fullPage: true });
  } finally { await context.close(); }
});

check("browser", "无未捕获前端脚本错误", evidence.page_errors.length === 0, evidence.page_errors.slice(0, 3));
evidence.local_fake_status = await controlStatus();
evidence.passed = evidence.checks.every((row) => row.ok);
writeFileSync(path.join(OUT, "run.json"), JSON.stringify(evidence, null, 2), "utf-8");
await browser.close();
stack.child.kill();
console.log(`${evidence.passed ? "ALL CHECKS PASSED" : "REGRESSION DETECTED"} -> ${path.join(OUT, "run.json")}`);
process.exit(evidence.passed ? 0 : 1);
