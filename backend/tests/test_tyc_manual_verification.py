"""风险助手手动实时天眼查完整核查（清单内供应商）。

契约：
- 清单内供应商不再读历史替代实时：直接 ``await gateway.fetch_dimensions``，
  无 ``asyncio.run``、无跨线程共享 Session；
- 新证据入库（手动观察身份：同周重复实时核查不被周内幂等吞掉）后，只对该
  新信号 ``await analyze_raw_signal`` → ``process_analysis``，不调用全局队列；
- 真实执行 ``process_analysis`` 并证明正式告警写库；同周重复核查不产生
  第二条有效告警；
- 只有有效主体证据才允许持久化与解析：额度耗尽/停用/鉴权失败/网络失败/
  部分维度一律不落库、不解析，且不得声称「无风险」或「已完成」；
- AI 失败保留证据（待既有队列重试），返回明确未完成状态。

执行环境：Compose 隔离测试栈，真实 PostgreSQL；MCP 走确定性 stub
（``tyc_batch_support.MultidimMcpStub`` 的 MockTransport），零真实外网；
AI 走 ``FakeAIProvider``（不访问真实模型）。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pytest import MonkeyPatch
from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session
from tyc_batch_support import (
    CompanyStub,
    MultidimMcpStub,
    committed_tyc_daily_used,
    committed_tyc_rows,
    configure_committed_tyc,
    truncate_committed_tyc_usage,
)

import app.agent.tyc_gateway as tyc_gateway_module
import app.agent.tyc_quota as tyc_quota_module
import app.ai.service as ai_service_module
from app.agent.models import TycUsageRecord
from app.agent.tools import VerifyCompanyTool
from app.agent.tyc_analysis_context import (
    load_analysis_context,
    render_analysis_context,
)
from app.agent.tyc_manual_verification import verify_supplier_realtime
from app.ai.models import AIAnalysisRecord
from app.ai.providers import AIProviderError, FakeAIProvider
from app.ai.schemas import SignalAnalysisInput, SignalAnalysisResult
from app.database import engine
from app.risks.models import RiskAlert, RiskEvent, RiskEventSignal
from app.scheduler.jobs import pending_signal_candidate_select
from app.signals.models import DataSource, RawSignal
from app.suppliers.models import Supplier

_DIMS = ["get_risk_overview", "get_judicial_case"]
_ANCHOR = "search_companies"
_CODE = "SUP-MANUAL-001"
_NOW = datetime(2026, 5, 6, 9, 30, tzinfo=UTC)
_LATER = _NOW + timedelta(hours=2)


class _JudicialProvider(FakeAIProvider):
    """固定 judicial 分析结果：触发主体强匹配 → 强制 P1。"""

    def __init__(self) -> None:
        super().__init__(
            SignalAnalysisResult(
                event_type="judicial",
                event_subtype="judicial_case",
                suggested_severity="high",
                organizations=[],
                locations=[],
                affected_activities=["operations"],
                affected_products=[],
                summary_zh="手动核查：存在被执行记录",
                evidence_sentences=["报告中存在司法命中。"],
                confidence=0.9,
            )
        )


class _FailingProvider(FakeAIProvider):
    """恒定失败：验证证据保留、状态明确未完成。"""

    async def analyze_signal(self, value: SignalAnalysisInput) -> SignalAnalysisResult:
        raise AIProviderError("模型不可用（测试注入）")


def _fresh_source(session: Session) -> DataSource:
    session.expire_all()
    source = session.scalar(select(DataSource).where(DataSource.code == "tianyancha"))
    assert source is not None, "迁移应已注册 tianyancha 信息源"
    return source


def _supplier(session: Session, code: str = _CODE) -> Supplier:
    supplier = Supplier(
        supplier_code=code,
        legal_name=f"{code} 有限公司",
        country_code="CN",
        enabled=True,
    )
    session.add(supplier)
    session.flush()
    return supplier


def _signals(session: Session, code: str = _CODE) -> list[RawSignal]:
    return list(
        session.scalars(
            select(RawSignal)
            .where(RawSignal.external_id.like(f"tyc-{code}-%"))
            .order_by(RawSignal.id)
        )
    )


def _current_alerts(session: Session) -> list[RiskAlert]:
    return list(
        session.scalars(
            select(RiskAlert).where(RiskAlert.status == "current").order_by(RiskAlert.id)
        )
    )


def _prepare(
    session: Session,
    monkeypatch: MonkeyPatch,
    *,
    provider: FakeAIProvider | None = None,
    stub: MultidimMcpStub | None = None,
    dimensions: list[str] | None = None,
) -> tuple[Supplier, MultidimMcpStub]:
    dims = dimensions or _DIMS
    _fresh_source(session)
    supplier = _supplier(session)
    configure_committed_tyc(daily_limit=100, monthly_limit=1000, dimensions=dims)
    gateway_stub = stub or MultidimMcpStub()
    monkeypatch.setattr(
        tyc_gateway_module,
        "build_tyc_gateway",
        lambda **kwargs: gateway_stub.gateway(dims),
    )
    monkeypatch.setattr(
        ai_service_module,
        "get_ai_provider",
        lambda settings: provider or _JudicialProvider(),
    )
    return supplier, gateway_stub


@pytest.fixture(autouse=True)
def _isolate_usage() -> None:
    """跨连接计费行在用例前后清空，避免状态泄漏。"""
    yield
    truncate_committed_tyc_usage()


# ── 成功路径：真实 process_analysis 生成正式告警 ────────────────────────


def test_manual_verification_writes_signal_and_creates_formal_alert(
    db_session: Session, committed_tyc_env: None, monkeypatch: MonkeyPatch
) -> None:
    """Given 清单内启用供应商，When 手动完整核查，
    Then 新证据入库、真实 process_analysis 产出事件与正式告警，返回真实 ID。"""
    supplier, stub = _prepare(db_session, monkeypatch)

    result = asyncio.run(
        verify_supplier_realtime(db_session, supplier=supplier, now_utc=_NOW)
    )

    assert result.status == "completed"
    assert result.signal_id is not None
    assert result.event_id is not None
    assert result.alert_ids
    assert result.analysis_status == "succeeded"
    assert result.processing_status == "processed"

    signals = _signals(db_session)
    assert len(signals) == 1
    assert signals[0].id == result.signal_id
    assert signals[0].raw_data["supplier_code"] == _CODE

    event = db_session.get(RiskEvent, result.event_id)
    assert event is not None
    alerts = _current_alerts(db_session)
    assert [alert.id for alert in alerts] == list(result.alert_ids)
    assert alerts[0].level == "P1"
    assert {call for call, _tool in stub.calls} == {supplier.legal_name}


def test_manual_verification_returns_real_alert_ids_in_db(
    db_session: Session, committed_tyc_env: None, monkeypatch: MonkeyPatch
) -> None:
    """返回的 alert_ids 必须是数据库真实行，而非工具内部 mock 值。"""
    supplier, _stub = _prepare(db_session, monkeypatch)

    result = asyncio.run(
        verify_supplier_realtime(db_session, supplier=supplier, now_utc=_NOW)
    )

    for alert_id in result.alert_ids:
        alert = db_session.get(RiskAlert, alert_id)
        assert alert is not None
        assert alert.status == "current"
        assert alert.level == "P1"


# ── 同周重复实时核查：新证据不被吞掉，且不重复有效告警 ──────────────────


def test_repeat_same_week_keeps_new_evidence_without_duplicate_alert(
    db_session: Session, committed_tyc_env: None, monkeypatch: MonkeyPatch
) -> None:
    """同周两次实时核查：两次都产生独立证据行，事件与有效告警各只有一条。"""
    supplier, _stub = _prepare(db_session, monkeypatch)

    first = asyncio.run(
        verify_supplier_realtime(db_session, supplier=supplier, now_utc=_NOW)
    )
    second = asyncio.run(
        verify_supplier_realtime(db_session, supplier=supplier, now_utc=_LATER)
    )

    signals = _signals(db_session)
    assert [signal.id for signal in signals] == [first.signal_id, second.signal_id]
    assert len({signal.external_id for signal in signals}) == 2
    assert first.signal_id != second.signal_id
    assert first.external_id != second.external_id
    # 事件身份稳定（supplier_profile:<code>），告警不重复。
    assert second.event_id == first.event_id
    assert second.alert_ids == first.alert_ids
    assert len(_current_alerts(db_session)) == 1
    assert db_session.scalar(select(func.count()).select_from(RiskEvent)) == 1


# ── 无效证据路径：额度耗尽 / 停用 / 锚定失败，一律不落库不解析 ────────────


def test_revoked_key_writes_nothing_and_reports_not_configured(
    db_session: Session, committed_tyc_env: None, monkeypatch: MonkeyPatch
) -> None:
    """运行密钥撤销 → 执行器锁内返回 not_configured：不落库、不解析、零远程调用。"""
    supplier, stub = _prepare(db_session, monkeypatch)
    configure_committed_tyc(
        daily_limit=100, monthly_limit=1000, dimensions=_DIMS, api_key=None
    )

    result = asyncio.run(
        verify_supplier_realtime(db_session, supplier=supplier, now_utc=_NOW)
    )

    assert result.status == "not_configured"
    assert result.signal_id is None
    assert result.alert_ids == ()
    assert result.analysis_status == "skipped"
    assert result.processing_status == "skipped"
    assert stub.calls == []
    assert _signals(db_session) == []
    assert _current_alerts(db_session) == []
    assert committed_tyc_rows() == []


def test_quota_exhausted_before_anchor_writes_nothing(
    db_session: Session, committed_tyc_env: None, monkeypatch: MonkeyPatch
) -> None:
    """锚定前额度已耗尽：执行器锁内拒绝，零远程调用、无证据、无正式告警、无解析。"""
    supplier, stub = _prepare(db_session, monkeypatch)
    # 真提交一条已计费消费，执行器锁内重读后剩余额度为 0。
    with Session(engine) as seeded:
        seeded.add(
            TycUsageRecord(
                tool_name=_ANCHOR, company_name="预占额度", status="success"
            )
        )
        seeded.commit()
    configure_committed_tyc(daily_limit=1, monthly_limit=1000, dimensions=_DIMS)

    result = asyncio.run(
        verify_supplier_realtime(db_session, supplier=supplier, now_utc=_NOW)
    )

    assert result.status == "quota_exhausted"
    assert result.complete is False
    assert result.signal_id is None
    assert result.alert_ids == ()
    assert result.analysis_status == "skipped"
    assert result.processing_status == "skipped"
    assert result.usage is not None
    assert result.usage["daily_remaining"] == 0
    assert stub.calls == []
    assert _signals(db_session) == []
    assert _current_alerts(db_session) == []
    assert db_session.scalar(select(func.count()).select_from(RiskEvent)) == 0
    # 预占那条消费是唯一计费行：本次核查没有再产生记账。
    assert committed_tyc_rows() == [(_ANCHOR, "预占额度", "success")]


def test_quota_lock_busy_before_anchor_writes_nothing(
    db_session: Session, committed_tyc_env: None, monkeypatch: MonkeyPatch
) -> None:
    """额度锁被占用超时：结构化 busy，零远程调用、无证据、无正式告警、无解析。"""
    supplier, stub = _prepare(db_session, monkeypatch)
    monkeypatch.setattr(tyc_quota_module, "QUOTA_LOCK_TIMEOUT_MS", 100)

    holder = engine.connect()
    holder_transaction = holder.begin()
    try:
        holder.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": tyc_quota_module.QUOTA_ADVISORY_LOCK_KEY},
        )
        result = asyncio.run(
            verify_supplier_realtime(db_session, supplier=supplier, now_utc=_NOW)
        )
    finally:
        holder_transaction.rollback()
        holder.close()

    assert result.status == "busy"
    assert result.complete is False
    assert result.signal_id is None
    assert result.alert_ids == ()
    assert result.analysis_status == "skipped"
    assert result.processing_status == "skipped"
    assert stub.calls == []
    assert _signals(db_session) == []
    assert _current_alerts(db_session) == []
    assert db_session.scalar(select(func.count()).select_from(RiskEvent)) == 0
    assert committed_tyc_rows() == []


def test_anchor_failure_writes_nothing(
    db_session: Session, committed_tyc_env: None, monkeypatch: MonkeyPatch
) -> None:
    """主体锚定远程失败（无有效主体证据）：不持久化、不解析。"""
    name = f"{_CODE} 有限公司"
    stub = MultidimMcpStub(per_company={name: CompanyStub(anchor_error=True)})
    supplier, _ = _prepare(db_session, monkeypatch, stub=stub)

    result = asyncio.run(
        verify_supplier_realtime(db_session, supplier=supplier, now_utc=_NOW)
    )

    assert result.status == "error"
    assert result.signal_id is None
    assert result.alert_ids == ()
    assert _signals(db_session) == []
    assert db_session.scalar(select(func.count()).select_from(RiskAlert)) == 0


def test_partial_dimension_result_is_explicit(
    db_session: Session, committed_tyc_env: None, monkeypatch: MonkeyPatch
) -> None:
    """部分维度额度耗尽：显式 partial 维度结果，不得当作完整核查。"""
    configure_committed_tyc(daily_limit=1, monthly_limit=50, dimensions=_DIMS)
    supplier = _supplier(db_session)
    stub = MultidimMcpStub()
    _fresh_source(db_session)
    monkeypatch.setattr(
        tyc_gateway_module, "build_tyc_gateway", lambda **kwargs: stub.gateway(_DIMS)
    )
    monkeypatch.setattr(
        ai_service_module, "get_ai_provider", lambda settings: _JudicialProvider()
    )

    result = asyncio.run(
        verify_supplier_realtime(db_session, supplier=supplier, now_utc=_NOW)
    )

    assert result.status == "partial"
    # 日额度 1 被锚定用尽：两个维度都未取数，必须逐个显式列为未完成。
    assert result.incomplete_dimensions == tuple(_DIMS)
    # 有效主体证据仍然入库并解析：已执行维度的证据不丢弃。
    assert result.signal_id is not None
    assert result.analysis_status == "succeeded"
    assert result.processing_status == "processed"
    assert result.complete is False
    assert committed_tyc_rows() == [(_ANCHOR, supplier.legal_name, "success")]


# ── AI 失败：保留证据待既有队列重试 ─────────────────────────────────────


def test_ai_failure_keeps_signal_for_queue_retry(
    db_session: Session, committed_tyc_env: None, monkeypatch: MonkeyPatch
) -> None:
    """AI 失败：证据已入库且保持可被既有队列捞取，状态为未完成而非无风险。"""
    supplier, _stub = _prepare(db_session, monkeypatch, provider=_FailingProvider())

    result = asyncio.run(
        verify_supplier_realtime(db_session, supplier=supplier, now_utc=_NOW)
    )

    assert result.status == "analysis_failed"
    assert result.signal_id is not None
    assert result.alert_ids == ()
    assert result.analysis_status == "failed"
    assert result.processing_status == "skipped"

    candidates = {
        signal_id
        for signal_id, _code in db_session.execute(
            pending_signal_candidate_select(now_utc=_NOW + timedelta(minutes=5))
        )
    }
    assert result.signal_id in candidates
    assert _current_alerts(db_session) == []


def test_ai_failure_evidence_survives_caller_rollback(
    committed_tyc_env: None, monkeypatch: MonkeyPatch
) -> None:
    """证据持久性不依赖聊天会话的最终提交。

    ``analyze_raw_signal`` 在 await provider 之前提交记录、失败分支再次提交
    （``app/ai/service.py``），因此调用方随后回滚自己的事务也不会带走已入库的
    证据：独立连接仍能读到该信号，且它仍在既有待处理队列的候选集中。

    本用例需要真实提交语义，故不使用事务回滚型 ``db_session``，改用独立 Session
    并在 finally 中按 supplier_code 清理本次写入的行。
    """
    code = f"SUP-MANUAL-DURABLE-{uuid4().hex[:8]}"
    dims = _DIMS
    configure_committed_tyc(daily_limit=100, monthly_limit=1000, dimensions=dims)
    stub = MultidimMcpStub()
    monkeypatch.setattr(
        tyc_gateway_module, "build_tyc_gateway", lambda **kwargs: stub.gateway(dims)
    )
    monkeypatch.setattr(
        ai_service_module, "get_ai_provider", lambda settings: _FailingProvider()
    )

    try:
        with Session(engine) as setup:
            setup.add(
                Supplier(
                    supplier_code=code,
                    legal_name=f"{code} 有限公司",
                    country_code="CN",
                    enabled=True,
                )
            )
            setup.commit()
            supplier = setup.scalar(select(Supplier).where(Supplier.supplier_code == code))
        assert supplier is not None

        with Session(engine) as caller:
            result = asyncio.run(
                verify_supplier_realtime(caller, supplier=supplier, now_utc=_NOW)
            )
            assert result.status == "analysis_failed"
            assert result.signal_id is not None
            signal_id = result.signal_id
            caller.rollback()

        with Session(engine) as probe:
            persisted = probe.get(RawSignal, signal_id)
            assert persisted is not None
            assert persisted.validity_state == "active"
            candidates = {
                found
                for found, _code in probe.execute(
                    pending_signal_candidate_select(now_utc=_NOW + timedelta(minutes=5))
                )
            }
            assert signal_id in candidates
            # AI 失败未进入事件链，因此该信号没有任何事件证据关联，更不会产出告警。
            linked_events = probe.scalar(
                select(func.count())
                .select_from(RiskEventSignal)
                .where(RiskEventSignal.signal_id == signal_id)
            )
            assert linked_events == 0

        with Session(engine) as cleanup:
            cleanup.execute(delete(AIAnalysisRecord).where(AIAnalysisRecord.signal_id == signal_id))
            cleanup.execute(
                delete(RawSignal).where(RawSignal.external_id.like(f"tyc-{code}-%"))
            )
            cleanup.execute(delete(Supplier).where(Supplier.supplier_code == code))
            cleanup.commit()
    finally:
        truncate_committed_tyc_usage()


# ── 工具层集成：清单内走实时完整核查 ────────────────────────────────────


def test_verify_company_tool_routes_registered_supplier_to_live_check(
    db_session: Session, committed_tyc_env: None, monkeypatch: MonkeyPatch
) -> None:
    """Given 清单内供应商（即使已有历史报告），When 调用 verify_company，
    Then 走实时完整核查并产生新的 signal_id，而非回读历史替代。"""
    supplier, _stub = _prepare(db_session, monkeypatch)
    historical = RawSignal(
        source_id=_fresh_source(db_session).id,
        external_id=f"tyc-{_CODE}-2026-W19-historical",
        title="历史周报信号",
        content="历史内容",
        fingerprint="historical-fingerprint-manual-001",
        raw_data={"report_kind": "supplier_profile"},
    )
    db_session.add(historical)
    db_session.flush()

    result = asyncio.run(
        VerifyCompanyTool().execute({"company_name": supplier.legal_name}, db_session)
    )

    assert result["status"] == "success"
    assert result["verification_status"] == "completed"
    assert result["source"] == "tianyancha_realtime"
    assert result["signal_id"] is not None
    assert result["signal_id"] != historical.id
    assert result["analysis_status"] == "succeeded"
    assert result["processing_status"] == "processed"
    assert result["alert_ids"]


class _CapturingJudicialProvider(_JudicialProvider):
    """记录每次 analyze_signal 的输入：证明画像上下文只经一次 LLM 调用进入正文。"""

    def __init__(self) -> None:
        super().__init__()
        self.inputs: list[SignalAnalysisInput] = []

    async def analyze_signal(self, value: SignalAnalysisInput) -> SignalAnalysisResult:
        self.inputs.append(value)
        return await super().analyze_signal(value)


def test_manual_verification_stores_analysis_context_and_sends_it_to_llm(
    db_session: Session, committed_tyc_env: None, monkeypatch: MonkeyPatch
) -> None:
    """手动入口：新证据带私有上下文，且该上下文就是唯一一次 LLM 调用的正文。"""
    provider = _CapturingJudicialProvider()
    supplier, _stub = _prepare(db_session, monkeypatch, provider=provider)

    result = asyncio.run(
        verify_supplier_realtime(db_session, supplier=supplier, now_utc=_NOW)
    )

    signal = db_session.get(RawSignal, result.signal_id)
    assert signal is not None
    context = load_analysis_context(signal.raw_data)
    assert context is not None
    assert context.company_name == supplier.legal_name
    assert [item.key for item in context.dimensions] == [
        "get_risk_overview",
        "get_judicial_case",
    ]
    # 根报告既有字段不变：私有上下文只是附加键。
    assert signal.raw_data["report_kind"] == "supplier_profile"
    assert signal.raw_data["supplier_code"] == _CODE
    assert len(provider.inputs) == 1
    assert provider.inputs[0].content == render_analysis_context(context)


def test_verify_company_tool_keeps_out_of_list_path_readonly(
    db_session: Session, committed_tyc_env: None, monkeypatch: MonkeyPatch
) -> None:
    """清单外企业保留原有一次性核查：不创建供应商、不落库、不宣称正式告警。"""
    _prepare(db_session, monkeypatch)
    out_of_list_gateway = MultidimMcpStub().gateway()

    result = asyncio.run(
        VerifyCompanyTool(gateway=out_of_list_gateway).execute(
            {"company_name": "清单外示例有限公司"}, db_session
        )
    )

    assert result["status"] == "success"
    assert "alert_ids" not in result
    assert db_session.scalar(select(func.count()).select_from(Supplier)) == 1
    assert db_session.scalar(select(func.count()).select_from(RawSignal)) == 0
    assert db_session.scalar(select(func.count()).select_from(RiskAlert)) == 0


def test_manual_verification_rejects_disabled_supplier(
    db_session: Session, committed_tyc_env: None, monkeypatch: MonkeyPatch
) -> None:
    """停用供应商不是有效核查对象：显式拒绝且零远程调用。"""
    supplier = Supplier(
        supplier_code="SUP-MANUAL-DISABLED",
        legal_name="停用的手动核查供应商",
        country_code="CN",
        enabled=False,
    )
    db_session.add(supplier)
    db_session.flush()
    stub = MultidimMcpStub()
    _fresh_source(db_session)
    monkeypatch.setattr(
        tyc_gateway_module, "build_tyc_gateway", lambda **kwargs: stub.gateway(_DIMS)
    )

    with pytest.raises(ValueError, match="启用"):
        asyncio.run(verify_supplier_realtime(db_session, supplier=supplier, now_utc=_NOW))

    assert stub.calls == []
    assert committed_tyc_daily_used() == 0


def test_manual_verification_does_not_run_global_pending_queue(
    db_session: Session, committed_tyc_env: None, monkeypatch: MonkeyPatch
) -> None:
    """手动路径只处理自己新建的信号，绝不调用全局待处理队列。"""
    supplier, _stub = _prepare(db_session, monkeypatch)
    import app.scheduler.jobs as jobs_module

    def _forbidden(*args: object, **kwargs: object) -> int:
        raise AssertionError("手动核查不得调用全局待处理队列")

    monkeypatch.setattr(jobs_module, "_process_pending_signals", _forbidden)

    result = asyncio.run(
        verify_supplier_realtime(db_session, supplier=supplier, now_utc=_NOW)
    )

    assert result.status == "completed"
    assert result.alert_ids
    # 只产生本供应商一条证据的解析记录，不批量处理其他信号。
    assert db_session.scalar(
        select(func.count()).select_from(TycUsageRecord).where(
            TycUsageRecord.tool_name == _ANCHOR
        )
    ) == 1