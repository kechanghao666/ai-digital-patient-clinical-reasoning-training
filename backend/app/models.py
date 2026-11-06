from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


class ReviewStatus(str, Enum):
    DRAFT = "draft"
    VALIDATED = "validated"
    UNDER_REVIEW = "under_review"
    PUBLISHED = "published"


class ExamState(str, Enum):
    NOT_CONFIGURED = "not_configured"
    NOT_ORDERED = "not_ordered"
    PENDING = "pending"
    READY = "ready"
    FAILED = "failed"


class Workflow(str, Enum):
    HISTORY = "history_taking"
    EXAMS = "exam_ordering"
    DIFFERENTIAL = "differential"
    DIAGNOSIS = "diagnosis"
    TREATMENT = "treatment"
    SUBMITTED = "submitted"


class ClinicalState(str, Enum):
    INITIAL = "initial"
    DETERIORATING = "deteriorating"
    DETERIORATED = "deteriorated"
    STABILIZED = "stabilized"


class ActionKind(str, Enum):
    ASK = "ask"
    ORDER_EXAM = "order_exam"
    WAIT = "wait"
    SUBMIT_DIFFERENTIAL = "submit_differential"
    SUBMIT_DIAGNOSIS = "submit_diagnosis"
    PLAN_TREATMENT = "plan_treatment"
    EXECUTE_TREATMENT = "execute_treatment"
    UPDATE_NOTES = "update_notes"
    SEAL = "seal"


class Fact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fact_id: str
    content: str
    field: str
    patient_known: bool = True
    disclosure_triggers: list[str] = Field(default_factory=list)
    disclosure_level: Literal["voluntary", "on_inquiry", "deep_inquiry"] = "on_inquiry"
    auto_volunteer: bool = False
    status_valid_from: int = 0
    status_valid_to: int | None = None
    polarity: Literal["positive", "negative", "unknown"] = "positive"
    current: bool = True


class ExamDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    exam_id: str
    standard_name: str
    order_required: bool = True
    precondition: str | None = None
    return_after_minutes: int = Field(ge=0)
    allowed_values: list[str] = Field(min_length=1)
    configured: bool = True
    result: str | None = None
    unit: str | None = None
    negative_explicit: str | None = None


class RuleEffect(BaseModel):
    to_state: ClinicalState
    new_fact_ids: list[str] = Field(default_factory=list)
    severity: Literal["low", "medium", "high"] = "medium"
    message: str


class EvolutionRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rule_id: str
    from_state: ClinicalState
    anchor_event: str
    delay_minutes: int = Field(ge=0)
    exclusions: list[str] = Field(default_factory=list)
    effect: RuleEffect
    repeatable: bool = False
    medically_reviewed: bool = False


class RubricItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item_id: str
    stage: Literal["病史采集", "检查选择", "鉴别诊断", "诊断", "治疗"]
    description: str
    evidence_type: str
    weight: float = Field(gt=0)
    critical: bool = False
    latest_workflow: Workflow | None = None
    ordering_constraint: Literal["any", "first_exam_before_wait"] = "any"


class CaseDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str
    version: int = Field(ge=1)
    public_id: str
    specialty: str
    title: str
    learning_goals: list[str] = Field(min_length=1)
    target_population: str
    review_status: ReviewStatus
    reviewer: str | None = None
    review_date: str | None = None
    sources: list[str] = Field(default_factory=list)
    chief_complaint: str
    facts: list[Fact] = Field(min_length=1)
    exams: list[ExamDefinition] = Field(default_factory=list)
    rules: list[EvolutionRule] = Field(default_factory=list)
    rubric: list[RubricItem] = Field(min_length=1)
    hidden_diagnosis: str | None = None

    @model_validator(mode="after")
    def validate_references(self) -> "CaseDefinition":
        ids = [f.fact_id for f in self.facts]
        if len(ids) != len(set(ids)):
            raise ValueError("fact_id must be unique")
        exam_ids = [e.exam_id for e in self.exams]
        if len(exam_ids) != len(set(exam_ids)):
            raise ValueError("exam_id must be unique")
        rule_ids = [r.rule_id for r in self.rules]
        if len(rule_ids) != len(set(rule_ids)):
            raise ValueError("rule_id must be unique")
        fact_ids = set(ids)
        for rule in self.rules:
            missing = set(rule.effect.new_fact_ids) - fact_ids
            if missing:
                raise ValueError(f"rule {rule.rule_id} references missing facts: {sorted(missing)}")
        for fact in self.facts:
            if fact.status_valid_to is not None and fact.status_valid_to < fact.status_valid_from:
                raise ValueError(f"fact {fact.fact_id} has an invalid validity range")
        for exam in self.exams:
            if exam.result is not None and exam.result not in exam.allowed_values:
                raise ValueError(f"exam {exam.exam_id} result is not an allowed value")
            if exam.configured and exam.result is None:
                raise ValueError(f"configured exam {exam.exam_id} must have a result")
        exam_ids_set = set(exam_ids)
        for rule in self.rules:
            if not rule.anchor_event.strip():
                raise ValueError(f"rule {rule.rule_id} must have an anchor event")
            if rule.delay_minutes < 0:
                raise ValueError(f"rule {rule.rule_id} has an invalid delay")
        rubric_ids = [r.item_id for r in self.rubric]
        if len(rubric_ids) != len(set(rubric_ids)):
            raise ValueError("item_id must be unique")
        for rubric in self.rubric:
            if rubric.evidence_type == "ask_event" and rubric.item_id not in fact_ids:
                raise ValueError(f"rubric references unknown fact: {rubric.item_id}")
            if rubric.evidence_type == "exam_order" and rubric.item_id not in exam_ids_set:
                raise ValueError(f"rubric references unknown exam: {rubric.item_id}")
            if rubric.evidence_type != "exam_order" and rubric.ordering_constraint != "any":
                raise ValueError("ordering_constraint is only valid for exam_order rubrics")
        if self.review_status == ReviewStatus.PUBLISHED and (not self.reviewer or not self.review_date):
            raise ValueError("published case requires reviewer and review_date")
        return self


class OrderInstance(BaseModel):
    order_id: str = Field(default_factory=lambda: str(uuid4()))
    exam_id: str
    state: ExamState = ExamState.PENDING
    ordered_sim_time: int
    sample_sim_time: int
    return_sim_time: int
    result: str | None = None
    viewed: bool = False


class Event(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    request_id: str
    seq: int
    actor: str
    action: ActionKind
    intent: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    executed: bool = True
    result: dict[str, Any] = Field(default_factory=dict)
    sim_time: int
    state_before: dict[str, Any]
    state_after: dict[str, Any]
    rule_id: str | None = None
    fact_ids_revealed: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=now_utc)


class SessionState(BaseModel):
    session_id: str = Field(default_factory=lambda: str(uuid4()))
    owner_id: str
    case_id: str
    case_version: int
    workflow: Workflow = Workflow.HISTORY
    clinical_state: ClinicalState = ClinicalState.INITIAL
    sim_time_minutes: int = 0
    disclosed_fact_ids: list[str] = Field(default_factory=list)
    clinical_updates: list[str] = Field(default_factory=list)
    orders: list[OrderInstance] = Field(default_factory=list)
    events: list[Event] = Field(default_factory=list)
    pending_request_hashes: dict[str, str] = Field(default_factory=dict)
    sealed: bool = False
    seal_seq: int | None = None
    seal_sim_time: int | None = None
    state_version: int = 0
    diagnosis_submission: dict[str, Any] | None = None
    differential_submission: dict[str, Any] | None = None
    treatment_submission: dict[str, Any] | None = None
    case_notes: str = ""
    score_snapshot: "ScoreSnapshot | None" = None


class ActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1)
    action: ActionKind
    payload: dict[str, Any] = Field(default_factory=dict)


class OrderExamRequest(BaseModel):
    request_id: str = Field(min_length=1)
    exam_id: str = Field(min_length=1)


class WaitRequest(BaseModel):
    request_id: str = Field(min_length=1)
    minutes: int = Field(ge=0, le=240)


class DiagnosisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1)
    diagnosis: str


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1)
    question: str = Field(min_length=1)


class TreatmentPlanRequest(BaseModel):
    request_id: str = Field(min_length=1)
    treatment_id: str = Field(min_length=1)
    parameters: dict[str, Any] = Field(default_factory=dict)
    confirm_execution: bool = False


class SealRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1)


class DifferentialCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_id: str
    supporting_evidence: list[str] = Field(default_factory=list)
    opposing_evidence: list[str] = Field(default_factory=list)
    evidence_event_ids: list[str] = Field(default_factory=list)


class DifferentialRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str
    candidates: list[DifferentialCandidate]


class TreatmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str
    treatment_id: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    confirm_execution: bool = False


class ScoreItem(BaseModel):
    item_id: str
    stage: str
    hit: bool
    score: float
    max_score: float
    evidence_event_ids: list[str] = Field(default_factory=list)
    reason: str


class ScoreSnapshot(BaseModel):
    session_id: str
    seal_seq: int
    total_score: float
    max_score: float
    status: Literal["complete", "failed", "needs_review"] = "complete"
    items: list[ScoreItem]
    generated_at: datetime = Field(default_factory=now_utc)
