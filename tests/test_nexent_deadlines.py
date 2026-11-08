"""Real localhost stalls and slow streams; these are not real-platform performance results."""
import asyncio
from contextlib import contextmanager
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time

import httpx
import pytest

from app import main
from app.integration.config import IntegrationSettings
from app.integration.nexent_client import NexentClient, NexentError
from app.integration.runtime import IntegrationRuntime
from test_integration import Env, _play_and_seal, feedback_agent, select, settings_for, token_of


@contextmanager
def platform(mode="stall", drive=None, slow_stop=False, stall_s=0.9):
    closed = threading.Event()
    records = []
    stop_called = threading.Event()
    script = {"mode": mode}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            records.append(body)
            if drive:
                drive(body)
            if script["mode"] == "headers":
                closed.wait(0.9)
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("conversation_id", "73")
                self.end_headers()
                if script["mode"] == "trickle":
                    for _ in range(30):
                        self.wfile.write(b':partial-comment')
                        self.wfile.flush()
                        if closed.wait(0.03):
                            return
                    self.wfile.write(b'\n\n')
                else:
                    self.wfile.write(b'data: {"type":"model_output_thinking","content":"x"}\n\n')
                    self.wfile.flush()
                    if script["mode"] == "stall":
                        closed.wait(stall_s)
                    elif script["mode"] == "timing":
                        closed.wait(0.1)
                self.wfile.write(b'data: {"type":"final_answer","answer_phase":"complete","content":"ok"}\n\n')
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

        def do_GET(self):
            stop_called.set()
            if slow_stop:
                closed.wait(0.9)
            try:
                self.send_response(503 if slow_stop else 200)
                self.end_headers()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", records, stop_called, script
    finally:
        closed.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        assert not thread.is_alive()


@pytest.mark.parametrize("mode", ["headers", "stall", "trickle"])
def test_total_deadline_interrupts_real_socket_waits(tmp_path, mode, caplog, monkeypatch):
    with platform(mode, slow_stop=True) as (url, records, stop_called, _):
        client = NexentClient(settings_for(tmp_path, nexent_base_url=url, total_timeout_s=0.3, first_event_timeout_s=2))
        # Isolate socket stalls from synchronous HTTPX/TLS initialization, checked separately.
        prepared = client._client(2)
        monkeypatch.setattr(client, "_client", lambda _: prepared)
        started = time.monotonic()
        with pytest.raises(NexentError) as raised:
            client.run(agent_name="a", query="q")
        elapsed = time.monotonic() - started
        assert raised.value.kind == "timeout"
        # At most 150ms scheduling/closing tolerance, far less than the scripted 900ms stall.
        assert 0.2 <= elapsed < 0.45, elapsed
        assert len(records) == 1
        # A tiny budget cannot guarantee peer receipt after response cleanup.
        if mode == "headers":
            assert "stop_outcome=no_conversation" in caplog.text
        else:
            assert any(f"stop_outcome={outcome}" in caplog.text
                       for outcome in ("timeout", "budget_exhausted", "failed"))


def test_peer_receives_stop_and_ack_is_distinct_from_worker_exit(tmp_path, caplog):
    with platform("stall", stall_s=3) as (url, _, stop_called, _):
        client = NexentClient(settings_for(tmp_path, nexent_base_url=url, total_timeout_s=1.5, first_event_timeout_s=2))
        started = time.monotonic()
        with pytest.raises(NexentError) as raised:
            client.run(agent_name="a", query="q")
        assert raised.value.kind == "timeout"
        assert time.monotonic() - started < 1.65
        assert stop_called.is_set()
        assert "stop_outcome=acknowledged" in caplog.text


def test_client_setup_exhaustion_does_not_start_remote_run(tmp_path, caplog, monkeypatch):
    requests = []
    def handler(request):
        requests.append(request.method)
        return httpx.Response(200)
    client = NexentClient(settings_for(tmp_path, total_timeout_s=0.05), httpx.MockTransport(handler))
    prepared = client._client(1)
    def slow_setup(_):
        time.sleep(0.08)
        return prepared
    monkeypatch.setattr(client, "_client", slow_setup)
    with pytest.raises(NexentError) as raised:
        client.run(agent_name="a", query="q")
    assert raised.value.kind == "timeout" and not requests
    assert "stop_outcome=no_conversation" in caplog.text
    assert "client_ready_ms=" in caplog.text


def test_cleanup_can_exhaust_stop_budget_and_reports_it(tmp_path, caplog):
    requests = []
    class SlowClosingStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"type":"model_output_thinking","content":"x"}\n\n'
            await asyncio.sleep(10)
        async def aclose(self):
            await asyncio.sleep(0.1)
    def handler(request):
        requests.append(request.method)
        return httpx.Response(200, headers={"conversation_id": "73"}, stream=SlowClosingStream())
    client = NexentClient(settings_for(tmp_path, total_timeout_s=0.12), httpx.MockTransport(handler))
    with pytest.raises(NexentError) as raised:
        client.run(agent_name="a", query="q")
    assert raised.value.kind == "timeout"
    assert requests == ["POST"] and "stop_outcome=budget_exhausted" in caplog.text


@pytest.mark.parametrize("status,outcome", [(200, "acknowledged"), (503, "rejected")])
def test_stop_http_status_is_not_assumed_success(tmp_path, status, outcome):
    paths = []
    def handler(request):
        paths.append(request.url.path)
        return httpx.Response(status)
    client = NexentClient(settings_for(tmp_path), httpx.MockTransport(handler))
    async def run():
        async with client._client(1) as http:
            assert await client._best_effort_stop(http, 73, time.monotonic() + 1) == outcome
            assert await client._best_effort_stop(http, 73, time.monotonic() - 1) == "budget_exhausted"
            assert await client._best_effort_stop(http, None, time.monotonic() + 1) == "no_conversation"
    asyncio.run(run())
    assert paths == ["/nb/v1/chat/stop/73"]


def test_first_event_deadline_rejects_comment_trickle(tmp_path):
    with platform("trickle") as (url, _, _, _):
        client = NexentClient(settings_for(tmp_path, nexent_base_url=url, total_timeout_s=2, first_event_timeout_s=0.2))
        started = time.monotonic()
        with pytest.raises(NexentError) as raised:
            client.run(agent_name="a", query="q")
        assert raised.value.kind == "timeout"
        assert time.monotonic() - started < 0.4


def test_platform_thinking_and_final_timestamps_are_separate(tmp_path):
    with platform("timing") as (url, _, _, _):
        result = NexentClient(settings_for(tmp_path, nexent_base_url=url)).run(agent_name="a", query="q")
        assert result.first_event_ms is not None
        assert result.first_final_event_ms >= result.first_event_ms + 60
        assert result.total_ms >= result.first_final_event_ms


@pytest.mark.parametrize("role", ["patient", "feedback"])
def test_deadline_cancels_submitted_turn_and_same_request_retries_once(tmp_path, monkeypatch, role):
    env = Env(tmp_path, monkeypatch, mode="simulator")
    sid = env.new_session() if role == "patient" else _play_and_seal(env)
    original = env.repository.get(sid, "demo-integration").model_dump_json()

    def drive(body):
        agent = select("f_hx_htn") if role == "patient" else feedback_agent()
        agent(httpx.Request("POST", "http://local.test"), body, env.service)

    with platform(drive=drive, slow_stop=True) as (url, records, _, script):
        settings = replace(env.settings, mode="nexent", nexent_base_url=url, total_timeout_s=0.3, first_event_timeout_s=2)
        runtime = IntegrationRuntime(settings)
        monkeypatch.setattr(main, "RUNTIME", runtime)
        path = f"/api/sessions/{sid}/{'ask' if role == 'patient' else 'feedback'}"
        payload = {"request_id": "retry"}
        if role == "patient":
            payload["question"] = "有高血压吗"
        failed = env.client.post(path, headers=env.headers, json=payload)
        assert failed.status_code == 504, failed.text
        token = token_of(records[0])
        late = (env.service.submit_fact_selection(token, ["f_hx_htn"]) if role == "patient"
                else env.service.get_sealed_review(token))
        assert not late["ok"] and late["error"] == "turn_cancelled"
        assert env.repository.get(sid, "demo-integration").model_dump_json() == original
        assert runtime.feedback_store.get(sid, "demo-integration") is None
        script["mode"] = "success"
        for _ in range(2):
            result = env.client.post(path, headers=env.headers, json=payload)
            assert result.status_code == 200, result.text
        assert len(records) == 2
        if role == "patient":
            state = env.repository.get(sid, "demo-integration")
            assert len(state.events) == 1
            trace = state.events[0].result["adapter"]["trace"]
            assert trace["platform_first_final_event_ms"] >= trace["platform_first_event_ms"]


@pytest.mark.parametrize("name", ["NEXENT_CONNECT_TIMEOUT_S", "NEXENT_FIRST_EVENT_TIMEOUT_S", "NEXENT_TOTAL_TIMEOUT_S", "TURN_TTL_S"])
@pytest.mark.parametrize("raw", ["nan", "inf", "-inf"])
def test_nonfinite_environment_times_fail_before_runtime(name, raw, monkeypatch):
    monkeypatch.setenv(name, raw)
    with pytest.raises(ValueError, match="finite positive"):
        IntegrationSettings.from_env()


@pytest.mark.parametrize("field", ["connect_timeout_s", "first_event_timeout_s", "total_timeout_s", "turn_ttl_s"])
def test_programmatic_settings_enforce_same_time_boundary(tmp_path, field):
    with pytest.raises(ValueError, match="finite positive"):
        replace(settings_for(tmp_path), **{field: float("nan")})


def test_cancels_waiting_transport_before_headers_without_reader_task(tmp_path):
    cancelled = []
    async def handler(request):
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.append(True)
    client = NexentClient(settings_for(tmp_path, total_timeout_s=0.15), httpx.MockTransport(handler))
    with pytest.raises(NexentError) as raised:
        client.run(agent_name="a", query="q")
    assert raised.value.kind == "timeout" and cancelled == [True]
