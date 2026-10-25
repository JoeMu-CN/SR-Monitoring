"""政策情报的确定性制造业过滤：模型调用前的硬门槛。

为什么需要这一层
----------------
门控提示词的放行条件是「与 scope 的主题、行业、产品关键词或国家有交集」，四者
取或。政策维度的 ``themes`` 是抽象类目（行业监管 / 进出口政策 / 供应链合规法规 /
劳动法规 / 税收法规），``countries`` 恒为启用供应商的国别（当前 CN、JP）。于是
**任何国内政策公告都同时命中「主题」与「国家」**，判定近似恒真：食品安全、动物
检疫、环保督察新闻、环评程序、纯勘误都会进入政策情报页，而真正该拦的只有与
关注域完全不搭界的欧盟文书。

本模块只处理**高置信度、可举证**的非制造业类别：命中即判定「不值得进入政策情报
页」，不发起模型调用（省一次门控调用），并写 ``gate_reason`` 供 ``status=skipped``
抽查。未命中时仍交由 LLM 门控判断（拿不准仍放行），因此不会扩大误杀面。

匹配口径
--------
- 只匹配标题与事件摘要（AI 生成的摘要就是「这条公告讲什么」），不扫正文：
  正文常引述无关领域作背景，扫正文会误伤制造业公告。
- 规则按「领域 → 文书形态」排序，先命中先返回；``rule_id`` 写进理由便于审计与
  后续调参（哪条规则拦了多少条，直接看 gate_reason 后缀）。
- 仅对 ``policy`` 分类生效：行业情报信源噪声低，且这些规则是政策页的收录口径，
  不应外溢到行情/供需通道。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class ExclusionRule:
    """一条确定性排除规则：``rule_id`` 用于审计，``reason`` 直接落 ``gate_reason``。"""

    rule_id: str
    pattern: re.Pattern[str]
    reason: str


def _rule(rule_id: str, pattern: str, reason: str) -> ExclusionRule:
    return ExclusionRule(rule_id, re.compile(pattern, re.IGNORECASE), reason)


#: 规则按顺序匹配，先命中先返回；领域类在前，文书形态类在后。
#: 每条理由都回答「为什么与制造业供应链无关」，便于运营在 skipped 列表抽查。
EXCLUSION_RULES: Final[tuple[ExclusionRule, ...]] = (
    _rule(
        "environmental_supervision",
        r"生态环境保护督察|环保督察",
        "环保督察进驻与典型案例通报属工作动态，未对制造业提出新的合规义务",
    ),
    _rule(
        "eia_credit",
        r"环评信用|环境影响评价信用",
        "环评机构信用管理信息，与供应商生产与供应链风险无关",
    ),
    _rule(
        "eia_procedure",
        r"环评|环境影响评价|生态环境影响评价",
        "环评审批、受理公示或项目环评属程序性信息，未改变制造业监管要求",
    ),
    _rule(
        "grain",
        r"进境粮食|粮食企业|粮食收储|储备粮",
        "粮食收储与进境监管，与制造业投入品及产成品无关",
    ),
    _rule(
        "animal_products",
        r"动植物检疫|检疫和卫生要求|动物疫病|禽流感|口蹄疫|非洲猪瘟|牛皮|羊皮|"
        r"活动物|肉类|水产品",
        "动物产品检疫与疫病防控，与制造业供应链无关",
    ),
    _rule(
        "food_agriculture",
        r"食品|农产品|水果|蔬菜|柑橘|豌豆蛋白|北京鸭|pekin duck|乳制品|转基因|饲料",
        "食品与农产品领域，不构成本页关注的制造业投入品或产成品",
    ),
    _rule(
        "terror_sanctions",
        r"ISIL|Da'esh|基地组织|Al-Qaida|反恐",
        "反恐与个人/实体制裁名单，未改变制造业供应链条件",
    ),
    _rule(
        "ai_standards",
        r"高风险人工智能|人工智能系统|AI systems?",
        "人工智能系统标准，与制造业供应链无关",
    ),
    _rule(
        "pharma",
        r"制药|药品|医疗器械|卫生健康",
        "医药与卫生健康领域，不属本页制造业范围",
    ),
    _rule(
        "marine_dumping",
        r"海洋倾倒区|倾废|海洋工程",
        "海洋倾废与海洋工程管理，与制造业供应链无关",
    ),
    _rule(
        "radiation",
        r"伴生放射性矿|辐射环境",
        "辐射环境标准与放射性矿管理，未直接改变制造业生产或贸易条件",
    ),
    _rule(
        "groundwater",
        r"地下水污染",
        "地下水污染防治指导意见，与制造业投入品和贸易条件无直接关联",
    ),
    _rule(
        "agricultural_water",
        r"农业灌溉",
        "农业灌溉用水安全，不属制造业供应链政策",
    ),
    _rule(
        "toys",
        r"玩具|化学实验套装",
        "玩具与化学实验套装等消费品安全标准，与关注制造业无关",
    ),
    _rule(
        "aquatic_toxicity",
        r"水生毒性",
        "化学物质水生毒性测试技术指南，属测试方法类标准，不改变制造业供应链条件",
    ),
    _rule(
        "monitoring_fraud",
        r"生态环境监测数据|监测数据弄虚作假",
        "生态环境监测数据整治新闻，未对制造业提出具体要求",
    ),
    # 文书形态类放在最后：领域规则优先，理由才指向真正的无关原因
    # （例如带「央视《新闻联播》」的环保督察新闻应说明是督察动态，而非新闻转载）。
    _rule(
        "errata",
        r"corrigendum|rectificatif|更正件|勘误|更正公告",
        "勘误/更正类文书，只更正文本或编号，未改变制造业的监管与贸易条件",
    ),
    _rule(
        "digest",
        r"一图读懂|答记者问|政策解读|新闻发布会|新闻联播|视频\s*\|",
        "解读、答问或新闻转载类内容，未产生新的监管义务或贸易条件",
    ),
)

#: 规则理由的字数预算：与 ``GATE_REASON_MAX_CHARS``（200）共用 ``gate_reason`` 字段。
REASON_MAX_CHARS: Final[int] = 160


def manufacturing_exclusion_reason(category: str, title: str, summary: str | None) -> str | None:
    """返回确定性排除理由；``None`` 表示未命中，交回 LLM 门控判断。

    只读标题与事件摘要，两者都为空时返回 ``None``（没有可判定文本，不得凭空拦）。
    """
    if category != "policy":
        return None
    text = f"{title or ''}\n{summary or ''}".strip()
    if not text:
        return None
    for rule in EXCLUSION_RULES:
        if rule.pattern.search(text):
            reason = f"{rule.reason}（规则 {rule.rule_id}）"
            return reason[:REASON_MAX_CHARS]
    return None
