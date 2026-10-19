"""情报聚合端到端：分类归属、生成（含 Fake Provider）、失败落库、幂等与列表过滤。

锁定的行为：
- 分类归属 = 声明该信源的 policy / industry 维度键，且**不受维度 enabled 影响**
  （policy 默认停用，但政策情报必须能生成）。
- 同一 (event_id, category) 只生成一次；调度批量入口重复执行不产生第二行。
- 行情类事件（market / exchange_rate）不走情报通道：即使信源归属 policy / industry
  也不生成报告（由确定性行情链路处理），同来源的非行情事件照常生成。
- 模型失败落 failed 行 + error，不抛出、不污染整批。
- 列表接口按 tab 过滤，只返回 ready 行；详情接口可读到 failed 行的错误原因。
"""

from __future__ import annotations

import asyncio
from contextlib import nullcontext
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from pytest import MonkeyPatch
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.intelligence_providers import FakeIntelligenceProvider
from app.ai.intelligence_schemas import INTELLIGENCE_PROMPT_VERSION
from app.ai.providers import AIProviderError
from app.ai.schemas import MARKET_EVENT_TYPES
from app.risks import intelligence_batch, intelligence_service
from app.risks.intelligence_models import IntelligenceReport
from app.risks.models import RiskEvent, RiskEventSignal
from app.signals.models import DataSource, RawSignal

NOW_UTC = datetime(2026, 10, 10, 12, tzinfo=UTC)

# 维度声明中真实存在的政策/行业信源 code（policy.py / industry.py）。
POLICY_SOURCE_CODE = "customs-announcement"
INDUSTRY_SOURCE_CODE = "sse-shipping"
# 其它维度的信源：不得进入情报通道。
OTHER_SOURCE_CODE = "nmc-weather"


def _source(session: Session, code: str) -> DataSource:
    existing = session.scalar(select(DataSource).where(DataSource.code == code))
    if existing is not None:
        return existing
    source = DataSource(
        code=code,
        name=f"测试信源 {code}",
        source_type="api",
        credibility=90,
        enabled=True,
        adapter_status="builtin",
        adapter_version=0,
        auth_type="none",
        login_config={},
        adapter_config={},
    )
    session.add(source)
    session.flush()
    return source


def _event(
    session: Session,
    source_code: str,
    suffix: str,
    *,
    event_type: str = "trade_policy",
    event_subtype: str | None = "regulatory_change",
) -> RiskEvent:
    """造一个已归并的风险事件及其唯一关联信号（不经信号解析链）。"""
    source = _source(session, source_code)
    signal = RawSignal(
        source_id=source.id,
        external_id=f"{source_code}-{suffix}",
        title=f"情报标题 {suffix}",
        content=f"{source_code} 公告正文 {suffix}：涉及出口许可与合规义务。",
        url=f"https://example.com/{source_code}/{suffix}",
        published_at=NOW_UTC,
        collected_at=NOW_UTC,
        fingerprint=f"intelligence-{source_code}-{suffix}",
        raw_data={},
        validity_state="pending_classification",
        validity_reason={
            "code": "pending_classification",
            "anchor_source": "published_at",
            "details": {},
        },
    )
    event = RiskEvent(
        dedup_key=f"intelligence-{source_code}-{suffix}",
        event_type=event_type,
        event_subtype=event_subtype,
        severity="medium",
        summary=f"事件摘要 {suffix}",
        confidence=0.8,
        facts={},
    )
    session.add_all([signal, event])
    session.flush()
    session.add(RiskEventSignal(event_id=event.id, signal_id=signal.id))
    session.flush()
    return event


def _use_fake_provider(monkeypatch: MonkeyPatch) -> FakeIntelligenceProvider:
    """批量入口自行构建 Provider；测试在这里注入离线确定性实现。"""
    provider = FakeIntelligenceProvider()
    monkeypatch.setenv("AI_PROVIDER", "fake")
    monkeypatch.setattr(
        intelligence_batch, "build_intelligence_provider", lambda _settings: provider
    )
    return provider


def _use_test_session(monkeypatch: MonkeyPatch, db_session: Session) -> None:
    """让批量入口复用测试会话（其数据写在未提交事务里，默认 SessionLocal 看不到）。"""
    monkeypatch.setattr(
        intelligence_batch, "SessionLocal", lambda: nullcontext(db_session)
    )


def test_category_comes_from_owning_dimension_regardless_of_enabled_flag() -> None:
    # policy 维度声明 enabled=False；情报通道仍必须把它解析为 policy。
    mapping = intelligence_service.intelligence_source_categories()

    assert mapping[POLICY_SOURCE_CODE] == "policy"
    assert mapping[INDUSTRY_SOURCE_CODE] == "industry"
    assert OTHER_SOURCE_CODE not in mapping
    assert intelligence_service.resolve_intelligence_category({POLICY_SOURCE_CODE}) == "policy"
    # 跨分类信源集合视为归属冲突，不猜测。
    assert (
        intelligence_service.resolve_intelligence_category(
            {POLICY_SOURCE_CODE, INDUSTRY_SOURCE_CODE}
        )
        is None
    )
    assert intelligence_service.resolve_intelligence_category({"unknown"}) is None


def test_generation_persists_report_and_suggestions(
    db_session: Session, monkeypatch: MonkeyPatch
) -> None:
    # Given：一条政策维度事件
    _use_fake_provider(monkeypatch)
    event = _event(db_session, POLICY_SOURCE_CODE, "gen")

    # When：生成情报报告
    report = asyncio.run(
        intelligence_service.generate_intelligence_report(
            db_session, event, "policy", provider=FakeIntelligenceProvider()
        )
    )

    # Then：落库 ready 行，携带报告、建议、模型与提示词版本
    assert report is not None
    assert report.status == "ready"
    assert report.category == "policy"
    assert report.report_zh
    assert 1 <= len(report.suggestions) <= 3
    assert report.prompt_version == INTELLIGENCE_PROMPT_VERSION
    assert report.generated_at is not None
    assert report.error is None
    stored = db_session.scalar(
        select(IntelligenceReport).where(
            IntelligenceReport.event_id == event.id,
            IntelligenceReport.category == "policy",
        )
    )
    assert stored is not None


def test_provider_failure_is_recorded_as_failed(
    db_session: Session, monkeypatch: MonkeyPatch
) -> None:
    # Given：始终失败的 Provider
    class FailingProvider(FakeIntelligenceProvider):
        async def generate_intelligence(self, value):  # type: ignore[override]
            raise AIProviderError("模型不可用")

    event = _event(db_session, INDUSTRY_SOURCE_CODE, "fail")

    # When：生成情报报告
    report = asyncio.run(
        intelligence_service.generate_intelligence_report(
            db_session, event, "industry", provider=FailingProvider()
        )
    )

    # Then：落 failed 行并记录原因，不向上抛出
    assert report is not None
    assert report.status == "failed"
    assert report.error == "模型不可用"
    assert report.generated_at is None
    assert report.suggestions == []


def test_batch_generation_is_idempotent_and_skips_other_dimensions(
    db_session: Session, monkeypatch: MonkeyPatch
) -> None:
    # Given：政策、行业各一事件，外加一个非情报维度的天气事件
    _use_fake_provider(monkeypatch)
    policy_event = _event(db_session, POLICY_SOURCE_CODE, "idem-policy")
    industry_event = _event(db_session, INDUSTRY_SOURCE_CODE, "idem-industry")
    _event(db_session, OTHER_SOURCE_CODE, "idem-other")
    _use_test_session(monkeypatch, db_session)

    # When：连续执行两轮批量生成
    first = intelligence_batch.generate_pending_intelligence_reports(now_utc=NOW_UTC)
    second = intelligence_batch.generate_pending_intelligence_reports(now_utc=NOW_UTC)

    # Then：两轮各只生成政策/行业两条，重复执行不新增行
    assert first == 2
    assert second == 0
    assert db_session.scalar(select(func.count()).select_from(IntelligenceReport)) == 2
    categories = set(db_session.scalars(select(IntelligenceReport.category)))
    assert categories == {"policy", "industry"}
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(IntelligenceReport)
            .where(IntelligenceReport.event_id.in_([policy_event.id, industry_event.id]))
        )
        == 2
    )


def test_market_event_is_filtered_out_at_sql_coarse_select(
    db_session: Session,
) -> None:
    # Given：同一行业信源下的 market / exchange_rate / logistics 三类事件
    market_event = _event(
        db_session,
        INDUSTRY_SOURCE_CODE,
        "sql-market",
        event_type="market",
        event_subtype="commodity_price_move",
    )
    fx_event = _event(
        db_session,
        INDUSTRY_SOURCE_CODE,
        "sql-fx",
        event_type="exchange_rate",
        event_subtype="fx_volatility",
    )
    logistics_event = _event(
        db_session,
        INDUSTRY_SOURCE_CODE,
        "sql-logistics",
        event_type="logistics",
        event_subtype="transport_disruption",
    )

    # When：直接执行 SQL 粗筛（排除行情必须发生在粗筛层，不能只靠 Python 兜底）
    coarse_event_ids = {
        event_id for event_id, _ in db_session.execute(
            intelligence_batch._candidate_event_select()
        ).all()
    }

    # Then：行情事件不进粗筛，同来源的 logistics 事件照常入选
    assert market_event.event_type in MARKET_EVENT_TYPES
    assert fx_event.event_type in MARKET_EVENT_TYPES
    assert market_event.id not in coarse_event_ids
    assert fx_event.id not in coarse_event_ids
    assert logistics_event.id in coarse_event_ids


def test_market_event_never_generates_report_but_logistics_still_does(
    db_session: Session, monkeypatch: MonkeyPatch
) -> None:
    # Given：同一行业信源下的 market 事件与 logistics 事件
    _use_fake_provider(monkeypatch)
    market_event = _event(
        db_session,
        INDUSTRY_SOURCE_CODE,
        "gen-market",
        event_type="market",
        event_subtype="commodity_price_move",
    )
    logistics_event = _event(
        db_session,
        INDUSTRY_SOURCE_CODE,
        "gen-logistics",
        event_type="logistics",
        event_subtype="transport_disruption",
    )
    _use_test_session(monkeypatch, db_session)

    # When / Then：待生成集合只含 logistics 事件
    pending = intelligence_batch.pending_intelligence_events(db_session, now_utc=NOW_UTC)
    assert [event.id for event, _ in pending] == [logistics_event.id]

    # When：连续执行两轮批量生成
    first = intelligence_batch.generate_pending_intelligence_reports(now_utc=NOW_UTC)
    second = intelligence_batch.generate_pending_intelligence_reports(now_utc=NOW_UTC)

    # Then：行情事件零报告，logistics 事件正常生成且幂等
    assert first == 1
    assert second == 0
    assert db_session.scalar(select(func.count()).select_from(IntelligenceReport)) == 1
    assert (
        db_session.scalar(
            select(IntelligenceReport.event_id).where(
                IntelligenceReport.event_id.in_(
                    [market_event.id, logistics_event.id]
                )
            )
        )
        == logistics_event.id
    )


def test_failed_report_is_not_retried_before_cooldown(
    db_session: Session, monkeypatch: MonkeyPatch
) -> None:
    # Given：24 小时内刚失败的情报报告
    event = _event(db_session, POLICY_SOURCE_CODE, "cooldown")
    db_session.add(
        IntelligenceReport(
            event_id=event.id,
            category="policy",
            provider="fake",
            model="fake-deterministic-v1",
            prompt_version=INTELLIGENCE_PROMPT_VERSION,
            status="failed",
            suggestions=[],
            error="模型不可用",
            generated_at=None,
            created_at=NOW_UTC,
            updated_at=NOW_UTC,
        )
    )
    db_session.flush()
    _use_fake_provider(monkeypatch)
    _use_test_session(monkeypatch, db_session)

    # When：冷却期内批量生成
    generated = intelligence_batch.generate_pending_intelligence_reports(now_utc=NOW_UTC)

    # Then：不重复消耗额度
    assert generated == 0
    assert (
        db_session.scalar(
            select(IntelligenceReport.status).where(
                IntelligenceReport.event_id == event.id
            )
        )
        == "failed"
    )


def test_list_endpoint_filters_by_tab_and_skips_failed(
    client: TestClient, db_session: Session
) -> None:
    policy_event = _event(db_session, POLICY_SOURCE_CODE, "list-policy")
    industry_event = _event(db_session, INDUSTRY_SOURCE_CODE, "list-industry")
    failed_event = _event(db_session, POLICY_SOURCE_CODE, "list-failed")
    db_session.add_all(
        [
            IntelligenceReport(
                event_id=policy_event.id,
                category="policy",
                report_zh="政策报告正文",
                suggestions=["建议一", "建议二"],
                provider="fake",
                model="fake-deterministic-v1",
                prompt_version=INTELLIGENCE_PROMPT_VERSION,
                status="ready",
                generated_at=NOW_UTC,
            ),
            IntelligenceReport(
                event_id=industry_event.id,
                category="industry",
                report_zh="行业报告正文",
                suggestions=["行业建议"],
                provider="fake",
                model="fake-deterministic-v1",
                prompt_version=INTELLIGENCE_PROMPT_VERSION,
                status="ready",
                generated_at=NOW_UTC,
            ),
            IntelligenceReport(
                event_id=failed_event.id,
                category="policy",
                provider="fake",
                model="fake-deterministic-v1",
                prompt_version=INTELLIGENCE_PROMPT_VERSION,
                status="failed",
                suggestions=[],
                error="模型不可用",
            ),
        ]
    )
    db_session.commit()

    policy = client.get("/api/v1/intelligence", params={"tab": "policy"})
    industry = client.get("/api/v1/intelligence", params={"tab": "industry"})
    invalid = client.get("/api/v1/intelligence", params={"tab": "corporate"})

    assert policy.status_code == 200
    assert industry.status_code == 200
    assert invalid.status_code == 422
    assert policy.json()["total"] == 1
    assert [item["category"] for item in policy.json()["items"]] == ["policy"]
    policy_item = policy.json()["items"][0]
    assert policy_item["report_zh"] == "政策报告正文"
    assert policy_item["suggestions"] == ["建议一", "建议二"]
    assert policy_item["source_code"] == POLICY_SOURCE_CODE
    assert policy_item["source_name"] == f"测试信源 {POLICY_SOURCE_CODE}"
    assert policy_item["title"] == "情报标题 list-policy"
    assert policy_item["event_summary"] == "事件摘要 list-policy"
    assert policy_item["generated_at"] is not None
    assert industry.json()["total"] == 1
    assert industry.json()["items"][0]["source_code"] == INDUSTRY_SOURCE_CODE


def test_pagination_slices_ready_reports(
    client: TestClient, db_session: Session
) -> None:
    for index in range(3):
        event = _event(db_session, POLICY_SOURCE_CODE, f"page-{index}")
        db_session.add(
            IntelligenceReport(
                event_id=event.id,
                category="policy",
                report_zh=f"报告 {index}",
                suggestions=[],
                provider="fake",
                model="fake-deterministic-v1",
                prompt_version=INTELLIGENCE_PROMPT_VERSION,
                status="ready",
                generated_at=NOW_UTC,
            )
        )
    db_session.commit()

    first_page = client.get(
        "/api/v1/intelligence", params={"tab": "policy", "limit": 2, "offset": 0}
    )
    second_page = client.get(
        "/api/v1/intelligence", params={"tab": "policy", "limit": 2, "offset": 2}
    )

    assert first_page.status_code == second_page.status_code == 200
    assert first_page.json()["total"] == 3
    assert len(first_page.json()["items"]) == 2
    assert len(second_page.json()["items"]) == 1


def test_detail_endpoint_returns_report_and_missing_id_is_404(
    client: TestClient, db_session: Session
) -> None:
    event = _event(db_session, POLICY_SOURCE_CODE, "detail")
    report = IntelligenceReport(
        event_id=event.id,
        category="policy",
        provider="fake",
        model="fake-deterministic-v1",
        prompt_version=INTELLIGENCE_PROMPT_VERSION,
        status="failed",
        suggestions=[],
        error="模型不可用",
    )
    db_session.add(report)
    db_session.commit()
    db_session.refresh(report)

    detail = client.get(f"/api/v1/intelligence/{report.id}")
    missing = client.get("/api/v1/intelligence/999999")

    assert detail.status_code == 200
    assert detail.json()["status"] == "failed"
    assert detail.json()["error"] == "模型不可用"
    assert missing.status_code == 404