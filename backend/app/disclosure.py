from __future__ import annotations

import re

from .models import Fact, SessionState


def normalize(text: str) -> str:
    return re.sub(r"\s+", "", text.lower())


_PUNCT = re.compile(r"[，。？！、,.?!：:；;~～…\"'“”‘’（）()\[\]【】]")

# An opening question requests the chief complaint, not the whole history. It is recognised when
# an opening phrase is present and the rest of the sentence (address, greeting) asks nothing else.
OPENING_PHRASE = re.compile(
    r"(?:哪(?:里|儿)?(?:不舒服|难受|不好|不得劲)|有什么(?:不舒服|症状|问题|事)|什么地方不舒服|"
    r"(?:身体|这)?怎么(?:了|啦|不舒服)|为什么来看病|来看什么(?:病|问题)?|主诉是什么|是什么问题|"
    r"(?:来|来医院|来看病)(?:是)?因为什么|因为什么来)"
)
OPENING_FILLER = re.compile(r"^(?:你好|您好|请问|请|您|你|现在|目前|今天|这次|这是|这|主要|具体|先生|女士|大爷|大妈|阿姨|叔叔|"
                            r"老人家|同学|小伙子|姑娘|这回|呢|吗|呀|啊|么|了|的|是|来|说说|"
                            r"我是\S{0,10}?(?:医生|大夫|实习生|学生))*$")

# Clinical history intents. Each maps natural phrasings to canonical words that contain the
# trigger words cases already use. Intents only *route* a question to existing synthetic facts;
# they never create clinical content. Ordered as a standard history.
CLINICAL_INTENTS: tuple[tuple[str, re.Pattern[str], str], ...] = tuple(
    (name, re.compile(pattern), canonical)
    for name, pattern, canonical in (
        ("onset", r"什么时候|啥时候|几点|多久|多长时间|有?几天(了|前)|几个小时|几小时|多少天|起病|开始的|从什么|何时|哪天",
         "什么时候 多久 开始 时间 起病 几点"),
        ("trigger", r"诱因|怎么引起|因为什么|什么原因|当时(在)?(干|做)|什么情况下|之前(在)?(干|做)|发病(前|之前|时|的时候)|(在|正在)(干|做)什么|怎么(发生|出现|得)的",
         "诱因"),
        ("recent_infection", r"感冒|受凉|着凉", "感冒 受凉"),
        ("location", r"(哪(里|儿|个地方|个位置|块|边|个部位)|什么(地方|位置)|具体在哪)(疼|痛)|(疼|痛)(在|的)?(哪|什么地方)|部位|位置",
         "哪里疼 哪里痛 部位 位置 哪个地方"),
        ("character", r"怎么(个)?(疼|痛)|(什么|啥)(样)?的?(疼|痛)|(疼|痛)(法|起来|得怎么样|的时候)|(什么|啥)感觉|性质|像什么|压榨|闷痛|刺痛|绞痛|胀痛|隐痛|撕裂|闷着|压着|针扎|刀割|烧灼|(疼|痛)得(厉害|很)|多(疼|痛)|有多(疼|痛)|(疼|痛)的程度",
         "性质 怎么疼 怎么痛 什么样的痛"),
        ("radiation", r"放射|窜|传到|牵扯|跑到|扩散|蔓延|别的地方也(疼|痛)|后背|背(疼|痛)",
         "放射 传到哪里 窜到哪 转移 后背"),
        ("modifiers", r"加重|缓解|减轻|好(一)?点|好些|舒服(一)?(点|些)|姿势|好受|更(疼|痛|厉害)|一阵一阵|阵发|间断|没停|一直(疼|痛)",
         "加重 缓解 持续"),
        ("accompany", r"(还有|其他|别的|其它|另外|同时)\S{0,4}(症状|不舒服|难受|感觉|不得劲)|伴随|伴有|还有哪",
         "伴随 其他症状"),
        ("sweat", r"出汗|冷汗|大汗|流汗", "出汗 大汗"),
        ("fever", r"发烧|发热|体温|烧不烧|有没有烧|烧了|发冷|怕冷|寒战|打寒颤|畏寒", "发热 发烧 体温 寒战 畏寒"),
        ("nausea", r"恶心|想吐|呕吐|吐(了|过|没|吗|不)|反胃", "恶心 呕吐 吐"),
        ("appetite", r"食欲|胃口|吃(饭|东西)|吃(不|得)下|吃得怎么样|饮食", "食欲 胃口 吃饭 吃不下"),
        ("bowel", r"大便|拉肚子|腹泻|拉稀|排便|便秘|解手|大小便|黑便|便血", "大便 排便 腹泻"),
        ("urine", r"小便|尿(多|少|频|急|痛|血|量|次数|怎么样|色)|撒尿|排尿|大小便|夜尿|尿尿", "小便 尿"),
        ("breath", r"气短|气促|喘|憋|呼吸|上不来气|透不过气|胸闷", "呼吸 气短 喘 憋 胸闷"),
        ("palpitation", r"心慌|心悸|心跳", "心慌 心悸"),
        ("head", r"头(痛|疼|晕|昏)|眩晕", "头痛 头疼"),
        ("consciousness", r"晕(倒|过去|厥)|昏迷|抽搐|抽筋|意识|清醒|神志", "抽搐 意识 昏迷"),
        ("chest_pain", r"胸(口|部)?(疼|痛)", "胸痛"),
        ("cough", r"咳|痰", "咳嗽 痰 咳"),
        ("hemoptysis", r"咯血|咳血|痰\S{0,4}血|血丝", "咯血 痰中带血 血丝"),
        ("neuro", r"无力|没力气|没劲儿?|麻木|发麻|手脚|嘴歪|口角|说话|口齿|看东西|视物|看不清|走路|"
                  r"拿(东西|不住|得住|不动)|抬(起|不起)|使不上劲|动不了|(手|腿|胳膊)还?能",
         "无力 麻木 手脚 嘴歪 症状"),
        ("thirst", r"口渴|口干|喝水|多饮|嘴(巴|里)?干|渴不渴", "口渴 喝水 多饮"),
        ("general", r"睡(觉|眠|得)|精神|体重|瘦|胖|乏力|累", "一般情况 睡眠 体重 精神 乏力"),
        ("past_history", r"(既往|以前|之前|过去|从前|平时|原来|一直)\S{0,6}(病|毛病|疾病)|基础病|慢性病|老毛病|病史|身体(一直|平时|平常)?(怎么样|咋样|好吗|好不好|如何)|住过院|住院",
         "既往 既往史 病史 基础病"),
        # Routed through DISEASE_ALIASES and only as a fallback (see classify_turn).
        ("named_disease", r"高血压|血压|糖尿病|血糖|房颤|心脏病|心脏|支气管炎|肺病|冠心病|中风|脑梗", "既往"),
        ("medication", r"(吃|服|用|打)(过|了|着)?(什么|啥|哪些|点|些)?药|药物(?!过敏)|用药|吃药|的药|胰岛素|阿司匹林|抗凝|华法林|止痛",
         "用药 吃过药"),
        ("allergy", r"过敏", "过敏"),
        ("surgery", r"手术|开过刀|开刀|外伤|受(过)?伤|摔(倒|伤|过|跤)|撞", "手术 外伤"),
        ("smoking", r"抽烟|吸烟|烟|抽(多少|几)|几根|几包", "吸烟 烟"),
        ("alcohol", r"喝酒|饮酒|酒", "饮酒 喝酒"),
        ("family", r"家(里|人|族|属)|父母|父亲|母亲|爸|妈|兄弟|姐妹|亲戚|遗传", "家族史 家里人"),
        ("exposure", r"接触|外地|出差|旅(游|行)|去过哪|传染|周围\S{0,4}(人|病)|同事|禽|鸡鸭", "接触 外地 流行 旅居"),
        ("reproductive", r"月经|例假|大姨妈|怀孕|末次|经期|生育|孩子|结婚|婚育", "月经 婚育 怀孕"),
        ("age", r"多大|几岁|年龄|年纪|岁数|贵庚|高寿", "年龄"),
        ("name", r"名字|叫什么|怎么称呼|贵姓|姓什么", "姓名"),
        ("occupation", r"工作|职业|干什么的|做什么的|上班|退休", "职业"),
        ("prior_episode", r"以前\S{0,8}(有过(?!敏)|出现过|发生过|也|这样|这么|类似|这种)|第一次|类似\S{0,6}(情况|症状|时候)|"
                          r"这种情况|(这样|这么)\S{0,3}(疼|痛|难受)过",
         "发作史 以前有过"),
        ("prior_care", r"(去过|看过|到过)\S{0,4}(医院|医生|诊所|急诊)|就诊|看过病|处理过|治疗过|来之前|送(到|来|过来)|怎么(来|过来|到)的|过来的|谁(陪|送)|陪(你|您)来|跟谁来|怎么到(医院|这)",
         "就诊经过"),
    )
)

# Non-clinical turns. These never disclose a case fact; the reply text is fixed and safe.
SOCIAL_INTENTS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # Asking the patient for test results: they come from the order system, never from the patient.
    ("exam_request", re.compile(
        r"ct|心电图|超声|b超|彩超|化验|胸片|片子|血气|血常规|尿常规|核磁|磁共振|结果|报告|查出来|指标")),
    ("diagnosis_request", re.compile(
        r"什么病|诊断|严重(吗|不)|要紧|会不会(死|有事)|结果|报告|化验单|隐藏|答案|评分|是不是\S{0,6}(病|炎|梗|塞|中风)|我(得|会)了")),
    ("greeting", re.compile(r"^(你好|您好|医生好|早上好|上午好|下午好|晚上好|hello|hi)|我是\S{0,8}(医生|大夫|学生|实习)")),
    ("acknowledge", re.compile(r"谢谢|好的|明白|知道了|别(担心|紧张|着急|害怕)|放心|稍等|马上|我们(会|先|给|来)|配合|躺好|坚持")),
)

SOCIAL_REPLIES = {
    "greeting": "医生您好。",
    "acknowledge": "好的，谢谢医生。",
    "diagnosis_request": "这个我也不清楚，得请医生帮我看看。",
    "exam_request": "检查结果我不清楚，您看看报告吧。",
}

# Disease words in a question -> trigger words that name that disease in case facts.
DISEASE_ALIASES: tuple[tuple[re.Pattern[str], tuple[str, ...]], ...] = tuple(
    (re.compile(pattern), words) for pattern, words in (
        (r"高血压|血压", ("高血压",)),
        (r"糖尿病|血糖", ("糖尿病", "血糖")),
        (r"房颤|心脏|心律", ("房颤", "心脏")),
        (r"冠心病|心脏病", ("冠心病", "心脏病", "心脏")),
        (r"中风|脑梗|卒中", ("中风",)),
        (r"支气管炎|肺病|老慢支", ("支气管炎",)),
    )
)
UNCONFIGURED_REPLY = "这个我没太注意，说不好。"

# Words whose characters overlap a short trigger but mean something else, e.g. "尿" in 糖尿病.
# A trigger occurring only inside such a word does not count, unless the trigger itself contains it.
MASK_TERMS = ("糖尿病", "尿常规", "尿检", "尿酸", "烧心", "吐字", "检查结果", "化验结果", "最开始", "一开始",
              "头晕", "眩晕")


def _clean(question: str) -> str:
    return _PUNCT.sub("", normalize(question))


def _mask(query: str) -> str:
    for term in MASK_TERMS:
        query = query.replace(term, "＃" * len(term))
    return query


def _trigger_hit(trigger: str, query: str, masked: str, canonicals: list[str]) -> bool:
    if not trigger:
        return False
    if any(term in trigger for term in MASK_TERMS):
        return trigger in query
    return trigger in masked or any(trigger in text for text in canonicals)


def _visible(fact: Fact, state: SessionState) -> bool:
    if not fact.current or fact.patient_known is False:
        return False
    if fact.status_valid_from > state.sim_time_minutes:
        return False
    if fact.status_valid_to is not None and state.sim_time_minutes > fact.status_valid_to:
        return False
    return True


def detect_intents(question: str) -> tuple[bool, list[str], dict[str, list[str]]]:
    """Return (is_opening, clinical intent names, canonical routing words per intent)."""
    query = _clean(question)
    opening = OPENING_PHRASE.search(query)
    if opening and OPENING_FILLER.fullmatch(query[:opening.start()] + query[opening.end():]):
        return True, ["opening"], {}
    names: list[str] = []
    canonicals: dict[str, list[str]] = {}
    for name, pattern, canonical in CLINICAL_INTENTS:
        if pattern.search(query):
            names.append(name)
            canonicals[name] = canonical.split()
    # "爸妈身体怎么样" asks about relatives, not the patient's own past history.
    if "family" in canonicals and "past_history" in canonicals and not re.search(r"您|你|自己", query):
        names.remove("past_history")
        del canonicals["past_history"]
    return False, names, canonicals


def social_intent(question: str) -> str | None:
    query = _clean(question)
    for name, pattern in SOCIAL_INTENTS:
        if pattern.search(query):
            return name
    return None


def classify_turn(state: SessionState, question: str, facts: list[Fact]) -> tuple[list[Fact], str]:
    """Authorize facts for one student question and label the turn.

    Kinds: ``fact`` (facts disclosed), ``social`` (greeting/acknowledgement/diagnosis request, no
    facts), ``unconfigured`` (a recognised history item the case does not define) and ``unknown``.
    """
    query = _clean(question)
    opening, intents, by_intent = detect_intents(question)
    masked = _mask(query)
    visible = [fact for fact in facts if _visible(fact, state)]
    if opening:
        chief = [fact for fact in visible if fact.field == "主诉"]
        return chief, ("fact" if chief else "unknown")

    def hits(fact: Fact, canonicals: list[str]) -> bool:
        triggers = [normalize(t) for t in fact.disclosure_triggers]
        return any(_trigger_hit(trigger, query, masked, canonicals) for trigger in triggers)

    social = social_intent(question)
    # Test results and diagnoses are never the patient's to give, whatever words they contain.
    if social == "exam_request" or (social == "diagnosis_request" and set(intents) <= {"named_disease"}):
        return [], "social"
    if "family" in by_intent and not re.search(r"您|你|自己", query):
        # Questions about relatives only open family-history facts, even if they name a disease.
        matches = [fact for fact in visible if hits(fact, by_intent["family"]) and not hits(fact, [])
                   or fact.field == "家族史"]
        return (matches, "fact") if matches else ([], "unconfigured")
    general = [word for name, words in by_intent.items() if name != "named_disease" for word in words]
    matched = {fact.fact_id for fact in visible if hits(fact, general)}
    if "named_disease" in by_intent and not matched:
        # "血压高吗" -> facts naming that disease; only then the general past-history fact,
        # so asking about one disease does not disclose unrelated ones.
        aliases = [word for pattern, words in DISEASE_ALIASES if pattern.search(query) for word in words]
        matched = {fact.fact_id for fact in visible if any(normalize(t) in aliases for t in fact.disclosure_triggers)}
        if not matched:
            matched = {fact.fact_id for fact in visible if hits(fact, by_intent["named_disease"])}
    matches = [fact for fact in visible if fact.fact_id in matched]
    if matches:
        return matches, "fact"
    if social == "diagnosis_request" or (social and not intents):
        return [], "social"
    if intents and not opening:
        return [], "unconfigured"
    return [], "unknown"


def non_fact_reply(question: str, kind: str) -> str | None:
    """Fixed, fact-free patient reply for a turn without disclosed facts (None = generic fallback)."""
    if kind == "social":
        return SOCIAL_REPLIES.get(social_intent(question) or "")
    if kind == "unconfigured":
        return UNCONFIGURED_REPLY
    return None


def classify_and_authorize(state: SessionState, question: str, facts: list[Fact]) -> list[Fact]:
    return classify_turn(state, question, facts)[0]
