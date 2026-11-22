"""OSCE-style history-taking structure progress, derived only from the student's own questions.

The view says which standard history domains the student has *asked about*. It never looks at case
facts, rubric items or answers, so it cannot reveal what a case contains and does not affect scores.
"""
from __future__ import annotations

from .disclosure import detect_intents
from .models import ActionKind

# (domain id, label, intents that count, hint shown while not yet asked)
DOMAINS: tuple[tuple[str, str, frozenset[str], str], ...] = (
    ("chief", "主诉", frozenset({"opening"}), "用开放式问题请患者说出这次来就诊的原因"),
    ("hpi", "现病史", frozenset({"onset", "trigger", "location", "character", "radiation", "modifiers"}),
     "围绕主要症状追问起病、部位、性质、放射和加重缓解"),
    ("associated", "伴随症状与系统回顾", frozenset({
        "accompany", "sweat", "fever", "nausea", "appetite", "bowel", "urine", "breath", "palpitation",
        "head", "consciousness", "chest_pain", "cough", "hemoptysis", "neuro", "thirst", "recent_infection"}),
     "询问伴随症状，并简要回顾其他系统"),
    ("past", "既往史", frozenset({"past_history", "named_disease", "surgery", "prior_episode"}),
     "了解基础疾病、手术外伤史和既往类似发作"),
    ("drugs", "用药与过敏", frozenset({"medication", "allergy"}), "核对长期用药、近期用药和药物过敏"),
    ("social", "个人史与一般情况", frozenset({"age", "name", "occupation", "general", "smoking", "alcohol",
                                       "exposure", "reproductive"}),
     "了解年龄职业、吸烟饮酒、接触史，必要时询问月经婚育"),
    ("family", "家族史", frozenset({"family"}), "询问家族中有无类似或相关疾病"),
    ("course", "就诊经过", frozenset({"prior_care"}), "了解来院前的处理、用药和就诊经过"),
)

# SOCRATES-style elements of the present illness.
HPI_ELEMENTS: tuple[tuple[str, frozenset[str]], ...] = (
    ("起病时间", frozenset({"onset"})),
    ("诱因", frozenset({"trigger", "recent_infection"})),
    ("部位", frozenset({"location"})),
    ("性质与程度", frozenset({"character"})),
    ("放射", frozenset({"radiation"})),
    ("加重与缓解", frozenset({"modifiers"})),
    ("伴随症状", frozenset({"accompany", "sweat", "nausea", "breath", "fever", "head", "consciousness",
                         "cough", "neuro"})),
)


def history_coverage(events) -> dict:
    """Summarise which history domains the student's questions touched, in asking order."""
    counts = {domain: 0 for domain, *_ in DOMAINS}
    elements: set[str] = set()
    questions = 0
    for event in events:
        action = event.action if hasattr(event, "action") else event.get("action")
        question = event.intent if hasattr(event, "intent") else event.get("intent")
        if action not in (ActionKind.ASK, ActionKind.ASK.value) or not question:
            continue
        questions += 1
        opening, intents, _ = detect_intents(question)
        names = {"opening"} if opening else set(intents)
        for domain, _, members, _ in DOMAINS:
            if names & members:
                counts[domain] += 1
        for label, members in HPI_ELEMENTS:
            if names & members:
                elements.add(label)
    domains = [
        {"domain": domain, "label": label, "asked": counts[domain] > 0, "question_count": counts[domain],
         "hint": "" if counts[domain] else hint}
        for domain, label, _, hint in DOMAINS
    ]
    return {
        "question_count": questions,
        "covered": sum(item["asked"] for item in domains),
        "total": len(domains),
        "domains": domains,
        "hpi_elements": [{"label": label, "asked": label in elements} for label, _ in HPI_ELEMENTS],
        "notice": "依据你提出的问题归类，仅提示常规问诊结构，不代表病例要点或评分。",
    }
