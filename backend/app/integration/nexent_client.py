from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from .config import IntegrationSettings


class NexentError(RuntimeError):
    """Failure talking to the Nexent northbound API.

    kind: unavailable | timeout | auth | rejected | conflict | protocol
    The message never contains credentials, request bodies or model output.
    """

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


@dataclass
class NexentRun:
    conversation_id: int | None
    final_text: str
    event_types: dict[str, int] = field(default_factory=dict)
    first_event_ms: int | None = None
    total_ms: int = 0
    stopped: bool = False


class NexentClient:
    """Minimal client for POST /nb/v1/chat/run (SSE) and GET /nb/v1/chat/stop/{id}.

    Protocol facts are taken from the published Nexent northbound API documentation and must be
    re-verified against the concrete instance (see docs/integration-environment.md). The client
    never retries a run on its own: a run may already have executed tools.
    """

    def __init__(self, settings: IntegrationSettings, transport: httpx.BaseTransport | None = None):
        if not settings.nexent_base_url or not settings.nexent_api_key:
            raise NexentError("unavailable", "Nexent base URL or API key is not configured")
        self.settings = settings
        self._transport = transport

    def _client(self, read_timeout: float) -> httpx.Client:
        return httpx.Client(
            base_url=self.settings.nexent_base_url,
            transport=self._transport,
            timeout=httpx.Timeout(
                connect=self.settings.connect_timeout_s, read=read_timeout, write=10.0, pool=5.0
            ),
            headers={"Authorization": f"Bearer {self.settings.nexent_api_key}"},
            follow_redirects=False,
        )

    def run(
        self,
        *,
        agent_name: str,
        query: str,
        metadata: dict[str, Any] | None = None,
        audit_metadata: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        conversation_id: int | None = None,
    ) -> NexentRun:
        if not agent_name:
            raise NexentError("unavailable", "Nexent agent name is not configured")
        body: dict[str, Any] = {
            "agent_name": agent_name,
            "query": query,
            "final_answer_mode": "complete",
        }
        if metadata:
            body["metadata"] = metadata
        if audit_metadata:
            body["meta_data"] = audit_metadata
        if conversation_id is not None:
            body["conversation_id"] = conversation_id
        headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key

        started = time.monotonic()
        deadline = started + self.settings.total_timeout_s
        types: dict[str, int] = {}
        final_parts: list[str] = []
        final_text: str | None = None
        first_event_ms: int | None = None
        conv_id: int | None = None
        try:
            with self._client(self.settings.first_event_timeout_s) as client:
                with client.stream("POST", "/nb/v1/chat/run", json=body, headers=headers) as response:
                    conv_id = _conversation_id(response.headers)
                    if response.status_code in (401, 403):
                        raise NexentError("auth", "Nexent rejected the credentials")
                    if response.headers.get("x-stream-status", "").lower() == "conflict":
                        raise NexentError("conflict", "Nexent conversation is still busy")
                    if response.status_code == 429:
                        raise NexentError("conflict", "Nexent rate limit reached")
                    if response.status_code >= 500:
                        raise NexentError("unavailable", f"Nexent server error {response.status_code}")
                    if response.status_code >= 400:
                        raise NexentError("rejected", f"Nexent rejected the request ({response.status_code})")
                    for line in response.iter_lines():
                        if time.monotonic() > deadline:
                            self._best_effort_stop(conv_id)
                            raise NexentError("timeout", "Nexent run exceeded the total time limit")
                        line = line.strip()
                        if not line.startswith("data:"):
                            continue
                        payload = line[5:].strip()
                        if not payload or payload == "[DONE]":
                            continue
                        try:
                            event = json.loads(payload)
                        except json.JSONDecodeError:
                            types["malformed"] = types.get("malformed", 0) + 1
                            continue
                        if not isinstance(event, dict):
                            types["malformed"] = types.get("malformed", 0) + 1
                            continue
                        event_type = str(event.get("type", "unknown"))
                        types[event_type] = types.get(event_type, 0) + 1
                        if first_event_ms is None:
                            first_event_ms = int((time.monotonic() - started) * 1000)
                        if event_type in {"error", "run_error", "failed"}:
                            raise NexentError("rejected", "Nexent reported a run error")
                        if event_type == "final_answer":
                            content = event.get("content")
                            text = content if isinstance(content, str) else ""
                            phase = event.get("answer_phase")
                            if phase == "delta":
                                final_parts.append(text)
                            elif phase == "complete":
                                final_text = text if text else "".join(final_parts)
                            else:
                                final_text = text
        except NexentError:
            raise
        except httpx.TimeoutException as exc:
            self._best_effort_stop(conv_id)
            raise NexentError("timeout", "Nexent did not respond in time") from exc
        except httpx.TransportError as exc:
            self._best_effort_stop(conv_id)
            raise NexentError("unavailable", "Connection to Nexent was lost") from exc
        if final_text is None and final_parts:
            final_text = "".join(final_parts)
        if final_text is None:
            raise NexentError("protocol", "Nexent stream ended without a final answer")
        return NexentRun(
            conversation_id=conv_id,
            final_text=final_text,
            event_types=types,
            first_event_ms=first_event_ms,
            total_ms=int((time.monotonic() - started) * 1000),
        )

    def _best_effort_stop(self, conversation_id: int | None) -> None:
        if conversation_id is None:
            return
        try:
            with self._client(5.0) as client:
                client.get(f"/nb/v1/chat/stop/{conversation_id}")
        except Exception:  # noqa: BLE001 - stop is advisory; the turn token is cancelled regardless
            pass


def _conversation_id(headers: httpx.Headers) -> int | None:
    for name in ("conversation_id", "x-conversation-id"):
        raw = headers.get(name)
        if raw and raw.strip().isdigit():
            return int(raw.strip())
    return None
