from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from threading import RLock

from .models import ActionRequest, ActionResponse, SessionState


class ConflictError(ValueError):
    pass


class SessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, SessionState] = {}
        self._lock = RLock()

    def create(self, owner: str) -> SessionState:
        from .models import new_session
        state = new_session(owner)
        with self._lock:
            self._sessions[state.session_id] = state
        return deepcopy(state)

    def get(self, session_id: str) -> SessionState | None:
        with self._lock:
            state = self._sessions.get(session_id)
            return deepcopy(state) if state else None

    def apply(self, session_id: str, req: ActionRequest) -> ActionResponse:
        with self._lock:
            state = self._sessions.get(session_id)
            if state is None:
                raise KeyError(session_id)
            digest = hashlib.sha256(req.model_dump_json().encode()).hexdigest()
            prior = state.replay.get(req.request_id)
            if prior:
                if prior["digest"] != digest:
                    raise ConflictError("request_id 已被不同请求占用")
                return ActionResponse.model_validate(deepcopy(prior["response"]))
            if state.status == "sealed":
                raise ConflictError("会话已封存，不能继续操作")
            previous_error = state.last_error
            state.last_error = None
            state.round += 1
            if req.action == "ask":
                message = self._answer(state, req.text)
            elif req.action == "wait":
                message = "患者病情继续观察，当前未触发新的公开变化。"
                state.events.append({"type": "wait", "message": message})
            else:
                message = self._diagnose(state, req.text)
            response = ActionResponse(
                session_id=state.session_id,
                status=state.status,
                round=state.round,
                message=message,
                score_status=state.score_status,
                score=state.score,
                error=None,
                events=list(state.events[-5:]),
            )
            state.replay[req.request_id] = {"digest": digest, "response": response.model_dump(mode="json")}
            state.updated_at = state.updated_at
            return response

    @staticmethod
    def _answer(state: SessionState, text: str) -> str:
        normalized = text.strip().lower()
        if "胸痛" in normalized or "pain" in normalized:
            answer = "患者诉持续胸痛，伴出汗和恶心。"
        elif "过敏" in normalized or "allerg" in normalized:
            answer = "患者否认已知药物过敏。"
        else:
            answer = "患者暂未提供与该问题直接对应的新信息。"
        state.facts.append(answer)
        state.events.append({"type": "answer", "message": answer})
        return answer

    @staticmethod
    def _diagnose(state: SessionState, text: str) -> str:
        value = text.strip().lower()
        positive = "stemi" in value or "急性st段抬高型心肌梗死" in value
        uncertain = any(token in value for token in ("可能", "考虑", "疑似", "合并", "多病灶", "multiple"))
        composite = any(token in value for token in ("和", "并", "+", "/", "伴"))
        if positive and not uncertain and not composite:
            state.score_status = "complete"
            state.score = 100
            state.status = "sealed"
            message = "诊断与当前量表完全匹配，评分完成。"
        elif positive or uncertain or composite:
            state.score_status = "needs_review"
            state.score = None
            message = "诊断包含复合或不确定表述，已转人工复核。"
        else:
            state.score_status = "complete"
            state.score = 0
            state.status = "sealed"
            message = "诊断未匹配量表，评分完成。"
        state.events.append({"type": "diagnosis", "message": message})
        return message
