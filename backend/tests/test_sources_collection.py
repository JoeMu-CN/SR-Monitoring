"""采集服务、手动触发端点与保留清理测试。"""

import hashlib
import json
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session
from tyc_batch_support import (
    MultidimMcpStub,
    committed_tyc_daily_used,
    committed_tyc_rows,
    configure_committed_tyc,
)

from app.agent.models import TycUsageRecord
from app.config import RetentionSettings
from app.database import engine
from app.risks.models import (
    RiskAlert,
    RiskEvent,
    RiskEventSignal,
    SupplierEventMatch,
)
from app.scheduler.retention import cleanup_retention
from app.signals.declarative import AdapterSpec, DeclarativeSourceAdapter
from app.signals.models import CollectionRun, DataSource, RawSignal
from app.signals.schemas import ManualSignalInput
from app.signals.service import (
    COLLECTION_RUN_STALE_SECONDS,
    STALE_COLLECTION_RUN_ERROR,
    CollectionDeferred,
    CollectionFailed,
    collect_source,
    finalize_stale_collection_runs,
)
from app.signals.sources import NmcWeatherAdapter, RawSourceItem, SourceFetchError, StatsPmiAdapter
from app.suppliers.models import Supplier


def _nmc_rows() -> list[dict[str, object]]:
    return [
        {
            "alertid": "33000033200000_20260807181838",
            "issuetime": "2026/08/07 18:18",
            "title": "浙江省水利厅、浙江省气象台发布山洪灾害蓝色预警",
            "url": "/publish/alarm/33000033200000_20260807181838.html",
        },
        {
            "alertid": "14000041600000_20260807165704",
            "issuetime": "2026/08/07 16:57",
            "title": "山西省自然资源厅和山西省气象台发布地质灾害黄色预警",
            "url": "/publish/alarm/14000041600000_20260807165704.html",
        },
    ]


def _mock_adapter() -> NmcWeatherAdapter:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = {"msg": "success", "code": 0, "data": {"page": {"list": _nmc_rows()}}}
        return httpx.Response(200, text=json.dumps(payload))

    return NmcWeatherAdapter(transport=httpx.MockTransport(handler))


def _get_nmc_source(session: Session) -> DataSource:
    source = session.scalar(select(DataSource).where(DataSource.code == "nmc-weather"))
    assert source is not None, "迁移 0009 应已注册 nmc-weather 信息源"
    return source


def test_manual_trigger_run_collects_signals(
    client: TestClient, db_session: Session
) -> None:
    source = _get_nmc_source(db_session)
    # 直接调用采集服务写入数据
    run = collect_source(db_session, source, _mock_adapter())
    assert run.status == "succeeded"
    assert run.created_count == 2

    signals = list(
        db_session.scalars(
            select(RawSignal).where(RawSignal.source_id == source.id)
        )
    )
    assert len(signals) == 2
    titles = {signal.title for signal in signals}
    assert "山洪灾害" in " ".join(titles)


def test_collect_source_is_idempotent(db_session: Session) -> None:
    source = _get_nmc_source(db_session)
    first = collect_source(db_session, source, _mock_adapter())
    second = collect_source(db_session, source, _mock_adapter())
    assert first.created_count == 2
    assert second.created_count == 0
    assert second.duplicate_count == 2
    count = db_session.scalar(
        select(func.count()).select_from(RawSignal).where(RawSignal.source_id == source.id)
    )
    assert count == 2


def test_collection_validity_when_weather_is_current_or_old_persists_initial_state(
    db_session: Session,
) -> None:
    # Given
    source = _get_nmc_source(db_session)
    source.validity_policy = {
        "profile": "weather_alert",
        "mode": "fixed_days",
        "fixed_days": 3,
    }
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    rows = [
        {
            "alertid": "validity-current-weather",
            "issuetime": (now - timedelta(hours=1)).strftime("%Y/%m/%d %H:%M"),
            "title": "当前天气预警",
            "url": "/publish/alarm/validity-current-weather.html",
        },
        {
            "alertid": "validity-expired-weather",
            "issuetime": (now - timedelta(days=10)).strftime("%Y/%m/%d %H:%M"),
            "title": "十天前天气预警",
            "url": "/publish/alarm/validity-expired-weather.html",
        },
    ]
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200,
            text=json.dumps({"msg": "success", "code": 0, "data": {"page": {"list": rows}}}),
        )
    )

    # When
    collect_source(db_session, source, NmcWeatherAdapter(transport=transport))

    # Then
    signals = list(
        db_session.scalars(
            select(RawSignal)
            .where(RawSignal.source_id == source.id)
            .order_by(RawSignal.external_id)
        )
    )
    by_external_id = {signal.external_id: signal for signal in signals}
    current = by_external_id["validity-current-weather"]
    expired = by_external_id["validity-expired-weather"]
    assert current.validity_state == "active"
    assert expired.validity_state == "expired"
    assert current.validity_profile == expired.validity_profile == "weather_alert"
    assert current.validity_policy_version == expired.validity_policy_version
    assert current.valid_until == current.published_at + timedelta(days=3)
    assert expired.valid_until == expired.published_at + timedelta(days=3)


def test_collection_validity_when_duplicate_is_recollected_keeps_original_deadline(
    db_session: Session,
) -> None:
    # Given
    source = _get_nmc_source(db_session)
    source.validity_policy = {
        "profile": "weather_alert",
        "mode": "fixed_days",
        "fixed_days": 3,
    }
    first = collect_source(db_session, source, _mock_adapter())
    assert first.created_count == 2
    signal = db_session.scalar(
        select(RawSignal)
        .where(RawSignal.source_id == source.id)
        .order_by(RawSignal.id)
    )
    assert signal is not None
    original_deadline = signal.valid_until
    original_version = signal.validity_policy_version
    source.validity_policy = {
        "profile": "weather_alert",
        "mode": "fixed_days",
        "fixed_days": 30,
    }

    # When
    second = collect_source(db_session, source, _mock_adapter())

    # Then
    db_session.refresh(signal)
    assert second.created_count == 0
    assert signal.valid_until == original_deadline
    assert signal.validity_policy_version == original_version


def test_declarative_collection_validity_when_policy_is_unbounded_persists_snapshot(
    db_session: Session,
) -> None:
    # Given
    source = DataSource(
        code="validity-declarative-sanctions",
        name="声明式永久清单",
        source_type="official_api",
        credibility=95,
        endpoint_url="https://official.example/sanctions",
        adapter_status="published",
        enabled=True,
        validity_policy={
            "profile": "sanctions",
            "mode": "until_revoked",
            "review_required": False,
        },
    )
    db_session.add(source)
    db_session.flush()
    spec = AdapterSpec.model_validate(
        {
            "format": "json",
            "request": {"url": source.endpoint_url},
            "items_path": "items",
            "mapping": {
                "external_id": "id",
                "title": "name",
                "content": "reason",
            },
        }
    )
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200,
            json={"items": [{"id": "member-1", "name": "清单成员", "reason": "官方列名"}]},
        )
    )

    # When
    collect_source(
        db_session,
        source,
        DeclarativeSourceAdapter(source.code, spec, transport=transport),
    )

    # Then
    signal = db_session.scalar(select(RawSignal).where(RawSignal.source_id == source.id))
    assert signal is not None
    assert signal.validity_profile == "sanctions"
    assert signal.validity_mode == "until_revoked"
    assert signal.validity_state == "active"
    assert signal.valid_until is None
    assert signal.review_due_at is None
    assert signal.validity_policy_version is not None


def test_manual_import_validity_when_weather_is_old_keeps_expired_history(
    client: TestClient,
    db_session: Session,
) -> None:
    # Given
    source = db_session.scalar(select(DataSource).where(DataSource.code == "manual-json"))
    assert source is not None
    source.validity_policy = None
    db_session.flush()
    payload = json.dumps(
        {
            "version": "1.0",
            "signals": [
                {
                    "external_id": "manual-expired-weather",
                    "title": "历史台风预警",
                    "content": "十天前发布，仍需保留历史记录。",
                    "published_at": (datetime.now(UTC) - timedelta(days=10)).isoformat(),
                    "validity_profile": "weather_alert",
                }
            ],
        },
        ensure_ascii=False,
    ).encode()

    # When
    response = client.post(
        "/api/v1/signals/import",
        files={"file": ("signals.json", payload, "application/json")},
    )

    # Then
    assert response.status_code == 200
    signal = db_session.scalar(
        select(RawSignal).where(RawSignal.external_id == "manual-expired-weather")
    )
    assert signal is not None
    assert signal.validity_state == "expired"
    assert signal.valid_until == signal.published_at + timedelta(days=3)


def test_manual_import_validity_when_body_forges_lifecycle_does_not_change_target(
    client: TestClient,
    db_session: Session,
) -> None:
    # Given
    source = db_session.scalar(select(DataSource).where(DataSource.code == "manual-json"))
    assert source is not None
    target = RawSignal(
        source_id=source.id,
        external_id="lifecycle-target",
        title="有效法规",
        content="仍然有效",
        fingerprint="lifecycle-target-fingerprint",
        raw_data={},
        validity_profile="regulatory_change",
        validity_state="active",
        valid_from=datetime.now(UTC) - timedelta(days=1),
        validity_mode="until_revoked",
        review_due_at=datetime.now(UTC) + timedelta(days=89),
        lifecycle_action="assert",
        validity_policy_version="a" * 64,
        validity_reason={
            "code": "policy_resolved",
            "anchor_source": "published_at",
            "details": {},
        },
    )
    db_session.add(target)
    db_session.flush()
    payload = json.dumps(
        {
            "version": "1.0",
            "signals": [
                {
                    "external_id": "lifecycle-body-forgery",
                    "title": "普通更新",
                    "content": (
                        f'{{"lifecycle_action":"revoke","target_signal_id":{target.id}}}'
                    ),
                    "validity_profile": "other",
                }
            ],
        },
        ensure_ascii=False,
    ).encode()

    # When
    response = client.post(
        "/api/v1/signals/import",
        files={"file": ("signals.json", payload, "application/json")},
    )

    # Then
    assert response.status_code == 200
    db_session.refresh(target)
    assert target.validity_state == "active"


def test_manual_import_validity_when_admin_revokes_target_updates_atomically(
    client: TestClient,
    db_session: Session,
) -> None:
    # Given
    source = db_session.scalar(select(DataSource).where(DataSource.code == "manual-json"))
    assert source is not None
    source.validity_policy = {
        "profile": "regulatory_change",
        "mode": "until_revoked",
        "review_days": 90,
    }
    target = RawSignal(
        source_id=source.id,
        external_id="admin-lifecycle-target",
        title="待撤销法规",
        content="当前仍有效",
        fingerprint="admin-lifecycle-target-fingerprint",
        raw_data={},
        validity_profile="regulatory_change",
        validity_state="active",
        valid_from=datetime.now(UTC) - timedelta(days=1),
        validity_mode="until_revoked",
        review_due_at=datetime.now(UTC) + timedelta(days=89),
        lifecycle_action="assert",
        validity_policy_version="b" * 64,
        validity_reason={
            "code": "policy_resolved",
            "anchor_source": "published_at",
            "details": {},
        },
    )
    db_session.add(target)
    db_session.flush()
    payload = json.dumps(
        {
            "version": "1.0",
            "signals": [
                {
                    "external_id": "admin-lifecycle-revoke",
                    "title": "官方废止公告",
                    "content": "该法规已经废止。",
                    "published_at": datetime.now(UTC).isoformat(),
                    "validity_profile": "regulatory_change",
                    "validity_key": "regulation-1",
                    "lifecycle_action": "revoke",
                    "target_signal_id": target.id,
                    "lifecycle_reason": "官方废止公告",
                }
            ],
        },
        ensure_ascii=False,
    ).encode()

    # When
    response = client.post(
        "/api/v1/signals/import",
        files={"file": ("signals.json", payload, "application/json")},
    )

    # Then
    assert response.status_code == 200
    db_session.refresh(target)
    assert target.validity_state == "revoked"
    assert target.valid_until is not None
    assert target.validity_reason["code"] == "lifecycle_revoke"


def test_manual_import_validity_when_deadline_has_no_timezone_is_rejected(
    client: TestClient,
) -> None:
    # Given
    payload = json.dumps(
        {
            "version": "1.0",
            "signals": [
                {
                    "title": "无时区截止时间",
                    "content": "应在边界校验失败。",
                    "validity_profile": "weather_alert",
                    "valid_until": "2026-09-08T12:00:00",
                }
            ],
        },
        ensure_ascii=False,
    ).encode()

    # When
    response = client.post(
        "/api/v1/signals/import",
        files={"file": ("signals.json", payload, "application/json")},
    )

    # Then
    assert response.status_code == 422
    assert response.json()["detail"]["errors"][0]["path"] == "signals.0.valid_until"


def test_manual_import_validity_when_deadline_precedes_publish_without_revoke_is_rejected(
    client: TestClient,
    db_session: Session,
) -> None:
    # Given
    before_publish = (datetime.now(UTC) - timedelta(days=30)).isoformat()
    payload = json.dumps(
        {
            "version": "1.0",
            "signals": [
                {
                    "title": "截止早于发布",
                    "content": "未标记撤销时截止不得早于发布。",
                    "published_at": datetime.now(UTC).isoformat(),
                    "validity_profile": "weather_alert",
                    "valid_until": before_publish,
                }
            ],
        },
        ensure_ascii=False,
    ).encode()

    # When
    response = client.post(
        "/api/v1/signals/import",
        files={"file": ("signals.json", payload, "application/json")},
    )

    # Then
    assert response.status_code == 422
    assert db_session.scalar(select(func.count()).select_from(RawSignal)) == 0


def test_manual_import_revocation_is_rejected_for_non_admin_role(
    client: TestClient,
    db_session: Session,
    auth_as,
) -> None:
    # Given
    source = db_session.scalar(select(DataSource).where(DataSource.code == "manual-json"))
    assert source is not None
    target = RawSignal(
        source_id=source.id,
        external_id="viewer-revoke-target",
        title="有效法规",
        content="仍然有效",
        fingerprint="viewer-revoke-target-fingerprint",
        raw_data={},
        validity_profile="regulatory_change",
        validity_state="active",
        valid_from=datetime.now(UTC) - timedelta(days=1),
        validity_mode="until_revoked",
        review_due_at=datetime.now(UTC) + timedelta(days=89),
        lifecycle_action="assert",
        validity_policy_version="c" * 64,
        validity_reason={
            "code": "policy_resolved",
            "anchor_source": "published_at",
            "details": {},
        },
    )
    db_session.add(target)
    db_session.flush()
    auth_as("viewer", "revocation-forging-viewer")
    payload = json.dumps(
        {
            "version": "1.0",
            "signals": [
                {
                    "external_id": "viewer-lifecycle-revoke",
                    "title": "伪造撤销",
                    "content": "普通角色不得触发生命周期动作。",
                    "validity_profile": "regulatory_change",
                    "validity_key": "regulation-viewer",
                    "lifecycle_action": "revoke",
                    "target_signal_id": target.id,
                    "lifecycle_reason": "伪造撤销",
                }
            ],
        },
        ensure_ascii=False,
    ).encode()

    # When
    response = client.post(
        "/api/v1/signals/import",
        files={"file": ("signals.json", payload, "application/json")},
    )

    # Then
    assert response.status_code == 403
    db_session.refresh(target)
    assert target.validity_state == "active"


def test_collect_source_batches_large_fetch(db_session: Session) -> None:
    source = _get_nmc_source(db_session)

    def handler(request: httpx.Request) -> httpx.Response:
        rows = [
            {
                "alertid": f"large-{index}",
                "issuetime": "2026/08/07 18:18",
                "title": f"批量测试预警 {index}",
                "url": f"/publish/alarm/large-{index}.html",
            }
            for index in range(9000)
        ]
        payload = {"msg": "success", "code": 0, "data": {"page": {"list": rows}}}
        return httpx.Response(200, text=json.dumps(payload))

    run = collect_source(
        db_session,
        source,
        NmcWeatherAdapter(transport=httpx.MockTransport(handler)),
    )
    assert run.created_count == 9000
    assert run.duplicate_count == 0


def test_collect_source_failure_records_failed_run(db_session: Session) -> None:
    source = _get_nmc_source(db_session)

    def broken_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="unavailable")

    adapter = NmcWeatherAdapter(transport=httpx.MockTransport(broken_handler))
    with pytest.raises(CollectionFailed):
        collect_source(db_session, source, adapter)
    run = db_session.scalar(
        select(CollectionRun).order_by(CollectionRun.id.desc())
    )
    assert run is not None
    assert run.status == "failed"
    assert "中央气象台" in (run.error or "")


def test_manual_trigger_endpoint(client: TestClient, db_session: Session) -> None:
    source = _get_nmc_source(db_session)
    # 测试与共享开发库状态解耦：先确保信息源处于启用状态
    source.enabled = True
    db_session.flush()

    # 用依赖注入的 adapter 不可行（router 构建真实适配器），改为检查失败路径
    response = client.post(
        f"/api/v1/sources/{source.id}/run",
        headers={"X-User-Role": "admin"},
    )
    # 真实网络不可用时返回 502；若网络可用则成功。两种都可接受，但必须有运行记录
    assert response.status_code in {200, 502}
    run = db_session.scalar(
        select(CollectionRun)
        .where(CollectionRun.source_id == source.id)
        .order_by(CollectionRun.id.desc())
    )
    assert run is not None


def test_manual_trigger_rejects_manual_json(
    client: TestClient, db_session: Session
) -> None:
    manual = db_session.scalar(
        select(DataSource).where(DataSource.code == "manual-json")
    )
    assert manual is not None
    response = client.post(
        f"/api/v1/sources/{manual.id}/run",
        headers={"X-User-Role": "admin"},
    )
    assert response.status_code == 422


def test_manual_trigger_404(client: TestClient) -> None:
    response = client.post(
        "/api/v1/sources/999999/run",
        headers={"X-User-Role": "admin"},
    )
    assert response.status_code == 404


def test_cleanup_retention_deletes_expired_alerts_and_old_data(
    db_session: Session,
) -> None:
    """构造过期提醒与旧信号，验证清理逻辑。"""
    source = _get_nmc_source(db_session)
    supplier = Supplier(
        supplier_code="SUP-RET",
        legal_name="清理测试供应商",
        country_code="CN",
        registry_no="91310000RETTEST01",
    )
    db_session.add(supplier)
    db_session.flush()
    signal = RawSignal(
        source_id=source.id,
        title="旧信号",
        content="超过保留期的历史信号",
        fingerprint="old-signal-fp",
        raw_data={},
    )
    db_session.add(signal)
    db_session.flush()
    # 直接更新 collected_at 为 100 天前
    old = datetime.now(UTC) - timedelta(days=100)
    db_session.execute(
        RawSignal.__table__.update()
        .where(RawSignal.id == signal.id)
        .values(collected_at=old)
    )

    # 过期提醒

    event = RiskEvent(
        dedup_key="retention-test-event",
        event_type="compliance",
        severity="high",
        summary="过期事件",
        start_at=old,
        end_at=old,
        confidence=0.9,
        facts={},
    )
    db_session.add(event)
    db_session.flush()
    match = SupplierEventMatch(
        supplier_id=supplier.id,
        event_id=event.id,
        match_type="registry_no",
        score=90,
        reasons=["测试"],
        evidence=[],
    )
    db_session.add(match)
    db_session.flush()
    alert = RiskAlert(
        match_id=match.id,
        level="P1",
        score=90,
        score_detail={},
        status="expired",
        expires_at=old,
    )
    db_session.add(alert)
    db_session.flush()
    # 旧运行记录
    run = CollectionRun(source_id=source.id, status="succeeded", started_at=old)
    db_session.add(run)
    db_session.commit()

    result = cleanup_retention(
        db_session,
        RetentionSettings(signal_days=90, event_days=90, run_days=30),
    )
    assert result.expired_alerts >= 1
    assert result.deleted_events >= 1
    assert result.deleted_signals >= 1
    assert result.deleted_runs >= 1

    remaining = db_session.scalar(
        select(func.count()).select_from(RawSignal).where(RawSignal.id == signal.id)
    )
    assert remaining == 0


def test_cleanup_keeps_recent_data(db_session: Session) -> None:
    """新数据不应被清理。"""
    source = _get_nmc_source(db_session)
    signal = RawSignal(
        source_id=source.id,
        title="新信号",
        content="刚刚采集",
        fingerprint="fresh-fp",
        raw_data={},
    )
    db_session.add(signal)
    db_session.commit()

    result = cleanup_retention(
        db_session,
        RetentionSettings(signal_days=90, event_days=90, run_days=30),
    )
    assert result.deleted_signals == 0
    assert (
        db_session.scalar(
            select(func.count()).select_from(RawSignal).where(RawSignal.id == signal.id)
        )
        == 1
    )


def test_cleanup_keeps_old_signal_supporting_current_unbounded_alert(
    db_session: Session,
) -> None:
    """任务6补充回归：旧信号仍被现行无限提醒引用时，闭包整体保留。

    不依赖新 now_utc 签名，仅用真实时钟与超期旧数据刻画闭包保护；
    当前实现会误删该信号，失败即缺陷证据。
    """
    source = _get_nmc_source(db_session)
    supplier = Supplier(
        supplier_code="SUP-RET-KEEP",
        legal_name="保留测试供应商",
        country_code="CN",
        registry_no="91310000RETKEEP01",
    )
    db_session.add(supplier)
    db_session.flush()
    signal = RawSignal(
        source_id=source.id,
        title="被引用旧信号",
        content="仍支撑现行无限提醒",
        fingerprint="kept-old-signal-fp",
        raw_data={},
    )
    db_session.add(signal)
    db_session.flush()
    old = datetime.now(UTC) - timedelta(days=100)
    db_session.execute(
        RawSignal.__table__.update()
        .where(RawSignal.id == signal.id)
        .values(collected_at=old)
    )
    event = RiskEvent(
        dedup_key="retention-keep-event",
        event_type="compliance",
        severity="high",
        summary="现行事件",
        start_at=old,
        end_at=old,
        confidence=0.9,
        facts={},
    )
    db_session.add(event)
    db_session.flush()
    db_session.add(RiskEventSignal(event_id=event.id, signal_id=signal.id))
    match = SupplierEventMatch(
        supplier_id=supplier.id,
        event_id=event.id,
        match_type="registry_no",
        score=90,
        reasons=["测试"],
        evidence=[],
    )
    db_session.add(match)
    db_session.flush()
    alert = RiskAlert(
        match_id=match.id,
        level="P1",
        score=90,
        score_detail={},
        status="current",
        expires_at=None,
        expiry_kind="unbounded",
    )
    db_session.add(alert)
    db_session.commit()

    result = cleanup_retention(
        db_session,
        RetentionSettings(signal_days=90, event_days=90, run_days=30),
    )

    assert result.deleted_signals == 0
    assert (
        db_session.scalar(
            select(func.count()).select_from(RawSignal).where(RawSignal.id == signal.id)
        )
        == 1
    )
    assert (
        db_session.scalar(
            select(func.count()).select_from(RiskAlert).where(RiskAlert.id == alert.id)
        )
        == 1
    )
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(SupplierEventMatch)
            .where(SupplierEventMatch.id == match.id)
        )
        == 1
    )
    assert (
        db_session.scalar(
            select(func.count()).select_from(RiskEvent).where(RiskEvent.id == event.id)
        )
        == 1
    )


def _pmi_list_html(href: str, title: str) -> str:
    return (
        "<html><body>\n"
        "<ul>\n"
        f'<li><a href="{href}">{title}</a></li>\n'
        "</ul>\n"
        "</body></html>\n"
    )


def _pmi_detail_html(month: str, pmi: str, change: str) -> str:
    return f"""<html><body>
    国家统计局服务业调查中心 中国物流与采购联合会
    一、中国制造业采购经理指数运行情况
    {month}，制造业采购经理指数（PMI）为 {pmi}% ，比上月{change}个百分点。
    </body></html>
    """


def _pmi_handler(href: str, title: str, month: str, pmi: str, change: str):
    def handler(request: httpx.Request) -> httpx.Response:
        if href.split("/")[-1] in str(request.url):
            return httpx.Response(200, text=_pmi_detail_html(month, pmi, change))
        if "zxfb/" in str(request.url):
            return httpx.Response(200, text=_pmi_list_html(href, title))
        return httpx.Response(404, text="not found")

    return handler


def test_collection_supersession_when_two_pmi_months_collected(
    db_session: Session,
) -> None:
    """按月连续采集两期 PMI：上一期在新一期生效时结束，当前范围只含新一期。"""
    # Given
    source = DataSource(
        code="stats-pmi-test",
        name="PMI 测试信源",
        source_type="official_api",
        credibility=90,
        enabled=True,
        adapter_status="builtin",
        adapter_version=0,
        auth_type="none",
        login_config={},
        adapter_config={},
    )
    db_session.add(source)
    db_session.flush()

    # When：连续采集两期 PMI
    first = collect_source(
        db_session,
        source,
        StatsPmiAdapter(
            transport=httpx.MockTransport(
                _pmi_handler(
                    "./202608/t20260831_1964253.html",
                    "2026年8月中国采购经理指数运行情况",
                    "2026年8月",
                    "49.2",
                    "下降 1.1",
                )
            )
        ),
    )
    second = collect_source(
        db_session,
        source,
        StatsPmiAdapter(
            transport=httpx.MockTransport(
                _pmi_handler(
                    "./202609/t20260930_1970000.html",
                    "2026年9月中国采购经理指数运行情况",
                    "2026年9月",
                    "49.8",
                    "上升 0.6",
                )
            )
        ),
    )

    # Then
    assert first.created_count == 1
    assert second.created_count == 1
    signals = list(
        db_session.scalars(
            select(RawSignal)
            .where(RawSignal.source_id == source.id)
            .order_by(RawSignal.valid_from)
        )
    )
    assert len(signals) == 2
    aug, sep = signals
    assert aug.external_id == "stats-pmi-2026-08"
    assert sep.external_id == "stats-pmi-2026-09"
    assert aug.validity_state == "superseded"
    assert aug.valid_until == aug.valid_from
    assert sep.validity_state == "active"
    active = db_session.scalar(
        select(func.count())
        .select_from(RawSignal)
        .where(RawSignal.source_id == source.id, RawSignal.validity_state == "active")
    )
    assert active == 1


class _DeferredFetchAdapter:
    """fetch 阶段抛受控延后（域名冷却/租约/节流）的最小适配器。"""

    source_code = "deferred-test"

    async def fetch(self, cursor: str | None = None) -> list[RawSourceItem]:
        del cursor
        raise SourceFetchError(
            "信息源域名 official.example 已有请求正在执行，请稍后重试",
            error_kind="deferred",
        )


def test_collect_source_deferred_leaves_no_failed_run(db_session: Session) -> None:
    """受控延后是类型化结果：临时运行记录被删除，不落 failed 运行。"""
    # Given
    source = _get_nmc_source(db_session)

    # When / Then
    with pytest.raises(CollectionDeferred) as exc_info:
        collect_source(db_session, source, _DeferredFetchAdapter())
    assert exc_info.value.error_kind == "deferred"
    assert db_session.scalar(select(func.count()).select_from(CollectionRun)) == 0
    assert db_session.scalar(select(func.count()).select_from(RawSignal)) == 0


def test_collect_source_failure_after_fetch_still_records_failed_run(
    db_session: Session,
) -> None:
    """仅在 fetch 阶段延期清理：fetch 之后的异常仍记为失败运行。"""
    # Given
    source = _get_nmc_source(db_session)

    class _FingerprintBoomAdapter:
        source_code = "boom-test"

        async def fetch(self, cursor: str | None = None) -> list[RawSourceItem]:
            del cursor
            return [RawSourceItem(external_id="boom-1", title="标题", content="正文")]

        def normalize(self, item: RawSourceItem) -> ManualSignalInput:
            return ManualSignalInput(
                external_id=item.external_id, title=item.title, content=item.content
            )

        def fingerprint(self, signal: ManualSignalInput) -> str:
            del signal
            raise RuntimeError("指纹计算失败（测试注入）")

    # When / Then
    with pytest.raises(CollectionFailed):
        collect_source(db_session, source, _FingerprintBoomAdapter())
    run = db_session.scalar(select(CollectionRun).order_by(CollectionRun.id.desc()))
    assert run is not None
    assert run.status == "failed"


def test_finalize_stale_collection_runs_marks_old_running_failed(
    db_session: Session,
) -> None:
    """超过保守阈值的遗留 running 运行被收尾为失败并写入稳定错误文本。"""
    # Given
    source = _get_nmc_source(db_session)
    now = datetime.now(UTC)
    stale = CollectionRun(
        source_id=source.id,
        status="running",
        started_at=now - timedelta(seconds=COLLECTION_RUN_STALE_SECONDS + 60),
    )
    db_session.add(stale)
    db_session.commit()
    stale_id = stale.id

    # When
    finalized = finalize_stale_collection_runs(
        db_session,
        stale_before=now - timedelta(seconds=COLLECTION_RUN_STALE_SECONDS),
        now=now,
    )

    # Then
    assert finalized == 1
    run = db_session.get(CollectionRun, stale_id)
    assert run is not None
    assert run.status == "failed"
    assert run.finished_at == now
    assert run.error == STALE_COLLECTION_RUN_ERROR


def test_finalize_stale_collection_runs_leaves_fresh_and_terminal_unchanged(
    db_session: Session,
) -> None:
    """阈值内的新鲜 running 与终态记录保持原样，不被误收尾。"""
    # Given
    source = _get_nmc_source(db_session)
    now = datetime.now(UTC)
    fresh = CollectionRun(
        source_id=source.id,
        status="running",
        started_at=now - timedelta(minutes=5),
    )
    succeeded = CollectionRun(
        source_id=source.id,
        status="succeeded",
        started_at=now - timedelta(hours=2),
        finished_at=now - timedelta(hours=2),
    )
    db_session.add_all([fresh, succeeded])
    db_session.commit()
    fresh_id, succeeded_id = fresh.id, succeeded.id

    # When
    finalized = finalize_stale_collection_runs(
        db_session,
        stale_before=now - timedelta(seconds=COLLECTION_RUN_STALE_SECONDS),
        now=now,
    )

    # Then
    assert finalized == 0
    db_session.refresh(fresh)
    db_session.refresh(succeeded)
    fresh_run = db_session.get(CollectionRun, fresh_id)
    succeeded_run = db_session.get(CollectionRun, succeeded_id)
    assert fresh_run is not None and succeeded_run is not None
    assert fresh_run.status == "running"
    assert fresh_run.finished_at is None
    assert fresh_run.error is None
    assert succeeded_run.status == "succeeded"
    assert succeeded_run.error is None


def test_finalize_stale_collection_runs_rejects_naive_and_future_threshold(
    db_session: Session,
) -> None:
    """收尾入参沿用项目时区校验：朴素时间与晚于当前时间的阈值直接拒绝。"""
    # Given
    now = datetime.now(UTC)

    # When / Then
    with pytest.raises(ValueError):
        finalize_stale_collection_runs(
            db_session, stale_before=datetime.now(), now=now
        )
    with pytest.raises(ValueError):
        finalize_stale_collection_runs(
            db_session,
            stale_before=now + timedelta(minutes=1),
            now=now,
        )


def test_collect_enabled_sources_leaves_stale_running_untouched(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """采集循环不再扫描/改写 running 记录，超阈值的活动长任务保持原样。

    误伤场景：活动网络采集不持行锁，若每轮按 started_at 收尾，一个仍在
    执行的长任务会被另一轮采集误标为失败。收尾只允许发生在 Scheduler 启动时。
    """
    # Given
    import app.scheduler.jobs as jobs_module

    source = _get_nmc_source(db_session)
    stale = CollectionRun(
        source_id=source.id,
        status="running",
        started_at=datetime.now(UTC) - timedelta(minutes=45),
    )
    db_session.add(stale)
    db_session.commit()
    stale_id = stale.id
    visited: list[int] = []

    @contextmanager
    def _session_factory():
        yield db_session

    def _skip_adapter(_source: DataSource):
        visited.append(_source.id)
        raise ValueError("测试跳过真实适配器构建")

    monkeypatch.setattr(jobs_module, "SessionLocal", _session_factory)
    monkeypatch.setattr(jobs_module, "build_pull_adapter", _skip_adapter)

    # When
    summary = jobs_module._collect_enabled_sources(source_ids=[source.id])

    # Then
    assert summary == {}
    assert visited == [source.id], "采集循环应实际进入适配器构建步骤"
    run = db_session.get(CollectionRun, stale_id)
    assert run is not None
    assert run.status == "running"
    assert run.finished_at is None
    assert run.error is None


def test_manual_run_returns_409_collection_deferred(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """单源手动触发：受控延后返回稳定 409 collection_deferred，不产生失败运行。"""
    # Given
    db_session.execute(update(DataSource).values(enabled=False))
    source = _get_nmc_source(db_session)
    source.enabled = True
    db_session.flush()
    import app.signals.router as source_router

    def _deferred(session: Session, src: DataSource, adapter: object) -> None:
        del session, src, adapter
        raise CollectionDeferred("信息源域名冷却中")

    monkeypatch.setattr(source_router, "collect_source", _deferred)

    # When
    response = client.post(f"/api/v1/sources/{source.id}/run")

    # Then
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "collection_deferred"
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(CollectionRun)
            .where(CollectionRun.source_id == source.id)
        )
        == 0
    )


def test_run_all_reports_deferred_separately(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """全量刷新：受控延后单列 outcome/count，不计入 failed。"""
    # Given
    db_session.execute(update(DataSource).values(enabled=False))
    source = _get_nmc_source(db_session)
    source.enabled = True
    db_session.flush()
    import app.signals.router as source_router

    def _deferred(session: Session, src: DataSource, adapter: object) -> None:
        del session, src, adapter
        raise CollectionDeferred("信息源域名冷却中")

    monkeypatch.setattr(source_router, "collect_source", _deferred)

    # When
    response = client.post("/api/v1/sources/run-all")

    # Then
    assert response.status_code == 200
    body = response.json()
    assert body["deferred"] == 1
    assert body["failed"] == 0
    item = next(entry for entry in body["items"] if entry["code"] == "nmc-weather")
    assert item["status"] == "deferred"


# ---------------------------------------------------------------------------
# 天眼查手动单供应商核查端点（POST /api/v1/sources/{id}/run-tyc-batch?supplier_id=）
# ---------------------------------------------------------------------------


def _committed_tianyancha(db_session: Session) -> DataSource:
    """返回提交态配置后的天眼查 ORM 对象（D9 执行器跨连接可见）。"""
    db_session.expire_all()
    source = db_session.scalar(select(DataSource).where(DataSource.code == "tianyancha"))
    assert source is not None, "迁移应已注册 tianyancha 信息源"
    return source


def _route_supplier(
    db_session: Session, code: str, name: str, *, enabled: bool = True
) -> Supplier:
    supplier = Supplier(
        supplier_code=code, legal_name=name, country_code="CN", enabled=enabled
    )
    db_session.add(supplier)
    db_session.flush()
    return supplier


def _literal_bucket(code: str) -> int:
    return int(hashlib.sha256(code.encode()).hexdigest(), 16) % 2


def test_run_tyc_batch_endpoint_returns_per_tool_counts(
    client: TestClient,
    db_session: Session,
    committed_tyc_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Given 已启用天眼查与一个启用供应商，When 手动单供应商核查，
    Then 200 返回逐工具计数与报告信号。
    """
    import app.agent.tyc_gateway as tyc_gateway_module

    configure_committed_tyc(
        daily_limit=100,
        monthly_limit=1000,
        dimensions=["get_risk_overview", "get_judicial_case"],
    )
    source = _committed_tianyancha(db_session)
    supplier = _route_supplier(db_session, "SUP-ROUTE-TYC-OK", "路由单供应商核查有限公司")
    stub = MultidimMcpStub()
    monkeypatch.setattr(
        tyc_gateway_module, "build_tyc_gateway", lambda **kwargs: stub.gateway()
    )

    response = client.post(
        f"/api/v1/sources/{source.id}/run-tyc-batch?supplier_id={supplier.id}"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["source_id"] == source.id
    assert body["supplier_id"] == supplier.id
    assert body["shard_index"] == _literal_bucket("SUP-ROUTE-TYC-OK")
    assert body["shard_count"] == 2
    assert body["targeted_count"] == 1
    assert body["attempted_count"] == 1
    assert body["created_count"] == 1
    assert body["duplicate_count"] == 0
    assert body["empty_count"] == 0
    assert body["failed_count"] == 0
    assert body["quota_exhausted"] is False
    assert body["per_tool_counts"]["search_companies"]["success_with_records"] == 1
    assert body["per_tool_counts"]["get_risk_overview"]["success_with_records"] == 1
    assert body["per_tool_counts"]["get_judicial_case"]["success_with_records"] == 1
    signal = db_session.scalar(
        select(RawSignal).where(RawSignal.external_id.like("tyc-SUP-ROUTE-TYC-OK-%"))
    )
    assert signal is not None
    assert signal.raw_data["report_kind"] == "supplier_profile"


def test_run_tyc_batch_endpoint_requires_supplier_id(
    client: TestClient, db_session: Session, committed_tyc_env: None
) -> None:
    """缺 supplier_id → 422（不再提供同步全量）。"""
    configure_committed_tyc(daily_limit=100, monthly_limit=1000)
    source = _committed_tianyancha(db_session)

    response = client.post(f"/api/v1/sources/{source.id}/run-tyc-batch")

    assert response.status_code == 422


def test_run_tyc_batch_endpoint_422_when_supplier_id_malformed(
    client: TestClient, db_session: Session, committed_tyc_env: None
) -> None:
    configure_committed_tyc(daily_limit=100, monthly_limit=1000)
    source = _committed_tianyancha(db_session)

    response = client.post(f"/api/v1/sources/{source.id}/run-tyc-batch?supplier_id=abc")

    assert response.status_code == 422


def test_run_tyc_batch_endpoint_404_when_source_missing(client: TestClient) -> None:
    response = client.post("/api/v1/sources/999999/run-tyc-batch?supplier_id=1")
    assert response.status_code == 404
    assert response.json()["detail"] == "信息源不存在"


def test_run_tyc_batch_endpoint_422_when_not_tianyancha(
    client: TestClient, db_session: Session
) -> None:
    source = _get_nmc_source(db_session)
    source.enabled = True
    db_session.flush()
    response = client.post(f"/api/v1/sources/{source.id}/run-tyc-batch?supplier_id=1")
    assert response.status_code == 422


def test_run_tyc_batch_endpoint_409_when_source_disabled(
    client: TestClient, db_session: Session, committed_tyc_env: None
) -> None:
    configure_committed_tyc(daily_limit=100, monthly_limit=1000, enabled=False)
    source = _committed_tianyancha(db_session)

    response = client.post(f"/api/v1/sources/{source.id}/run-tyc-batch?supplier_id=1")

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "source_inactive"


def test_run_tyc_batch_endpoint_409_when_key_unavailable(
    client: TestClient, db_session: Session, committed_tyc_env: None
) -> None:
    configure_committed_tyc(daily_limit=100, monthly_limit=1000, api_key=None)
    source = _committed_tianyancha(db_session)

    response = client.post(f"/api/v1/sources/{source.id}/run-tyc-batch?supplier_id=1")

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "unavailable"
    assert detail["reason"] == "key_unavailable"


def test_run_tyc_batch_endpoint_409_when_start_quota_unavailable(
    client: TestClient, db_session: Session, committed_tyc_env: None
) -> None:
    configure_committed_tyc(daily_limit=1, monthly_limit=50)
    with Session(engine) as external:
        external.add(
            TycUsageRecord(
                tool_name="verify_company", company_name="预占额度", status="success"
            )
        )
        external.commit()
    source = _committed_tianyancha(db_session)

    response = client.post(f"/api/v1/sources/{source.id}/run-tyc-batch?supplier_id=1")

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "unavailable"
    assert detail["reason"] == "quota_exhausted"
    assert detail["daily_remaining"] == 0


def test_run_tyc_batch_endpoint_404_when_supplier_unknown_or_disabled(
    client: TestClient, db_session: Session, committed_tyc_env: None
) -> None:
    """稳定契约：不存在或未启用供应商一律 404「供应商不存在或未启用」。"""
    configure_committed_tyc(daily_limit=100, monthly_limit=1000)
    source = _committed_tianyancha(db_session)

    unknown = client.post(f"/api/v1/sources/{source.id}/run-tyc-batch?supplier_id=999999")
    assert unknown.status_code == 404
    assert unknown.json()["detail"] == "供应商不存在或未启用"

    disabled = _route_supplier(
        db_session, "SUP-ROUTE-TYC-DISABLED", "路由停用供应商", enabled=False
    )
    response = client.post(
        f"/api/v1/sources/{source.id}/run-tyc-batch?supplier_id={disabled.id}"
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "供应商不存在或未启用"


def test_run_tyc_batch_endpoint_reports_midway_quota_exhaustion(
    client: TestClient,
    db_session: Session,
    committed_tyc_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When 中途额度耗尽（锚定成功后维度被锁内拒绝），
    Then 仍返回 200 汇总并标记 quota_exhausted。

    修复登记红灯的方式：额度配置改为提交态（独立执行器 Session 可见），
    并由 committed_tyc_env 在用例前后清理跨连接计费行消除跨用例泄漏；
    断言保持「200 + quota_exhausted=True + attempted 正确 + 中途停止」。
    """
    import app.agent.tyc_gateway as tyc_gateway_module

    configure_committed_tyc(daily_limit=1, monthly_limit=50)
    source = _committed_tianyancha(db_session)
    supplier = _route_supplier(db_session, "SUP-ROUTE-TYC-Q1", "路由额度首单有限公司")
    stub = MultidimMcpStub()
    monkeypatch.setattr(
        tyc_gateway_module, "build_tyc_gateway", lambda **kwargs: stub.gateway()
    )

    response = client.post(
        f"/api/v1/sources/{source.id}/run-tyc-batch?supplier_id={supplier.id}"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["targeted_count"] == 1
    assert body["attempted_count"] == 1
    assert body["created_count"] == 1
    assert body["quota_exhausted"] is True
    assert body["per_tool_counts"]["search_companies"]["success_with_records"] == 1
    assert (
        sum(counts["quota_exhausted"] for counts in body["per_tool_counts"].values())
        == 12
    )
    # 状态隔离 + 提交态可见：本用例只产生 1 条跨连接消费（锚定成功计费）。
    assert committed_tyc_rows() == [
        ("search_companies", "路由额度首单有限公司", "success")
    ]
    assert committed_tyc_daily_used() == 1
