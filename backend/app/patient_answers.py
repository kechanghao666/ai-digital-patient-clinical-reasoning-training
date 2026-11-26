from __future__ import annotations

import re
from dataclasses import dataclass

from .disclosure import DISEASE_ALIASES, detect_intents, normalize
from .models import Fact


@dataclass(frozen=True)
class FactFragment:
    fact_id: str
    atom_id: str
    content: str
    subject_prefix: str | None = None


_BOUNDARY = re.compile(r"[，,。；;]+")
_CONNECTOR = re.compile(r"^(?:也|并且|而且|同时|以及|另外|还|并)\s*")
_ADDITIVE_NEGATION = re.compile(r"^(.{1,8})也(?=(?:没有|没|未|无))")
_ALTERNATIVE = re.compile(r"(或或者|或者|或|和|及|与)")
_DISCOVERY_AFTER = re.compile(r"^(.*?发现)后(?=(?:马上|立即|随即|很快))(.+)$")
_PUNCTUATION = "，,。；;、 "
_OPERATION_QUERY = re.compile(r"手术|开刀|开过刀")
_TRAUMA_QUERY = re.compile(r"外伤|受(?:过)?伤|摔|撞")
_LOCATION_CONTENT = re.compile(
    r"胸口|胸部|左胸|右胸|腹部|肚脐|上腹|下腹|右下腹|左下腹|腰|后背|肩|上肢|手臂|头部|头痛|腿|心前区"
)
_TRIGGER_CONTENT = re.compile(r"休息|活动|运动|走路|进食|受凉|着凉|感冒|发烧|感染|发作|诱因|当时")
_TIME_CONTENT = re.compile(r"\d+(?:\.\d+)?\s*(?:点|分钟|小时|天)|凌晨|清晨|上午|下午|昨晚|昨天|今天|前天|半小时前|一小时前")
_TEMPERATURE_VALUE = re.compile(r"\d+(?:\.\d+)?\s*(?:℃|度)")
_THIRST_CONTENT = re.compile(r"口渴|喝.{0,2}水|多饮|饮水")
_GENERAL_CONDITION_QUERY = re.compile(
    r"(?:以前|之前|过去|既往|平时|从前)(?:都|还)?(?:有过|得过|患过|有)?(?:哪些|什么|啥)(?:疾病|病|毛病)"
    r"|(?:基础病|慢性病|老毛病)(?=$|[吗呢么？?，,。；;和、]|有哪些|是什么)"
)
_FAMILY_SUBJECT = re.compile(r"我(?:父母|父亲|母亲|爸爸|妈妈|爸|妈|哥哥|姐姐|弟弟|妹妹|兄弟|姐妹)|家里人|家人|家族|父母|父亲|母亲|爸爸|妈妈|爸|妈|哥哥|姐姐|弟弟|妹妹|兄弟|姐妹|亲戚")
_FAMILY_DETAIL = re.compile(r"病|身体|健康|患|得过|疾病|冠心病|糖尿病|中风")
_FAMILY_BROAD_QUERY = re.compile(r"家族史|(?:家族|家里人|家人).{0,8}(?:什么|哪些|啥)?(?:病|疾病|身体|健康)|(?:什么|哪些|啥).{0,4}(?:家族|家里人|家人).{0,8}(?:病|疾病)")
_FAMILY_TOPIC_TERMS = ("类似", "相似", "相同", "一样", "高血压", "糖尿病", "冠心病", "心脏病", "中风", "卒中", "结核", "癌")
_SUBJECT_RESET = re.compile(r"^(?:路上|途中|回家后|我自己|患者|本人)")


def _content_triggers(text: str, triggers: list[str]) -> list[tuple[int, str]]:
    normalized = normalize(text)
    matches = []
    for trigger in triggers:
        needle = normalize(trigger)
        if needle:
            start = normalized.find(needle)
            if start >= 0:
                matches.append((start, needle))
    return sorted(matches)


def _split_alternatives(clause: str, triggers: list[str]) -> list[str]:
    for match in _ALTERNATIVE.finditer(clause):
        left = clause[:match.start()].rstrip()
        right = clause[match.end():].lstrip()
        left_hits = _content_triggers(left, triggers)
        right_hits = _content_triggers(right, triggers)
        if not left_hits or not right_hits:
            continue
        left_term_start, _ = left_hits[-1]
        shared_scope = left[:left_term_start]
        if not shared_scope.strip():
            continue
        return [
            _ADDITIVE_NEGATION.sub(r"\1", left),
            _ADDITIVE_NEGATION.sub(r"\1", shared_scope + right),
        ]
    return [clause]


def _split_clause(clause: str, triggers: list[str], field: str) -> list[str]:
    discovery = _DISCOVERY_AFTER.match(clause)
    pieces = [discovery.group(1), discovery.group(2)] if discovery else [clause]
    expanded: list[str] = []
    for piece in pieces:
        if "放射" in field:
            expanded.append(piece)
        else:
            expanded.extend(_split_alternatives(piece, triggers))
    return expanded


def fact_fragments(fact: Fact) -> list[FactFragment]:
    """Split a stored fact into deterministic, source-derived answer atoms."""
    clauses = [part.strip(_PUNCTUATION) for part in _BOUNDARY.split(fact.content) if part.strip(_PUNCTUATION)]
    atoms: list[tuple[str, str | None]] = []
    subject_scope: str | None = None
    for clause in clauses:
        if _SUBJECT_RESET.match(clause):
            subject_scope = None
        explicit_subject = _FAMILY_SUBJECT.search(clause)
        if explicit_subject:
            subject_scope = explicit_subject.group(0)
        for piece in _split_clause(clause, fact.disclosure_triggers, fact.field):
            piece_subject = _FAMILY_SUBJECT.search(piece)
            if piece_subject:
                subject_scope = piece_subject.group(0)
            cleaned = _CONNECTOR.sub("", piece).strip()
            if cleaned:
                atoms.append((cleaned, subject_scope))
    return [FactFragment(fact.fact_id, f"{fact.fact_id}#a{index + 1}", atom, subject)
            for index, (atom, subject) in enumerate(atoms)]


def _family_topic_matches(question: str, fragment: FactFragment, fact: Fact) -> bool:
    has_family_subject = bool(_FAMILY_SUBJECT.search(fragment.content) or fragment.subject_prefix)
    if not has_family_subject:
        return False
    query = normalize(question)
    content = normalize(fragment.content)
    if any(term in query for term in ("类似", "相似", "相同", "一样")):
        if any(term in content for term in ("类似", "相似", "相同", "一样")):
            return True
        if any(term in "".join(atom.content for atom in fact_fragments(fact))
               for term in ("类似", "相似", "相同", "一样")):
            return False
        return bool(re.search(r"冠心病|高血压|糖尿病|中风|卒中|结核|癌|心脏病", content))
    if any(term in query and term in content for term in _FAMILY_TOPIC_TERMS):
        return True
    return bool(_FAMILY_BROAD_QUERY.search(question) and _FAMILY_DETAIL.search(fragment.content))


def fragment_matches_question(question: str, fragment: FactFragment, fact: Fact, *, continuing_topic: bool = False) -> bool:
    _, question_intents, question_canonicals = detect_intents(question)
    if not question_intents:
        return False
    _, fragment_intents, _ = detect_intents(fragment.content)

    query = normalize(question)
    content = normalize(fragment.content)
    for intent, canonicals in question_canonicals.items():
        if intent == "surgery":
            asks_surgery = bool(_OPERATION_QUERY.search(question))
            asks_trauma = bool(_TRAUMA_QUERY.search(question))
            atom_surgery = bool(_OPERATION_QUERY.search(fragment.content))
            atom_trauma = bool(_TRAUMA_QUERY.search(fragment.content))
            if asks_surgery or asks_trauma:
                if asks_surgery and atom_surgery or asks_trauma and atom_trauma:
                    return True
                continue
        if intent == "named_disease" and "named_disease" in fragment_intents:
            # A follow-up can continue the disease-history topic; a direct question names a specific disease.
            if continuing_topic or any(pattern.search(question) and pattern.search(fragment.content)
                                       for pattern, _ in DISEASE_ALIASES):
                return True
            continue
        if (
            intent == "past_history" and "既往史" in fact.field
            and _GENERAL_CONDITION_QUERY.search(question) and "family" not in question_intents
            and not fragment.subject_prefix and fragment_intents == ["named_disease"]
            and fragment.content.startswith(("有", "患有", "确诊"))
        ):
            return True
        if intent == "location" and _LOCATION_CONTENT.search(fragment.content):
            return True
        if intent == "trigger" and _TRIGGER_CONTENT.search(fragment.content):
            return True
        if intent == "onset" and _TIME_CONTENT.search(fragment.content):
            return True
        if intent == "thirst" and _THIRST_CONTENT.search(fragment.content):
            return True
        if intent == "fever" and re.search(r"多少度|几度|体温多少|温度", question) and _TEMPERATURE_VALUE.search(fragment.content):
            return True
        if intent in fragment_intents:
            return True
        if intent == "family" and _family_topic_matches(question, fragment, fact):
            return True
        if any(
            normalize(trigger) in content and normalize(trigger) in query
            for trigger in fact.disclosure_triggers
        ):
            return True
        if any(normalize(word) in content for word in canonicals if len(normalize(word)) > 1):
            return True
    return False


def select_answer_fragments(
    question: str,
    candidates: list[Fact],
    already_answered_atom_ids: set[str] | None = None,
) -> list[FactFragment]:
    """Project adapter-selected facts to source atoms asked on this turn."""
    opening, intents, _ = detect_intents(question)
    answered = already_answered_atom_ids or set()
    result: list[FactFragment] = []
    for fact in candidates:
        fragments = fact_fragments(fact)
        if opening:
            result.extend(fragments)
            continue
        if not intents:
            result.extend(fragment for fragment in fragments if fragment.atom_id not in answered)
            continue
        matched = [fragment for fragment in fragments if fragment_matches_question(question, fragment, fact)]
        if matched:
            result.extend(matched)
        elif len(fragments) == 1:
            result.extend(fragments)
    return result


def render_fragments(fragments: list[FactFragment]) -> str:
    if not fragments:
        return ""
    text = ""
    for fragment in fragments:
        if text and not text.endswith(("。", "！", "？", "!", "?", "…")):
            text += "，"
        content = fragment.content
        if fragment.subject_prefix and fragment.subject_prefix not in content and fragment.subject_prefix not in text:
            content = fragment.subject_prefix + content
        text += content
    if not text.endswith(("。", "！", "？", "!", "?", "…")):
        text += "。"
    return text
