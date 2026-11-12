from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from collections.abc import Callable
from typing import Any

import httpx

from .config import IntegrationSettings

logger = logging.getLogger(__name__)

class NexentError(RuntimeError):
    """A model-safe failure; never includes credentials, request bodies or model output."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


@dataclass
class NexentRun:
    conversation_id: int | None
    final_text: str
    event_types: dict[str, int] = field(default_factory=dict)
    first_event_ms: int | None = None
    first_final_event_ms: int | None = None
    total_ms: int = 0
    stopped: bool = False
    trusted_completion: bool = False


class NexentClient:
    """Northbound SSE client. One run per call; tools make automatic retries unsafe.

    The synchronous entry point is used by worker-thread API handlers. Its private event loop
    permits cancelling network reads without leaving a background reader running.
    """

    def __init__(self, settings: IntegrationSettings, transport: httpx.AsyncBaseTransport | None = None):
        if not settings.nexent_base_url or not settings.nexent_api_key:
            raise NexentError("unavailable", "Nexent base URL or API key is not configured")
        self.settings = settings
        self._transport = transport

    def _client(self, read_timeout: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.settings.nexent_base_url,
            transport=self._transport,
            timeout=httpx.Timeout(connect=self.settings.connect_timeout_s, read=read_timeout, write=10.0, pool=5.0),
            headers={"Authorization": f"Bearer {self.settings.nexent_api_key}"},
            follow_redirects=False,
        )

    def run(
        self, *, agent_name: str, query: str, metadata: dict[str, Any] | None = None,
        audit_metadata: dict[str, Any] | None = None, idempotency_key: str | None = None,
        conversation_id: int | None = None,
        trusted_completion: Callable[[], bool] | None = None,
    ) -> NexentRun:
        return asyncio.run(self._run(
            agent_name=agent_name, query=query, metadata=metadata, audit_metadata=audit_metadata,
            idempotency_key=idempotency_key, conversation_id=conversation_id, started=time.monotonic(),
            trusted_completion=trusted_completion,
        ))

    async def _run(
        self, *, agent_name: str, query: str, metadata: dict[str, Any] | None,
        audit_metadata: dict[str, Any] | None, idempotency_key: str | None,
        conversation_id: int | None, started: float,
        trusted_completion: Callable[[], bool] | None,
    ) -> NexentRun:
        if not agent_name:
            raise NexentError("unavailable", "Nexent agent name is not configured")
        body: dict[str, Any] = {"agent_name": agent_name, "query": query, "final_answer_mode": "complete"}
        if metadata:
            body["metadata"] = metadata
        if audit_metadata:
            body["meta_data"] = audit_metadata
        if conversation_id is not None:
            body["conversation_id"] = conversation_id
        headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key

        deadline = started + self.settings.total_timeout_s
        # Reserve a small part of the same budget for advisory stop, never an extra five seconds.
        work_deadline = deadline - min(0.25, self.settings.total_timeout_s * 0.25)
        types: dict[str, int] = {}
        final_parts: list[str] = []
        final_text: str | None = None
        first_event_ms: int | None = None
        first_final_event_ms: int | None = None
        conv_id: int | None = None
        response_status: int | None = None
        failure_event: str | None = None
        completed_by_tool = False
        async with self._client(self.settings.first_event_timeout_s) as client:
            client_ready_ms = int((time.monotonic() - started) * 1000)
            try:
                if time.monotonic() >= work_deadline:
                    raise TimeoutError
                async with asyncio.timeout_at(work_deadline):
                    async with asyncio.timeout_at(min(work_deadline, started + self.settings.first_event_timeout_s)) as first_timer:
                        async with client.stream("POST", "/nb/v1/chat/run", json=body, headers=headers) as response:
                            response_status = response.status_code
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
                            async for line in response.aiter_lines():
                                if time.monotonic() >= work_deadline:
                                    raise TimeoutError
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
                                    first_timer.reschedule(None)
                                if event_type in {"error", "run_error", "failed"}:
                                    failure_event = event_type
                                    raise NexentError("rejected", "Nexent reported a run error")
                                # An SSE event is only a wakeup; completion comes from the trusted local store.
                                if event_type in {"execution_logs", "execution_result"} and trusted_completion and trusted_completion():
                                    completed_by_tool = True
                                    final_text = ""
                                    break
                                if event_type == "final_answer":
                                    content = event.get("content")
                                    text = content if isinstance(content, str) else ""
                                    if text and first_final_event_ms is None:
                                        first_final_event_ms = int((time.monotonic() - started) * 1000)
                                    phase = event.get("answer_phase")
                                    if phase == "delta":
                                        final_parts.append(text)
                                    elif phase == "complete":
                                        final_text = text if text else "".join(final_parts)
                                    else:
                                        final_text = text
                if completed_by_tool:
                    stop_outcome = await self._best_effort_stop(client, conv_id, min(deadline, time.monotonic() + 0.25))
                    logger.info("nexent_trusted_submission_received stop_outcome=%s", stop_outcome)
            except NexentError as exc:
                logger.warning(
                    "nexent_run_rejected kind=%s http_status=%s error_event=%s elapsed_ms=%d "
                    "first_event_ms=%s first_final_event_ms=%s event_count=%d",
                    exc.kind, response_status, failure_event, int((time.monotonic() - started) * 1000),
                    first_event_ms, first_final_event_ms, sum(types.values()),
                )
                raise
            except (TimeoutError, httpx.TimeoutException) as exc:
                stop_outcome = await self._best_effort_stop(client, conv_id, deadline)
                logger.warning("nexent_run_failed kind=timeout elapsed_ms=%d client_ready_ms=%d first_event_ms=%s first_final_event_ms=%s event_count=%d stop_outcome=%s",
                               int((time.monotonic() - started) * 1000), client_ready_ms, first_event_ms,
                               first_final_event_ms, sum(types.values()), stop_outcome)
                raise NexentError("timeout", "Nexent did not complete within the time limit") from exc
            except httpx.TransportError as exc:
                stop_outcome = await self._best_effort_stop(client, conv_id, deadline)
                logger.warning("nexent_run_failed kind=unavailable elapsed_ms=%d client_ready_ms=%d first_event_ms=%s first_final_event_ms=%s event_count=%d stop_outcome=%s",
                               int((time.monotonic() - started) * 1000), client_ready_ms, first_event_ms,
                               first_final_event_ms, sum(types.values()), stop_outcome)
                raise NexentError("unavailable", "Connection to Nexent was lost") from exc
        if time.monotonic() >= work_deadline:
            raise NexentError("timeout", "Nexent did not complete within the time limit")
        if final_text is None and final_parts:
            final_text = "".join(final_parts)
        if final_text is None:
            raise NexentError("protocol", "Nexent stream ended without a final answer")
        return NexentRun(
            conversation_id=conv_id, final_text=final_text, event_types=types,
            first_event_ms=first_event_ms, first_final_event_ms=first_final_event_ms,
            total_ms=int((time.monotonic() - started) * 1000),
            trusted_completion=completed_by_tool,
        )

    async def _best_effort_stop(self, client: httpx.AsyncClient, conversation_id: int | None, deadline: float) -> str:
        remaining = deadline - time.monotonic()
        if conversation_id is None:
            return "no_conversation"
        if remaining <= 0:
            return "budget_exhausted"
        try:
            async with asyncio.timeout_at(deadline):
                response = await client.get(f"/nb/v1/chat/stop/{conversation_id}")
                return "acknowledged" if 200 <= response.status_code < 300 else "rejected"
        except (TimeoutError, httpx.TimeoutException):
            return "timeout"
        except Exception as exc:  # Stop is advisory; the caller still cancels the trusted turn token.
            logger.debug("nexent_stop_failed kind=%s", type(exc).__name__)
            return "failed"


def _conversation_id(headers: httpx.Headers) -> int | None:
    for name in ("conversation_id", "x-conversation-id"):
        raw = headers.get(name)
        if raw and raw.strip().isdigit():
            return int(raw.strip())
    return None
