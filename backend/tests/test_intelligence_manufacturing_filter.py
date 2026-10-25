"""政策情报的确定性制造业过滤：规则口径与批量入口短路。

锁定的行为：
- 高置信度非制造业类别（食品农产品、动物检疫、粮食、环保督察、环评程序、
  勘误与解读、反恐制裁、AI 标准等）命中规则，理由写明规则 id 供审计。
- 制造业公告（反倾销、海关申报、排污许可、化学品名录）不命中规则，仍交由
  LLM 门控判断，不得被规则误杀。
- 批量入口命中规则时落 skipped 且**不调用模型**（门控与报告都不调用）；未命中
  时照旧走 LLM 门控，规则不改变既有语义。
"""

from __future__ import annotations

import pytest
from pytest import MonkeyPatch
from sqlalchemy.orm import Session
from test_intelligence import POLICY_SOURCE_CODE, _event, _use_test_session
from test_intelligence_gate import GatedProvider, _row, _set_gate, _use_provider

from app.risks import intelligence_batch, intelligence_service
from app.risks.intelligence_filter import manufacturing_exclusion_reason
from app.risks.models import RiskEvent


def _set_text(
    session: Session,
    event_id: int,
    *,
    title: str | None = None,
    summary: str | None = None,
) -> None:
    """改写事件主信号的标题与事件摘要；规则只看这两处文本。"""
    if title is not None:
        signal = intelligence_service.primary_signal_rows(session, [event_id])[event_id]
        signal.title = title
    if summary is not None:
        event = session.get(RiskEvent, event_id)
        assert event is not None
        event.summary = summary
    session.flush()


@pytest.mark.parametrize(
    ("title", "summary", "rule_id"),
    [
        (
            "关于优化进境粮食企业监管措施及有关事宜的公告",
            "海关总署优化进境粮食企业监管措施。",
            "grain",
        ),
        (
            "COMMISSION IMPLEMENTING REGULATION … making imports of Pekin duck "
            "originating in the People's Republic of China subject to registration",
            "欧盟对原产于中国的北京鸭进口实施登记。",
            "food_agriculture",
        ),
        (
            "海关总署公告2026年第134号（关于进口约旦牛皮和羊皮检疫和卫生要求的公告）",
            "对进口约旦牛皮和羊皮的检疫和卫生要求作出规定。",
            "animal_products",
        ),
        (
            "第三轮第七批中央生态环境保护督察全部实现督察进驻（央视《新闻联播》）",
            "生态环境部发布督察进驻消息。",
            "environmental_supervision",
        ),
        (
            "关于2026年上半年环评信用管理对象列入“黑名单”情况的通报",
            "公布环评信用管理对象被列入黑名单的情况。",
            "eia_credit",
        ),
        (
            "Corrigendum to Commission Implementing Regulation (EU) 2026/397",
            "对实施条例的更正件。",
            "errata",
        ),
        (
            "一图读懂 | 火电、锅炉、造纸、钢铁、石化和汽车制造六项排污许可证技术规范修订",
            "以图解形式介绍排污许可技术规范修订。",
            "digest",
        ),
        (
            "COMMISSION IMPLEMENTING REGULATION … amending for the 363rd time "
            "Council Regulation (EC) No 881/2002 … ISIL (Da'esh) and Al-Qaida",
            "对与 ISIL 和基地组织有关联的个人和实体实施限制性措施。",
            "terror_sanctions",
        ),
        (
            "COMMISSION IMPLEMENTING DECISION … on the harmonised standards for "
            "high-risk AI systems",
            "为支持人工智能条例而起草的高风险人工智能系统协调标准。",
            "ai_standards",
        ),
    ],
)
def test_non_manufacturing_titles_are_excluded(title: str, summary: str, rule_id: str) -> None:
    reason = manufacturing_exclusion_reason("policy", title, summary)

    assert reason is not None
    assert f"规则 {rule_id}" in reason


@pytest.mark.parametrize(
    ("title", "summary"),
    [
        (
            "COMMISSION IMPLEMENTING REGULATION … making imports of bolted and boltless "
            "steel shelves originating in the People's Republic of China subject to registration",
            "欧盟对原产于中国的钢制货架进口实施登记。",
        ),
        (
            "COMMISSION IMPLEMENTING REGULATION … imposing a definitive anti-dumping duty "
            "on imports of titanium dioxide originating in the People's Republic of China",
            "欧盟对原产于中国的钛白粉维持反倾销税。",
        ),
        (
            "海关总署公告2026年第133号（关于二氯二氢硅商品编号申报要求的公告）",
            "就二氯二氢硅商品编号申报要求作出规定。",
        ),
        (
            "关于发布《排污许可证申请与核发技术规范 钢铁工业》等六项国家生态环境标准的公告",
            "发布钢铁工业排污许可技术规范。",
        ),
        (
            "关于增补和变更《中国现有化学物质名录》的公告",
            "化学品监管名录的例行更新。",
        ),
    ],
)
def test_manufacturing_titles_pass_to_llm_gate(title: str, summary: str) -> None:
    assert manufacturing_exclusion_reason("policy", title, summary) is None


def test_filter_only_applies_to_policy_category() -> None:
    # 行业情报信源噪声低，同一标题不得被政策页口径拦掉。
    assert (
        manufacturing_exclusion_reason(
            "industry", "关于优化进境粮食企业监管措施及有关事宜的公告", ""
        )
        is None
    )


def test_batch_skips_excluded_event_without_model_calls(
    db_session: Session, monkeypatch: MonkeyPatch
) -> None:
    # Given：一条命中确定性规则的进境粮食监管公告
    provider = GatedProvider(relevant=True)
    _use_provider(monkeypatch, provider)
    _use_test_session(monkeypatch, db_session)
    _set_gate(monkeypatch)
    event = _event(db_session, POLICY_SOURCE_CODE, "grain")
    _set_text(
        db_session,
        event.id,
        title="关于优化进境粮食企业监管措施及有关事宜的公告",
        summary="海关总署优化进境粮食企业监管措施。",
    )

    # When
    generated = intelligence_batch.generate_pending_intelligence_reports(limit=5)

    # Then：规则命中落 skipped，门控与报告都不调用模型
    assert generated == 0
    assert provider.gate_calls == []
    assert provider.report_calls == 0
    row = _row(db_session, event.id)
    assert row.status == "skipped"
    assert row.gate_reason is not None and "规则 grain" in row.gate_reason
    assert row.report_zh is None


def test_batch_still_calls_llm_gate_for_manufacturing_event(
    db_session: Session, monkeypatch: MonkeyPatch
) -> None:
    # Given：一条命中关注域的反倾销公告（规则不拦）
    provider = GatedProvider(relevant=True)
    _use_provider(monkeypatch, provider)
    _use_test_session(monkeypatch, db_session)
    _set_gate(monkeypatch)
    event = _event(db_session, POLICY_SOURCE_CODE, "shelves")
    _set_text(
        db_session,
        event.id,
        title=(
            "COMMISSION IMPLEMENTING REGULATION … making imports of bolted and boltless "
            "steel shelves originating in the People's Republic of China subject to registration"
        ),
        summary="欧盟对原产于中国的钢制货架进口实施登记。",
    )

    # When
    generated = intelligence_batch.generate_pending_intelligence_reports(limit=5)

    # Then：仍走 LLM 门控，放行后生成报告
    assert generated == 1
    assert len(provider.gate_calls) == 1
    assert provider.report_calls == 1
    assert _row(db_session, event.id).status == "ready"
