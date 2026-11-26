// O10a browser measurement: from clicking "发送" to the first visible answer for THIS question.
//
// Zero real model calls: the driver starts web/e2e/o10a_stack.py (NEXENT_MODE=simulator, temporary
// databases, synthetic teaching cases). It records three instants per turn with a single monotonic
// clock — click, POST response, first visible answer (DOM insertion + double-rAF paint) — and links
// them to the saved ask event. It also reads the UI's own "应答 X 秒" tooltip so the two meanings can
// be compared. Covers first ask, a later ask in the same session, and one controlled failure + retry.
//
// Usage: OUT=local-only/o10a-latency node web/e2e/o10a-latency.mjs
// Optional env: PROJECT_ROOT, PYTHON, CHROME, OUT.
import { spawn } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";

const ROOT = process.env.PROJECT_ROOT || path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const OUT = process.env.OUT || mkdtempSync(path.join(tmpdir(), "o10a-latency-"));
const PYTHON = process.env.PYTHON || path.join(ROOT, ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
const CHROME = process.env.CHROME || [
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
  "C:/Program Files/Microsoft/Edge/Application/msedge.exe",
  "C:/Program Files/Google/Chrome/Application/chrome.exe",
  path.join(process.env.LOCALAPPDATA || "", "Google/Chrome/Application/chrome.exe"),
].find(existsSync);
const OWNER = "demo-o10a";
if (process.env.OUT) mkdirSync(OUT); // Require a fresh output directory; never overwrite evidence.

const evidence = {
  base_commit: process.env.BASE_COMMIT || "run",
  model_policy: "zero real model calls; NEXENT_MODE=simulator (rule simulator) only",
  case_id: "CASE-001 (synthetic teaching case)",
  checks: [], scenarios: {}, page_errors: [],
};
const check = (scenario, name, ok, detail = "") => {
  evidence.checks.push({ scenario, name, ok: !!ok, detail });
  console.log(`${ok ? "OK  " : "FAIL"} [${scenario}] ${name}${detail ? `  ${typeof detail === "string" ? detail : JSON.stringify(detail)}` : ""}`);
  return !!ok;
};

// NEXENT_MODE=nexent -> real Nexent instance (requires NEXENT_BASE_URL / NEXENT_API_KEY /
// NEXENT_PATIENT_AGENT / MCP_SERVICE_TOKEN); otherwise the rule simulator. The driver only
// starts the chosen stack and never auto-starts the simulator when a real instance is requested.
const STACK = process.env.NEXENT_MODE === "nexent" ? "o10a_nexent_stack.py" : "o10a_stack.py";
function startStack() {
  return new Promise((resolve, reject) => {
    const child = spawn(PYTHON, [path.join(ROOT, "web", "e2e", STACK)], {
      cwd: ROOT, env: { ...process.env, PYTHONUNBUFFERED: "1" }, stdio: ["ignore", "pipe", "pipe"],
    });
    let output = "";
    let ready = false;
    const timer = setTimeout(() => { child.kill(); reject(new Error(`stack not ready\n${output}`)); }, 90000);
    child.stdout.on("data", (chunk) => {
      output += chunk.toString();
      const match = output.match(/READY (\S+)/);
      if (match && !ready) {
        ready = true;
        clearTimeout(timer);
        resolve({ child, api: match[1] });
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
    window.__o10a = { clickT: null, postT: null, visibleT: null, paintT: null, visibleText: null, installed: false, observer: null };
    if (!window.__o10a.installed) {
      window.__o10a.installed = true;
      document.addEventListener("click", (e) => {
        const b = e.target && e.target.closest ? e.target.closest("button") : null;
        if (b && (b.textContent || "").trim() === "发送") {
          window.__o10a.clickT = performance.now();
        }
      }, true);
      const origFetch = window.fetch.bind(window);
      window.fetch = (...args) => {
        const url = typeof args[0] === "string" ? args[0] : "";
        const p = origFetch(...args);
        if (url.includes("/api/sessions/") && url.includes("/ask")) {
          // Record the response instant without leaking an unhandled rejection when the request fails.
          p.then(() => { window.__o10a.postT = performance.now(); }, () => {});
        }
        return p;
      };
    }
  }, OWNER);
  const page = await context.newPage();
  page.on("pageerror", (error) => evidence.page_errors.push(String(error)));
  return { context, page };
}

async function openTraining(page, sid) {
  await page.goto(stack.api + `/#/training/${sid}`);
  await page.getByLabel("问诊内容").waitFor();
  await page.waitForFunction((id) => document.body.innerText.includes(id.slice(0, 8)), sid);
  await page.evaluate(() => {
    if (window.__o10a.observer) return;
    const log = document.querySelector(".dialog-log");
    if (!log) return;
    window.__o10a.observer = new MutationObserver((muts) => {
      for (const m of muts) {
        for (const node of m.addedNodes) {
          if (node.nodeType === 1 && node.classList && node.classList.contains("turn") && node.classList.contains("patient")) {
            const text = node.querySelector(".text:not(.faint)");
            if (text && text.textContent.trim() && window.__o10a.visibleT === null) {
              window.__o10a.visibleT = performance.now();
              window.__o10a.visibleText = text.textContent.trim();
              requestAnimationFrame(() => requestAnimationFrame(() => {
                window.__o10a.paintT = performance.now();
              }));
            }
          }
        }
      }
    });
    window.__o10a.observer.observe(log, { childList: true, subtree: true });
  });
}

const postResponse = (page, sid) => page.waitForResponse((response) =>
  response.request().method() === "POST" && new URL(response.url()).pathname === `/api/sessions/${sid}/ask`);
const clickSend = async (page, question) => {
  await page.getByLabel("问诊内容").fill(question);
  await page.getByRole("button", { name: "发送", exact: true }).click();
};
const resetHarness = (page) => page.evaluate(() => {
  window.__o10a.clickT = null;
  window.__o10a.postT = null;
  window.__o10a.visibleT = null;
  window.__o10a.paintT = null;
  window.__o10a.visibleText = null;
});
const readHarness = (page) => page.evaluate(() => ({ ...window.__o10a }));
const patientCount = (page) => page.locator(".turn.patient .text:not(.faint)").count();
const uiLatencyTitle = (page) => page.evaluate(() => {
  const spans = [...document.querySelectorAll(".panel-head .faint")];
  const s = spans.find((x) => (x.title || "").includes("应答"));
  return s ? s.title : null;
});
const waitPainted = (page) => page.waitForFunction(() => window.__o10a && window.__o10a.paintT !== null, null, { timeout: 15000 });

function deltas(t, base = "clickT") {
  const out = {};
  if (t.clickT !== null) {
    out.click_to_response_ms = t.postT !== null ? +(t.postT - t.clickT).toFixed(2) : null;
    out.click_to_visible_ms = t.visibleT !== null ? +(t.visibleT - t.clickT).toFixed(2) : null;
    out.click_to_paint_ms = t.paintT !== null ? +(t.paintT - t.clickT).toFixed(2) : null;
  }
  out.response_to_visible_ms = (t.postT !== null && t.visibleT !== null) ? +(t.visibleT - t.postT).toFixed(2) : null;
  out.response_to_paint_ms = (t.postT !== null && t.paintT !== null) ? +(t.paintT - t.postT).toFixed(2) : null;
  return out;
}

async function measureAsk(page, sid, scenario, turnIndex, question) {
  await resetHarness(page);
  const beforeCount = await patientCount(page);
  const waitingPost = postResponse(page, sid);
  await clickSend(page, question);
  const response = await waitingPost;
  const posted = await response.json();
  await waitPainted(page);
  const t = await readHarness(page);
  const uiTitle = await uiLatencyTitle(page);
  const saved = await detail(sid);
  const askEvents = saved.state.events.filter((e) => e.action === "ask");
  const answerMatches = t.visibleText === posted.event.result.answer;
  const savedMatches = askEvents.some((e) => e.event_id === posted.event.event_id);
  const row = {
    scenario, turn: turnIndex, question,
    timings: { clickT: t.clickT, postT: t.postT, visibleT: t.visibleT, paintT: t.paintT },
    durations_ms: deltas(t),
    ui_latency_title: uiTitle,
    posted_event: { event_id: posted.event.event_id, seq: posted.event.seq, answer: posted.event.result.answer, replayed: posted.timing?.replayed },
    visible_answer: t.visibleText,
    answer_matches_posted: answerMatches,
    saved_event_ids: askEvents.map((e) => e.event_id),
    saved_ask_count: askEvents.length,
  };
  check(scenario, `第 ${turnIndex} 轮显示回答与 POST 事件一致`, answerMatches, { visible: t.visibleText, posted: posted.event.result.answer });
  check(scenario, `第 ${turnIndex} 轮 POST 事件已在服务端保存`, savedMatches, { event_id: posted.event.event_id });
  return row;
}

// --- scenario 1: first ask -------------------------------------------------------
const firstSid = await createSession();
const first = await newPage();
const firstRows = [];
try {
  await openTraining(first.page, firstSid);
  firstRows.push(await measureAsk(first.page, firstSid, "first-ask", 1, "有高血压吗"));
  evidence.scenarios["first-ask"] = firstRows;
} finally { await first.context.close(); }

// --- scenario 2: a later ask in the same session --------------------------------
const laterSid = await createSession();
const later = await newPage();
const laterRows = [];
try {
  await openTraining(later.page, laterSid);
  laterRows.push(await measureAsk(later.page, laterSid, "later-ask", 1, "有高血压吗"));
  laterRows.push(await measureAsk(later.page, laterSid, "later-ask", 2, "有糖尿病吗"));
  check("later-ask", "第二轮只新增一条问答且不覆盖第一轮",
    (await detail(laterSid)).state.events.filter((e) => e.action === "ask").length === 2);
  evidence.scenarios["later-ask"] = laterRows;
} finally { await later.context.close(); }

// --- scenario 3: one controlled failure, then retry -----------------------------
const retrySid = await createSession();
const retry = await newPage();
const retryRows = [];
try {
  await openTraining(retry.page, retrySid);
  await resetHarness(retry.page);
  let dropped = false;
  let firstSaved;
  // Forward the first POST to the server (so it is saved) but abort the browser's response,
  // reproducing a network-level failure that leaves the answer invisible on the first try.
  await retry.page.route(`**/api/sessions/${retrySid}/ask`, async (route) => {
    if (dropped) { await route.continue(); return; }
    dropped = true;
    const upstream = await retry.context.request.fetch(route.request().url(), {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Owner-Id": OWNER },
      data: route.request().postData(),
    });
    firstSaved = await upstream.json();
    await route.abort("failed");
  });
  await clickSend(retry.page, "有高血压吗");
  await retry.page.locator(".msg-bar.warn").waitFor({ timeout: 15000 });
  const failedT = await readHarness(retry.page);
  const failedUi = await uiLatencyTitle(retry.page);
  retryRows.push({
    scenario: "failure-retry", turn: "failure",
    question: "有高血压吗",
    timings: { clickT: failedT.clickT, postT: failedT.postT, visibleT: failedT.visibleT, paintT: failedT.paintT },
    durations_ms: deltas(failedT),
    ui_latency_title: failedUi,
    outcome: "no visible answer; unconfirmed error shown",
    saved_ask_count: (await detail(retrySid)).state.events.filter((e) => e.action === "ask").length,
  });
  check("failure-retry", "失败轮无可见回答且服务端已落一条", firstSaved && firstSaved.event && failedT.visibleT === null
    && (await detail(retrySid)).state.events.filter((e) => e.action === "ask").length === 1, { visibleT: failedT.visibleT });
  const firstClickT = failedT.clickT;

  // Retry the same unchanged question: the frontend reuses its request_id, so the server replays
  // the single saved event instead of writing a second one.
  await resetHarness(retry.page);
  const waitingPost = postResponse(retry.page, retrySid);
  await clickSend(retry.page, "有高血压吗");
  const replayed = await (await waitingPost).json();
  await waitPainted(retry.page);
  const retryT = await readHarness(retry.page);
  const retryUi = await uiLatencyTitle(retry.page);
  const saved = await detail(retrySid);
  const askEvents = saved.state.events.filter((e) => e.action === "ask");
  retryRows.push({
    scenario: "failure-retry", turn: "retry",
    question: "有高血压吗",
    timings: { clickT: retryT.clickT, postT: retryT.postT, visibleT: retryT.visibleT, paintT: retryT.paintT },
    durations_ms: deltas(retryT),
    total_wait_ms: (retryT.paintT !== null && firstClickT !== null) ? +(retryT.paintT - firstClickT).toFixed(2) : null,
    ui_latency_title: retryUi,
    posted_event: { event_id: replayed.event.event_id, seq: replayed.event.seq, answer: replayed.event.result.answer, replayed: replayed.timing?.replayed },
    visible_answer: retryT.visibleText,
    answer_matches_posted: retryT.visibleText === replayed.event.result.answer,
    saved_event_ids: askEvents.map((e) => e.event_id),
    saved_ask_count: askEvents.length,
  });
  check("failure-retry", "重试为已保存事件的重放", replayed.timing?.replayed === true && replayed.event.event_id === firstSaved.event.event_id);
  check("failure-retry", "重试后显示回答与事件一致", retryT.visibleText === replayed.event.result.answer);
  check("failure-retry", "失败+重试只落一条问答", askEvents.length === 1, askEvents.map((e) => e.event_id));
  evidence.scenarios["failure-retry"] = retryRows;
} finally { await retry.context.close(); }

check("browser", "无未捕获前端脚本错误", evidence.page_errors.length === 0, evidence.page_errors.slice(0, 3));
evidence.passed = evidence.checks.every((row) => row.ok);
writeFileSync(path.join(OUT, "run.json"), JSON.stringify(evidence, null, 2), "utf-8");
await browser.close();
stack.child.kill();
console.log(`${evidence.passed ? "ALL CHECKS PASSED" : "REGRESSION DETECTED"} -> ${path.join(OUT, "run.json")}`);
process.exit(evidence.passed ? 0 : 1);
