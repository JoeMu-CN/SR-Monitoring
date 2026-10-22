"""采集入队确定性预筛测试（不联网、不调用 LLM）。

覆盖三类过滤（相关性 / 清单类 / 结构化分级）与供应商命中放行，并验证写库
失败被吞掉、不阻断采集。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.models import AIAnalysisRecord
from app.signals import prefilter
from app.signals.models import DataSource, RawSignal
from app.signals.prefilter import PrefilterOutcome, prefilter_new_signals
from app.signals.priority import assign_signal_priorities
from app.signals.relevance import invalidate_filter_rules_cache
from app.signals.validity import ValidityState
from app.suppliers.models import Supplier, SupplierAlias

NOW = datetime(2026, 10, 21, 8, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _fresh_filter_rules() -> None:
    """signal-filter 规则有 60s 进程内缓存，用例前失效避免继承上一条配置。"""
    invalidate_filter_rules_cache()


def _add_source(session: Session, code: str, *, source_type: str) -> DataSource:
    """取或建测试信息源（迁移已 seed 的 code 直接复用，避免唯一键冲突）。"""
    existing = session.scalar(select(DataSource).where(DataSource.code == code))
    if existing is not None:
        return existing
    source = DataSource(
        code=code,
        name=code,
        source_type=source_type,
        credibility=50,
        enabled=True,
    )
    session.add(source)
    session.flush()
    return source


def _add_signal(
    session: Session, source: DataSource, title: str, content: str
) -> RawSignal:
    signal = RawSignal(
        source_id=source.id,
        title=title,
        content=content,
        collected_at=NOW,
        fingerprint=f"{source.code}:{title}",
        dedup_key=f"{source.code}:{title}",
        raw_data={},
        validity_state=ValidityState.PENDING_CLASSIFICATION,
    )
    session.add(signal)
    session.flush()
    return signal


def _add_supplier(
    session: Session, code: str, name: str, aliases: tuple[str, ...] = ()
) -> Supplier:
    supplier = Supplier(
        supplier_code=code,
        legal_name=name,
        country_code="CN",
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


def _only_record(session: Session, signal: RawSignal) -> AIAnalysisRecord:
    record = session.scalar(
        select(AIAnalysisRecord).where(AIAnalysisRecord.signal_id == signal.id)
    )
    assert record is not None, "被过滤信号必须有确定性过滤记录"
    return record


def test_list_source_signal_without_supplier_is_filtered(db_session: Session) -> None:
    # Given: 全量实体清单信源的一条实体，文本不含任何供应商主体/别名。
    source = _add_source(db_session, "ofac-sdn", source_type="sanctions_list")
    signal = _add_signal(
        db_session, source, "Acme Trading Ltd.", "SDN listing entry 42."
    )

    # When
    outcome = prefilter_new_signals(db_session, [signal.id], now_utc=NOW)

    # Then: 不进 LLM，信号置无关，记录为清单类过滤。
    assert outcome == PrefilterOutcome(filtered=1, kept=0)
    db_session.refresh(signal)
    assert signal.validity_state == ValidityState.IRRELEVANT
    assert signal.validity_reason["code"] == "filtered_out"
    assert signal.validity_reason["details"]["filter"] == "entity_list"
    record = _only_record(db_session, signal)
    assert record.model == "entity-list-v1"
    assert record.prompt_version == "entity-list-v1"
    assert record.provider == "deterministic-filter"
    assert record.error == "filtered: 清单类信号未命中任何供应商"
    assert record.review_reason == "实体清单未命中任何供应商，跳过 LLM"
    assert record.needs_review is True
    assert record.result is None


def test_foreign_geography_without_supplier_is_filtered_by_relevance(
    db_session: Session,
) -> None:
    # Given: 明确国外地理、无供应商、无高影响关键词。
    source = _add_source(db_session, "gdelt-news", source_type="news")
    signal = _add_signal(
        db_session,
        source,
        "德国化工园区设备检修",
        "德国路德维希港工厂设备检修，暂无人员伤亡。",
    )

    # When
    outcome = prefilter_new_signals(db_session, [signal.id], now_utc=NOW)

    # Then
    assert outcome == PrefilterOutcome(filtered=1, kept=0)
    db_session.refresh(signal)
    assert signal.validity_state == ValidityState.IRRELEVANT
    assert signal.validity_reason["details"]["filter"] == "relevance"
    record = _only_record(db_session, signal)
    assert record.prompt_version == "relevance-v1"
    assert record.error.startswith("filtered: ")
    assert record.review_reason.startswith("预过滤结果待复核：")


def test_list_source_signal_matching_supplier_is_kept(db_session: Session) -> None:
    # Given: 清单类信源，文本命中供应商别名。
    _add_supplier(
        db_session, "SUP-100", "宁波鸿腾精密制造有限公司", aliases=("鸿腾精密",)
    )
    source = _add_source(db_session, "uflpa-entity-list", source_type="sanctions_list")
    signal = _add_signal(
        db_session, source, "鸿腾精密", "UFLPA 实体清单：鸿腾精密及其关联公司。"
    )

    # When
    outcome = prefilter_new_signals(db_session, [signal.id], now_utc=NOW)

    # Then: 放行给 LLM，状态不变且不写过滤记录。
    assert outcome.filtered == 0
    assert outcome.kept == 1
    assert outcome.kept_ids == (signal.id,)
    db_session.refresh(signal)
    assert signal.validity_state == ValidityState.PENDING_CLASSIFICATION
    assert (
        db_session.scalar(
            select(AIAnalysisRecord).where(AIAnalysisRecord.signal_id == signal.id)
        )
        is None
    )


def test_low_commodity_change_is_filtered_by_structured_grade(
    db_session: Session,
) -> None:
    # Given: commodity 涨跌幅低于默认阈值 5%。
    source = _add_source(db_session, "commodity-futures", source_type="market_data")
    signal = _add_signal(
        db_session,
        source,
        "螺纹钢主力合约",
        "较昨结算：+1.20%，成交量 12 万手。",
    )

    # When
    outcome = prefilter_new_signals(db_session, [signal.id], now_utc=NOW)

    # Then
    assert outcome == PrefilterOutcome(filtered=1, kept=0)
    db_session.refresh(signal)
    assert signal.validity_reason["details"]["filter"] == "structured_grade"
    record = _only_record(db_session, signal)
    assert record.prompt_version == "structured-grade-v1"
    assert record.review_reason.startswith("结构化分级跳过：")


def test_write_failure_is_swallowed(db_session: Session, monkeypatch: Any) -> None:
    # Given: 命中清单类过滤，但写过滤记录时抛错。
    source = _add_source(db_session, "ofac-sdn", source_type="sanctions_list")
    signal = _add_signal(
        db_session, source, "Acme Trading Ltd.", "SDN listing entry 42."
    )

    def _boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("write failed")

    monkeypatch.setattr(prefilter, "insert", _boom)

    # When / Then: 不抛出，返回 (0, 0)，采集不受影响。
    outcome = prefilter_new_signals(db_session, [signal.id], now_utc=NOW)
    assert outcome == PrefilterOutcome(filtered=0, kept=0)


def test_empty_input_returns_zero(db_session: Session) -> None:
    assert prefilter_new_signals(db_session, []) == PrefilterOutcome(
        filtered=0, kept=0
    )


def test_market_reading_signal_is_kept_for_consumption(db_session: Session) -> None:
    # Given: 文本明显无关、但带 source_extra 行情读数的信号。
    source = _add_source(db_session, "commodity-futures", source_type="market_data")
    signal = RawSignal(
        source_id=source.id,
        title="某品种收盘",
        content="巴西某产区降雨。",
        collected_at=NOW,
        fingerprint="commodity:reading",
        dedup_key="commodity:reading",
        raw_data={"source_extra": {"symbol": "CU", "close": 1.0}},
        validity_state=ValidityState.PENDING_CLASSIFICATION,
    )
    db_session.add(signal)
    db_session.flush()

    # When / Then: 预筛不接管，留给消费链的行情评估。
    outcome = prefilter_new_signals(db_session, [signal.id], now_utc=NOW)
    assert outcome.filtered == 0
    assert outcome.kept_ids == (signal.id,)
    db_session.refresh(signal)
    assert signal.validity_state == ValidityState.PENDING_CLASSIFICATION


def test_assign_priorities_scores_kept_signals(db_session: Session) -> None:
    # Given: 清单源、普通新闻、命中供应商的新闻各一条。
    _add_supplier(db_session, "SUP-200", "宁波鸿腾精密制造有限公司", aliases=("鸿腾精密",))
    list_source = _add_source(db_session, "ofac-sdn", source_type="sanctions_list")
    news_source = _add_source(db_session, "gdelt-news", source_type="news")
    listed = _add_signal(db_session, list_source, "Acme Trading Ltd.", "SDN listing entry 42.")
    plain = _add_signal(db_session, news_source, "某企业发布公告", "无地理与关键词线索。")
    matched = _add_signal(
        db_session, news_source, "鸿腾精密发布公告", "无地理与关键词线索。"
    )
    db_session.flush()

    # When: 采集后统一打分。
    scored = assign_signal_priorities(
        db_session, [listed.id, plain.id, matched.id], now_utc=NOW
    )

    # Then: 清单 10 / 普通 50 / 供应商命中 +20。
    assert scored == 3
    for signal in (listed, plain, matched):
        db_session.refresh(signal)
    assert listed.priority == 10
    assert plain.priority == 50
    assert matched.priority == 70
