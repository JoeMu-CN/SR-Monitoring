"""任务6：保留清理证据闭包与共享记录安全的回归测试。

覆盖计划要求的全部行为：
- 无限有效提醒（current unbounded）与其完整证据链受保护。
- expired 提醒按 expires_at/updated_at 锚点在 90 天窗口边界解除保护。
- 共享 match（多 alert 同一 match）与共享 event（多 supplier 同一 event）只清理
  到期且无剩余引用的部分，不得级联误删。
- 研究溯源（research_claims.promoted_signal_id）引用的信号受保护。
- 被保护信号的全部分析记录（AIAnalysisRecord）受保护；独立分析删除不得绕过。
- 异常 NULL 锚点保留并记入诊断，不被误删。
- 清理重复运行幂等；删除中途异常整轮回滚（无局部提交）。
- 清理与新增关联并发：真实双 Session 证明，任一成功结局不损失有效证据。

本模块以 ``cleanup_retention(session, settings, now_utc=...)`` 接口编写，
返回携带 ``protected_skipped`` / ``anomaly_anchor_skipped`` 的结果；
RED 证据见 run ``20260909T071852141Z-c1a875e3``（43 用例 13 失败）。
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from psycopg import errors as psycopg_errors
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.ai.models import AIAnalysisRecord
from app.auth.models import User
from app.config import RetentionSettings
from app.database import SessionLocal
from app.research.models import ResearchClaim, ResearchTask
from app.risks.models import (
    RiskAlert,
    RiskEvent,
    RiskEventSignal,
    SupplierEventMatch,
)
from app.scheduler import retention as retention_module
from app.scheduler.retention import (
    SERIALIZABLE_RETRY_LIMIT,
    CleanupResult,
    cleanup_retention,
)
from app.scheduler.retention_queries import alert_removal_anchor
from app.signals.models import DataSource, RawSignal, SourceMemberState
from app.suppliers.models import Supplier

NOW = datetime(2026, 9, 8, 12, tzinfo=UTC)
SETTINGS = RetentionSettings(signal_days=90, event_days=90, run_days=30)


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:10]}"


def _source(session: Session, code: str) -> DataSource:
    source = DataSource(
        code=code,
        name="保留闭包测试源",
        source_type="api",
        credibility=80,
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


def _supplier(session: Session, code: str) -> Supplier:
    supplier = Supplier(
        supplier_code=code,
        legal_name=f"保留闭包{code}",
        country_code="CN",
    )
    session.add(supplier)
    session.flush()
    return supplier


def _chain(
    session: Session,
    *,
    source_id: int,
    supplier_id: int,
    tag: str,
    signal_collected_at: datetime,
    alert_status: str,
    alert_expires_at: datetime | None,
    alert_expiry_kind: str,
    alert_updated_at: datetime | None = None,
    alert_created_at: datetime | None = None,
    with_analysis: bool = False,
) -> tuple[RawSignal, RiskEvent, SupplierEventMatch, RiskAlert]:
    """创建 signal→event→match→alert 完整证据链并返回各实体。"""
    signal = RawSignal(
        source_id=source_id,
        external_id=tag,
        title=f"信号{tag}",
        content=f"内容{tag}",
        published_at=signal_collected_at,
        collected_at=signal_collected_at,
        fingerprint=f"retention-evidence-{tag}",
        raw_data={},
    )
    session.add(signal)
    session.flush()
    event = RiskEvent(
        dedup_key=f"retention-evidence-{tag}",
        event_type="compliance",
        severity="high",
        summary=f"事件{tag}",
        start_at=signal_collected_at,
        end_at=signal_collected_at,
        confidence=0.9,
        facts={},
    )
    session.add(event)
    session.flush()
    session.add(RiskEventSignal(event_id=event.id, signal_id=signal.id))
    match = SupplierEventMatch(
        supplier_id=supplier_id,
        event_id=event.id,
        match_type="legal_name",
        score=70,
        reasons=["测试"],
        evidence=[],
    )
    session.add(match)
    session.flush()
    alert = RiskAlert(
        match_id=match.id,
        level="P1",
        score=85,
        score_detail={},
        status=alert_status,
        expires_at=alert_expires_at,
        expiry_kind=alert_expiry_kind,
    )
    if alert_updated_at is not None:
        alert.updated_at = alert_updated_at
    if alert_created_at is not None:
        alert.created_at = alert_created_at
    session.add(alert)
    session.flush()
    if with_analysis:
        session.add(
            AIAnalysisRecord(
                signal_id=signal.id,
                provider="fake",
                model="fake-test",
                prompt_version="v1",
                status="succeeded",
                started_at=signal_collected_at,
                finished_at=signal_collected_at,
            )
        )
        session.flush()
    return signal, event, match, alert


def _exists(session: Session, model: type, row_id: int) -> bool:
    return session.get(model, row_id) is not None


# ── A. 无限有效提醒决定完整证据链保留 ──────────────────────────────


def test_unbounded_current_alert_chain_is_fully_retained(db_session: Session) -> None:
    # Given：91 天旧信号支撑一条无限有效的 current 提醒（含一条成功分析）。
    tag = _unique("unbounded")
    source = _source(db_session, f"ret-src-{tag}")
    supplier = _supplier(db_session, f"RET-{tag}")
    old = NOW - timedelta(days=91)
    signal, event, match, alert = _chain(
        db_session,
        source_id=source.id,
        supplier_id=supplier.id,
        tag=tag,
        signal_collected_at=old,
        alert_status="current",
        alert_expires_at=None,
        alert_expiry_kind="unbounded",
        with_analysis=True,
    )
    db_session.commit()

    # When：冻结时钟清理。
    result = cleanup_retention(db_session, SETTINGS, now_utc=NOW)

    # Then：完整闭包全部保留，分析不受独立删除绕过。
    assert _exists(db_session, RiskAlert, alert.id)
    assert _exists(db_session, SupplierEventMatch, match.id)
    assert _exists(db_session, RiskEvent, event.id)
    assert _exists(db_session, RawSignal, signal.id)
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(RiskEventSignal)
            .where(
                RiskEventSignal.event_id == event.id,
                RiskEventSignal.signal_id == signal.id,
            )
        )
        == 1
    )
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(AIAnalysisRecord)
            .where(AIAnalysisRecord.signal_id == signal.id)
        )
        == 1
    )
    assert result.protected_skipped >= 1


# ── B. expired 90 天保留窗口边界 ───────────────────────────────────


def test_expired_alert_within_window_keeps_evidence(db_session: Session) -> None:
    # Given：提醒已失效 89 天（窗口内），其信号已 91 天。
    tag = _unique("within")
    source = _source(db_session, f"ret-src-{tag}")
    supplier = _supplier(db_session, f"RET-{tag}")
    signal, event, match, alert = _chain(
        db_session,
        source_id=source.id,
        supplier_id=supplier.id,
        tag=tag,
        signal_collected_at=NOW - timedelta(days=91),
        alert_status="expired",
        alert_expires_at=NOW - timedelta(days=89),
        alert_expiry_kind="finite",
        alert_updated_at=NOW - timedelta(days=89),
        with_analysis=True,
    )
    db_session.commit()

    # When
    result = cleanup_retention(db_session, SETTINGS, now_utc=NOW)

    # Then：提醒仍在保留窗口内，闭包整体保留。
    assert _exists(db_session, RiskAlert, alert.id)
    assert _exists(db_session, SupplierEventMatch, match.id)
    assert _exists(db_session, RiskEvent, event.id)
    assert _exists(db_session, RawSignal, signal.id)
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(AIAnalysisRecord)
            .where(AIAnalysisRecord.signal_id == signal.id)
        )
        == 1
    )
    assert result.expired_alerts == 0


def test_expired_alert_just_past_window_releases_chain(db_session: Session) -> None:
    # Given：提醒已失效 91 天（窗口外），单链无其他引用。
    tag = _unique("past")
    source = _source(db_session, f"ret-src-{tag}")
    supplier = _supplier(db_session, f"RET-{tag}")
    signal, event, match, alert = _chain(
        db_session,
        source_id=source.id,
        supplier_id=supplier.id,
        tag=tag,
        signal_collected_at=NOW - timedelta(days=91),
        alert_status="expired",
        alert_expires_at=NOW - timedelta(days=91),
        alert_expiry_kind="finite",
        alert_updated_at=NOW - timedelta(days=91),
        with_analysis=True,
    )
    db_session.commit()

    # When
    result = cleanup_retention(db_session, SETTINGS, now_utc=NOW)

    # Then：到期且无剩余引用的链整体解除并清理。
    assert not _exists(db_session, RiskAlert, alert.id)
    assert not _exists(db_session, SupplierEventMatch, match.id)
    assert not _exists(db_session, RiskEvent, event.id)
    assert not _exists(db_session, RawSignal, signal.id)
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(AIAnalysisRecord)
            .where(AIAnalysisRecord.signal_id == signal.id)
        )
        == 0
    )
    assert result.expired_alerts >= 1
    assert result.deleted_events >= 1
    assert result.deleted_signals >= 1


def test_expired_alert_at_exact_cutoff_is_retained(db_session: Session) -> None:
    # Given：expires_at 恰等于冻结时钟减 90 天（`<` 为删除边界，等于不删）。
    tag = _unique("cutoff")
    source = _source(db_session, f"ret-src-{tag}")
    supplier = _supplier(db_session, f"RET-{tag}")
    cutoff = NOW - timedelta(days=90)
    _, _, _, alert = _chain(
        db_session,
        source_id=source.id,
        supplier_id=supplier.id,
        tag=tag,
        signal_collected_at=NOW - timedelta(days=91),
        alert_status="expired",
        alert_expires_at=cutoff,
        alert_expiry_kind="finite",
        alert_updated_at=cutoff,
    )
    db_session.commit()

    # When
    result = cleanup_retention(db_session, SETTINGS, now_utc=NOW)

    # Then：边界当日仍保留。
    assert _exists(db_session, RiskAlert, alert.id)
    assert result.expired_alerts == 0


# ── C. 共享 match / 共享 event 不级联误删 ──────────────────────────


def test_shared_match_expired_and_current_keeps_shared(db_session: Session) -> None:
    # Given：同一 match 上一条 expired（91 天前）提醒与一条 current 无限提醒。
    tag = _unique("shared-match")
    source = _source(db_session, f"ret-src-{tag}")
    supplier = _supplier(db_session, f"RET-{tag}")
    old_at = NOW - timedelta(days=91)
    signal, event, match, expired_alert = _chain(
        db_session,
        source_id=source.id,
        supplier_id=supplier.id,
        tag=tag,
        signal_collected_at=old_at,
        alert_status="expired",
        alert_expires_at=old_at,
        alert_expiry_kind="finite",
        alert_updated_at=old_at,
        with_analysis=True,
    )
    current_alert = RiskAlert(
        match_id=match.id,
        level="P2",
        score=70,
        score_detail={},
        status="current",
        expires_at=None,
        expiry_kind="unbounded",
    )
    db_session.add(current_alert)
    db_session.flush()
    db_session.commit()

    # When
    result = cleanup_retention(db_session, SETTINGS, now_utc=NOW)

    # Then：只删到期提醒；共享 match、事件、信号、分析与现行提醒全部保留。
    assert not _exists(db_session, RiskAlert, expired_alert.id)
    assert _exists(db_session, RiskAlert, current_alert.id)
    assert _exists(db_session, SupplierEventMatch, match.id)
    assert _exists(db_session, RiskEvent, event.id)
    assert _exists(db_session, RawSignal, signal.id)
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(AIAnalysisRecord)
            .where(AIAnalysisRecord.signal_id == signal.id)
        )
        == 1
    )
    assert result.expired_alerts >= 1


def test_two_suppliers_share_event_only_unreferenced_part_cleaned(
    db_session: Session,
) -> None:
    # Given：同一事件下两家供应商各自 match；一家提醒过期超窗，另一家现行有效。
    tag = _unique("shared-event")
    source = _source(db_session, f"ret-src-{tag}")
    supplier_a = _supplier(db_session, f"RET-A-{tag}")
    supplier_b = _supplier(db_session, f"RET-B-{tag}")
    old_at = NOW - timedelta(days=91)
    signal_a, event, match_a, alert_a = _chain(
        db_session,
        source_id=source.id,
        supplier_id=supplier_a.id,
        tag=f"{tag}-a",
        signal_collected_at=old_at,
        alert_status="expired",
        alert_expires_at=old_at,
        alert_expiry_kind="finite",
        alert_updated_at=old_at,
    )
    match_b = SupplierEventMatch(
        supplier_id=supplier_b.id,
        event_id=event.id,
        match_type="legal_name",
        score=70,
        reasons=["测试"],
        evidence=[],
    )
    db_session.add(match_b)
    db_session.flush()
    alert_b = RiskAlert(
        match_id=match_b.id,
        level="P2",
        score=70,
        score_detail={},
        status="current",
        expires_at=None,
        expiry_kind="unbounded",
    )
    db_session.add(alert_b)
    db_session.flush()
    db_session.commit()

    # When
    result = cleanup_retention(db_session, SETTINGS, now_utc=NOW)

    # Then：只清理到期且无剩余引用的 A 支；B 支与共享事件及信号保留。
    assert not _exists(db_session, RiskAlert, alert_a.id)
    assert not _exists(db_session, SupplierEventMatch, match_a.id)
    assert _exists(db_session, RiskAlert, alert_b.id)
    assert _exists(db_session, SupplierEventMatch, match_b.id)
    assert _exists(db_session, RiskEvent, event.id)
    assert _exists(db_session, RawSignal, signal_a.id)
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(RiskEventSignal)
            .where(RiskEventSignal.event_id == event.id)
        )
        == 1
    )
    assert result.expired_alerts >= 1


# ── D. 研究溯源 FK 保护 ────────────────────────────────────────────


def test_research_promoted_signal_is_not_deleted(db_session: Session) -> None:
    # Given：91 天旧信号被一条研究结论 promoted（SET NULL 溯源引用）。
    tag = _unique("research")
    source = _source(db_session, f"ret-src-{tag}")
    supplier = _supplier(db_session, f"RET-{tag}")
    old = NOW - timedelta(days=91)
    signal, _, _, _ = _chain(
        db_session,
        source_id=source.id,
        supplier_id=supplier.id,
        tag=tag,
        signal_collected_at=old,
        alert_status="expired",
        alert_expires_at=old,
        alert_expiry_kind="finite",
        alert_updated_at=old,
    )
    owner = User(
        username=f"retention-evidence-{tag}",
        password_hash="not-used",
        display_name="保留测试用户",
        role="risk_admin",
        status="active",
    )
    db_session.add(owner)
    db_session.flush()
    task = ResearchTask(
        owner_user_id=owner.id,
        task_type="manual",
        topic="保留闭包研究溯源",
    )
    db_session.add(task)
    db_session.flush()
    claim = ResearchClaim(
        task_id=task.id,
        claim_type="fact",
        claim_text="该信号支撑研究结论",
        promoted_signal_id=signal.id,
    )
    db_session.add(claim)
    db_session.flush()
    db_session.commit()

    # When
    cleanup_retention(db_session, SETTINGS, now_utc=NOW)

    # Then：信号保留，研究引用事实不被清空（不靠 SET NULL 制造可删条件）。
    assert _exists(db_session, RawSignal, signal.id)
    db_session.refresh(claim)
    assert claim.promoted_signal_id == signal.id


# ── E. 无界过期提醒的更新锚点与异常诊断 ───────────────────────────


def test_expired_unbounded_with_verified_termination_anchor_is_cleaned(
    db_session: Session,
) -> None:
    # Given：status=expired 且 expires_at 为 NULL 的无界撤销记录；
    # 终止更新 updated_at 明确存在且已超 90 天（保守锚点）。
    tag = _unique("unbounded-expired")
    source = _source(db_session, f"ret-src-{tag}")
    supplier = _supplier(db_session, f"RET-{tag}")
    terminated_at = NOW - timedelta(days=91)
    signal, event, match, alert = _chain(
        db_session,
        source_id=source.id,
        supplier_id=supplier.id,
        tag=tag,
        signal_collected_at=terminated_at,
        alert_status="expired",
        alert_expires_at=None,
        alert_expiry_kind="unbounded",
        alert_updated_at=terminated_at,
    )
    db_session.commit()

    # When
    result = cleanup_retention(db_session, SETTINGS, now_utc=NOW)

    # Then：按 updated_at 锚点清理到期单链。
    assert not _exists(db_session, RiskAlert, alert.id)
    assert not _exists(db_session, SupplierEventMatch, match.id)
    assert not _exists(db_session, RiskEvent, event.id)
    assert not _exists(db_session, RawSignal, signal.id)
    assert result.expired_alerts >= 1


def test_expired_alert_missing_anchor_is_diagnosed_and_retained(
    db_session: Session,
) -> None:
    # Given：status=expired、expires_at 缺失且从未发生终止更新
    # （updated_at == created_at，schema 中 updated_at NOT NULL，无法为 NULL）。
    tag = _unique("missing-anchor")
    source = _source(db_session, f"ret-src-{tag}")
    supplier = _supplier(db_session, f"RET-{tag}")
    old = NOW - timedelta(days=200)
    _, _, _, alert = _chain(
        db_session,
        source_id=source.id,
        supplier_id=supplier.id,
        tag=tag,
        signal_collected_at=old,
        alert_status="expired",
        alert_expires_at=None,
        alert_expiry_kind="unbounded",
        alert_updated_at=old,
        alert_created_at=old,
    )
    db_session.commit()

    # When
    result = cleanup_retention(db_session, SETTINGS, now_utc=NOW)

    # Then：异常锚点不删并记入诊断。
    assert _exists(db_session, RiskAlert, alert.id)
    assert result.expired_alerts == 0
    assert result.anomaly_anchor_skipped >= 1


def test_alert_removal_anchor_treats_null_updated_at_as_missing() -> None:
    # 防御分支锁定：updated_at 为 NULL（当前 schema 不可持久化）也视为缺锚。
    unsaved = RiskAlert(
        status="expired", expires_at=None, expiry_kind="unbounded"
    )
    unsaved.updated_at = None
    assert alert_removal_anchor(unsaved) is None


# ── F. 幂等与事务回滚 ─────────────────────────────────────────────


def test_cleanup_is_idempotent_across_repeated_runs(db_session: Session) -> None:
    # Given：一条可删旧链与一条受保护现行链。
    tag = _unique("idempotent")
    source = _source(db_session, f"ret-src-{tag}")
    supplier = _supplier(db_session, f"RET-{tag}")
    old = NOW - timedelta(days=91)
    _, _, _, doomed = _chain(
        db_session,
        source_id=source.id,
        supplier_id=supplier.id,
        tag=f"{tag}-old",
        signal_collected_at=old,
        alert_status="expired",
        alert_expires_at=old,
        alert_expiry_kind="finite",
        alert_updated_at=old,
    )
    _, _, _, kept = _chain(
        db_session,
        source_id=source.id,
        supplier_id=supplier.id,
        tag=f"{tag}-kept",
        signal_collected_at=old,
        alert_status="current",
        alert_expires_at=None,
        alert_expiry_kind="unbounded",
    )
    db_session.commit()

    # When：连续运行两轮。
    first = cleanup_retention(db_session, SETTINGS, now_utc=NOW)
    second = cleanup_retention(db_session, SETTINGS, now_utc=NOW)

    # Then：第一轮清理到期链并保留现行链；第二轮无可删项。
    assert not _exists(db_session, RiskAlert, doomed.id)
    assert _exists(db_session, RiskAlert, kept.id)
    assert first.expired_alerts >= 1
    assert second.expired_alerts == 0
    assert second.deleted_events == 0
    assert second.deleted_signals == 0
    assert second.deleted_analysis == 0
    assert second.deleted_runs == 0


def test_mid_cleanup_failure_rolls_back_entire_round(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given：两条可删旧链各带一条旧信号。
    tag = _unique("rollback")
    source = _source(db_session, f"ret-src-{tag}")
    supplier = _supplier(db_session, f"RET-{tag}")
    old = NOW - timedelta(days=91)
    chains = tuple(
        _chain(
            db_session,
            source_id=source.id,
            supplier_id=supplier.id,
            tag=f"{tag}-{index}",
            signal_collected_at=old,
            alert_status="expired",
            alert_expires_at=old,
            alert_expiry_kind="finite",
            alert_updated_at=old,
        )
        for index in range(2)
    )
    db_session.commit()

    def table_counts() -> dict[str, int]:
        return {
            name: db_session.scalar(select(func.count()).select_from(model))
            for name, model in (
                ("alerts", RiskAlert),
                ("matches", SupplierEventMatch),
                ("events", RiskEvent),
                ("signals", RawSignal),
            )
        }

    before = table_counts()

    # When：第二次 session.delete 注入失败。
    calls = {"count": 0}
    real_delete = Session.delete

    def flaky_delete(self: Session, instance: object) -> None:
        calls["count"] += 1
        if calls["count"] == 2:
            raise RuntimeError("injected-mid-cleanup-failure")
        real_delete(self, instance)

    monkeypatch.setattr(Session, "delete", flaky_delete)
    with pytest.raises(RuntimeError, match="injected-mid-cleanup-failure"):
        cleanup_retention(db_session, SETTINGS, now_utc=NOW)
    db_session.rollback()

    # Then：所有表摘要与失败前一致（整轮回滚，无局部提交）。
    assert calls["count"] >= 2
    assert table_counts() == before
    for _, _, match, alert in chains:
        assert _exists(db_session, RiskAlert, alert.id)
        assert _exists(db_session, SupplierEventMatch, match.id)


# ── G. 真实双 Session 并发证明 ────────────────────────────────────


def _concurrent_setup() -> dict[str, int]:
    """在独立事务中提交旧到期链，返回 id 集合；调用方负责清理。"""
    tag = _unique("concurrent")
    with SessionLocal.begin() as session:
        source = DataSource(
            code=f"ret-src-{tag}",
            name="保留闭包并发测试源",
            source_type="api",
            credibility=80,
            enabled=True,
            adapter_status="builtin",
            adapter_version=0,
            auth_type="none",
            login_config={},
            adapter_config={},
        )
        session.add(source)
        session.flush()
        supplier_a = Supplier(
            supplier_code=f"RET-A-{tag}",
            legal_name=f"保留闭包A{tag}",
            country_code="CN",
        )
        supplier_b = Supplier(
            supplier_code=f"RET-B-{tag}",
            legal_name=f"保留闭包B{tag}",
            country_code="CN",
        )
        session.add_all((supplier_a, supplier_b))
        session.flush()
        old = NOW - timedelta(days=91)
        signal = RawSignal(
            source_id=source.id,
            external_id=tag,
            title=f"信号{tag}",
            content=f"内容{tag}",
            published_at=old,
            collected_at=old,
            fingerprint=f"retention-evidence-{tag}",
            raw_data={},
        )
        session.add(signal)
        session.flush()
        event = RiskEvent(
            dedup_key=f"retention-evidence-{tag}",
            event_type="compliance",
            severity="high",
            summary=f"事件{tag}",
            start_at=old,
            end_at=old,
            confidence=0.9,
            facts={},
        )
        session.add(event)
        session.flush()
        session.add(RiskEventSignal(event_id=event.id, signal_id=signal.id))
        match = SupplierEventMatch(
            supplier_id=supplier_a.id,
            event_id=event.id,
            match_type="legal_name",
            score=70,
            reasons=["测试"],
            evidence=[],
        )
        session.add(match)
        session.flush()
        alert = RiskAlert(
            match_id=match.id,
            level="P1",
            score=85,
            score_detail={},
            status="expired",
            expires_at=old,
            expiry_kind="finite",
        )
        alert.updated_at = old
        session.add(alert)
        session.flush()
        return {
            "source_id": source.id,
            "supplier_a_id": supplier_a.id,
            "supplier_b_id": supplier_b.id,
            "signal_id": signal.id,
            "event_id": event.id,
            "match_id": match.id,
            "alert_id": alert.id,
        }


def _concurrent_teardown(ids: dict[str, int]) -> None:
    with SessionLocal.begin() as session:
        session.execute(
            RiskAlert.__table__.delete().where(
                RiskAlert.match_id.in_(
                    select(SupplierEventMatch.id).where(
                        SupplierEventMatch.event_id == ids["event_id"]
                    )
                )
            )
        )
        session.execute(
            SupplierEventMatch.__table__.delete().where(
                SupplierEventMatch.event_id == ids["event_id"]
            )
        )
        session.execute(
            RiskEventSignal.__table__.delete().where(
                RiskEventSignal.event_id == ids["event_id"]
            )
        )
        session.execute(
            RiskEvent.__table__.delete().where(RiskEvent.id == ids["event_id"])
        )
        session.execute(
            RawSignal.__table__.delete().where(RawSignal.id == ids["signal_id"])
        )
        session.execute(
            Supplier.__table__.delete().where(
                Supplier.id.in_([ids["supplier_a_id"], ids["supplier_b_id"]])
            )
        )
        session.execute(
            DataSource.__table__.delete().where(DataSource.id == ids["source_id"])
        )


def _cleanup_worker(ids: dict[str, int], barrier: Barrier) -> str:
    barrier.wait(timeout=30)
    with SessionLocal.begin() as session:
        cleanup_retention(session, SETTINGS, now_utc=NOW)
    return "cleaned"


def _associate_worker(ids: dict[str, int], barrier: Barrier) -> str:
    barrier.wait(timeout=30)
    try:
        with SessionLocal.begin() as session:
            match = SupplierEventMatch(
                supplier_id=ids["supplier_b_id"],
                event_id=ids["event_id"],
                match_type="legal_name",
                score=70,
                reasons=["并发新增关联"],
                evidence=[],
            )
            session.add(match)
            session.flush()
            session.add(
                RiskAlert(
                    match_id=match.id,
                    level="P2",
                    score=70,
                    score_detail={},
                    status="current",
                    expires_at=None,
                    expiry_kind="unbounded",
                )
            )
    except IntegrityError:
        return "rejected"
    return "associated"


def test_concurrent_new_association_never_loses_valid_evidence() -> None:
    # Given：一条可删旧链；Barrier 同步放行清理与新增关联两个真实 Session。
    ids = _concurrent_setup()
    try:
        barrier = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as executor:
            cleanup_future = executor.submit(_cleanup_worker, ids, barrier)
            associate_future = executor.submit(_associate_worker, ids, barrier)
            cleanup_outcome = cleanup_future.result(timeout=120)
            associate_outcome = associate_future.result(timeout=120)

        # Then：任一成功结局都不损失有效证据、不留下游离引用。
        assert cleanup_outcome == "cleaned"
        assert associate_outcome in {"associated", "rejected"}
        with SessionLocal() as session:
            if associate_outcome == "associated":
                new_alert = session.scalar(
                    select(RiskAlert)
                    .join(
                        SupplierEventMatch,
                        RiskAlert.match_id == SupplierEventMatch.id,
                    )
                    .where(
                        SupplierEventMatch.supplier_id == ids["supplier_b_id"],
                        SupplierEventMatch.event_id == ids["event_id"],
                        RiskAlert.status == "current",
                    )
                )
                assert new_alert is not None
                # 新增现行关联存活，则其事件与信号证据必须存活。
                assert session.get(RiskEvent, ids["event_id"]) is not None
                assert session.get(RawSignal, ids["signal_id"]) is not None
                assert (
                    session.scalar(
                        select(func.count())
                        .select_from(RiskEventSignal)
                        .where(
                            RiskEventSignal.event_id == ids["event_id"],
                            RiskEventSignal.signal_id == ids["signal_id"],
                        )
                    )
                    == 1
                )
            else:
                # 新增被拒绝（事件已先被合法清理），不得留下游离 match/alert。
                dangling = session.scalar(
                    select(func.count())
                    .select_from(SupplierEventMatch)
                    .where(SupplierEventMatch.supplier_id == ids["supplier_b_id"])
                )
                assert dangling == 0
    finally:
        _concurrent_teardown(ids)


# ── H. legacy 边界、membership 事实、40001 重试、精确计数 ─────────


def test_legacy_current_alert_keeps_chain_regardless_of_age(
    db_session: Session,
) -> None:
    # Given：legacy 无限现行提醒（200 天旧信号），legacy 只服从旧 status 真值。
    tag = _unique("legacy-current")
    source = _source(db_session, f"ret-src-{tag}")
    supplier = _supplier(db_session, f"RET-{tag}")
    very_old = NOW - timedelta(days=200)
    signal, event, match, alert = _chain(
        db_session,
        source_id=source.id,
        supplier_id=supplier.id,
        tag=tag,
        signal_collected_at=very_old,
        alert_status="current",
        alert_expires_at=None,
        alert_expiry_kind="legacy",
        with_analysis=True,
    )
    db_session.commit()

    # When
    result = cleanup_retention(db_session, SETTINGS, now_utc=NOW)

    # Then：legacy current 仍受 current_alert_condition 保护，闭包完整保留。
    assert _exists(db_session, RiskAlert, alert.id)
    assert _exists(db_session, SupplierEventMatch, match.id)
    assert _exists(db_session, RiskEvent, event.id)
    assert _exists(db_session, RawSignal, signal.id)
    assert result.protected_skipped >= 1


def test_legacy_expired_with_verified_termination_anchor_is_cleaned(
    db_session: Session,
) -> None:
    # Given：legacy 已失效提醒，expires_at 缺失但终止更新 updated_at 已超 90 天。
    tag = _unique("legacy-expired")
    source = _source(db_session, f"ret-src-{tag}")
    supplier = _supplier(db_session, f"RET-{tag}")
    terminated_at = NOW - timedelta(days=91)
    signal, event, match, alert = _chain(
        db_session,
        source_id=source.id,
        supplier_id=supplier.id,
        tag=tag,
        signal_collected_at=terminated_at,
        alert_status="expired",
        alert_expires_at=None,
        alert_expiry_kind="legacy",
        alert_updated_at=terminated_at,
    )
    db_session.commit()

    # When
    result = cleanup_retention(db_session, SETTINGS, now_utc=NOW)

    # Then：按 updated_at 保守锚点清理到期单链。
    assert not _exists(db_session, RiskAlert, alert.id)
    assert not _exists(db_session, SupplierEventMatch, match.id)
    assert not _exists(db_session, RiskEvent, event.id)
    assert not _exists(db_session, RawSignal, signal.id)
    assert result.expired_alerts >= 1


def test_active_membership_fact_protects_signal_and_state_stays_unchanged(
    db_session: Session,
) -> None:
    # Given：91 天旧信号（external_id=member_key）对应活跃名单成员事实。
    tag = _unique("membership")
    source = _source(db_session, f"ret-src-{tag}")
    supplier = _supplier(db_session, f"RET-{tag}")
    old = NOW - timedelta(days=91)
    signal, event, match, alert = _chain(
        db_session,
        source_id=source.id,
        supplier_id=supplier.id,
        tag=tag,
        signal_collected_at=old,
        alert_status="expired",
        alert_expires_at=old,
        alert_expiry_kind="finite",
        alert_updated_at=old,
    )
    member = SourceMemberState(
        source_id=source.id,
        member_key=tag,
        status="active",
        first_seen_at=old,
        last_seen_at=old,
        baseline_snapshot_hash="hash-membership",
    )
    db_session.add(member)
    db_session.flush()
    db_session.commit()

    # When
    result = cleanup_retention(db_session, SETTINGS, now_utc=NOW)

    # Then：信号因活跃成员资格受保护且事件不承载删除；成员状态原样未动。
    assert _exists(db_session, RawSignal, signal.id)
    assert _exists(db_session, RiskEvent, event.id)
    db_session.refresh(member)
    assert member.status == "active"
    assert member.first_seen_at == old
    assert member.last_seen_at == old
    assert member.consecutive_missing == 0
    assert member.quarantine_round == 0
    assert result.protected_skipped >= 1


def _serialization_conflict() -> OperationalError:
    return OperationalError(
        "SELECT 1", {}, psycopg_errors.SerializationFailure("simulated-40001")
    )


def test_serialization_conflict_retry_recomputes_and_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given：可删旧链；首轮注入 sqlstate=40001 序列化冲突。
    ids = _concurrent_setup()
    try:
        calls = {"count": 0}
        real_cleanup = retention_module._run_cleanup

        def flaky(
            work: Session, settings: RetentionSettings, now: datetime
        ) -> CleanupResult:
            calls["count"] += 1
            if calls["count"] == 1:
                raise _serialization_conflict()
            return real_cleanup(work, settings, now)

        monkeypatch.setattr(retention_module, "_run_cleanup", flaky)

        # When：独立 Engine 绑定 Session 触发重试路径。
        with SessionLocal() as session:
            result = cleanup_retention(session, SETTINGS, now_utc=NOW)

        # Then：重试后整轮成功提交，链被清理。
        assert calls["count"] == 2
        assert result.expired_alerts >= 1
        with SessionLocal() as verify:
            assert verify.get(RiskAlert, ids["alert_id"]) is None
            assert verify.get(SupplierEventMatch, ids["match_id"]) is None
            assert verify.get(RiskEvent, ids["event_id"]) is None
            assert verify.get(RawSignal, ids["signal_id"]) is None
    finally:
        _concurrent_teardown(ids)


def test_serialization_conflict_exhaustion_rolls_back_entire_round(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given：可删旧链；每轮都注入序列化冲突。
    ids = _concurrent_setup()
    try:
        calls = {"count": 0}

        def always_conflict(
            work: Session, settings: RetentionSettings, now: datetime
        ) -> CleanupResult:
            calls["count"] += 1
            raise _serialization_conflict()

        monkeypatch.setattr(retention_module, "_run_cleanup", always_conflict)

        # When：重试 3 次后失败上抛。
        with SessionLocal() as session:
            with pytest.raises(OperationalError):
                cleanup_retention(session, SETTINGS, now_utc=NOW)

        # Then：恰好尝试上限次，且所有表摘要不变（整轮回滚）。
        assert calls["count"] == SERIALIZABLE_RETRY_LIMIT
        with SessionLocal() as verify:
            assert verify.get(RiskAlert, ids["alert_id"]) is not None
            assert verify.get(SupplierEventMatch, ids["match_id"]) is not None
            assert verify.get(RiskEvent, ids["event_id"]) is not None
            assert verify.get(RawSignal, ids["signal_id"]) is not None
    finally:
        _concurrent_teardown(ids)


def test_exact_counts_match_actual_row_impact(db_session: Session) -> None:
    # Given：一条可删旧链（无分析）+ 一条年轻信号承载超龄分析记录。
    tag = _unique("exact")
    source = _source(db_session, f"ret-src-{tag}")
    supplier = _supplier(db_session, f"RET-{tag}")
    old = NOW - timedelta(days=91)
    _chain(
        db_session,
        source_id=source.id,
        supplier_id=supplier.id,
        tag=f"{tag}-doomed",
        signal_collected_at=old,
        alert_status="expired",
        alert_expires_at=old,
        alert_expiry_kind="finite",
        alert_updated_at=old,
    )
    fresh_signal = RawSignal(
        source_id=source.id,
        external_id=f"{tag}-fresh",
        title="年轻信号",
        content="信号本身年轻，分析记录超龄",
        published_at=NOW,
        collected_at=NOW,
        fingerprint=f"retention-evidence-{tag}-fresh",
        raw_data={},
    )
    db_session.add(fresh_signal)
    db_session.flush()
    db_session.add(
        AIAnalysisRecord(
            signal_id=fresh_signal.id,
            provider="fake",
            model="fake-test",
            prompt_version="v1",
            status="succeeded",
            started_at=old,
            finished_at=old,
        )
    )
    db_session.commit()

    # When：连续两轮清理。
    first = cleanup_retention(db_session, SETTINGS, now_utc=NOW)
    second = cleanup_retention(db_session, SETTINGS, now_utc=NOW)

    # Then：首轮计数逐项等于真实影响行数；第二轮全部归零（幂等）。
    assert first.expired_alerts == 1
    assert first.deleted_events == 1
    assert first.deleted_signals == 1
    assert first.deleted_analysis == 1
    assert first.deleted_runs == 0
    assert first.protected_skipped == 0
    assert first.anomaly_anchor_skipped == 0
    assert second.expired_alerts == 0
    assert second.deleted_events == 0
    assert second.deleted_signals == 0
    assert second.deleted_analysis == 0
    assert second.deleted_runs == 0
    assert _exists(db_session, RawSignal, fresh_signal.id)
