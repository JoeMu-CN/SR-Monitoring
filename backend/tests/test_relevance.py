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
