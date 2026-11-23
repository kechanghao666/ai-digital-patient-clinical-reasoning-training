"""Teacher-side OCR checklist draft: read-only, honest about failures, never fabricated.

Offline only: the OCR service is a stub wired through the settings; nothing here calls a real
vendor. The pre-registered field table demands 心率 75 次/分, 时间 10:32 and a deliberately blank
血钾 field — the draft must carry exactly that, never a filled-in blank, and a service failure
must return no draft at all (no stale or partial transcription can pass as a fresh one).
"""
from __future__ import annotations

import base64
import io

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import main
from app.integration.ocr import OcrClient, OcrError
from app.integration.runtime import IntegrationRuntime
from app.state_machine import StateMachine
from app.storage import SessionRepository
from test_basis_search import OWNER, TEACHER, seal_session
from test_integration import settings_for

OCR_PATH = "/api/teacher/feedback/ocr/draft"


def _png_bytes(width: int = 2, height: int = 2) -> bytes:
    """A genuinely small, real PNG — the success control, not a hand-written header."""
    buf = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buf, format="PNG")
    return buf.getvalue()


def _jpeg_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (2, 2), "white").save(buf, format="JPEG")
    return buf.getvalue()


def _data_url(raw: bytes, declared: str = "png") -> str:
    return f"data:image/{declared};base64," + base64.b64encode(raw).decode()


# A genuinely small, real PNG is the success control.
PNG = _data_url(_png_bytes())

TRANSCRIPTION = (
    "检查项：常规十二导联心电图\n"
    "心率：75 次/分\n"
    "报告时间：10:32\n"
    "血钾：（字段为空）\n"
    "结论提示：窦性心律，未见 ST 段抬高样改变（教学示例）"
)


def make_runtime(tmp_path, configured=True) -> IntegrationRuntime:
    overrides = (
        {"ocr_base_url": "http://ocr.test/v1/chat/completions", "ocr_api_key": "offline-test-key",
         "ocr_model": "xoppaddleocrv16"}
        if configured else {}
    )
    return IntegrationRuntime(settings_for(tmp_path, **overrides),
                              httpx.MockTransport(lambda request: httpx.Response(500)))


def install(tmp_path, monkeypatch, configured=True):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("APP_AUTH_MODE", raising=False)
    repository = SessionRepository(tmp_path / "app.db")
    monkeypatch.setattr(main, "REPOSITORY", repository)
    monkeypatch.setattr(main, "MACHINE", StateMachine(main.CASE_STORE, repository))
    runtime = make_runtime(tmp_path, configured)
    monkeypatch.setattr(main, "RUNTIME", runtime)
    calls: list[str] = []

    def fake_extract(self, image_data_url):  # noqa: ANN001 - patched onto OcrClient.extract_draft
        calls.append(image_data_url[:30])
        return TRANSCRIPTION

    monkeypatch.setattr(OcrClient, "extract_draft", fake_extract)
    return runtime, TestClient(main.app), calls, repository


def test_ocr_draft_returns_preregistered_fields_for_a_teacher(tmp_path, monkeypatch):
    runtime, client, calls, _ = install(tmp_path, monkeypatch)
    session_id = seal_session(client)
    before = client.get(f"/api/teacher/scores/{session_id}",
                        params={"owner_id": OWNER}, headers=TEACHER).json()
    response = client.post(OCR_PATH, headers=TEACHER, json={"image_data_url": PNG})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["available"] is True
    assert body["notice"] == "文字草稿，未经核对"
    assert "心率：75 次/分" in body["draft_text"]
    assert "报告时间：10:32" in body["draft_text"]
    assert "血钾：（字段为空）" in body["draft_text"]  # the blank field is reported, never filled
    assert body["warning"].startswith("缺失字段须人工核对")
    after = client.get(f"/api/teacher/scores/{session_id}",
                       params={"owner_id": OWNER}, headers=TEACHER).json()
    assert before == after  # sealed score untouched


def test_ocr_rejects_non_teacher(tmp_path, monkeypatch):
    runtime, client, _, _ = install(tmp_path, monkeypatch)
    seal_session(client)
    assert client.post(OCR_PATH, json={"image_data_url": PNG}).status_code == 401
    assert client.post(OCR_PATH, headers={"X-Owner-Id": "student-x"},
                       json={"image_data_url": PNG}).status_code == 403


def test_ocr_strict_validation_rejects_before_any_upstream_call(tmp_path, monkeypatch):
    runtime, client, calls, _ = install(tmp_path, monkeypatch)
    seal_session(client)
    # invalid base64 encoding (stray characters, not real base64)
    bad_b64 = "data:image/png;base64,not@@valid!!base64"
    # not an image at all, though declared as PNG
    non_image = _data_url(b"this is definitely not an image")
    # a real image, but the actual format (JPEG) does not match the declared one (PNG)
    mismatch = _data_url(_jpeg_bytes(), declared="png")
    # decoded size over 2 MiB (base64 of ~2 MiB of zeros)
    over_limit = _data_url(b"\x89PNG\r\n\x1a\n" + b"\x00" * (2 * 1024 * 1024))
    for payload in [bad_b64, non_image, mismatch, over_limit,
                    "data:image/gif;base64," + base64.b64encode(b"GIF89a").decode()]:
        response = client.post(OCR_PATH, headers=TEACHER, json={"image_data_url": payload})
        assert response.status_code == 422, (payload[:40], response.text)
    assert calls == []  # every rejection happened before any upstream call


def test_ocr_small_real_image_is_the_success_control(tmp_path, monkeypatch):
    runtime, client, calls, _ = install(tmp_path, monkeypatch)
    seal_session(client)
    body = client.post(OCR_PATH, headers=TEACHER, json={"image_data_url": PNG}).json()
    assert body["available"] is True and "draft_text" in body
    assert len(calls) == 1  # the valid image went upstream exactly once


def test_ocr_unconfigured_reports_honestly(tmp_path, monkeypatch):
    runtime, client, _, _ = install(tmp_path, monkeypatch, configured=False)
    body = client.post(OCR_PATH, headers=TEACHER, json={"image_data_url": PNG}).json()
    assert body == {"available": False, "reason": "ocr_unconfigured"}


@pytest.mark.parametrize("reason", ["ocr_http_error", "ocr_network_error", "ocr_bad_response"])
def test_ocr_failure_returns_no_draft(tmp_path, monkeypatch, reason):
    runtime, client, _, _ = install(tmp_path, monkeypatch)

    def boom(self, image_data_url):  # noqa: ANN001
        raise OcrError(reason)

    monkeypatch.setattr(OcrClient, "extract_draft", boom)
    body = client.post(OCR_PATH, headers=TEACHER, json={"image_data_url": PNG}).json()
    assert body == {"available": False, "reason": reason}
    assert "draft_text" not in body  # no stale or partial transcription can pass as a fresh one


def test_ocr_client_rejects_unalignable_responses(tmp_path, monkeypatch):
    settings = settings_for(tmp_path, ocr_base_url="http://ocr.test/v1/chat/completions",
                            ocr_api_key="k", ocr_model="xoppaddleocrv16")
    client = OcrClient(settings)

    def with_response(status, payload):
        monkeypatch.setattr(httpx, "post", lambda url, **kw: httpx.Response(status, json=payload))

    with_response(200, {"choices": [{"message": {"content": ""}}]})
    with pytest.raises(OcrError) as exc:
        client.extract_draft(PNG)
    assert exc.value.reason == "ocr_bad_response"

    with_response(200, {"unexpected": True})
    with pytest.raises(OcrError) as exc:
        client.extract_draft(PNG)
    assert exc.value.reason == "ocr_bad_response"

    with_response(503, {"detail": "down"})
    with pytest.raises(OcrError) as exc:
        client.extract_draft(PNG)
    assert exc.value.reason == "ocr_http_error"
