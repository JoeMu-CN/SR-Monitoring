"""解析前确定性相关性预过滤测试。"""

from sqlalchemy.orm import Session

from app.signals.relevance import assess_signal_relevance
from app.suppliers.models import Supplier, SupplierAlias


def _add_supplier(
    session: Session,
    code: str,
    name: str,
    country: str,
    aliases: tuple[str, ...] = (),
) -> Supplier:
    supplier = Supplier(
        supplier_code=code,
        legal_name=name,
        country_code=country,
        registry_no=f"REG-{code}",
        enabled=True,
    )
    session.add(supplier)
    session.flush()
    for alias in aliases:
        session.add(
            SupplierAlias(
                supplier_id=supplier.id,
                alias=alias,
                normalized_alias=" ".join(alias.casefold().split()),
            )
        )
    session.flush()
    return supplier


def test_supplier_name_hit_is_relevant(db_session: Session) -> None:
    _add_supplier(db_session, "SUP-1", "苏州欣科导轨有限公司", "CN")
    decision = assess_signal_relevance(
        db_session, "苏州欣科导轨有限公司被列入观察名单", "详见公告"
    )
    assert decision.relevant is True
    assert "主体" in decision.reason


def test_supplier_alias_hit_is_relevant(db_session: Session) -> None:
    _add_supplier(db_session, "SUP-2", "宁波鸿腾精密制造有限公司", "CN", aliases=("鸿腾精密",))
    decision = assess_signal_relevance(
        db_session, "鸿腾精密涉及欠税公告", "税务部门发布"
    )
    assert decision.relevant is True
    assert "别名" in decision.reason


def test_china_location_with_cn_supplier_is_relevant(db_session: Session) -> None:
    _add_supplier(db_session, "SUP-3", "江苏海菱机电设备工程有限公司", "CN")
    decision = assess_signal_relevance(
        db_session, "福建省南平市发布雷电黄色预警", "注意防范"
    )
    assert decision.relevant is True
    assert "中国" in decision.reason


def test_us_state_with_no_us_supplier_is_filtered(db_session: Session) -> None:
    _add_supplier(db_session, "SUP-4", "苏州美渡机电科技有限公司", "CN")
    decision = assess_signal_relevance(
        db_session, "M 6.5 - 10 km W of Pinnacles, California", "strong earthquake"
    )
    assert decision.relevant is False
    assert "US" in decision.reason


def test_us_state_abbreviation_tail_is_filtered(db_session: Session) -> None:
    _add_supplier(db_session, "SUP-5", "长江润发机械有限公司", "CN")
    decision = assess_signal_relevance(
        db_session, "M 5.1 - 3 km ENE of Borrego Springs, CA", "earthquake"
    )
    assert decision.relevant is False


def test_foreign_location_with_supplier_is_relevant(db_session: Session) -> None:
    _add_supplier(db_session, "SUP-6", "苏州美渡机电科技有限公司", "CN")
    _add_supplier(db_session, "SUP-7", "Pacific Parts Inc", "US")
    decision = assess_signal_relevance(
        db_session, "M 6.5 - 10 km W of Pinnacles, California", "strong earthquake"
    )
    assert decision.relevant is True


def test_no_geo_clue_is_conservatively_relevant(db_session: Session) -> None:
    _add_supplier(db_session, "SUP-8", "苏州美渡机电科技有限公司", "CN")
    decision = assess_signal_relevance(
        db_session, "某行业发布新的合规要求", "请各企业自查"
    )
    assert decision.relevant is True
    assert "放行" in decision.reason or "未命中" in decision.reason


def test_china_mention_overrides_foreign_filter(db_session: Session) -> None:
    _add_supplier(db_session, "SUP-9", "苏州美渡机电科技有限公司", "CN")
    decision = assess_signal_relevance(
        db_session, "中美贸易摩擦升级", "双方加征关税"
    )
    assert decision.relevant is True


def test_high_impact_topic_is_forced_through_even_without_matching_country(
    db_session: Session,
) -> None:
    _add_supplier(db_session, "SUP-10", "苏州美渡机电科技有限公司", "CN")
    decision = assess_signal_relevance(
        db_session,
        "美国出口管制升级",
        "限制先进芯片供应，影响全球供应链。",
    )
    assert decision.relevant is True
    assert "强制放行" in decision.reason


def test_priority_country_japan_not_filtered_without_supplier(
    db_session: Session,
) -> None:
    """日本事件（无日本供应商）→ 放行（海外供应链重点关注国家，默认 JP/KR）。"""
    _add_supplier(db_session, "SUP-11", "苏州美渡机电科技有限公司", "CN")
    decision = assess_signal_relevance(
        db_session,
        "M 6.2 - near the east coast of Honshu, Japan",
        "No tsunami warning issued.",
    )
    assert decision.relevant is True
    assert "重点关注" in decision.reason


def test_priority_country_korea_not_filtered_without_supplier(
    db_session: Session,
) -> None:
    """韩国事件（无韩国供应商）→ 放行（海外供应链重点关注国家）。"""
    _add_supplier(db_session, "SUP-12", "苏州美渡机电科技有限公司", "CN")
    decision = assess_signal_relevance(
        db_session, "韩国港口工人罢工", "釜山港作业停滞，船期延误风险。"
    )
    assert decision.relevant is True
    assert "重点关注" in decision.reason


def test_non_priority_country_still_filtered_without_supplier(
    db_session: Session,
) -> None:
    """非重点关注国家（巴西）事件且无供应商 → 仍被过滤（规则不回归）。"""
    _add_supplier(db_session, "SUP-13", "苏州美渡机电科技有限公司", "CN")
    decision = assess_signal_relevance(
        db_session, "Brazil initiates dispute", "regarding additional tariffs"
    )
    assert decision.relevant is False


def test_priority_country_with_mixed_foreign_hit_is_relevant(
    db_session: Session,
) -> None:
    """混合命中（日本+美国）且都无供应商 → 因含重点关注国家放行。"""
    _add_supplier(db_session, "SUP-14", "苏州美渡机电科技有限公司", "CN")
    decision = assess_signal_relevance(
        db_session,
        "M 5.1 - near the coast of Honshu, Japan",
        "Region: USGS network, M 5.1, Japan.",
    )
    assert decision.relevant is True
    assert "重点关注" in decision.reason


def test_eu_compliance_english_title_high_impact(db_session: Session) -> None:
    """EU 合规英文标题（CBAM）命中高影响词放行。"""
    _add_supplier(db_session, "SUP-15", "苏州美渡机电科技有限公司", "CN")
    decision = assess_signal_relevance(
        db_session,
        "Commission Implementing Regulation (EU) 2026/1200 on the carbon border adjustment mechanism transitional rules",  # noqa: E501
        "CBAM transitional reporting for importers.",
    )
    assert decision.relevant is True
    assert "高影响" in decision.reason


def test_uflpa_xinjiang_entity_high_impact(db_session: Session) -> None:
    """UFLPA 涉疆实体（含 Xinjiang + forced labor）命中高影响词。"""
    _add_supplier(db_session, "SUP-16", "苏州美渡机电科技有限公司", "CN")
    decision = assess_signal_relevance(
        db_session,
        "Xinjiang East Hope Nonferrous Metals Co., Ltd.",
        "Identified for forced labor under UFLPA.",
    )
    assert decision.relevant is True
    assert "高影响" in decision.reason


def test_lpr_and_pmi_high_impact(db_session: Session) -> None:
    """LPR 报价与 PMI 运行情况命中高影响词（宏观风险信号）。"""
    _add_supplier(db_session, "SUP-17", "苏州美渡机电科技有限公司", "CN")
    lpr = assess_signal_relevance(
        db_session, "LPR 报价：1年期 3.0%", "贷款利率市场报价"
    )
    assert lpr.relevant is True
    assert "高影响" in lpr.reason
    pmi = assess_signal_relevance(
        db_session, "制造业 PMI 49.2%（2026-07）", "采购经理指数运行情况"
    )
    assert pmi.relevant is True
    assert "高影响" in pmi.reason


def test_mee_regulatory_announcement_high_impact(db_session: Session) -> None:
    """生态环境部监管公告（环评/督察/排污/黑名单）命中高影响词。"""
    _add_supplier(db_session, "SUP-18", "苏州美渡机电科技有限公司", "CN")
    for title in (
        "中央生态环境保护督察通报典型案例",
        "关于2025年下半年环评信用管理对象列入黑名单情况的通报",
        "江西省寻乌县鑫鼎汇矿业有限公司违法排污致断面镉浓度超标调查结果",
        "机动车排放检验领域第三方机构专项整治涉刑典型问题通报",
    ):
        decision = assess_signal_relevance(db_session, title, "生态环境部公告")
        assert decision.relevant is True
        assert "高影响" in decision.reason, f"未命中高影响: {title}"
