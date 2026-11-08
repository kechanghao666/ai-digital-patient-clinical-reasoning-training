from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
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
    content: str = Field(min_length=1, pattern=r"\S")
    field: str
    patient_known: bool = True
    disclosure_triggers: list[str] = Field(default_factory=list)
    # Authoring metadata; disclosure always requires an explicit question or evolution event.
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
    order_required: Literal[True] = True
    precondition: None = None
    return_after_minutes: int = Field(ge=0)
    allowed_values: list[str] = Field(min_length=1)
    configured: bool = True
    result: str | None = None
    unit: str | None = None
    negative_explicit: str | None = None


class TreatmentDefinition(BaseModel):
    """A treatment action a student may plan or (after explicit confirmation) execute."""

    model_config = ConfigDict(extra="forbid")
    treatment_id: str
    name: str
    # Anchor event fired when this treatment is actually executed; rules key off it.
    trigger: str = Field(default="treatment_executed", min_length=1, pattern=r"\S")


class DiagnosisSpec(BaseModel):
    """Auditable diagnosis matching: accepted names plus literal whole-statement templates."""

    model_config = ConfigDict(extra="forbid")
    names: list[str] = Field(min_length=1)
    # Whole-statement forms that also earn credit; ``{name}`` is replaced by each accepted name.
    extra_positive: list[str] = Field(default_factory=list)
    # Words that must never appear in patient-visible text (diagnosis leakage guard).
    leak_terms: list[str] = Field(default_factory=list)


class DifferentialRule(BaseModel):
    """Which revealed information supports or opposes a differential candidate.

    Keys are fact ids (disclosed by an ask event) or exam ids (result ready before sealing).
    """

    model_config = ConfigDict(extra="forbid")
    supporting_keys: list[str] = Field(default_factory=list)
    opposing_keys: list[str] = Field(default_factory=list)
    min_supporting: int = Field(default=0, ge=0)
    min_opposing: int = Field(default=0, ge=0)
    medically_reviewed: bool = False


class DifferentialOption(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_id: str
    label: str


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
    # Delay rules: fire once ``delay_minutes`` have passed since this exam's result became ready,
    # unless one of the listed treatments has been executed.
    delay_anchor_exam: str | None = None
    delay_exclusion_treatments: list[str] = Field(default_factory=list)


class RubricItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item_id: str
    stage: Literal["病史采集", "检查选择", "鉴别诊断", "诊断", "治疗"]
    description: str
    evidence_type: Literal["ask_event", "exam_order", "ddx_evidence", "diagnosis", "treatment_execution"]
    weight: float = Field(gt=0, allow_inf_nan=False)
    critical: bool = False
    latest_workflow: None = None
    ordering_constraint: Literal["any", "first_exam_before_wait"] = "any"
    diagnosis: DiagnosisSpec | None = None
    differential: DifferentialRule | None = None
    # Shown to the student when the item is missed; defaults are generated from the item type.
    improvement: str = ""

    @model_validator(mode="after")
    def validate_stage(self) -> "RubricItem":
        stages = {"ask_event": "病史采集", "exam_order": "检查选择", "ddx_evidence": "鉴别诊断",
                  "diagnosis": "诊断", "treatment_execution": "治疗"}
        if self.stage != stages[self.evidence_type]:
            raise ValueError("rubric stage must match its evidence_type")
        return self


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
    treatments: list[TreatmentDefinition] = Field(default_factory=list)
    differential_options: list[DifferentialOption] = Field(default_factory=list)
    rules: list[EvolutionRule] = Field(default_factory=list)
    rubric: list[RubricItem] = Field(min_length=1)
    hidden_diagnosis: str | None = None
    authored_by: str | None = None

    @model_validator(mode="after")
    def validate_references(self) -> "CaseDefinition":
        if not isfinite(sum(item.weight for item in self.rubric)):
            raise ValueError("rubric total weight must be finite")
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
        treatment_ids = [t.treatment_id for t in self.treatments]
        if len(treatment_ids) != len(set(treatment_ids)):
            raise ValueError("treatment_id must be unique")
        treatment_ids_set = set(treatment_ids)
        treatment_triggers = {t.trigger for t in self.treatments}
        action_keys = {action.value for action in ActionKind}
        if action_keys & (treatment_ids_set | treatment_triggers | set(rule_ids)):
            raise ValueError("treatment ids, triggers and rule ids must not shadow action names")
        if set(rule_ids) & (treatment_ids_set | treatment_triggers):
            raise ValueError("rule ids must not shadow treatment ids or triggers")
        if any(t.treatment_id == other.trigger for t in self.treatments for other in self.treatments
               if t.treatment_id != other.treatment_id):
            raise ValueError("a treatment id must not shadow another treatment's trigger")
        exclusion_keys = action_keys | treatment_ids_set | treatment_triggers | set(rule_ids)
        for rule in self.rules:
            if not rule.anchor_event.strip():
                raise ValueError(f"rule {rule.rule_id} must have an anchor event")
            if rule.delay_minutes < 0:
                raise ValueError(f"rule {rule.rule_id} has an invalid delay")
            if rule.delay_anchor_exam is None and rule.delay_minutes:
                raise ValueError(f"rule {rule.rule_id} requires a delay_anchor_exam for delayed execution")
            if rule.delay_anchor_exam is None and rule.anchor_event not in treatment_triggers:
                raise ValueError(f"rule {rule.rule_id} references an unreachable treatment trigger")
            if rule.delay_anchor_exam is None and rule.delay_exclusion_treatments:
                raise ValueError(f"rule {rule.rule_id} uses delayed exclusions without a delay anchor")
            if rule.delay_anchor_exam is not None and rule.delay_anchor_exam not in exam_ids_set:
                raise ValueError(f"rule {rule.rule_id} references unknown exam: {rule.delay_anchor_exam}")
            if rule.delay_anchor_exam is not None and not next(e for e in self.exams if e.exam_id == rule.delay_anchor_exam).configured:
                raise ValueError(f"rule {rule.rule_id} requires a configured anchor exam")
            if set(rule.delay_exclusion_treatments) - treatment_ids_set:
                raise ValueError(f"rule {rule.rule_id} references unknown treatments")
            if set(rule.exclusions) - exclusion_keys:
                raise ValueError(f"rule {rule.rule_id} references unknown exclusions")
        option_ids = [o.candidate_id for o in self.differential_options]
        if len(option_ids) != len(set(option_ids)):
            raise ValueError("differential candidate_id must be unique")
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
            if rubric.evidence_type == "treatment_execution" and rubric.item_id not in treatment_ids_set:
                raise ValueError(f"rubric references unknown treatment: {rubric.item_id}")
            if rubric.evidence_type == "diagnosis" and rubric.diagnosis is None:
                raise ValueError(f"diagnosis rubric {rubric.item_id} requires a diagnosis spec")
            if rubric.evidence_type != "diagnosis" and rubric.diagnosis is not None:
                raise ValueError("diagnosis spec is only valid for diagnosis rubrics")
            if rubric.differential is not None:
                if rubric.evidence_type != "ddx_evidence":
                    raise ValueError("differential rule is only valid for ddx_evidence rubrics")
                keys = set(rubric.differential.supporting_keys) | set(rubric.differential.opposing_keys)
                if set(rubric.differential.supporting_keys) & set(rubric.differential.opposing_keys):
                    raise ValueError(f"differential rule {rubric.item_id} cannot use the same key on both sides")
                if keys - fact_ids - exam_ids_set:
                    raise ValueError(f"differential rule {rubric.item_id} references unknown facts or exams")
                if rubric.differential.min_supporting + rubric.differential.min_opposing < 1:
                    raise ValueError(f"differential rule {rubric.item_id} must require at least one piece of evidence")
                if rubric.differential.min_supporting > len(rubric.differential.supporting_keys):
                    raise ValueError(f"differential rule {rubric.item_id} requires more supporting keys than defined")
                if rubric.differential.min_opposing > len(rubric.differential.opposing_keys):
                    raise ValueError(f"differential rule {rubric.item_id} requires more opposing keys than defined")
            if rubric.evidence_type == "ddx_evidence" and option_ids and rubric.item_id not in option_ids:
                raise ValueError(f"differential rubric {rubric.item_id} is not in differential_options")
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
    # Concrete advice shown when points were lost; empty for full-credit items.
    improvement: str = ""


class ScoreSnapshot(BaseModel):
    session_id: str
    seal_seq: int
    total_score: float
    max_score: float
    status: Literal["complete", "failed", "needs_review"] = "complete"
    items: list[ScoreItem]
    generated_at: datetime = Field(default_factory=now_utc)
