import re

from .models import ActionKind, ScoreItem, ScoreSnapshot, SessionState


def _normalize(text: str) -> str:
    return "".join(text.casefold().split())


def _diagnosis_match(item_id: str, diagnosis: str) -> tuple[bool, bool]:
    normalized = _normalize(diagnosis)
    if item_id.lower() != "stemi":
        return False, False
    if not normalized:
        return False, False
    target = r"(?:stemi|急性st段抬高型心肌梗死)"
    if not re.search(rf"(?<![a-z]){target}(?![a-z])", normalized):
        return False, False

    # Automatic credit is deliberately limited to auditable, positive forms.
    # Other free-text formulations remain reviewable rather than becoming a
    # hidden keyword classifier.
    positive_patterns = (
        rf"^{target}$",
        rf"^(?:明确诊断|诊断为|确诊|明确为){target}$",
        rf"^{target}已确诊$",
        rf"^{target}已确诊(?:，|,)无药物过敏史$",
        rf"^{target}(?:，|,)已排除主动脉夹层$",
        rf"^{target}(?:，|,)考虑急诊pci$",
        rf"^{target}已确诊(?:并|，|,)(?:已)?排除(?:主动脉夹层|nstemi)$",
        rf"^{target}已确诊(?:，|,)非nstemi$",
    )
    if any(re.fullmatch(pattern, normalized) for pattern in positive_patterns):
        return True, False

    negative_patterns = (
        rf"^不是{target}$",
        rf"^非{target}$",
        rf"^未见{target}$",
        rf"^{target}(?:不成立|已否定|未被证实|未证实|已排除)$",
        rf"^(?:不考虑|不支持|否认){target}$",
        rf"^(?:排除诊断|排除)[:：]{target}$",
    )
    if any(re.fullmatch(pattern, normalized) for pattern in negative_patterns):
        return False, False

    # Any non-whitelisted composite or historical statement is reviewable.
    return False, True


def _event_supports_text(event, text: str, case) -> bool:
    candidate = _normalize(text)
    if not candidate:
        return False
    visible_parts: list[str] = []
    if event.action == ActionKind.ASK:
        visible_parts.extend(
            fact.content
            for fact in case.facts
            if fact.fact_id in event.payload.get("matched_fact_ids", [])
        )
        visible_parts.append(str(event.result.get("answer", "")))
    elif event.action == ActionKind.WAIT:
        ready_exam_ids = event.result.get("ready_exam_ids", [])
        visible_parts.extend(
            exam.result or ""
            for exam in case.exams
            if exam.exam_id in ready_exam_ids
        )
    return any(
        part and (_normalize(part) in candidate or candidate in _normalize(part))
        for part in visible_parts
    )


def score_session(state: SessionState, case) -> ScoreSnapshot:
    seal_seq = state.seal_seq if state.seal_seq is not None else len(state.events)
    events = state.events[:seal_seq]
    event_ids = {event.event_id for event in events}
    items: list[ScoreItem] = []
    needs_review = False

    for rubric in case.rubric:
        evidence: list[str] = []
        hit = False
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
            order_events = [
                event.event_id
                for event in events
                if event.action == ActionKind.ORDER_EXAM
            ]
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
            else:
                evidence = matching_events
                hit = bool(evidence)
        elif rubric.evidence_type == "ddx_evidence":
            for candidate in (state.differential_submission or {}).get("candidates", []):
                if candidate.get("candidate_id") != rubric.item_id:
                    continue
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
                break
        elif rubric.evidence_type == "diagnosis":
            diagnosis = str((state.diagnosis_submission or {}).get("diagnosis", "")).strip()
            hit, uncertain = _diagnosis_match(rubric.item_id, diagnosis)
            if uncertain:
                needs_review = True
                reason = "诊断表述不确定，需人工复核"
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

        if hit:
            reason = "已找到符合条件的事件证据"
        score = rubric.weight if hit else 0.0
        items.append(
            ScoreItem(
                item_id=rubric.item_id,
                stage=rubric.stage,
                hit=hit,
                score=score,
                max_score=rubric.weight,
                evidence_event_ids=evidence,
                reason=reason,
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
