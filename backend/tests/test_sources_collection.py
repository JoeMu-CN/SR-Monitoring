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
from app.signals.declarative import AdapterSpec, DeclarativeSourceAdapter
from app.signals.models import CollectionRun, DataSource, RawSignal
from app.signals.service import CollectionFailed, collect_source
from app.signals.sources import NmcWeatherAdapter, StatsPmiAdapter
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
