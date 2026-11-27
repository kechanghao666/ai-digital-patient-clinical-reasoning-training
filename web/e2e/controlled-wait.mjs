// Controlled-wait browser probe: where does the ask pipeline actually wait?
//
// Uses web/e2e/ask_stack.py (fake Nexent + real backend/MCP + temporary DB) and a real browser to
// inject an event-barrier pause at three points — before fact submission, after submission but
// before the completion event, and after the backend's answer authorization — and records whether
// the student sees or the server saves anything early, plus click->DOM after release. No real model.
import { spawn } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";

const ROOT = process.env.PROJECT_ROOT || path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const OUT = process.env.OUT || mkdtempSync(path.join(tmpdir(), "controlled-wait-"));
const PYTHON = process.env.PYTHON || path.join(ROOT, ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
const CHROME = process.env.CHROME || [
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
  "C:/Program Files/Microsoft/Edge/Application/msedge.exe",
  "C:/Program Files/Google/Chrome/Application/chrome.exe",
  path.join(process.env.LOCALAPPDATA || "", "Google/Chrome/Application/chrome.exe"),
].find(existsSync);
const OWNER = "demo-controlled-wait";
const PATIENT = ".turn.patient .text:not(.faint)";
if (process.env.OUT) mkdirSync(OUT);

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
const browser = await chromium.launch({ executablePath: CHROME, args: ["--no-sandbox"] });
async function api(method, route, body) {
  const r = await fetch(stack.api + route, {
    method, headers: { "Content-Type": "application/json", "X-Owner-Id": OWNER },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await r.text();
  let data = null; try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  return { status: r.status, data };
}
const setControl = async (patch) => (await fetch(stack.control + "/__control", {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(patch),
})).json();
const controlStatus = async () => (await fetch(stack.control + "/__control")).json();
const createSession = async () => {
  const r = await api("POST", "/api/sessions?case_id=CASE-001");
  if (r.status !== 200) throw new Error("create session failed " + JSON.stringify(r));
  return r.data.session_id;
};
const detail = async (sid) => (await api("GET", `/api/sessions/${sid}`)).data;

async function newPage() {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: "zh-CN" });
  await context.addInitScript((owner) => {
    sessionStorage.setItem("clinic.identity", JSON.stringify({
      mode: "demo", ownerId: owner,
      user: { id: owner, username: "demo", display_name: "合成学生", role: "student" },
    }));
    window.__o10a = { clickT: null, postT: null, visibleT: null, paintT: null, visibleText: null };
    if (!window.__o10aInstalled) {
      window.__o10aInstalled = true;
      document.addEventListener("click", (e) => {
        const b = e.target && e.target.closest ? e.target.closest("button") : null;
        if (b && (b.textContent || "").trim() === "发送") window.__o10a.clickT = performance.now();
      }, true);
      const origFetch = window.fetch.bind(window);
      window.fetch = (...args) => {
        const url = typeof args[0] === "string" ? args[0] : "";
        const p = origFetch(...args);
        if (url.includes("/api/sessions/") && url.includes("/ask")) p.then(() => { window.__o10a.postT = performance.now(); }, () => {});
        return p;
      };
    }
  }, OWNER);
  const page = await context.newPage();
  return { context, page };
}
async function openTraining(page, sid) {
  await page.goto(stack.api + `/#/training/${sid}`);
  await page.getByLabel("问诊内容").waitFor();
  await page.evaluate(() => {
    const log = document.querySelector(".dialog-log");
    if (!log || window.__o10aObs) return;
    window.__o10aObs = new MutationObserver((muts) => {
      for (const m of muts) for (const node of m.addedNodes) {
        if (node.nodeType === 1 && node.classList && node.classList.contains("turn") && node.classList.contains("patient")) {
          const text = node.querySelector(".text:not(.faint)");
          if (text && text.textContent.trim() && window.__o10a.visibleT === null) {
            window.__o10a.visibleT = performance.now();
            window.__o10a.visibleText = text.textContent.trim();
            requestAnimationFrame(() => requestAnimationFrame(() => { window.__o10a.paintT = performance.now(); }));
          }
        }
      }
    });
    window.__o10aObs.observe(log, { childList: true, subtree: true });
  });
}
const patientCount = (page) => page.locator(PATIENT).count();
const loadingVisible = (page) => page.locator(".turn.patient .text.faint").count() > 0;

async function runPause(position, question) {
  const sid = await createSession();
  const { context, page } = await newPage();
  await openTraining(page, sid);
  const row = { position, question, server_stages: null, paused_blocked_answer: null, paused_no_save: null, answer: null, click_to_visible_ms: null, saved_events: null, settled_single: null };
  try {
    await setControl({ pause_at: position });
    const before = await patientCount(page);
    const postPromise = page.waitForResponse((r) => r.request().method() === "POST" && new URL(r.url()).pathname === `/api/sessions/${sid}/ask`, { timeout: 30000 }).catch(() => null);
    await page.getByLabel("问诊内容").fill(question);
    await page.getByRole("button", { name: "发送", exact: true }).click();
    // Wait until the pause barrier is reached, then observe.
    for (let i = 0; i < 100; i++) {
      const st = await controlStatus();
      if (st.paused) break;
      await new Promise((r) => setTimeout(r, 50));
    }
    const st = await controlStatus();
    if (!st.paused) throw new Error("pause barrier never reached for " + position);
    row.paused_blocked_answer = (await patientCount(page)) === before;
    row.paused_no_save = (await detail(sid)).state.events.length === 0;
    await setControl({ release: true });
    const response = await postPromise;
    const posted = response ? await response.json() : null;
    row.server_stages = posted && posted.timing ? posted.timing.stages : null;
    await page.waitForFunction(() => window.__o10a && window.__o10a.paintT !== null, null, { timeout: 15000 });
    const t = await page.evaluate(() => ({ ...window.__o10a }));
    row.answer = t.visibleText;
    row.click_to_visible_ms = t.visibleT !== null && t.clickT !== null ? +(t.visibleT - t.clickT).toFixed(2) : null;
    const saved = await detail(sid);
    row.saved_events = saved.state.events.length;
    // Releasing the barrier must not cause a duplicate: after the page settles (refresh completes),
    // the server still holds exactly one turn. Re-sending the same text is a new turn, not a replay,
    // so idempotency is asserted by this single-event count rather than a second click.
    await page.waitForTimeout(500);
    const after = await detail(sid);
    row.settled_single = saved.state.events.length === 1 && after.state.events.length === 1;
  } finally {
    await setControl({ release: true, pause_at: null }).catch(() => {});
    await context.close();
  }
  return row;
}

const rows = [];
for (const [position, question] of [
  ["before_submission", "有高血压吗？"],
  ["after_submission", "有糖尿病吗？"],
  ["after_validation", "以前做过手术吗？"],
]) {
  console.log("=== pause at", position, "===");
  const row = await runPause(position, question);
  console.log(JSON.stringify(row, null, 2));
  rows.push(row);
}
const passed = rows.every((r) => r.paused_blocked_answer && r.paused_no_save && r.answer && r.saved_events === 1 && r.settled_single);
writeFileSync(path.join(OUT, "run.json"), JSON.stringify({ rows, passed }, null, 2), "utf-8");
console.log(passed ? "ALL CHECKS PASSED" : "REGRESSION DETECTED", "->", path.join(OUT, "run.json"));
await browser.close();
stack.child.kill();
process.exit(passed ? 0 : 1);
