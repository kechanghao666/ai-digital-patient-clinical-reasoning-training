import re

from .adapters import diagnosis_leak_terms
from .models import ActionKind, DiagnosisSpec, ScoreItem, ScoreSnapshot, SessionState

_PUNCTUATION = str.maketrans({",": "，", ":": "：", ";": "；", "?": "？", "(": "（", ")": "）"})


def _normalize(text: str) -> str:
    return "".join(text.casefold().split())


def student_score_snapshot(snapshot: ScoreSnapshot, case) -> ScoreSnapshot:
    """Filter feedback text without rewriting sealed scores or historical records."""
    forbidden = diagnosis_leak_terms(case) | {_normalize(case.hidden_diagnosis or "")}
    forbidden.discard("")
    items = []
    for item in snapshot.items:
        updates = {}
        for field, fallback in (("reason", "请结合本次训练证据复核评分。"),
                                ("improvement", "请根据病史与检查给出明确结论，并请教师复核依据。")):
            if any(term in _normalize(getattr(item, field)) for term in forbidden):
                updates[field] = fallback
        items.append(item.model_copy(update=updates))
    return snapshot.model_copy(update={"items": items})


def _normalize_statement(text: str) -> str:
    return _normalize(text).translate(_PUNCTUATION)


def _diagnosis_match(spec: DiagnosisSpec, diagnosis: str) -> tuple[bool, bool]:
    """Return (hit, needs_review) for a submitted diagnosis against a case's diagnosis spec."""
    normalized = _normalize_statement(diagnosis)
    if not normalized:
        return False, False
    names = [_normalize_statement(name) for name in spec.names if name.strip()]
    if not names:
        return False, False
    target = "(?:" + "|".join(re.escape(name) for name in names) + ")"
    if not re.search(rf"(?<![a-z]){target}(?![a-z])", normalized):
        return False, False

    # Automatic credit is deliberately limited to auditable, positive forms.
    # Other free-text formulations remain reviewable rather than becoming a
    # hidden keyword classifier.
    positive_patterns = [
        rf"^{target}$",
        rf"^(?:明确诊断|诊断为|确诊|明确为){target}$",
        rf"^{target}已确诊$",
    ]
    placeholder = re.escape("{name}")
    for template in spec.extra_positive:
        escaped = re.escape(_normalize_statement(template))
        positive_patterns.append("^" + escaped.replace(placeholder, target) + "$")
    if any(re.fullmatch(pattern, normalized) for pattern in positive_patterns):
        return True, False

    negative_patterns = (
        rf"^不是{target}$",
        rf"^非{target}$",
        rf"^未见{target}$",
        rf"^{target}(?:不成立|已否定|未被证实|未证实|已排除)$",
        rf"^(?:不考虑|不支持|否认){target}$",
        rf"^(?:排除诊断|排除)[：]{target}$",
    )
    if any(re.fullmatch(pattern, normalized) for pattern in negative_patterns):
        return False, False

    # Any non-whitelisted composite or historical statement is reviewable.
    return False, True


def _event_visible_parts(event, case) -> list[str]:
    parts: list[str] = []
    if event.action == ActionKind.ASK:
        parts.extend(
            fact.content
            for fact in case.facts
            if fact.fact_id in event.payload.get("matched_fact_ids", [])
        )
        parts.append(str(event.result.get("answer", "")))
    elif event.action == ActionKind.WAIT:
        ready_exam_ids = event.result.get("ready_exam_ids", [])
        parts.extend(
            exam.result or ""
            for exam in case.exams
            if exam.exam_id in ready_exam_ids
        )
    return parts


def _event_supports_text(event, text: str, case) -> bool:
    candidate = _normalize(text)
    if not candidate:
        return False
    return any(
        part and (_normalize(part) in candidate or candidate in _normalize(part))
        for part in _event_visible_parts(event, case)
    )


def _event_keys(event) -> set[str]:
    """Fact ids and exam ids that the student actually learned from this event."""
    if event.action == ActionKind.ASK:
        return set(event.payload.get("matched_fact_ids", []))
    if event.action == ActionKind.WAIT:
        return set(event.result.get("ready_exam_ids", []))
    return set()


def _matching_keys(event, text, case) -> set[str]:
    candidate = _normalize(text)
    if not candidate:
        return set()
    available = _event_keys(event)
    parts = [(f.fact_id, f.content) for f in case.facts]
    parts += [(e.exam_id, e.result or "") for e in case.exams]
    return {key for key, content in parts if key in available and content
            and (candidate in _normalize(content) or _normalize(content) in candidate)}


def _score_differential(rubric, candidate, events, event_ids, case):
    """Rule-based differential scoring. Returns (score, hit, review, evidence, reason, improvement)."""
    rule = rubric.differential
    if candidate is None:
        return 0.0, False, False, [], "未提交该鉴别诊断候选", rubric.improvement or f"建议把「{rubric.description}」所指的候选纳入鉴别，并写明支持与反对证据。"
    cited_ids = candidate.get("evidence_event_ids") or []
    cited = [event for event in events if event.event_id in cited_ids]
    invalid = [event_id for event_id in cited_ids if event_id not in event_ids]
    verified: dict[str, set[str]] = {"correct_supporting": set(), "correct_opposing": set()}
    reversed_texts: list[str] = []
    uncertain_texts: list[str] = []
    used_events: set[str] = set()
    support_keys, oppose_keys = set(rule.supporting_keys), set(rule.opposing_keys)
    for role, texts in (
        ("supporting", candidate.get("supporting_evidence", [])),
        ("opposing", candidate.get("opposing_evidence", [])),
    ):
        for text in texts:
            matched = [event for event in cited if _matching_keys(event, text, case)]
            keys = set().union(*(_matching_keys(event, text, case) for event in matched)) if matched else set()
            on_side = keys & (support_keys if role == "supporting" else oppose_keys)
            other_side = keys & (oppose_keys if role == "supporting" else support_keys)
            if on_side:
                verified[f"correct_{role}"].update(on_side)
                used_events.update(event.event_id for event in matched if _matching_keys(event, text, case) & on_side)
            elif other_side:
                reversed_texts.append(text)
            else:
                uncertain_texts.append(text)
    got_support = min(len(verified["correct_supporting"]), rule.min_supporting)
    got_oppose = min(len(verified["correct_opposing"]), rule.min_opposing)
    required = rule.min_supporting + rule.min_opposing
    fraction = (got_support + got_oppose) / required if required else 0.0
    score = rubric.weight * fraction
    full = required > 0 and got_support + got_oppose == required and not reversed_texts
    notes: list[str] = []
    advice: list[str] = []
    if got_support < rule.min_supporting:
        notes.append(f"有效支持证据 {len(verified['correct_supporting'])}/{rule.min_supporting}")
        advice.append("补充一条来自已获得问诊或检查结果、且确实支持该候选的证据")
    if got_oppose < rule.min_opposing:
        notes.append(f"有效反对证据 {len(verified['correct_opposing'])}/{rule.min_opposing}")
        advice.append("补充一条来自已获得问诊或检查结果、且确实反对该候选的证据")
    if reversed_texts:
        notes.append("支持/反对方向与病例规则相反")
        advice.append("核对证据的方向：把反对该候选的结果写进「反对证据」，反之写进「支持证据」")
    if invalid:
        notes.append("部分引用的事件不存在或不在封存范围内")
        advice.append("只引用回放中封存前已发生且已取得结果的事件ID")
    if not cited and not invalid:
        notes.append("未引用任何证据事件")
        advice.append("在证据事件中选择已取得结果的问诊或检查事件")
    review = (not full) and bool(uncertain_texts)
    if review:
        notes.append("部分证据文字无法与病例规则自动对应，需教师复核")
    reason = "已找到符合病例规则的支持/反对证据" if full else "；".join(notes) or "证据不足"
    improvement = "" if full else (rubric.improvement or "；".join(advice) or "补充可核对的证据")
    evidence = sorted(used_events) if used_events else [e for e in cited_ids if e in event_ids]
    return score, full, review, evidence, reason, improvement


def _default_improvement(rubric) -> str:
    if rubric.improvement:
        return rubric.improvement
    return {
        "ask_event": f"建议询问：{rubric.description}",
        "exam_order": f"建议开立：{rubric.description}",
        "diagnosis": "请给出明确、单一的诊断结论，避免使用否定、疑问或不确定的表述。",
        "treatment_execution": f"需在确认后实际执行：{rubric.description}（仅计划不计入执行）",
        "ddx_evidence": f"请补充：{rubric.description}，并引用已取得结果的事件。",
    }.get(rubric.evidence_type, f"建议改进：{rubric.description}")


def score_session(state: SessionState, case) -> ScoreSnapshot:
    seal_seq = state.seal_seq if state.seal_seq is not None else len(state.events)
    events = state.events[:seal_seq]
    event_ids = {event.event_id for event in events}
    items: list[ScoreItem] = []
    needs_review = False

    for rubric in case.rubric:
        evidence: list[str] = []
        hit = False
        score_override: float | None = None
        improvement: str | None = None
        reason = "未找到满足时点和证据类型的事件"
        if rubric.evidence_type == "ask_event":
            evidence = [
                event.event_id
                for event in events
                if event.action == ActionKind.ASK
                and rubric.item_id in event.payload.get("matched_fact_ids", [])
            ]
            hit = bool(evidence)
        elif rubric.evidence_type == "exam_order":
            matching_events = [
                event.event_id
                for event in events
                if event.action == ActionKind.ORDER_EXAM
                and event.payload.get("exam_id") == rubric.item_id
            ]
            if rubric.ordering_constraint == "first_exam_before_wait":
                first_order = next(
                    (event for event in events if event.action == ActionKind.ORDER_EXAM),
                    None,
                )
                prior_wait = any(
                    event.action == ActionKind.WAIT
                    and int(event.payload.get("minutes", 0)) > 0
                    for event in events
                    if first_order is None or event.seq < first_order.seq
                )
                hit = bool(matching_events) and bool(first_order) and (
                    first_order.payload.get("exam_id") == rubric.item_id
                ) and not prior_wait
                evidence = matching_events if hit else []
                if matching_events and not hit:
                    reason = "检查开立顺序不满足病例配置的优先条件"
                    improvement = rubric.improvement or f"该检查应优先于其他检查和等待开立：{rubric.description}"
            else:
                evidence = matching_events
                hit = bool(evidence)
        elif rubric.evidence_type == "ddx_evidence":
            candidate = next(
                (
                    c
                    for c in (state.differential_submission or {}).get("candidates", [])
                    if c.get("candidate_id") == rubric.item_id
                ),
                None,
            )
            if rubric.differential is not None:
                score_override, hit, review, evidence, reason, improvement = _score_differential(
                    rubric, candidate, events, event_ids, case
                )
                needs_review = needs_review or review
            elif candidate is not None:
                evidence = candidate.get("evidence_event_ids") or []
                texts = candidate.get("supporting_evidence", []) + candidate.get("opposing_evidence", [])
                cited_events = [event for event in events if event.event_id in evidence]
                valid_citations = (
                    bool(evidence)
                    and set(evidence).issubset(event_ids)
                    and bool(cited_events)
                    and bool(texts)
                )
                text_matches = valid_citations and all(
                    any(_event_supports_text(event, text, case) for event in cited_events)
                    for text in texts
                )
                if valid_citations:
                    needs_review = True
                    reason = (
                        "证据引用有效但医学支持/反对关系未配置可审计规则，需人工复核"
                        if text_matches
                        else "证据存在但无法可靠确认与该鉴别诊断的关联性，需人工复核"
                    )
        elif rubric.evidence_type == "diagnosis":
            diagnosis = str((state.diagnosis_submission or {}).get("diagnosis", "")).strip()
            hit, uncertain = _diagnosis_match(rubric.diagnosis, diagnosis)
            if uncertain:
                needs_review = True
                reason = "诊断表述不确定，需人工复核"
            elif not hit and diagnosis:
                reason = "提交的诊断与病例的诊断结论不符或表述为否定"
            elif not diagnosis:
                reason = "未提交诊断"
            if hit:
                evidence = [event.event_id for event in events if event.action == ActionKind.SUBMIT_DIAGNOSIS]
        elif rubric.evidence_type == "treatment_execution":
            evidence = [
                event.event_id
                for event in events
                if event.action == ActionKind.EXECUTE_TREATMENT
                and event.payload.get("treatment_id") == rubric.item_id
                and event.executed
            ]
            hit = bool(evidence)
            if not hit and any(
                event.action == ActionKind.PLAN_TREATMENT and event.payload.get("treatment_id") == rubric.item_id
                for event in events
            ):
                reason = "仅提交了计划，未确认执行"

        if hit:
            reason = "已找到符合条件的事件证据" if rubric.evidence_type != "ddx_evidence" or rubric.differential is None else reason
        score = score_override if score_override is not None else (rubric.weight if hit else 0.0)
        if improvement is None:
            improvement = "" if hit else _default_improvement(rubric)
        items.append(
            ScoreItem(
                item_id=rubric.item_id,
                stage=rubric.stage,
                hit=hit,
                score=score,
                max_score=rubric.weight,
                evidence_event_ids=evidence,
                reason=reason,
                improvement=improvement,
            )
        )

    return ScoreSnapshot(
        session_id=state.session_id,
        seal_seq=seal_seq,
        total_score=sum(item.score for item in items),
        max_score=sum(item.max_score for item in items),
        status="needs_review" if needs_review else "complete",
        items=items,
    )
