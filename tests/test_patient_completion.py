"""Only trusted fact selections can finish a patient turn before model closing text."""
import httpx
import pytest

from app.adapters import FALLBACK_ANSWER
from test_integration import Env, sse, token_of


@pytest.mark.parametrize("state", ["submitted", "empty", "forged", "unauthorized", "expired", "cancelled", "changed"])
def test_patient_execution_event_requires_valid_selection(tmp_path, monkeypatch, state):
    env = Env(tmp_path, monkeypatch)
    sid = env.new_session()
    original = env.repository.get(sid, "demo-integration").model_dump_json()
    expected_state = [original]
    tokens = []

    def agent(_, body, service):
        token = token_of(body)
        tokens.append(token)
        assert service.get_turn_context(token)["ok"]
        if state != "forged":
            ids = [] if state == "empty" else ["f_hx_smoke" if state == "unauthorized" else "f_hx_htn"]
            submitted = service.submit_fact_selection(token, ids)
            assert submitted["ok"] == (state != "unauthorized")
            if state == "expired":
                with env.runtime.turns._connect() as db:
                    db.execute("UPDATE turns SET expires_at=0 WHERE turn_id=?", (token.split(".")[0],))
            elif state == "cancelled":
                env.runtime.turns.cancel(token.split(".")[0], "test_cancel")
            elif state == "changed":
                r = env.client.post(f"/api/sessions/{sid}/action", headers=env.headers,
                                    json={"request_id":"concurrent", "action":"wait", "payload":{"minutes":1}})
                assert r.status_code == 200
                expected_state[0] = env.repository.get(sid, "demo-integration").model_dump_json()

        class Stream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield sse({"type":"execution_result", "content":'{"ok":true,"selected":["f_hx_htn"]}'})
                if state in {"submitted", "empty", "expired", "changed"}:
                    raise AssertionError("Patient must not wait for closing text after trusted submission")
                yield sse({"type":"final_answer", "content":"诊断为STEMI，api_key=forged"})

        return httpx.Response(200, headers={"conversation_id":"73"}, stream=Stream())

    env.agent = agent
    response = env.ask(sid, "有高血压吗", "trusted-patient")
    expected_status = 200 if state in {"submitted", "empty"} else 409 if state == "changed" else 502
    assert response.status_code == expected_status, response.text
    if expected_status == 200:
        assert response.json()["answer"] == (FALLBACK_ANSWER if state == "empty" else "有高血压10年。")
        assert "STEMI" not in response.text and "api_key" not in response.text
        events = env.stored_events(sid)
        assert len(events) == 1
        trace = events[0].result["adapter"]["trace"]
        assert trace["trusted_completion"] is True and trace["platform_first_final_event_ms"] is None
        assert env.runtime.turns.get(tokens[0].split(".")[0]).status == "finalized"
        assert env.stops == ["/nb/v1/chat/stop/73"]
        repeated = env.ask(sid, "有高血压吗", "trusted-patient")
        assert repeated.json()["event"] == response.json()["event"] and len(tokens) == 1
    else:
        assert env.repository.get(sid, "demo-integration").model_dump_json() == expected_state[0]
        assert all(e.action.value != "ask" for e in env.stored_events(sid))
