from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .adapters import NexentPatientAdapter, SimulatorPatientAdapter
from .case_store import CaseStore
from .disclosure import classify_and_authorize
from .models import (
    ActionKind,
    ActionRequest,
    AskRequest,
    CaseDefinition,
    DifferentialRequest,
    DiagnosisRequest,
    SealRequest,
    SessionState,
    TreatmentRequest,
)
from .scoring import score_session
from .state_machine import StateMachine
from .storage import ConcurrentUpdateError, SessionRepository


ROOT = Path(__file__).resolve().parents[2]
CASE_STORE = CaseStore(ROOT / "cases")
REPOSITORY = SessionRepository(ROOT / "backend" / "data" / "app.db")
MACHINE = StateMachine(CASE_STORE, REPOSITORY)
app = FastAPI(title="AI数字病人临床思维训练系统", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8501", "http://127.0.0.1:8501"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def current_owner(x_owner_id: str | None = Header(default=None)) -> str:
    if not x_owner_id:
        raise HTTPException(status_code=401, detail="需要业务身份")
    if os.getenv("APP_ENV", "production").lower() == "development" and (
        x_owner_id.startswith("demo-") or x_owner_id.startswith("student-")
    ):
        return x_owner_id
    raise HTTPException(status_code=401, detail="需要经验证的业务身份；本地自填身份仅在 APP_ENV=development 时可用")


def get_state(session_id: str, owner_id: str) -> SessionState:
    try:
        return REPOSITORY.get(session_id, owner_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="会话不存在或无权访问") from exc


def safe_case(case: CaseDefinition) -> dict[str, Any]:
    return {
        "public_id": case.public_id,
        "version": case.version,
        "title": case.title,
        "specialty": case.specialty,
        "chief_complaint": case.chief_complaint,
        "review_status": case.review_status.value,
    }


def safe_event(event, case: CaseDefinition | None = None) -> dict[str, Any]:
    result = event.result if isinstance(event.result, dict) else {}
    public_result = {
        key: value
        for key, value in result.items()
        if key in {
            "answer", "submitted", "planned", "executed", "state",
            "clinical_state", "advanced_minutes", "clinical_updates",
        }
    }
    if event.action == ActionKind.SUBMIT_DIAGNOSIS:
        public_result["diagnosis"] = str(event.payload.get("diagnosis", ""))
    elif event.action == ActionKind.SUBMIT_DIFFERENTIAL:
        public_result["candidates"] = _safe_differential(event.payload.get("candidates", []))
    elif event.action in {ActionKind.PLAN_TREATMENT, ActionKind.EXECUTE_TREATMENT}:
        public_result["treatment"] = _safe_treatment(event.payload)
    return {
        "event_id": event.event_id,
        "request_id": event.request_id,
        "seq": event.seq,
        "action": event.action.value,
        "intent": event.intent,
        "result": public_result,
        "sim_time": event.sim_time,
        "state_after": {
            key: value
            for key, value in event.state_after.items()
            if key in {"workflow", "clinical_state", "sim_time_minutes"}
        },
    }


def _safe_differential(candidates: Any) -> list[dict[str, Any]]:
    if not isinstance(candidates, list):
        return []
    safe = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        safe.append(
            {
                "candidate_id": str(candidate.get("candidate_id", "")),
                "supporting_evidence": [
                    str(value) for value in candidate.get("supporting_evidence", [])
                    if isinstance(value, str)
                ],
                "opposing_evidence": [
                    str(value) for value in candidate.get("opposing_evidence", [])
                    if isinstance(value, str)
                ],
                "evidence_event_ids": [
                    str(value) for value in candidate.get("evidence_event_ids", [])
                    if isinstance(value, str)
                ],
            }
        )
    return safe


def _safe_treatment(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    parameters = payload.get("parameters", {})
    return {
        "treatment_id": str(payload.get("treatment_id", "")),
        "parameters": parameters if isinstance(parameters, dict) else {},
        "confirm_execution": bool(payload.get("confirm_execution", False)),
    }


def safe_submissions(state: SessionState, case: CaseDefinition) -> dict[str, Any]:
    treatment = _safe_treatment(state.treatment_submission)
    if treatment.get("treatment_id"):
        treatment["treatment_name"] = {
            "aspirin": "阿司匹林",
            "reperfusion_pathway": "再灌注路径",
        }.get(treatment["treatment_id"], treatment["treatment_id"])
    return {
        "diagnosis": (state.diagnosis_submission or {}).get("diagnosis"),
        "differential": _safe_differential((state.differential_submission or {}).get("candidates", [])),
        "treatment": treatment if treatment.get("treatment_id") else None,
    }


def safe_action_response(response: dict[str, Any], case: CaseDefinition) -> dict[str, Any]:
    result = {
        key: value
        for key, value in response.items()
        if key in {
            "submitted", "planned", "executed", "state", "clinical_state",
            "advanced_minutes", "clinical_updates", "sealed", "seal_seq",
        }
    }
    if "ready_exam_ids" in response:
        result["ready_exam_names"] = [
            next((exam.standard_name for exam in case.exams if exam.exam_id == exam_id), "未命名检查")
            for exam_id in response["ready_exam_ids"]
        ]
    return result


def safe_state(state: SessionState) -> dict[str, Any]:
    case = CASE_STORE.get(state.case_id, state.case_version)
    exams = {exam.exam_id: exam.standard_name for exam in case.exams}
    return {
        "session_id": state.session_id,
        "workflow": state.workflow.value,
        "clinical_state": state.clinical_state.value,
        "sim_time_minutes": state.sim_time_minutes,
        "case_notes": state.case_notes,
        "submissions": safe_submissions(state, case),
        "disclosed_fact_count": len(state.disclosed_fact_ids),
        "clinical_updates": list(state.clinical_updates),
        "orders": [
            {
                "state": order.state.value,
                "exam_name": exams.get(order.exam_id, "未命名检查"),
                "ordered_sim_time": order.ordered_sim_time,
                "sample_sim_time": order.sample_sim_time,
                "return_sim_time": order.return_sim_time,
                "result": order.result,
                "viewed": order.viewed,
            }
            for order in state.orders
        ],
        "events": [safe_event(event, case) for event in state.events],
        "sealed": state.sealed,
        "seal_seq": state.seal_seq,
        "seal_sim_time": state.seal_sim_time,
        "state_version": state.state_version,
    }


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "patient_adapter": os.getenv("NEXENT_MODE", "simulator")}


@app.get("/api/cases")
def list_cases(_: str = Depends(current_owner)) -> list[dict]:
    cases = CASE_STORE.list_public()
    if os.getenv("APP_ENV", "production").lower() == "development":
        return cases
    return [case for case in cases if case["review_status"] == "published"]


@app.post("/api/sessions")
def create_session(case_id: str, owner_id: str = Depends(current_owner)) -> dict:
    try:
        case = CASE_STORE.get(case_id)
    except KeyError:
        try:
            case = CASE_STORE.get_by_public_id(case_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="病例不存在") from exc
    if os.getenv("APP_ENV", "production").lower() != "development" and case.review_status.value != "published":
        raise HTTPException(status_code=404, detail="病例不存在")
    state = SessionState(owner_id=owner_id, case_id=case.case_id, case_version=case.version)
    REPOSITORY.create(state)
    return {"session_id": state.session_id, "case": safe_case(case), "state": safe_state(state)}


@app.get("/api/sessions")
def list_sessions(owner_id: str = Depends(current_owner)) -> list[dict]:
    result = []
    for state in REPOSITORY.list_for_owner(owner_id):
        case = CASE_STORE.get(state.case_id, state.case_version)
        result.append(
            {
                "session_id": state.session_id,
                "case": safe_case(case),
                "sealed": state.sealed,
                "sim_time_minutes": state.sim_time_minutes,
                "event_count": len(state.events),
                "summary_id": state.session_id[:8],
                "score_status": state.score_snapshot.status if state.score_snapshot else None,
            }
        )
    return result


@app.get("/api/sessions/{session_id}")
def session_detail(session_id: str, owner_id: str = Depends(current_owner)) -> dict:
    state = get_state(session_id, owner_id)
    return {"state": safe_state(state), "case": safe_case(CASE_STORE.get(state.case_id, state.case_version))}


@app.post("/api/sessions/{session_id}/ask")
def ask(session_id: str, request: AskRequest, owner_id: str = Depends(current_owner)) -> dict:
    state = get_state(session_id, owner_id)
    case = CASE_STORE.get(state.case_id, state.case_version)
    visible = classify_and_authorize(state, request.question, case.facts)
    fact_ids = [fact.fact_id for fact in visible]
    content = [fact.content for fact in visible]
    before = state.model_copy(deep=True)
    try:
        result = MACHINE._check_request(state, request.request_id, {"action": "ask", "question": request.question})
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if result is not None:
        event = next(event for event in state.events if event.request_id == request.request_id)
        return {"answer": event.result.get("answer", ""), "event": safe_event(event, case)}
    if state.sealed:
        raise HTTPException(status_code=409, detail="session is sealed")
    adapter = SimulatorPatientAdapter()
    if os.getenv("NEXENT_MODE", "simulator") == "nexent":
        adapter = NexentPatientAdapter(
            os.getenv("NEXENT_BASE_URL", ""),
            os.getenv("NEXENT_API_KEY", ""),
            os.getenv("NEXENT_AGENT_NAME", ""),
        )
    try:
        raw_answer = adapter.answer(request.question, content, {"clinical_state": state.clinical_state.value})
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail="患者适配器暂不可用") from exc
    from .adapters import authorized_answer_or_fallback

    answer, final_fact_ids, was_fallback = authorized_answer_or_fallback(raw_answer, visible, case)
    state.disclosed_fact_ids.extend(
        fact_id for fact_id in final_fact_ids if fact_id not in state.disclosed_fact_ids
    )
    event = MACHINE._append(
        state,
        request.request_id,
        ActionKind.ASK,
        {"question": request.question, "matched_fact_ids": final_fact_ids, "fallback": was_fallback},
        {"answer": answer, "fact_ids": final_fact_ids, "fallback": was_fallback},
        intent=request.question,
        fact_ids=final_fact_ids,
        before={
            "workflow": before.workflow.value,
            "clinical_state": before.clinical_state.value,
            "sim_time_minutes": before.sim_time_minutes,
            "state_version": before.state_version,
        },
    )
    try:
        REPOSITORY.save(state, expected_version=before.state_version)
    except ConcurrentUpdateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "answer": answer,
        "event": safe_event(event, case),
        "disclosed_fact_count": len(final_fact_ids),
    }


@app.post("/api/sessions/{session_id}/action")
def action(session_id: str, request: ActionRequest, owner_id: str = Depends(current_owner)) -> dict:
    state = get_state(session_id, owner_id)
    case = CASE_STORE.get(state.case_id, state.case_version)
    try:
        result = MACHINE.apply(state, request.request_id, request.action, request.payload)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ConcurrentUpdateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "response": safe_action_response(result.response, case),
        "event": safe_event(result.event, case),
        "state": safe_state(state),
    }


@app.post("/api/sessions/{session_id}/differential")
def differential(session_id: str, request: DifferentialRequest, owner_id: str = Depends(current_owner)) -> dict:
    state = get_state(session_id, owner_id)
    return action(
        session_id,
        ActionRequest(
            request_id=request.request_id,
            action=ActionKind.SUBMIT_DIFFERENTIAL,
            payload={"candidates": [candidate.model_dump() for candidate in request.candidates]},
        ),
        owner_id,
    )


@app.post("/api/sessions/{session_id}/diagnosis")
def diagnosis(session_id: str, request: DiagnosisRequest, owner_id: str = Depends(current_owner)) -> dict:
    state = get_state(session_id, owner_id)
    return action(
        session_id,
        ActionRequest(
            request_id=request.request_id,
            action=ActionKind.SUBMIT_DIAGNOSIS,
            payload={"diagnosis": request.diagnosis},
        ),
        owner_id,
    )


@app.post("/api/sessions/{session_id}/treatment")
def treatment(session_id: str, request: TreatmentRequest, owner_id: str = Depends(current_owner)) -> dict:
    state = get_state(session_id, owner_id)
    return action(
        session_id,
        ActionRequest(
            request_id=request.request_id,
            action=ActionKind.EXECUTE_TREATMENT if request.confirm_execution else ActionKind.PLAN_TREATMENT,
            payload={
                "treatment_id": request.treatment_id,
                "parameters": request.parameters,
                "confirm_execution": request.confirm_execution,
            },
        ),
        owner_id,
    )


@app.post("/api/sessions/{session_id}/seal")
def seal(session_id: str, request: SealRequest, owner_id: str = Depends(current_owner)) -> dict:
    state = get_state(session_id, owner_id)
    try:
        result = MACHINE.apply(state, request.request_id, ActionKind.SEAL, {})
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ConcurrentUpdateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    case = CASE_STORE.get(state.case_id, state.case_version)
    return {
        "state": safe_state(state),
        "event": safe_event(result.event, case),
        "request_id": request.request_id,
    }


@app.get("/api/sessions/{session_id}/score")
def score(session_id: str, owner_id: str = Depends(current_owner)) -> dict:
    state = get_state(session_id, owner_id)
    if not state.sealed:
        raise HTTPException(status_code=409, detail="提交训练后才能评分")
    snapshot = state.score_snapshot or score_session(state, CASE_STORE.get(state.case_id, state.case_version))
    return snapshot.model_dump(mode="json")
