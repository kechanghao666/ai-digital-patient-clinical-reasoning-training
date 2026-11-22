'''Hospital-wide exam catalogue so students choose what to order instead of picking from case answers.

Every case sees the identical list. A catalogue item maps to a case exam through ``aliases``; the case
keeps its authored result. Any other item returns a neutral, non-diagnostic result, so ordering it
reveals nothing about the hidden diagnosis.
'''
from __future__ import annotations

from dataclasses import dataclass, field

GENERIC_RESULT = '未见明显异常（本模拟病例未设置该项异常发现）'
CATEGORIES = ('床旁与基础检验', '生化与代谢', '心血管', '影像', '功能与内镜', '微生物与免疫', '其他')


@dataclass(frozen=True)
class CatalogExam:
    catalog_id: str
    name: str
    category: str
    cost: int
    minutes: int
    aliases: tuple[str, ...] = field(default_factory=tuple)
    baseline: bool = False  # routine, low-cost admission work-up: costed but never penalised

    def matches(self, case_name: str) -> bool:
        return case_name == self.name or case_name in self.aliases


# (name, category, cost in yuan, minutes to result, aliases used by case files)
_RAW = (
    ('血氧饱和度', '床旁与基础检验', 5, 0, ()), ('快速血糖', '床旁与基础检验', 10, 0, ('指尖血糖',)),
    ('血常规', '床旁与基础检验', 25, 30, ()), ('尿常规', '床旁与基础检验', 15, 20, ()),
    ('粪便常规加潜血', '床旁与基础检验', 20, 30, ()), ('凝血功能', '床旁与基础检验', 80, 45, ()),
    ('血型与交叉配血', '床旁与基础检验', 60, 45, ()), ('C反应蛋白', '床旁与基础检验', 40, 30, ()),
    ('血气分析', '生化与代谢', 70, 10, ('动脉血气分析',)), ('电解质', '生化与代谢', 45, 30, ()),
    ('肝功能', '生化与代谢', 90, 60, ()), ('肾功能', '生化与代谢', 60, 60, ()), ('血脂', '生化与代谢', 70, 60, ()),
    ('淀粉酶与脂肪酶', '生化与代谢', 60, 45, ()), ('血酮体或尿酮体', '生化与代谢', 30, 15, ('血酮体', '尿酮体')),
    ('糖化血红蛋白', '生化与代谢', 60, 60, ()), ('血乳酸', '生化与代谢', 40, 15, ()),
    ('心电图', '心血管', 30, 5, ('十二导联心电图',)), ('肌钙蛋白', '心血管', 90, 30, ('高敏肌钙蛋白',)),
    ('BNP', '心血管', 110, 45, ('NT-proBNP',)), ('D-二聚体', '心血管', 100, 30, ()),
    ('心脏超声', '心血管', 280, 40, ()), ('动态心电图', '心血管', 220, 120, ()), ('冠脉造影', '心血管', 6500, 90, ()),
    ('胸部X线', '影像', 90, 30, ('胸片',)), ('胸部CT', '影像', 480, 60, ()),
    ('头颅CT平扫', '影像', 420, 30, ('头颅CT',)), ('头颅MRI', '影像', 1100, 120, ()),
    ('腹部超声', '影像', 160, 30, ('腹部B超',)), ('腹部CT', '影像', 620, 60, ()),
    ('CT肺动脉造影', '影像', 1300, 75, ()), ('主动脉CT血管成像', '影像', 1400, 75, ()),
    ('肺功能', '功能与内镜', 180, 40, ()), ('胃镜', '功能与内镜', 380, 90, ()), ('肠镜', '功能与内镜', 520, 120, ()),
    ('脑电图', '功能与内镜', 260, 90, ()),
    ('痰培养', '微生物与免疫', 90, 1440, ()), ('血培养', '微生物与免疫', 130, 2880, ()), ('尿培养', '微生物与免疫', 80, 1440, ()),
    ('降钙素原', '微生物与免疫', 160, 60, ()), ('流感与新冠抗原', '微生物与免疫', 60, 20, ()), ('抗核抗体', '微生物与免疫', 120, 240, ()),
    ('甲状腺功能', '其他', 150, 120, ()), ('妊娠试验', '其他', 20, 15, ()), ('腰椎穿刺脑脊液检查', '其他', 450, 120, ()),
    ('肿瘤标志物', '其他', 300, 240, ()),
)

BASELINE = frozenset({'血氧饱和度', '快速血糖', '血常规', '电解质', '肾功能'})

CATALOG: tuple[CatalogExam, ...] = tuple(
    CatalogExam(f'cat_{i:03d}', name, category, cost, minutes, aliases, name in BASELINE)
    for i, (name, category, cost, minutes, aliases) in enumerate(_RAW, 1)
)
_BY_ID = {e.catalog_id: e for e in CATALOG}


def find(catalog_id: str) -> CatalogExam | None:
    return _BY_ID.get(catalog_id)


def entry_for_name(case_name: str) -> CatalogExam | None:
    return next((e for e in CATALOG if e.matches(case_name)), None)


def case_exam_for(case, entry: CatalogExam):
    '''The configured case exam this catalogue item stands for, if any.'''
    return next((e for e in case.exams if e.configured and entry.matches(e.standard_name)), None)


def unmapped_case_exams(case) -> list[str]:
    '''Configured case exams that no catalogue item maps to (authoring error: unreachable for students).'''
    return [e.standard_name for e in case.exams if e.configured and entry_for_name(e.standard_name) is None]


def public_catalog(case=None) -> list[dict]:
    '''Identical for every case, so the menu itself never hints at the answer.'''
    return [{'catalog_id': e.catalog_id, 'name': e.name, 'category': e.category, 'cost': e.cost, 'minutes': e.minutes,
             'baseline': e.baseline} for e in CATALOG]
