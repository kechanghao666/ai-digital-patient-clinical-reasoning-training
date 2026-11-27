from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .adapters import (
    PatientSelection,
    PatientTurnRequest,
    authorized_answer_or_fallback,
    diagnosis_leak_terms,
    render_selected_answer,
)
from . import exam_catalog
from .case_store import CaseStore
from .department_catalog import map_legacy_specialty
from .department_config import (
    DepartmentConfigError,
    config_path_for_case_store,
    load_department_config,
)
from .disclosure import classify_turn, non_fact_reply
from .history_coverage import history_coverage
from .patient_answers import FactFragment, fact_fragments
from .integration.feedback import FeedbackUnavailable
from .integration.config import data_directory
from .integration.nexent_client import NexentError
from .integration.runtime import IntegrationRuntime
from .integration.tools import validate_feedback_record
from .integration.turns import read_stats
from .models import (
    ActionKind,
    ActionRequest,
    AskRequest,
    CaseDefinition,
    DifferentialRequest,
    DiagnosisRequest,
    SealRequest,
    ScoreSnapshot,
    SessionState,
    TreatmentRequest,
)
from .scoring import score_session, student_score_snapshot
from .teacher_api import build_router as build_teacher_router
from .teacher_feedback_api import build_router as build_feedback_review_router
from .teacher_score_api import build_router as build_score_review_router
from .teacher_dashboard_api import build_router as build_dashboard_router
from .teacher_export_api import build_router as build_export_router
from .export import ExportAudit
from .roster import build_router as build_roster_router, roster_for
from .state_machine import StateMachine
from .storage import ConcurrentUpdateError, SessionRepository
from .accounts import AccountStore, auth_mode, build_router as build_auth_router


ROOT = Path(__file__).resolve().parents[2]
CASE_STORE = CaseStore(ROOT / "cases", data_directory() / "teacher_cases")
REPOSITORY = SessionRepository(data_directory() / "app.db")
ACCOUNTS = AccountStore(data_directory() / "accounts.db")
EXPORT_AUDIT = ExportAudit(data_directory() / "accounts.db")
MACHINE = StateMachine(CASE_STORE, REPOSITORY)
RUNTIME = IntegrationRuntime()
app = FastAPI(title="AI数字病人临床思维训练", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8501", "http://127.0.0.1:8501"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _account(authorization: str | None) -> dict:
    user = ACCOUNTS.authenticate(authorization)
    if user.get("must_change_password"):
        raise HTTPException(status_code=403, detail="请先修改初始密码后再继续使用")
    return user


def current_owner(x_owner_id: str | None = Header(default=None), authorization: str | None = Header(default=None)) -> str:
    if authorization is not None or auth_mode() == "local":
        return _account(authorization)["id"]
    if not x_owner_id:
        raise HTTPException(status_code=401, detail="需要业务身份")
    if os.getenv("APP_ENV", "production").lower() == "development" and (
        x_owner_id.startswith("demo-") or x_owner_id.startswith("student-")
    ):
        return x_owner_id
    raise HTTPException(status_code=401, detail="需要经验证的业务身份；本地自填身份仅在 APP_ENV=development 时可用")


def _development() -> bool:
    return os.getenv("APP_ENV", "production").lower() == "development"


def _department_config():
    try:
        return load_department_config(config_path_for_case_store(CASE_STORE))
    except DepartmentConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _department_enabled(case: CaseDefinition, config) -> bool:
    department_code = case.department_code
    subspecialty_code = case.subspecialty_code
    if not department_code and not subspecialty_code:
        department_code, subspecialty_code = map_legacy_specialty(case.specialty)
    if not department_code or department_code not in config.enabled_departments:
        return False
    return not subspecialty_code or subspecialty_code in config.enabled_subspecialties


def current_teacher(x_owner_id: str | None = Header(default=None), authorization: str | None = Header(default=None)) -> str:
    if authorization is not None or auth_mode() == "local":
        user = _account(authorization)
        if user["role"] != "teacher":
            raise HTTPException(status_code=403, detail="需要教师角色")
        return user["id"]
    if not x_owner_id:
        raise HTTPException(status_code=401, detail="需要业务身份")
    if _development() and x_owner_id.startswith("teacher-"):
        return x_owner_id
    raise HTTPException(status_code=403, detail="需要教师角色；自填教师身份仅在本地演示模式可用")


app.include_router(build_auth_router(lambda: ACCOUNTS))
app.include_router(build_teacher_router(lambda: CASE_STORE, current_teacher, lambda: RUNTIME.knowledge,
                                        lambda: RUNTIME.case_training))
app.include_router(build_feedback_review_router(lambda: REPOSITORY, lambda: CASE_STORE, lambda: RUNTIME, current_teacher,
                                               lambda: roster_for(ACCOUNTS)))
app.include_router(build_score_review_router(lambda: REPOSITORY, lambda: CASE_STORE, lambda: RUNTIME, current_teacher,
                                            lambda: roster_for(ACCOUNTS)))
app.include_router(build_dashboard_router(lambda: REPOSITORY, lambda: CASE_STORE, lambda: ACCOUNTS, current_teacher,
                                          lambda: roster_for(ACCOUNTS),
                                          lambda owner_ids: read_stats(RUNTIME.settings.integration_db_path, owner_ids)))
app.include_router(build_export_router(lambda: REPOSITORY, lambda: CASE_STORE, lambda: roster_for(ACCOUNTS),
                                       current_teacher, lambda: EXPORT_AUDIT))
app.include_router(build_roster_router(lambda: roster_for(ACCOUNTS), lambda: ACCOUNTS, lambda: REPOSITORY, lambda: CASE_STORE,
                                       current_teacher, current_owner, _development,
                                       lambda case_id, version: RUNTIME.case_training.is_stopped(case_id, version),
                                       lambda case: _department_enabled(case, _department_config())))


def get_state(session_id: str, owner_id: str) -> SessionState:
    try:
        return REPOSITORY.get(session_id, owner_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="会话不存在或无权访问") from exc


def _patient_profile(case: CaseDefinition) -> dict[str, Any]:
    """Age and sex already stated in the public target population; nothing hidden is derived."""
    match = re.search(r"(\d{1,3})岁(男|女)", case.target_population or "")
    return {"age": int(match.group(1)) if match else None, "sex": match.group(2) if match else None}


def safe_case(case: CaseDefinition) -> dict[str, Any]:
    return {
        "public_id": case.public_id,
        "version": case.version,
        "title": case.title,
        "specialty": case.specialty,
        "chief_complaint": case.chief_complaint,
        "review_status": case.review_status.value,
        "patient": _patient_profile(case),
    }


def safe_options(case: CaseDefinition) -> dict[str, Any]:
    """Fixed, case-configured choices for the student UI; no hidden or scoring data."""
    # The same hospital-wide catalogue for every case: the menu itself never hints at the answer.
    return {
        "exams": [
            {"exam_id": item["catalog_id"], "name": item["name"], "category": item["category"],
             "cost": item["cost"], "minutes": item["minutes"], "baseline": item["baseline"]}
            for item in exam_catalog.public_catalog(case)
        ],
        "treatments": [{"treatment_id": t.treatment_id, "name": t.name} for t in case.treatments],
        "differentials": [
            {"candidate_id": o.candidate_id, "label": o.label} for o in case.differential_options
        ],
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
    if "ready_exam_ids" in result:
        public_result["exam_results"] = _ready_exam_results(result, case) if case else []
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
        names = {t.treatment_id: t.name for t in case.treatments}
        treatment["treatment_name"] = names.get(treatment["treatment_id"], treatment["treatment_id"])
    return {
        "diagnosis": (state.diagnosis_submission or {}).get("diagnosis"),
        "differential": _safe_differential((state.differential_submission or {}).get("candidates", [])),
        "treatment": treatment if treatment.get("treatment_id") else None,
    }


def _exam_name(case: CaseDefinition, exam_id: str) -> str:
    exam = next((e for e in case.exams if e.exam_id == exam_id), None)
    if exam is not None:
        return exam.standard_name
    entry = exam_catalog.find(exam_id)
    return entry.name if entry else "未命名检查"


def _exam_meta(case: CaseDefinition, exam_id: str) -> tuple[str, int, str | None]:
    """Category, cost and catalogue id for an order, matching the student catalogue by name or alias."""
    name = _exam_name(case, exam_id)
    entry = exam_catalog.find(exam_id) or exam_catalog.entry_for_name(name)
    return (entry.category, entry.cost, entry.catalog_id) if entry else ("其他", 100, None)


def _ready_exam_results(response: dict[str, Any], case: CaseDefinition) -> list[dict[str, Any]]:
    results = []
    for exam_id in response.get("ready_exam_ids", []):
        exam = next((e for e in case.exams if e.exam_id == exam_id), None)
        results.append({"name": _exam_name(case, exam_id),
                        "result": exam.result if exam is not None else exam_catalog.GENERIC_RESULT})
    return results


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
        result["exam_results"] = _ready_exam_results(response, case)
        result["ready_exam_names"] = [_exam_name(case, exam_id) for exam_id in response["ready_exam_ids"]]
    return result


def safe_state(state: SessionState) -> dict[str, Any]:
    case = CASE_STORE.get(state.case_id, state.case_version)
    metas = [_exam_meta(case, order.exam_id) for order in state.orders]
    return {
        "exam_cost_total": sum(meta[1] for meta in metas),
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
                "exam_name": _exam_name(case, order.exam_id),
                "category": meta[0],
                "cost": meta[1],
                "catalog_id": meta[2],
                "ordered_sim_time": order.ordered_sim_time,
                "sample_sim_time": order.sample_sim_time,
                "return_sim_time": order.return_sim_time,
                "result": order.result,
                "viewed": order.viewed,
            }
            for order, meta in zip(state.orders, metas)
        ],
        "events": [safe_event(event, case) for event in state.events],
        "sealed": state.sealed,
        "seal_seq": state.seal_seq,
        "seal_sim_time": state.seal_sim_time,
        "state_version": state.state_version,
    }


def _nexent_http_error(exc: NexentError) -> HTTPException:
    if exc.kind == "state_changed":
        return HTTPException(status_code=409, detail="训练状态已更新，请刷新后重试")
    if exc.kind == "timeout":
        return HTTPException(status_code=504, detail="患者智能体响应超时，本次提问未记录，可用原请求重试")
    if exc.kind == "conflict":
        return HTTPException(status_code=429, detail="患者智能体繁忙，本次提问未记录，请稍后重试")
    return HTTPException(status_code=502, detail="患者智能体暂不可用，本次提问未记录")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "patient_adapter": RUNTIME.settings.mode}


@app.get("/api/cases")
def list_cases(_: str = Depends(current_owner)) -> list[dict]:
    config = _department_config()
    listed = CASE_STORE.list_public(
        include_unpublished=_development(),
        available=lambda case: (
            not RUNTIME.case_training.is_stopped(case.case_id, case.version)
            and _department_enabled(case, config)
        ),
    )
    for item in listed:
        try:
            item["patient"] = _patient_profile(CASE_STORE.resolve_for_student(item["public_id"], _development()))
        except KeyError:
            item["patient"] = {"age": None, "sex": None}
    return listed


@app.get("/api/department-catalog")
def student_department_catalog(_: str = Depends(current_owner)) -> dict:
    """仅返回已启用且有当前可训练病例的科室/亚专科。"""
    from .department_catalog import catalog_for_student

    config = _department_config()
    listed = CASE_STORE.list_public(
        include_unpublished=_development(),
        available=lambda case: (
            not RUNTIME.case_training.is_stopped(case.case_id, case.version)
            and _department_enabled(case, config)
        ),
    )

    has_cases: set[tuple[str | None, str | None]] = set()
    for item in listed:
        dept = item.get("department_code")
        subsp = item.get("subspecialty_code")
        if dept or subsp:
            has_cases.add((dept, subsp))

    catalog = catalog_for_student(has_cases)
    return {**catalog, "configured": config.configured}


@app.post("/api/sessions")
def create_session(case_id: str, owner_id: str = Depends(current_owner)) -> dict:
    try:
        case = CASE_STORE.resolve_for_student(case_id, include_unpublished=_development())
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="病例不存在") from exc
    if RUNTIME.case_training.is_stopped(case.case_id, case.version):
        # Resolve the newest published version first, then refuse. Do not fall back to an older
        # published version of the same public id.
        raise HTTPException(status_code=409, detail="该病例版本已停止接受新训练，请联系教师重新选版")
    if not _department_enabled(case, _department_config()):
        raise HTTPException(status_code=409, detail="该病例所属科室或亚专科未启用，暂不可开始新训练")
    state = SessionState(owner_id=owner_id, case_id=case.case_id, case_version=case.version)
    REPOSITORY.create(state)
    return {
        "session_id": state.session_id,
        "case": safe_case(case),
        "options": safe_options(case),
        "state": safe_state(state),
    }


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
    case = CASE_STORE.get(state.case_id, state.case_version)
    return {"state": safe_state(state), "case": safe_case(case), "options": safe_options(case),
            "history_coverage": history_coverage(state.events)}


@app.post("/api/sessions/{session_id}/ask")
def ask(session_id: str, request: AskRequest, owner_id: str = Depends(current_owner)) -> dict:
    started = time.monotonic()
    # Monotonic stage stamps for this request, all in the API process on one clock. These mark when
    # this handler reached a point, not when an upstream event happened: "adapter_returned" is when
    # the patient adapter returned (after the model run and turn finalize), NOT when the model's
    # submit_fact_selection landed in the turn store — that landing is wall-clock `turn_calls.at` in
    # the (separate) MCP process and cannot be subtracted from these values. Never a credential,
    # token or model text.
    stage_ms: dict[str, int] = {"request_entry": 0}

    def mark(stage: str) -> None:
        stage_ms[stage] = int((time.monotonic() - started) * 1000)

    state = get_state(session_id, owner_id)
    case = CASE_STORE.get(state.case_id, state.case_version)
    visible, turn_kind = classify_turn(state, request.question, case.facts)
    mark("classified")
    before = state.model_copy(deep=True)
    try:
        result = MACHINE._check_request(state, request.request_id, {"action": "ask", "question": request.question})
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if result is not None:
        event = next(event for event in state.events if event.request_id == request.request_id)
        return {"answer": event.result.get("answer", ""), "event": safe_event(event, case),
                "timing": {"replayed": True, "answer_validated_ms": None,
                           "api_ready_ms": int((time.monotonic() - started) * 1000)}}
    if state.sealed:
        raise HTTPException(status_code=409, detail="session is sealed")
    if visible:
        turn_request = PatientTurnRequest(
            owner_id=state.owner_id,
            session_id=state.session_id,
            case_id=state.case_id,
            case_version=state.case_version,
            request_id=request.request_id,
            state_version=state.state_version,
            question=request.question,
            candidates=tuple(visible),
        )
        try:
            selection = RUNTIME.patient_adapter().select_facts(turn_request)
        except NexentError as exc:
            raise _nexent_http_error(exc) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail="患者适配器暂不可用") from exc
    else:
        selection = PatientSelection(fact_ids=[], source="local_no_candidates")
    mark("adapter_returned")
    prior_answer_atoms = {
        atom_id
        for previous_event in state.events
        if previous_event.action == ActionKind.ASK
        for atom_id in previous_event.result.get("answer_atom_ids", [])
    }
    selected, rendered = render_selected_answer(
        visible, selection.fact_ids, request.question, prior_answer_atoms
    )
    if not visible:
        # Fixed, fact-free reply (greeting, diagnosis request, or an unrecorded detail).
        rendered = non_fact_reply(request.question, turn_kind) or rendered
    answer, selected_fact_ids, was_fallback = authorized_answer_or_fallback(rendered, selected, case)
    answer_validated_ms = int((time.monotonic() - started) * 1000)
    mark("validated")
    selection.trace["answer_validated_ms"] = answer_validated_ms
    was_fallback = was_fallback or (bool(visible) and not selected)
    answer_atom_ids = [
        fragment.atom_id for fragment in selected if isinstance(fragment, FactFragment)
    ] if not was_fallback else []
    learned_atoms = prior_answer_atoms | set(answer_atom_ids)
    atoms_by_fact = {
        fact.fact_id: {fragment.atom_id for fragment in fact_fragments(fact)}
        for fact in case.facts
    }
    final_fact_ids = [
        fact_id for fact_id in selected_fact_ids
        if atoms_by_fact.get(fact_id) and atoms_by_fact[fact_id].issubset(learned_atoms)
    ]
    state.disclosed_fact_ids.extend(
        fact_id for fact_id in final_fact_ids if fact_id not in state.disclosed_fact_ids
    )
    event = MACHINE._append(
        state,
        request.request_id,
        ActionKind.ASK,
        {"question": request.question, "matched_fact_ids": final_fact_ids, "fallback": was_fallback},
        {
            "answer": answer,
            "fact_ids": final_fact_ids,
            "answer_atom_ids": answer_atom_ids,
            "fallback": was_fallback,
            "turn_kind": turn_kind if answer_atom_ids or not visible else "fallback",
            "adapter": {"source": selection.source, "trace": selection.trace},
            "server_stage_ms": stage_ms,
        },
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
    mark("saved")
    return {
        "answer": answer,
        "event": safe_event(event, case),
        "disclosed_fact_count": len(final_fact_ids),
        "timing": {"replayed": False, "answer_validated_ms": answer_validated_ms,
                   "api_ready_ms": int((time.monotonic() - started) * 1000),
                   "stages": stage_ms},
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


@app.post("/api/sessions/{session_id}/feedback")
def feedback(session_id: str, request: SealRequest, owner_id: str = Depends(current_owner)) -> dict:
    state = get_state(session_id, owner_id)
    if not state.sealed:
        raise HTTPException(status_code=409, detail="提交训练后才能生成反馈")
    case = CASE_STORE.get(state.case_id, state.case_version)
    try:
        return _public_feedback(RUNTIME.feedback_service.get_or_generate(state, case, request.request_id), state, case)
    except FeedbackUnavailable as exc:
        status = 504 if exc.kind == "timeout" else 502
        raise HTTPException(status_code=status, detail=str(exc)) from exc


@app.get("/api/sessions/{session_id}/feedback")
def get_feedback(session_id: str, owner_id: str = Depends(current_owner)) -> dict:
    state = get_state(session_id, owner_id)
    if not state.sealed:
        raise HTTPException(status_code=409, detail="提交训练后才能查看反馈")
    stored = RUNTIME.feedback_store.effective(session_id, owner_id)
    if stored is None:
        raise HTTPException(status_code=404, detail="反馈尚未生成")
    return _public_feedback(stored, state, CASE_STORE.get(state.case_id, state.case_version))


def _public_feedback(stored: dict[str, Any], state: SessionState, case: CaseDefinition) -> dict[str, Any]:
    checked = validate_feedback_record(stored, state, case, RUNTIME.knowledge)
    if isinstance(checked, str):
        raise HTTPException(status_code=409, detail="已存反馈不符合当前五环节与安全规则，请教师复核；原记录保持不变。")
    items, saved_entries = checked
    if stored["source"] == "nexent":
        return {
            "source": "nexent", "review_status": "pending_teacher_review", "review_revision": 0,
            "items": [], "teaching_basis": [], "basis_status": stored["basis_status"],
            "notice": "模型反馈草稿已生成，待教师复核后展示。原封存评分仍可查看，请将训练编号提供给教师。",
        }
    forbidden = diagnosis_leak_terms(case) | {"".join((case.hidden_diagnosis or "").casefold().split())}
    forbidden.discard("")
    basis = []
    for basis_id in dict.fromkeys(basis_id for item in items for basis_id in item["basis_ids"]):
        if saved_entries is None:
            basis.append({"basis_id": basis_id, "title": "教学依据（历史版本未留存）", "sources": [],
                          "restricted": False, "revision": None, "content_hash": None})
            continue
        entry = saved_entries[basis_id]
        title_hidden = any(term in "".join(entry.title.casefold().split()) for term in forbidden)
        sources = [source for source in entry.sources
                   if not any(term in "".join(source.casefold().split()) for term in forbidden)]
        basis.append({"basis_id": basis_id,
                      "title": "教学依据（题名含参考答案，已隐藏）" if title_hidden else entry.title,
                      "sources": sources, "restricted": title_hidden or len(sources) != len(entry.sources),
                      "revision": entry.revision, "content_hash": entry.content_hash})
    return {
        "source": stored["source"],
        "review_status": "teacher_reviewed" if stored["source"] == "teacher_review" else "local_rules",
        "basis_status": stored["basis_status"],
        "items": items,
        "teaching_basis": basis,
        "basis_provenance": "snapshot" if saved_entries is not None else "legacy_unversioned",
        "review_revision": stored.get("review_revision", 0),
        "basis_coverage": {key: value for key, value in RUNTIME.knowledge.coverage(case.case_id, case.version).items()
                           if key in {"status", "missing_clinical_stages", "notice"}},
        "notice": "反馈基于原封存规则评分，教师评分裁定独立展示；反馈与教学依据均为未经医学审核的教学草稿。",
    }


@app.get("/api/sessions/{session_id}/score")
def score(session_id: str, owner_id: str = Depends(current_owner)) -> dict:
    state = get_state(session_id, owner_id)
    if not state.sealed:
        raise HTTPException(status_code=409, detail="提交训练后才能评分")
    case = CASE_STORE.get(state.case_id, state.case_version)
    snapshot = state.score_snapshot or score_session(state, case)
    return student_score_snapshot(snapshot, case).model_dump(mode="json")


@app.get("/api/sessions/{session_id}/score-review")
def score_review(session_id: str, owner_id: str = Depends(current_owner)) -> dict:
    state = get_state(session_id, owner_id)
    if not state.sealed:
        raise HTTPException(status_code=409, detail="提交训练后才能查看评分裁定")
    reviews = RUNTIME.score_reviews.list(session_id, owner_id)
    if not reviews:
        raise HTTPException(status_code=404, detail="尚无教师评分裁定")
    latest = reviews[-1]
    case = CASE_STORE.get(state.case_id, state.case_version)
    snapshot = ScoreSnapshot.model_validate(latest["snapshot"])
    return {"revision": latest["revision"], "created_at": latest["created_at"],
            "reviewed_score": student_score_snapshot(snapshot, case).model_dump(mode="json"),
            "notice": "教师裁定为独立的教学复核结果；原规则评分与反馈保留，不属于医学审核或正式评测。"}


# The React front end (web/dist) is served from the same origin; registered last so every
# /api and /health route above takes precedence. Absent build output simply disables it.
def _mount_web() -> None:
    from pathlib import Path as _Path

    from fastapi.responses import JSONResponse
    from fastapi.staticfiles import StaticFiles

    class WebFiles(StaticFiles):
        """Static SPA files; unknown API paths and non-GET methods keep plain JSON 404 semantics."""

        async def __call__(self, scope, receive, send):
            path = scope.get("path", "")
            if scope["type"] == "http" and (path.startswith("/api/") or path == "/api"
                                            or scope.get("method") not in {"GET", "HEAD"}):
                await JSONResponse({"detail": "Not Found"}, status_code=404)(scope, receive, send)
                return
            await super().__call__(scope, receive, send)

    configured = os.getenv("WEB_DIST_DIR")
    dist = _Path(configured) if configured else _Path(__file__).resolve().parents[2] / "web" / "dist"
    if (dist / "index.html").is_file():
        app.mount("/", WebFiles(directory=dist, html=True), name="web")


_mount_web()
