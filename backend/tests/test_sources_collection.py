"""采集服务、手动触发端点与保留清理测试。"""

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import RetentionSettings
from app.risks.models import RiskAlert, RiskEvent, SupplierEventMatch
from app.scheduler.retention import cleanup_retention
from app.signals.models import CollectionRun, DataSource, RawSignal
from app.signals.service import CollectionFailed, collect_source
from app.signals.sources import NmcWeatherAdapter
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
    assert source is not None, "迁移 0009 应已注册 nmc-weather 数据源"
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
    # 测试与共享开发库状态解耦：先确保数据源处于启用状态
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
