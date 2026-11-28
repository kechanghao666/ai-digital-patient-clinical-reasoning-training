"""The built React front end is served by the API process without shadowing API routes."""
import re

from fastapi.testclient import TestClient

from app import main
from app.disclosure import detect_intents
from test_cases_config import ROOT


def test_root_serves_built_spa_and_api_routes_still_win():
    assert (ROOT / "web/dist/index.html").is_file(), "run `node build.mjs` in web/ and commit dist/"
    client = TestClient(main.app)
    page = client.get("/")
    assert page.status_code == 200 and '<div id="root">' in page.text
    assert client.get("/assets/main.js").status_code == 200
    assert client.get("/health").json()["status"] == "ok"
    # API errors stay JSON, never the SPA shell.
    missing = client.get("/api/sessions/does-not-exist", headers={"X-Owner-Id": "demo-web"})
    assert missing.headers["content-type"].startswith("application/json")


def test_built_bundle_has_no_hidden_case_data():
    bundle = (ROOT / "web/dist/assets/main.js").read_text(encoding="utf-8")
    for term in ("hidden_diagnosis\":", "阑尾炎", "心肌梗死", "酮症酸中毒", "缺血性卒中", "社区获得性肺炎"):
        assert term not in bundle


def test_self_introduction_before_opening_question_counts_as_opening():
    assert detect_intents("您好，我是今天接诊的医生，请问哪里不舒服？")[0] is True
    assert detect_intents("您好，我是今天接诊的医生")[0] is False


def test_built_bundle_ships_the_ask_failure_notice():
    # A failed ask must leave a persistent, student-visible explanation in the main React UI instead of only
    # a transient toast. A lost response cannot be told apart from an unrecorded turn, so the notice must
    # never claim the turn was not recorded; it keeps the draft and asks the student to resend the same
    # question so the idempotent retry can confirm what, if anything, was saved.
    bundle = (ROOT / "web/dist/assets/main.js").read_text(encoding="utf-8")
    decoded = re.sub(r"\\u([0-9A-Fa-f]{4})", lambda m: chr(int(m.group(1), 16)), bundle)
    assert "未收到本次提问的结果。输入已保留，请先原样重试以确认记录。" in decoded
    assert "本轮未记录任何问诊" not in decoded


def test_saved_ask_event_is_merged_before_refresh_and_scoped_to_its_training():
    # The real-browser behavior regression is web/e2e/ask-saved-answer.mjs. This source/bundle guard
    # keeps its protocol invariant visible to the Python CI suite: consume only the saved public event,
    # merge it before reloading, dedupe both event and request ids, and reject late cross-training writes.
    src = (ROOT / "web/src/pages/Training.tsx").read_text(encoding="utf-8")
    flat = re.sub(r"\s+", "", src)
    merge = "onSavedAsk(sessionId,saved.event)"
    refresh = "constrefreshed=awaitreload();"
    assert merge in flat and refresh in flat
    assert flat.index(merge) < flat.index(refresh, flat.index(merge))
    assert "current.state.session_id!==askedSession" in flat
    assert "e.event_id===event.event_id||e.request_id===event.request_id" in flat
    assert "saved.answer" not in src  # never render the unprojected top-level text or an optimistic draft
    bundle = (ROOT / "web/dist/assets/main.js").read_text(encoding="utf-8")
    decoded = re.sub(r"\\u([0-9A-Fa-f]{4})", lambda m: chr(int(m.group(1), 16)), bundle)
    assert "回答已保存并显示，但训练记录重新读取失败。请重新读取。" in decoded


def test_teacher_feedback_load_discards_a_superseded_record():
    # The teacher can load training A, then load training B and start reviewing it. If A's whole-record
    # response arrives last, it must be dropped instead of replacing the record (and the unsaved draft)
    # now on screen, and A's failure must not surface as B's error. The page keeps a monotonic load
    # counter and bails out once a newer load has started. There is no JS test runner in this repo, so
    # this guards the shipped source the same way the bundle checks above guard web/dist.
    src = (ROOT / "web/src/pages/teacher/FeedbackReview.tsx").read_text(encoding="utf-8")
    flat = re.sub(r"\s+", "", src)
    assert "loadSeq=useRef" in flat
    assert "++loadSeq.current" in flat
    assert flat.count("!==loadSeq.current") >= 2  # one after the record resolves, one on its error


def test_teacher_score_load_discards_a_superseded_record():
    # The score-review page loads a whole score record through a separate code path from the feedback
    # page, and it writes score adjudications. It has the same race: the teacher loads training A, then
    # loads training B via the in-page form and starts adjudicating it (per-item reasons, overall
    # reason, cited evidence). If A's whole-record response (success or failure) arrives last, it must
    # be dropped instead of swapping the page back to A and discarding B's unsaved decisions, and A's
    # error must not surface on B. A monotonic load counter gates both outcomes. No JS runtime here, so
    # this guards the shipped source like the feedback regression above.
    src = (ROOT / "web/src/pages/teacher/ScoreReview.tsx").read_text(encoding="utf-8")
    flat = re.sub(r"\s+", "", src)
    assert "loadSeq=useRef" in flat
    assert "++loadSeq.current" in flat
    assert flat.count("!==loadSeq.current") >= 2  # one after the record resolves, one on its error


def test_teacher_review_save_receipt_survives_refresh_and_names_its_training():
    # Both review pages save a version and then refresh from the server. The receipt must survive that
    # refresh (so the teacher can tell which training and which version was saved without clicking save
    # again), name which kind of version it is, and be scoped to the training it belongs to. Each page
    # keeps the on-screen training in activeSession; the receipt is kept only while it matches, and is
    # dropped if a newer load (another training) took over mid-save. Save failure and refresh failure are
    # judged apart: a rejection keeps the input, a successful save whose refresh fails says so. There is
    # no JS test runner in this repo, so this guards the shipped source.
    for page, kind in (("FeedbackReview", "已保存反馈复核第"), ("ScoreReview", "已保存评分裁定第")):
        src = (ROOT / f"web/src/pages/teacher/{page}.tsx").read_text(encoding="utf-8")
        flat = re.sub(r"\s+", "", src)
        assert "activeSession=useRef" in flat
        assert "activeSession.current=session" in flat  # recorded on every load
        assert "cur&&cur.session_id===session?cur:null" in flat  # receipt kept only for its own training
        assert kind in src  # distinguishes feedback-review from score-adjudication versions
        assert "页面刷新失败" in src  # saved-but-refresh-failed is stated, not hidden by the refresh
        assert flat.count("activeSession.current!==session)return") >= 3  # reject / success / post-refresh
        assert "refreshed===false" in flat
