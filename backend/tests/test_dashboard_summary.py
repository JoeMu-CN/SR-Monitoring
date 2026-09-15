"""任务 4：总览汇总全量契约（当前统计与期间新增明确分离）。"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from app.auth.security import require_permission
from app.risks.models import RiskAlert, RiskEvent, RiskEventSignal, SupplierEventMatch
from app.signals.models import DataSource, RawSignal
from app.suppliers.models import Supplier

_LEVEL_SCORES = {"P1": 95, "P2": 80, "P3": 60, "P4": 30}


def _make_source(db_session: Session, label: str) -> DataSource:
    source = DataSource(
        code=f"dash-{label}",
        name=f"总览测试源 {label}",
        source_type="api",
        credibility=80,
        enabled=True,
        adapter_status="builtin",
        adapter_version=0,
        auth_type="none",
        login_config={},
        adapter_config={},
    )
    db_session.add(source)
    db_session.flush()
    return source


def _make_signal(
    db_session: Session, source: DataSource, label: str, *, moment: datetime
) -> RawSignal:
    signal = RawSignal(
        source_id=source.id,
        external_id=f"dash-{label}",
        title=f"总览测试信号 {label}",
        content=f"总览测试信号正文 {label}",
        published_at=moment,
        collected_at=moment,
        fingerprint=f"dash-{label}",
        raw_data={},
        validity_profile="weather_alert",
        validity_state="active",
        valid_from=moment,
        valid_until=moment + timedelta(days=30),
        validity_mode="fixed_days",
        lifecycle_action="assert",
        validity_policy_version="policy-dash",
        validity_reason={
            "code": "policy_resolved",
            "anchor_source": "published_at",
            "details": {},
        },
    )
    db_session.add(signal)
    db_session.flush()
    return signal


def _make_supplier(db_session: Session, code: str, *, enabled: bool = True) -> Supplier:
    supplier = Supplier(
        supplier_code=code,
        legal_name=f"{code} 供应商",
        country_code="CN",
        registry_no=None,
        enabled=enabled,
    )
    db_session.add(supplier)
    db_session.flush()
    return supplier


def _make_event(db_session: Session, label: str) -> RiskEvent:
    event = RiskEvent(
        dedup_key=f"dash-{label}",
        event_type="weather",
        event_subtype="weather_alert",
        severity="high",
        summary=f"总览测试事件 {label}",
        confidence=0.9,
        facts={},
        validity_state="active",
        validity_reason={
            "code": "effective_signal_support",
            "anchor_source": "published_at",
            "details": {},
        },
    )
    db_session.add(event)
    db_session.flush()
    return event


def _make_match(
    db_session: Session, supplier: Supplier, event: RiskEvent
) -> SupplierEventMatch:
    match = SupplierEventMatch(
        supplier_id=supplier.id,
        event_id=event.id,
        match_type="legal_name",
        score=30,
        reasons=["测试"],
        evidence=[],
    )
    db_session.add(match)
    db_session.flush()
    return match


def _make_alert(
    db_session: Session,
    match: SupplierEventMatch,
    *,
    level: str = "P1",
    status: str = "current",
    expiry_kind: str = "unbounded",
    expires_at: datetime | None = None,
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
) -> RiskAlert:
    alert = RiskAlert(
        match_id=match.id,
        level=level,
        score=_LEVEL_SCORES[level],
        score_detail={"rule_version": "rule-v1"},
        status=status,
        expires_at=expires_at,
        expiry_kind=expiry_kind,
    )
    if created_at is not None:
        alert.created_at = created_at
    if updated_at is not None:
        alert.updated_at = updated_at
    db_session.add(alert)
    db_session.flush()
    return alert


def _seed_alert(
    db_session: Session,
    label: str,
    source: DataSource,
    *,
    level: str = "P1",
    status: str = "current",
    expiry_kind: str = "unbounded",
    expires_at: datetime | None = None,
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
    enabled: bool = True,
) -> RiskAlert:
    moment = datetime.now(UTC)
    signal = _make_signal(db_session, source, label, moment=moment)
    supplier = _make_supplier(db_session, f"DASH-{label}", enabled=enabled)
    event = _make_event(db_session, label)
    db_session.add(RiskEventSignal(event_id=event.id, signal_id=signal.id))
    match = _make_match(db_session, supplier, event)
    return _make_alert(
        db_session,
        match,
        level=level,
        status=status,
        expiry_kind=expiry_kind,
        expires_at=expires_at,
        created_at=created_at,
        updated_at=updated_at,
    )


def _cleanup_committed_probe(
    supplier_codes: tuple[str, ...],
    source_codes: tuple[str, ...],
    usernames: tuple[str, ...],
) -> None:
    """删除测试用真实提交（真实 SessionLocal）的探针数据。

    这些探针必须先提交才能被独立会话读到；提交后不会随 db_session 事务回滚，
    因此必须在测试末尾显式清理，否则会污染后续文件的确定性断言
    （如 test_e2e_seed 的全量供应商/提醒/证据闭包契约）。
    """
    from app.auth.models import AuthSession, User
    from app.database import SessionLocal

    with SessionLocal.begin() as session:
        supplier_ids = list(
            session.scalars(
                select(Supplier.id).where(Supplier.supplier_code.in_(supplier_codes))
            )
        )
        match_ids = list(
            session.scalars(
                select(SupplierEventMatch.id).where(
                    SupplierEventMatch.supplier_id.in_(supplier_ids)
                )
            )
        )
        event_ids = list(
            session.scalars(
                select(SupplierEventMatch.event_id).where(
                    SupplierEventMatch.supplier_id.in_(supplier_ids)
                )
            )
        )
        session.execute(delete(RiskAlert).where(RiskAlert.match_id.in_(match_ids)))
        session.execute(
            delete(SupplierEventMatch).where(
                SupplierEventMatch.supplier_id.in_(supplier_ids)
            )
        )
        session.execute(
            delete(RiskEventSignal).where(RiskEventSignal.event_id.in_(event_ids))
        )
        session.execute(delete(RiskEvent).where(RiskEvent.id.in_(event_ids)))
        source_ids = list(
            session.scalars(select(DataSource.id).where(DataSource.code.in_(source_codes)))
        )
        session.execute(delete(RawSignal).where(RawSignal.source_id.in_(source_ids)))
        session.execute(delete(DataSource).where(DataSource.id.in_(source_ids)))
        session.execute(delete(Supplier).where(Supplier.id.in_(supplier_ids)))
        user_ids = list(
            session.scalars(select(User.id).where(User.username.in_(usernames)))
        )
        session.execute(delete(AuthSession).where(AuthSession.user_id.in_(user_ids)))
        session.execute(delete(User).where(User.id.in_(user_ids)))


def test_dashboard_summary_requires_session_returns_401_no_data(
    client: TestClient,
) -> None:
    # Given
    client.cookies.clear()
    client.headers.pop("X-CSRF-Token", None)

    # When
    response = client.get("/api/v1/dashboard/summary")

    # Then
    assert response.status_code == 401
    assert "total_current" not in response.json()


def test_dashboard_summary_permission_denial_returns_403() -> None:
    # Given
    deny_risk_view = require_permission("dashboard_summary_probe_permission")
    probe_user = SimpleNamespace(role="viewer", status="active")

    # When
    try:
        deny_risk_view(probe_user)
        denied = False
    except HTTPException as exc:
        denied = exc.status_code == 403

    # Then
    assert denied is True


def test_dashboard_summary_viewer_can_read(client, auth_as) -> None:
    # Given
    auth_as("viewer", "dashboard-viewer")

    # When
    response = client.get("/api/v1/dashboard/summary")

    # Then
    assert response.status_code == 200, response.text
    assert "total_current" in response.json()


def test_dashboard_summary_rejects_invalid_days_returns_422(
    client: TestClient,
) -> None:
    # Given / When
    bad_window = client.get("/api/v1/dashboard/summary", params={"days": 45})
    negative = client.get("/api/v1/dashboard/summary", params={"days": -1})
    not_a_number = client.get("/api/v1/dashboard/summary", params={"days": "abc"})

    # Then
    assert bad_window.status_code == 422
    assert negative.status_code == 422
    assert not_a_number.status_code == 422


def test_dashboard_summary_defaults_to_thirty_day_window(client: TestClient) -> None:
    # Given / When
    response = client.get("/api/v1/dashboard/summary")

    # Then
    assert response.status_code == 200, response.text
    assert response.json()["window_days"] == 30


def test_period_new_count_includes_expired_and_excludes_outside_window(
    client: TestClient, db_session: Session
) -> None:
    # Given
    import app.risks.dashboard as dashboard

    fixed = datetime(2026, 3, 15, 12, 0, tzinfo=UTC)
    source = _make_source(db_session, "period")
    seed = _seed_alert
    in_current = seed(
        db_session, "period-in", source, created_at=fixed - timedelta(days=10)
    )
    _in_expired = seed(
        db_session,
        "period-expired",
        source,
        status="expired",
        expiry_kind="finite",
        expires_at=fixed - timedelta(days=9),
        created_at=fixed - timedelta(days=10),
    )
    seed(db_session, "period-out", source, created_at=fixed - timedelta(days=40))
    seed(
        db_session,
        "period-out-expired",
        source,
        status="expired",
        expiry_kind="finite",
        expires_at=fixed - timedelta(days=39),
        created_at=fixed - timedelta(days=40),
    )
    db_session.commit()

    # When
    import unittest.mock as mock

    with mock.patch.object(dashboard, "utc_now", return_value=fixed):
        response = client.get("/api/v1/dashboard/summary", params={"days": 30})
        assert response.status_code == 200, response.text
        payload = response.json()

    # Then
    assert payload["period_new_count"] == 2
    assert payload["window_days"] == 30
    assert payload["total_current"] == 2
    assert in_current.id in {item["id"] for item in payload["recent_alerts"]}


def test_window_edges_use_half_open_interval_with_frozen_clock(
    client: TestClient, db_session: Session
) -> None:
    # Given
    import unittest.mock as mock

    import app.risks.dashboard as dashboard

    fixed = datetime(2026, 3, 15, 12, 0, tzinfo=UTC)
    source = _make_source(db_session, "edge")
    seed = _seed_alert
    seed(db_session, "edge-start", source, created_at=fixed - timedelta(days=30))
    seed(db_session, "edge-asof", source, created_at=fixed)
    seed(
        db_session,
        "edge-before",
        source,
        created_at=fixed - timedelta(days=30) - timedelta(microseconds=1),
    )
    seed(
        db_session,
        "edge-expired",
        source,
        status="expired",
        expiry_kind="finite",
        expires_at=fixed,
        created_at=fixed - timedelta(days=1),
    )
    seed(
        db_session,
        "edge-today",
        source,
        created_at=fixed - timedelta(hours=1),
    )
    db_session.commit()

    # When
    with mock.patch.object(dashboard, "utc_now", return_value=fixed):
        response = client.get("/api/v1/dashboard/summary", params={"days": 30})
        assert response.status_code == 200, response.text
        payload = response.json()
        week_response = client.get("/api/v1/dashboard/summary", params={"days": 7})
        assert week_response.status_code == 200, week_response.text
        week = week_response.json()

    # Then
    assert payload["as_of"].startswith("2026-03-15T12:00:00")
    assert payload["window_start"].startswith("2026-02-13T12:00:00")
    assert payload["total_current"] == 4
    assert payload["period_new_count"] == 3
    assert payload["today_new"] == 1
    assert week["total_current"] == 4
    assert week["period_new_count"] == 2


def test_current_stats_do_not_change_with_days(
    client: TestClient, db_session: Session
) -> None:
    # Given
    base = datetime.now(UTC)
    source = _make_source(db_session, "stable")
    _seed_alert(db_session, "stable-now", source, created_at=base - timedelta(hours=1))
    _seed_alert(
        db_session,
        "stable-old",
        source,
        level="P2",
        created_at=base - timedelta(days=20),
    )
    _seed_alert(
        db_session,
        "stable-expired",
        source,
        status="expired",
        expiry_kind="finite",
        expires_at=base - timedelta(days=2),
        created_at=base - timedelta(days=10),
    )
    db_session.commit()

    # When
    week_response = client.get("/api/v1/dashboard/summary", params={"days": 7})
    assert week_response.status_code == 200, week_response.text
    week = week_response.json()
    month_response = client.get("/api/v1/dashboard/summary", params={"days": 30})
    assert month_response.status_code == 200, month_response.text
    month = month_response.json()
    quarter_response = client.get("/api/v1/dashboard/summary", params={"days": 90})
    assert quarter_response.status_code == 200, quarter_response.text
    quarter = quarter_response.json()

    # Then
    assert week["total_current"] == month["total_current"] == quarter["total_current"]
    assert week["level_counts"] == month["level_counts"] == quarter["level_counts"]
    assert week["today_new"] == month["today_new"] == quarter["today_new"]
    assert (week["period_new_count"], month["period_new_count"]) == (1, 3)
    assert quarter["period_new_count"] == 3


def test_large_scale_counts_match_sql_truth(
    client: TestClient, db_session: Session
) -> None:
    # Given
    base = datetime.now(UTC)
    source_a = _make_source(db_session, "bulk-a")
    source_b = _make_source(db_session, "bulk-b")
    plan: list[tuple[str, str, timedelta, str]] = []
    plan += [("P1", "current", timedelta(hours=1), f"bulk-p1-{i:02d}") for i in range(8)]
    plan += [
        ("P2", "current", timedelta(days=10), f"bulk-p2-{i:02d}") for i in range(22)
    ]
    plan += [
        ("P3", "current", timedelta(days=40), f"bulk-p3-{i:02d}") for i in range(45)
    ]
    plan += [("P4", "current", timedelta(hours=2), f"bulk-p4-{i:02d}") for i in range(45)]
    plan += [
        ("P2", "expired", timedelta(days=10), f"bulk-x-{i:02d}") for i in range(15)
    ]
    for index, (level, status, age, label) in enumerate(plan):
        source = source_a if index % 3 else source_b
        moment = base - age
        signal_moment = moment
        if index == 0:
            event = _make_event(db_session, "bulk-shared")
            for extra in range(3):
                extra_signal = _make_signal(
                    db_session, source_a, f"bulk-shared-{extra}", moment=signal_moment
                )
                db_session.add(
                    RiskEventSignal(event_id=event.id, signal_id=extra_signal.id)
                )
            supplier = _make_supplier(db_session, "DASH-bulk-shared")
            match = _make_match(db_session, supplier, event)
            _make_alert(db_session, match, level=level, created_at=moment)
            continue
        if index == 1:
            event = _make_event(db_session, "bulk-cross")
            for cross_source, suffix in ((source_a, "x-a"), (source_b, "x-b")):
                cross_signal = _make_signal(
                    db_session, cross_source, f"bulk-cross-{suffix}", moment=signal_moment
                )
                db_session.add(
                    RiskEventSignal(event_id=event.id, signal_id=cross_signal.id)
                )
            supplier = _make_supplier(db_session, "DASH-bulk-cross")
            match = _make_match(db_session, supplier, event)
            _make_alert(db_session, match, level=level, created_at=moment)
            continue
        expired_kwargs: dict[str, object] = (
            {
                "status": "expired",
                "expiry_kind": "finite",
                "expires_at": moment + timedelta(hours=1),
            }
            if status == "expired"
            else {}
        )
        _seed_alert(
            db_session,
            label,
            source,
            level=level,
            created_at=moment,
            enabled=(index % 13 != 0),
            **expired_kwargs,
        )
    for tail in range(5):
        _make_supplier(db_session, f"DASH-bulk-idle-{tail:03d}")
    db_session.commit()

    # When
    summary_response = client.get("/api/v1/dashboard/summary", params={"days": 30})
    assert summary_response.status_code == 200, summary_response.text
    payload = summary_response.json()
    as_of = datetime.fromisoformat(payload["as_of"])
    window_start = datetime.fromisoformat(payload["window_start"])

    # Then
    level_truth = dict(
        db_session.execute(
            text(
                "SELECT level, COUNT(*) FROM risk_alerts WHERE status = 'current' AND "
                "(expiry_kind = 'legacy' OR (expiry_kind = 'finite' AND expires_at > :now) "
                "OR (expiry_kind = 'unbounded' AND expires_at IS NULL)) GROUP BY level"
            ),
            {"now": as_of},
        ).all()
    )
    assert payload["total_current"] == sum(level_truth.values()) == 120
    assert {item["level"]: item["count"] for item in payload["level_counts"]} == {
        "P1": level_truth.get("P1", 0),
        "P2": level_truth.get("P2", 0),
        "P3": level_truth.get("P3", 0),
        "P4": level_truth.get("P4", 0),
    }
    period_truth = db_session.scalar(
        text(
            "SELECT COUNT(*) FROM risk_alerts "
            "WHERE created_at >= :start AND created_at < :asof"
        ),
        {"start": window_start, "asof": as_of},
    )
    assert payload["period_new_count"] == period_truth
    supplier_truth = db_session.execute(
        text("SELECT COUNT(*), COUNT(*) FILTER (WHERE enabled) FROM suppliers")
    ).one()
    assert payload["supplier_total"] == supplier_truth[0] >= 125
    assert payload["active_supplier_total"] == supplier_truth[1]
    source_truth = dict(
        db_session.execute(
            text(
                "SELECT s.code, COUNT(DISTINCT a.id) FROM risk_alerts a "
                "JOIN supplier_event_matches m ON m.id = a.match_id "
                "JOIN risk_event_signals es ON es.event_id = m.event_id "
                "JOIN raw_signals g ON g.id = es.signal_id "
                "JOIN data_sources s ON s.id = g.source_id "
                "WHERE a.status = 'current' AND (a.expiry_kind = 'legacy' OR "
                "(a.expiry_kind = 'finite' AND a.expires_at > :now) OR "
                "(a.expiry_kind = 'unbounded' AND a.expires_at IS NULL)) "
                "GROUP BY s.code"
            ),
            {"now": as_of},
        ).all()
    )
    distribution = {item["code"]: item["count"] for item in payload["source_distribution"]}
    assert distribution == source_truth
    assert sum(distribution.values()) >= payload["total_current"]


def test_source_distribution_dedupes_alert_per_source(
    client: TestClient, db_session: Session
) -> None:
    # Given
    moment = datetime.now(UTC)
    source_a = _make_source(db_session, "src-a")
    source_b = _make_source(db_session, "src-b")
    event = _make_event(db_session, "dedup")
    for extra in range(2):
        signal = _make_signal(db_session, source_a, f"dedup-a-{extra}", moment=moment)
        db_session.add(RiskEventSignal(event_id=event.id, signal_id=signal.id))
    lone = _make_signal(db_session, source_b, "dedup-b", moment=moment)
    db_session.add(RiskEventSignal(event_id=event.id, signal_id=lone.id))
    supplier = _make_supplier(db_session, "DASH-dedup")
    match = _make_match(db_session, supplier, event)
    _make_alert(db_session, match, level="P1")
    db_session.commit()

    # When
    dedup_response = client.get("/api/v1/dashboard/summary")
    assert dedup_response.status_code == 200, dedup_response.text
    payload = dedup_response.json()

    # Then
    distribution = {item["code"]: item["count"] for item in payload["source_distribution"]}
    assert distribution.get("dash-src-a") == 1
    assert distribution.get("dash-src-b") == 1


def test_history_may_be_partial_when_days_exceeds_retention(
    client: TestClient, db_session: Session, monkeypatch
) -> None:
    # Given
    monkeypatch.setenv("RETENTION_EVENT_DAYS", "7")

    # When
    narrow_response = client.get("/api/v1/dashboard/summary", params={"days": 7})
    assert narrow_response.status_code == 200, narrow_response.text
    narrow = narrow_response.json()
    wide_response = client.get("/api/v1/dashboard/summary", params={"days": 30})
    assert wide_response.status_code == 200, wide_response.text
    wide = wide_response.json()

    # Then
    assert narrow["retention_window_days"] == 7
    assert narrow["history_may_be_partial"] is False
    assert wide["retention_window_days"] == 7
    assert wide["history_may_be_partial"] is True


def test_empty_database_returns_zero_contract(client: TestClient) -> None:
    # Given / When
    empty_response = client.get("/api/v1/dashboard/summary")
    assert empty_response.status_code == 200, empty_response.text
    payload = empty_response.json()

    # Then
    assert payload["total_current"] == 0
    assert payload["today_new"] == 0
    assert payload["period_new_count"] == 0
    assert payload["supplier_total"] == 0
    assert payload["active_supplier_total"] == 0
    assert [item["count"] for item in payload["level_counts"]] == [0, 0, 0, 0]
    assert payload["type_distribution"] == []
    assert payload["recent_alerts"] == []
    assert payload["source_distribution"] == []
    assert payload["history_may_be_partial"] is False


def test_recent_alerts_stable_order_and_limit_ten(
    client: TestClient, db_session: Session
) -> None:
    # Given
    moment = datetime.now(UTC)
    source = _make_source(db_session, "recent")
    created_ids: list[int] = []
    for index in range(12):
        alert = _seed_alert(
            db_session,
            f"recent-{index:02d}",
            source,
            updated_at=moment,
            created_at=moment - timedelta(minutes=index),
        )
        created_ids.append(alert.id)
    newest = _seed_alert(
        db_session,
        "recent-newest",
        source,
        updated_at=moment + timedelta(hours=1),
        created_at=moment,
    )
    db_session.commit()

    # When
    recent_response = client.get("/api/v1/dashboard/summary")
    assert recent_response.status_code == 200, recent_response.text
    payload = recent_response.json()

    # Then
    returned = [item["id"] for item in payload["recent_alerts"]]
    assert len(returned) == 10
    assert returned[0] == newest.id
    expected_rest = sorted(created_ids, reverse=True)[:9]
    assert returned[1:] == expected_rest


# ---------------------------------------------------------------------------
# 快照一致性：真实 /dashboard/summary 并发写回归
# ---------------------------------------------------------------------------


def test_dashboard_summary_snapshot_consistent_under_concurrent_write() -> None:
    """真实 /dashboard/summary 在并发写入时输出同一数据库快照。

    用真实 Postgres 多连接 + FastAPI TestClient + SQLAlchemy 事件监听：
    在 level_counts 聚合 SELECT 完成后由独立 writer 提交一条 P2 提醒（模拟
    请求处理期间的其他写入），断言响应的当前统计、期间新增、来源分布与
    近期提醒都不反映该并发写入——即全部聚合属于同一快照。

    断言相对基线（隔离测试库已由 seed_e2e 预置数据），不假设空库；
    若产品接线的独立 REPEATABLE READ 事务被移除，period_new_count 会 +1、
    来源分布会出现 dash-snap-writer、近期提醒会出现 P2 而失败。
    """
    from collections.abc import Generator

    from sqlalchemy import event

    from app.auth.models import User
    from app.auth.security import create_session, csrf_token_for_session
    from app.config import CSRF_COOKIE_NAME, SESSION_COOKIE_NAME
    from app.database import SessionLocal, engine, get_session
    from app.main import app
    from app.risks.query_validity import current_alert_condition

    # 1. 用真实提交会话预置基线：一条 current P1（1 天前）+ 登录用户
    seed = SessionLocal()
    try:
        source = _make_source(seed, "snap-base")
        signal = _make_signal(seed, source, "snap-base", moment=datetime.now(UTC))
        supplier = _make_supplier(seed, "DASH-snap-base")
        evt = _make_event(seed, "snap-base")
        seed.add(RiskEventSignal(event_id=evt.id, signal_id=signal.id))
        match = _make_match(seed, supplier, evt)
        base_alert = _make_alert(
            seed, match, level="P1", created_at=datetime.now(UTC) - timedelta(days=1)
        )
        user = User(
            username="dash-snapshot-viewer",
            password_hash="unused",
            display_name="snapshot-viewer",
            role="viewer",
            status="active",
        )
        seed.add(user)
        seed.commit()
        token = create_session(seed, user=user)
        csrf = csrf_token_for_session(token)
        seed.commit()
        base_alert_id = base_alert.id
    finally:
        seed.close()

    # 2. 请求前捕获已提交基线（隔离库含 seed_e2e 预置数据，不能假设空库）
    baseline_session = SessionLocal()
    try:
        as_of_baseline = datetime.now(UTC)
        window_start_baseline = as_of_baseline - timedelta(days=30)
        baseline_total_current = (
            baseline_session.scalar(
                select(func.count())
                .select_from(RiskAlert)
                .where(current_alert_condition(as_of_baseline))
            )
            or 0
        )
        baseline_period_new = (
            baseline_session.scalar(
                select(func.count())
                .select_from(RiskAlert)
                .where(
                    RiskAlert.created_at >= window_start_baseline,
                    RiskAlert.created_at < as_of_baseline,
                )
            )
            or 0
        )
    finally:
        baseline_session.close()

    # 3. 端点用真实生产式会话（expire_on_commit=False），触发产品的独立只读快照
    def fresh_session() -> Generator[Session]:
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = fresh_session

    # 4. 事件监听：level_counts 聚合完成后，独立 writer 提交一条 P2
    state: dict[str, object] = {"fired": False}

    def _after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        del cursor, parameters, context, executemany  # noqa: PLR0913
        if state["fired"] or "GROUP BY risk_alerts.level" not in statement:
            return
        state["fired"] = True
        raw = conn.connection.dbapi_connection
        side = raw.cursor()
        try:
            side.execute("SHOW transaction_read_only")
            state["read_only"] = side.fetchone()[0]
            side.execute("SHOW transaction_isolation")
            state["isolation"] = side.fetchone()[0]
        finally:
            side.close()
        writer = SessionLocal()
        try:
            wsource = _make_source(writer, "snap-writer")
            wsignal = _make_signal(
                writer, wsource, "snap-writer", moment=datetime.now(UTC)
            )
            wsplier = _make_supplier(writer, "DASH-snap-writer")
            wevt = _make_event(writer, "snap-writer")
            writer.add(RiskEventSignal(event_id=wevt.id, signal_id=wsignal.id))
            wmatch = _make_match(writer, wsplier, wevt)
            writer_alert = _make_alert(
                writer, wmatch, level="P2", created_at=datetime.now(UTC)
            )
            writer.commit()
            state["writer_alert_id"] = writer_alert.id
        finally:
            writer.close()

    try:
        with TestClient(app) as tc:
            tc.cookies.set(SESSION_COOKIE_NAME, token)
            tc.cookies.set(CSRF_COOKIE_NAME, csrf)
            tc.headers["Origin"] = "http://testserver"
            tc.headers["X-CSRF-Token"] = csrf
            event.listen(engine, "after_cursor_execute", _after_cursor_execute)
            try:
                response = tc.get("/api/v1/dashboard/summary", params={"days": 30})
            finally:
                event.remove(engine, "after_cursor_execute", _after_cursor_execute)
    finally:
        app.dependency_overrides.pop(get_session, None)

    # 5. writer 必须真的提交了，测试才有意义
    assert state["fired"] is True, "未捕获到 level_counts 查询，事件监听未触发"
    verify = SessionLocal()
    try:
        assert verify.get(RiskAlert, state["writer_alert_id"]) is not None
    finally:
        verify.close()

    # 6. 只读证据：业务查询确在只读 REPEATABLE READ 事务中
    assert state["read_only"] == "on", f"业务查询事务并非只读：{state.get('read_only')!r}"
    assert state["isolation"] == "repeatable read", (
        f"业务查询事务隔离级别不是 REPEATABLE READ：{state.get('isolation')!r}"
    )

    # 7. 响应必须属于写入前的同一快照：并发 P2 不出现在任何聚合里
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total_current"] == baseline_total_current, (
        f"当前统计反映了并发写入：{payload['total_current']} != {baseline_total_current}"
    )
    assert payload["period_new_count"] == baseline_period_new, (
        f"期间新增反映了并发写入：{payload['period_new_count']} != {baseline_period_new}"
    )
    codes = {item["code"] for item in payload["source_distribution"]}
    assert "dash-snap-base" in codes, f"基线来源缺失：{codes}"
    assert "dash-snap-writer" not in codes, f"来源分布泄漏并发写入：{codes}"
    recent_ids = {item["id"] for item in payload["recent_alerts"]}
    assert base_alert_id in recent_ids, f"基线提醒缺失：{recent_ids}"
    assert state["writer_alert_id"] not in recent_ids, (
        f"近期提醒泄漏并发写入：{recent_ids}"
    )

    # 清理真实提交的探针数据，避免泄漏到其他测试文件的确定性断言
    _cleanup_committed_probe(
        ("DASH-snap-base", "DASH-snap-writer"),
        ("dash-snap-base", "dash-snap-writer"),
        ("dash-snapshot-viewer",),
    )


# ---------------------------------------------------------------------------
# 真实 /dashboard/summary 权限拒绝（monkeypatch 移除 viewer 的 risk_view）
# ---------------------------------------------------------------------------


def test_dashboard_summary_403_when_viewer_lacks_risk_view(client, auth_as, monkeypatch) -> None:
    """viewer 的 risk_view 被临时移除后，真实 /dashboard/summary 应返回 403 且不含总览数据。"""
    from app.auth import permissions

    auth_as("viewer", "dash-viewer-403")
    monkeypatch.setitem(
        permissions.ROLE_PERMISSIONS,
        "viewer",
        permissions.ROLE_PERMISSIONS["viewer"] - {permissions.PERM_RISK_VIEW},
    )

    response = client.get("/api/v1/dashboard/summary")
    assert response.status_code == 403, response.text
    assert "total_current" not in response.text


# ---------------------------------------------------------------------------
# 7/30/90 各自半开时间边界回归
# ---------------------------------------------------------------------------


def test_window_boundary_7_30_90_half_open(
    client: TestClient, db_session: Session
) -> None:
    """验证各窗口 [window_start, as_of) 半开区间的包含与排除边界。"""
    import unittest.mock as mock

    import app.risks.dashboard as dashboard

    fixed = datetime(2026, 6, 15, 0, 0, tzinfo=UTC)
    source = _make_source(db_session, "boundary")
    signal = _make_signal(db_session, source, "boundary", moment=fixed)
    supplier = _make_supplier(db_session, "DASH-boundary")
    event = _make_event(db_session, "boundary")
    db_session.add(RiskEventSignal(event_id=event.id, signal_id=signal.id))
    db_session.flush()

    # 直接用 _make_alert 精确控制 created_at，每条 alert 独立 event+match
    for label, offset in [
        ("b7-exact", timedelta(days=7)),
        ("b7-before", timedelta(days=7, seconds=1)),
        ("b7-after", timedelta(days=7, hours=-1)),
        ("b30-exact", timedelta(days=30)),
        ("b30-before", timedelta(days=30, seconds=1)),
        ("b90-exact", timedelta(days=90)),
        ("b90-before", timedelta(days=90, seconds=1)),
        ("b90-out", timedelta(days=91)),
    ]:
        evt = _make_event(db_session, f"boundary-{label}")
        db_session.add(RiskEventSignal(event_id=evt.id, signal_id=signal.id))
        match = _make_match(db_session, supplier, evt)
        _make_alert(db_session, match, created_at=fixed - offset)
    db_session.flush()

    with mock.patch.object(dashboard, "utc_now", return_value=fixed):
        r7 = client.get("/api/v1/dashboard/summary", params={"days": 7})
        assert r7.status_code == 200, r7.text
        p7 = r7.json()
        r30 = client.get("/api/v1/dashboard/summary", params={"days": 30})
        assert r30.status_code == 200, r30.text
        p30 = r30.json()
        r90 = client.get("/api/v1/dashboard/summary", params={"days": 90})
        assert r90.status_code == 200, r90.text
        p90 = r90.json()

    # as_of 一致
    assert p7["as_of"] == p30["as_of"] == p90["as_of"]
    assert p7["as_of"].startswith("2026-06-15T00:00:00")

    # days=7: [6月8日0时, 6月15日0时) → 包含 b7-exact, 排除 b7-before(差1秒), 包含 b7-after
    assert p7["period_new_count"] == 2
    assert p7["window_start"].startswith("2026-06-08T00:00:00")

    # days=30: [5月16日0时, 6月15日0时) → 纳入 b7-before(6月7日23:59:59)
    # 与 b30-exact(5月16日)，排除 b30-before(5月15日23:59:59)
    assert p30["period_new_count"] == 4
    assert p30["window_start"].startswith("2026-05-16T00:00:00")

    # days=90: [3月17日0时, 6月15日0时) → +b90-exact(3月17日, IN) +b30-before(5月15日, IN)
    assert p90["period_new_count"] == 6
    assert p90["window_start"].startswith("2026-03-17T00:00:00")

    # 全部 8 条都是 current(默认), 跨窗口不变
    assert p7["total_current"] == p30["total_current"] == p90["total_current"] == 8


# ---------------------------------------------------------------------------
# 只读证明：业务查询运行在独立、显式只读、REPEATABLE READ 事务中
# ---------------------------------------------------------------------------


def test_dashboard_summary_business_queries_run_in_readonly_repeatable_read_snapshot() -> (
    None
):
    """证明 /dashboard/summary 的业务查询运行在独立、显式只读、REPEATABLE READ 事务中。

    在 dashboard 首条业务查询（level_counts 聚合）所用连接上，通过 SQLAlchemy
    after_cursor_execute 事件另开一个游标：
    - 断言 SHOW transaction_read_only = on 且 SHOW transaction_isolation = repeatable read；
    - 在 SAVEPOINT 保护下尝试一次写入，断言被 Postgres 以只读事务拒绝。
    全程不经任何产品写接口；写入探测走原始游标而非产品路由。
    """
    from collections.abc import Generator

    from sqlalchemy import event

    from app.auth.models import User
    from app.auth.security import create_session, csrf_token_for_session
    from app.config import CSRF_COOKIE_NAME, SESSION_COOKIE_NAME
    from app.database import SessionLocal, engine, get_session
    from app.main import app

    seed = SessionLocal()
    try:
        source = _make_source(seed, "ro-probe")
        signal = _make_signal(seed, source, "ro-probe", moment=datetime.now(UTC))
        supplier = _make_supplier(seed, "DASH-ro-probe")
        evt = _make_event(seed, "ro-probe")
        seed.add(RiskEventSignal(event_id=evt.id, signal_id=signal.id))
        match = _make_match(seed, supplier, evt)
        _make_alert(
            seed, match, level="P1", created_at=datetime.now(UTC) - timedelta(days=1)
        )
        user = User(
            username="dash-readonly-probe-viewer",
            password_hash="unused",
            display_name="readonly-probe-viewer",
            role="viewer",
            status="active",
        )
        seed.add(user)
        seed.commit()
        token = create_session(seed, user=user)
        csrf = csrf_token_for_session(token)
        seed.commit()
    finally:
        seed.close()

    def fresh_session() -> Generator[Session]:
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = fresh_session

    probe: dict[str, object] = {"fired": False}

    def _after_cursor_execute(
        conn, cursor, statement, parameters, context, executemany
    ):
        del cursor, parameters, context, executemany  # noqa: PLR0913
        if probe["fired"] or "GROUP BY risk_alerts.level" not in statement:
            return
        probe["fired"] = True
        raw = conn.connection.dbapi_connection
        side = raw.cursor()
        try:
            side.execute("SHOW transaction_read_only")
            probe["read_only"] = side.fetchone()[0]
            side.execute("SHOW transaction_isolation")
            probe["isolation"] = side.fetchone()[0]
            side.execute("SAVEPOINT dash_ro_probe")
            try:
                side.execute("UPDATE risk_alerts SET level = level WHERE 1 = 0")
                probe["write_rejected"] = False
            except Exception as exc:  # noqa: BLE001 - 只读事务应拒绝任何写入
                probe["write_rejected"] = True
                probe["write_error"] = type(exc).__name__
                side.execute("ROLLBACK TO SAVEPOINT dash_ro_probe")
            side.execute("RELEASE SAVEPOINT dash_ro_probe")
        finally:
            side.close()

    try:
        with TestClient(app) as tc:
            tc.cookies.set(SESSION_COOKIE_NAME, token)
            tc.cookies.set(CSRF_COOKIE_NAME, csrf)
            tc.headers["Origin"] = "http://testserver"
            tc.headers["X-CSRF-Token"] = csrf
            event.listen(engine, "after_cursor_execute", _after_cursor_execute)
            try:
                response = tc.get("/api/v1/dashboard/summary", params={"days": 30})
            finally:
                event.remove(engine, "after_cursor_execute", _after_cursor_execute)
    finally:
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 200, response.text
    assert probe["fired"] is True, "未捕获到 dashboard 的 level_counts 查询"
    assert probe["read_only"] == "on", (
        f"业务查询事务并非只读：{probe.get('read_only')!r}"
    )
    assert probe["isolation"] == "repeatable read", (
        f"业务查询事务隔离级别不是 REPEATABLE READ：{probe.get('isolation')!r}"
    )
    assert probe["write_rejected"] is True, (
        f"只读事务内的写入未被 Postgres 拒绝：{probe.get('write_error')!r}"
    )

    # 清理真实提交的探针数据，避免泄漏到其他测试文件的确定性断言
    _cleanup_committed_probe(
        ("DASH-ro-probe",),
        ("dash-ro-probe",),
        ("dash-readonly-probe-viewer",),
    )
