// Controlled-wait browser probe: where does the ask pipeline actually wait?
//
// Uses web/e2e/ask_stack.py (fake Nexent + real backend/MCP + temporary DB) and a real browser to
// inject an event-barrier pause at three points — before fact submission, after submission but
// before the completion event (with the SSE stream already established), and after the backend's
// answer authorization — and records whether the student sees or the server saves anything early,
// plus click->DOM after release and a same-request_id idempotent replay. No real model.
//
// Clock origins (recorded, NOT comparable across origins):
//   clickT / postT / visibleT / paintT : browser `performance.now()`
//   server_stages / adapter trace ms   : API-process `time.monotonic()`
//   barrier reached / released          : this driver's `Date.now()` wall clock
// These three clocks have different zero points and cannot be subtracted across origins.
import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, statSync, writeFileSync } from "node:fs";
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

// Pin the served frontend build and record a fingerprint, so the probe never inherits a stale
// WEB_DIST_DIR or a pre-existing page. The stack is told to serve the same directory.
const DIST = process.env.WEB_DIST_DIR || path.join(ROOT, "web", "dist");
const build = (() => {
  const index = path.join(DIST, "index.html");
  if (!existsSync(index)) return { error: `web build missing: ${index} — run \`npm run build\` in web/` };
  const mainJs = path.join(DIST, "assets", "main.js");
  return {
    dist: DIST,
    index_html_exists: true,
    main_js_sha256: existsSync(mainJs) ? createHash("sha256").update(readFileSync(mainJs)).digest("hex") : null,
    main_js_bytes: existsSync(mainJs) ? statSync(mainJs).size : null,
  };
})();
if (build.error) { console.error(build.error); process.exit(2); }

function startStack() {
  return new Promise((resolve, reject) => {
    const child = spawn(PYTHON, [path.join(ROOT, "web", "e2e", "ask_stack.py")], {
      cwd: ROOT, env: { ...process.env, PYTHONUNBUFFERED: "1", WEB_DIST_DIR: DIST }, stdio: ["ignore", "pipe", "pipe"],
    });
    let output = "";
    let ready = false;
    const timer = setTimeout(() => { child.kill(); reject(new Error(`stack not ready\n${output}`)); }, 90000);
    child.stdout.on("data", (chunk) => {
      output += chunk.toString();
      const match = output.match(/READY (\S+) (\S+) (\S+)/);
      if (match && !ready) {
        ready = true;
        clearTimeout(timer);
        resolve({ child, api: match[1], control: match[2], dataDir: match[3] });
      }
    });
    child.stderr.on("data", (chunk) => { output += chunk.toString(); });
    child.on("exit", (code) => {
      if (!ready) { clearTimeout(timer); reject(new Error(`stack exited ${code}\n${output}`)); }
    });
  });
}

let stack;
let browser = null;
let cleanup = null;
const rows = [];
let passed = false;

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

async function runPause(position, question) {
  const sid = await createSession();
  const { context, page } = await newPage();
  await openTraining(page, sid);
  const row = {
    position, question,
    request_id: null,
    server_stages: null, adapter_trace: null,
    // Each field's actual zero point; "monotonic" does not mean the same zero point.
    timing_origins: {
      server_stages: "API process time.monotonic(), zero = ask handler entry",
      "trace.first_event_ms/total_ms/trusted_completion_ms": "API process time.monotonic(), zero = Nexent client run() entry (a different zero)",
      "trace.finalize_ms": "API process time.monotonic(), zero = select_facts() entry (a different zero)",
      "trace.answer_validated_ms": "same zero as server_stages (ask handler entry)",
      browser_timings: "browser performance.now() (a different clock, cannot subtract from the above)",
      barrier: "driver Date.now() wall clock — the moment this driver's poll observed the pause / sent the release, not the server's exact barrier time",
    },
    barrier: { driver_observed_paused_after_ms: null, driver_release_sent_after_ms: null },
    paused_request_incomplete: null, paused_blocked_answer: null, paused_no_save: null,
    http_status: null, api_answer: null, dom_answer: null, persisted_answer: null, answers_consistent: null,
    browser_timings: null, browser_timings_ordered: null,
    click_to_visible_ms: null, saved_events: null, settled_single: null,
    replay_status: null, replay_replayed: null, replay_same_event: null, replay_no_extra_call: null,
    fake_platform: true, real_model_calls: 0,
    adapter_trace_note: "adapter_trace.real_platform=true reflects the Nexent adapter path; this run used the local fake Nexent (zero real vendor calls)",
  };
  try {
    // Capture the frontend's own POST body so we learn the idempotency request_id it used.
    const captured = {};
    const onReq = (req) => {
      if (req.method() === "POST" && req.url().endsWith(`/api/sessions/${sid}/ask`)) {
        try { const b = req.postDataJSON(); captured.request_id = b.request_id; captured.question = b.question; } catch {}
      }
    };
    page.on("request", onReq);

    await setControl({ pause_at: position });
    const before = await patientCount(page);
    let requestCompletedDuringPause = false;
    const postPromise = page.waitForResponse((r) => r.request().method() === "POST" && new URL(r.url()).pathname === `/api/sessions/${sid}/ask`, { timeout: 30000 }).catch(() => null);
    postPromise.then(() => { requestCompletedDuringPause = true; }, () => {});
    const barrierT0 = Date.now();
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
    row.barrier.driver_observed_paused_after_ms = Date.now() - barrierT0;
    row.request_id = captured.request_id;
    // During pause: the ask POST must still be in flight, no answer, no saved event.
    row.paused_request_incomplete = !requestCompletedDuringPause;
    row.paused_blocked_answer = (await patientCount(page)) === before;
    row.paused_no_save = (await detail(sid)).state.events.length === 0;
    await setControl({ release: true });
    row.barrier.driver_release_sent_after_ms = Date.now() - barrierT0;
    const response = await postPromise;
    row.http_status = response ? response.status() : null;
    const posted = response ? await response.json() : null;
    row.server_stages = posted && posted.timing ? posted.timing.stages : null;
    // The raw adapter trace is stripped by safe_event from the student API, so read it from the
    // fake Nexent's diagnostic endpoint (trusted_completion_ms / finalize_ms / first_event_ms ...).
    const diagAdapter = await (await fetch(stack.control + `/__trace/${sid}`)).json();
    row.adapter_trace = diagAdapter && typeof diagAdapter === "object" && diagAdapter.trace ? diagAdapter.trace : diagAdapter;
    row.api_answer = posted && posted.event && posted.event.result ? posted.event.result.answer : null;
    await page.waitForFunction(() => window.__o10a && window.__o10a.paintT !== null, null, { timeout: 15000 });
    const t = await page.evaluate(() => ({ ...window.__o10a }));
    row.dom_answer = t.visibleText;
    row.browser_timings = { clickT: t.clickT, postT: t.postT, visibleT: t.visibleT, paintT: t.paintT };
    const bt = row.browser_timings;
    row.browser_timings_ordered = [bt.clickT, bt.postT, bt.visibleT, bt.paintT].every((v) => v !== null)
      && bt.clickT <= bt.postT && bt.postT <= bt.visibleT && bt.visibleT <= bt.paintT;
    row.click_to_visible_ms = t.visibleT !== null && t.clickT !== null ? +(t.visibleT - t.clickT).toFixed(2) : null;
    const saved = await detail(sid);
    row.saved_events = saved.state.events.length;
    row.persisted_answer = saved.state.events[0] && saved.state.events[0].result ? saved.state.events[0].result.answer : null;
    // Release consistency: HTTP status, API answer, DOM answer and persisted answer must all agree.
    row.answers_consistent = row.http_status === 200 && row.api_answer !== null && row.api_answer === row.dom_answer && row.api_answer === row.persisted_answer;
    // True idempotent replay: re-send the SAME request_id + question directly to the API. The
    // backend must recognize the idempotency key, replay the same event (same event_id/seq/answer),
    // and NOT start a new platform run or persist a second event.
    const callsBefore = (await controlStatus()).calls;
    const replay = await api("POST", `/api/sessions/${sid}/ask`, { request_id: row.request_id, question: captured.question || question });
    row.replay_status = replay.status;
    row.replay_replayed = !!(replay.data && replay.data.timing && replay.data.timing.replayed);
    const afterReplay = await detail(sid);
    const replayEvent = replay.data && replay.data.event ? replay.data.event : null;
    const persistedEvent = afterReplay.state.events[0] || null;
    row.replay_event_id = replayEvent ? replayEvent.event_id : null;
    row.persisted_event_id = persistedEvent ? persistedEvent.event_id : null;
    row.replay_same_event = afterReplay.state.events.length === 1 && replayEvent && persistedEvent
      && replayEvent.event_id === persistedEvent.event_id
      && replayEvent.seq === persistedEvent.seq
      && replayEvent.result && replayEvent.result.answer === persistedEvent.result.answer
      && posted && posted.event && posted.event.event_id === persistedEvent.event_id;
    row.replay_no_extra_call = (await controlStatus()).calls === callsBefore;
    // After release + settle there must still be exactly one saved turn.
    await page.waitForTimeout(500);
    const after = await detail(sid);
    row.settled_single = saved.state.events.length === 1 && after.state.events.length === 1;
  } finally {
    await setControl({ release: true, pause_at: null }).catch(() => {});
    await context.close();
  }
  return row;
}

async function runTimeout(question) {
  const sid = await createSession();
  const { context, page } = await newPage();
  await openTraining(page, sid);
  const row = { scenario: "timeout", question, http_status: null, blocked_answer: null, no_save: null };
  try {
    // The platform stalls 4s before its first event; the backend's first-event deadline (1.5s)
    // must reject it. A failure must never surface a partial answer or save an event.
    await setControl({ hang: 4 });
    const before = await patientCount(page);
    const postPromise = page.waitForResponse((r) => r.request().method() === "POST" && new URL(r.url()).pathname === `/api/sessions/${sid}/ask`, { timeout: 30000 }).catch(() => null);
    await page.getByLabel("问诊内容").fill(question);
    await page.getByRole("button", { name: "发送", exact: true }).click();
    const response = await postPromise;
    row.http_status = response ? response.status() : null;
    await page.waitForTimeout(1500);
    row.blocked_answer = (await patientCount(page)) === before;
    row.no_save = (await detail(sid)).state.events.length === 0;
  } finally {
    await setControl({ hang: 0 }).catch(() => {});
    await context.close();
  }
  return row;
}

try {
  stack = await startStack();
  browser = await chromium.launch({ executablePath: CHROME, args: ["--no-sandbox"] });

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
  console.log("=== timeout scenario ===");
  const timeoutRow = await runTimeout("有高血压吗？");
  console.log(JSON.stringify(timeoutRow, null, 2));
  rows.push(timeoutRow);

  const pauseOk = rows.filter((r) => r.position).every((r) =>
    r.paused_request_incomplete && r.paused_blocked_answer && r.paused_no_save &&
    r.http_status === 200 && r.answers_consistent && r.saved_events === 1 && r.settled_single &&
    r.browser_timings_ordered &&
    r.replay_status === 200 && r.replay_replayed && r.replay_same_event && r.replay_no_extra_call
  );
  const timeoutOk = timeoutRow.http_status >= 400 && timeoutRow.blocked_answer && timeoutRow.no_save;
  passed = pauseOk && timeoutOk;
} catch (error) {
  console.error("probe failed:", error);
  passed = false;
} finally {
  if (browser) await browser.close().catch(() => {});
  cleanup = await shutdownStack();
}

writeFileSync(path.join(OUT, "run.json"), JSON.stringify({ rows, passed, build, cleanup, real_model_calls: 0, fake_platform: true }, null, 2), "utf-8");
console.log(passed ? "ALL CHECKS PASSED" : "REGRESSION DETECTED", "->", path.join(OUT, "run.json"));
process.exit(passed ? 0 : 1);

async function shutdownStack() {
  const result = { shutdown_sent: false, process_exited: false, temp_dir: null, temp_dir_removed: null, force_killed: false };
  if (!stack) return result;
  result.temp_dir = stack.dataDir || null;
  try {
    await fetch(stack.control + "/__shutdown", { method: "POST" });
    result.shutdown_sent = true;
  } catch (e) {
    result.shutdown_sent = false;
  }
  // Wait up to 5s for a graceful exit; only force-kill if it did not exit.
  if (stack.child.exitCode === null && stack.child.signalCode === null) {
    const exited = await new Promise((resolve) => {
      const timer = setTimeout(() => resolve(false), 5000);
      stack.child.once("exit", () => { clearTimeout(timer); resolve(true); });
    });
    result.process_exited = exited;
    if (!exited) { stack.child.kill(); result.force_killed = true; }
  } else {
    result.process_exited = true;
  }
  await new Promise((r) => setTimeout(r, 400));
  if (result.temp_dir) result.temp_dir_removed = !existsSync(result.temp_dir);
  return result;
}
