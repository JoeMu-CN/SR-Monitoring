"""维度运行轨迹接口测试（rule-engine-ui-revamp todo 3）。

先由测试锁定接口契约，再实现端点。覆盖计划验收标准：
- 无提醒的维度返回 available=false、samples=[] 且 HTTP 200（不得报错）；
- 未知维度 key 返回 404；
- 轨迹与库中提醒逐字段一致（score.total==alert.score、score.level==alert.level、
  match.match_type 与库一致、event 与信源名与库一致）；
- 取数口径与维度卡片 active_alerts 计数完全一致（status='current' +
  score_detail["dimension"]，含已过 expires_at 但 status 仍为 current 的提醒），
  不是 current_alert_condition 的实时判活口径；
- samples 按 updated_at DESC 取最多 10 条且只含本维度 current 提醒；
- ?alert_id= 可指定提醒（含已被取代者），跨维度/不存在 404、非法值 422；
- 多信号事件的 source_name 确定性：取 coalesce(published_at, collected_at) 最新，
  时间并列取最大 signal_id；重复调用结果稳定；
- 只读：重复请求不改变任何业务表计数；
- 无 rule_summary_view 权限返回 403。
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from itertools import count

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.models import SecurityAuditEvent
from app.auth.permissions import ROLE_PERMISSIONS
from app.risks.models import (
    RiskAlert,
    RiskEvent,
    RiskEventSignal,
    RuleDimensionConfig,
    SupplierEventMatch,
)
from app.signals.models import DataSource, RawSignal
from app.suppliers.models import Supplier

TRACE_PATH = "/api/v1/rule-engine/dimensions/{key}/trace"
_SIGNAL_SEQ = count(1)
_ROW_SEQ = count(1)


def _url(key: str, alert_id: int | None = None) -> str:
    url = TRACE_PATH.format(key=key)
    if alert_id is not None:
        url = f"{url}?alert_id={alert_id}"
    return url


@dataclass
class SignalSpec:
    """一条支持信号（及其信源）的构造参数。"""

    source_code: str
    source_name: str
    published_at: datetime | None = None
    collected_at: datetime | None = None


@dataclass
class AlertSpec:
    """一条 alert→match→event（→signal→source）链路的构造参数。"""

    dimension: str = "natural"
    score: int = 75
    level: str = "P2"
    status: str = "current"
    updated_at: datetime | None = None
    expiry_kind: str = "legacy"
    expires_at: datetime | None = None
    event_type: str = "weather"
    event_subtype: str | None = None
    event_severity: str = "high"
    event_summary: str = "测试事件摘要"
    event_confidence: float = 0.8
    match_type: str = "legal_name"
    match_reasons: list[str] = field(default_factory=lambda: ["法人全称精确匹配"])
    match_evidence: list[dict[str, object]] = field(default_factory=list)
    score_detail: dict[str, object] | None = None
    supplier_code: str | None = None
    supplier_name: str = "轨迹测试供应商"
    signals: list[SignalSpec] = field(default_factory=list)


@dataclass
class TraceChain:
    """构造完成的链路核心行，供断言直接引用。"""

    event: RiskEvent
    match: SupplierEventMatch
    supplier: Supplier
    alert: RiskAlert
    signals: list[RawSignal]
    sources: dict[str, DataSource]


def _add_source(db_session: Session, spec: SignalSpec) -> DataSource:
    now = datetime.now(UTC)
    source = DataSource(
        code=spec.source_code,
        name=spec.source_name,
        source_type="pull",
        credibility=50,
        enabled=True,
        adapter_status="builtin",
        adapter_version=0,
        auth_type="none",
        login_config={},
        adapter_config={},
        created_at=now,
        updated_at=now,
    )
    db_session.add(source)
    db_session.flush()
    return source


def _add_signal(db_session: Session, source: DataSource, spec: SignalSpec) -> RawSignal:
    sequence = next(_SIGNAL_SEQ)
    signal = RawSignal(
        source_id=source.id,
        external_id=f"trace-ext-{sequence}",
        title=f"轨迹测试信号 {sequence}",
        content=f"来自 {source.name} 的轨迹测试信号",
        url=f"https://example.com/trace/{sequence}",
        published_at=spec.published_at,
        collected_at=spec.collected_at or datetime.now(UTC),
        fingerprint=f"trace-fp-{sequence}",
        raw_data={"sequence": sequence},
        validity_state="legacy",
        validity_reason={
            "code": "legacy_unmigrated",
            "anchor_source": "legacy",
            "details": {},
        },
    )
    db_session.add(signal)
    db_session.flush()
    return signal


def _create_chain(db_session: Session, spec: AlertSpec | None = None) -> TraceChain:
    """构造 alert→match→event（→signal→source）完整链路并返回核心行。"""
    alert_spec = spec if spec is not None else AlertSpec()
    now = datetime.now(UTC)

    sources: dict[str, DataSource] = {}
    signals: list[RawSignal] = []
    for signal_spec in alert_spec.signals:
        source = sources.get(signal_spec.source_code)
        if source is None:
            source = _add_source(db_session, signal_spec)
            sources[signal_spec.source_code] = source
        signals.append(_add_signal(db_session, source, signal_spec))

    event = RiskEvent(
        dedup_key=f"trace-dedup-{next(_ROW_SEQ)}",
        event_type=alert_spec.event_type,
        event_subtype=alert_spec.event_subtype,
        severity=alert_spec.event_severity,
        summary=alert_spec.event_summary,
        confidence=alert_spec.event_confidence,
        facts={"origin": "trace-test"},
        validity_state="legacy",
    )
    db_session.add(event)
    db_session.flush()
    for signal in signals:
        db_session.add(RiskEventSignal(event_id=event.id, signal_id=signal.id))
    db_session.flush()

    supplier = Supplier(
        supplier_code=alert_spec.supplier_code or f"TRACE-{next(_ROW_SEQ)}",
        legal_name=alert_spec.supplier_name,
        country_code="CN",
        enabled=True,
    )
    db_session.add(supplier)
    db_session.flush()

    match = SupplierEventMatch(
        supplier_id=supplier.id,
        event_id=event.id,
        match_type=alert_spec.match_type,
        score=40,
        reasons=list(alert_spec.match_reasons),
        evidence=list(alert_spec.match_evidence),
    )
    db_session.add(match)
    db_session.flush()

    detail: dict[str, object] = {
        "severity": 15,
        "association": 40,
        "source_credibility": 16,
        "timeliness": 5,
        "product_relevance": 0,
    }
    if alert_spec.score_detail is not None:
        detail.update(alert_spec.score_detail)
    detail.setdefault("dimension", alert_spec.dimension)

    alert = RiskAlert(
        match_id=match.id,
        level=alert_spec.level,
        score=alert_spec.score,
        score_detail=detail,
        status=alert_spec.status,
        expiry_kind=alert_spec.expiry_kind,
        expires_at=alert_spec.expires_at,
        updated_at=alert_spec.updated_at or now,
    )
    db_session.add(alert)
    db_session.flush()
    return TraceChain(
        event=event,
        match=match,
        supplier=supplier,
        alert=alert,
        signals=signals,
        sources=sources,
    )


def _table_counts(db_session: Session) -> dict[str, int]:
    """读取涉及表的行数快照，用于"只读端点"回归。"""
    models = (
        RiskAlert,
        SupplierEventMatch,
        RiskEvent,
        RiskEventSignal,
        RawSignal,
        DataSource,
        Supplier,
        RuleDimensionConfig,
        SecurityAuditEvent,
    )
    counts: dict[str, int] = {}
    for model in models:
        value = db_session.scalar(select(func.count()).select_from(model))
        counts[model.__tablename__] = int(value or 0)
    return counts


# ── 空数据与错误输入 ─────────────────────────────────────────────────


def test_empty_dimension_returns_available_false(client: TestClient) -> None:
    """无提醒的维度返回 available=false、samples=[] 且 HTTP 200（不得报错）。"""
    response = client.get(_url("natural"))
    assert response.status_code == 200
    body = response.json()
    assert body["available"] is False
    assert body["event"] is None
    assert body["routing"] is None
    assert body["match"] is None
    assert body["score"] is None
    assert body["samples"] == []


def test_unknown_dimension_key_returns_404(client: TestClient) -> None:
    """未知维度 key 返回 404（与 /dimensions/{key}、/inputs 口径一致）。"""
    assert client.get(_url("no-such-dimension")).status_code == 404


def test_malformed_alert_id_returns_422(client: TestClient) -> None:
    """alert_id 非法（0 或非整数）返回 422。"""
    assert client.get(_url("natural", alert_id=0)).status_code == 422
    assert client.get(f"{TRACE_PATH.format(key='natural')}?alert_id=abc").status_code == 422


# ── 轨迹与库中数据逐字段一致 ─────────────────────────────────────────


def test_trace_reflects_alert_row_and_chain(
    client: TestClient, db_session: Session
) -> None:
    """真实链路下的轨迹字段、评分明细与命中信息必须与库中一致。"""
    published_at = datetime(2026, 3, 1, 8, 0, tzinfo=UTC)
    updated_at = datetime(2026, 3, 2, 9, 30, tzinfo=UTC)
    chain = _create_chain(
        db_session,
        AlertSpec(
            dimension="natural",
            score=68,
            level="P2",
            updated_at=updated_at,
            event_subtype="weather_alert",
            event_summary="台风逼近华东沿海",
            match_type="site_text",
            match_reasons=["地点名称匹配：上海工厂"],
            match_evidence=[{"object_type": "site", "site_name": "上海工厂"}],
            score_detail={
                "level_cap": "weak_association_max_p2",
                "forced_rule": {
                    "name": "sanctions_entity_hit",
                    "description": "主体命中制裁",
                    "reason": "受制裁主体",
                    "original_level": "P1",
                    "original_score": 100,
                },
            },
            supplier_name="华东精密制造有限公司",
            signals=[
                SignalSpec(
                    source_code="trace-nmc",
                    source_name="中央气象台",
                    published_at=published_at,
                )
            ],
        ),
    )

    response = client.get(_url("natural"))
    assert response.status_code == 200
    body = response.json()

    # 响应结构锁定：不泄露原始信号正文/URL/raw_data 等契约外字段
    assert body["available"] is True
    assert set(body) == {"available", "event", "routing", "match", "score", "samples"}
    assert set(body["event"]) == {
        "event_type",
        "event_subtype",
        "severity",
        "summary",
        "confidence",
        "published_at",
        "source_name",
    }
    assert set(body["routing"]) == {"key", "label", "match_columns"}
    assert set(body["match"]) == {"match_type", "match_reasons", "match_evidence"}
    assert set(body["score"]) == {"total", "level", "detail", "level_cap", "forced_rule"}
    assert set(body["samples"][0]) == {
        "id",
        "supplier_id",
        "supplier_name",
        "level",
        "event_summary",
        "updated_at",
    }

    event = body["event"]
    assert event["event_type"] == chain.event.event_type
    assert event["event_subtype"] == chain.event.event_subtype
    assert event["severity"] == chain.event.severity
    assert event["summary"] == chain.event.summary
    assert event["confidence"] == pytest.approx(chain.event.confidence)
    assert event["source_name"] == "中央气象台"
    assert datetime.fromisoformat(event["published_at"]) == published_at

    routing = body["routing"]
    assert routing["key"] == "natural"
    assert routing["label"] == "自然环境"
    assert routing["match_columns"] == ["entity", "location", "product"]

    match = body["match"]
    assert match["match_type"] == chain.match.match_type
    assert match["match_reasons"] == chain.match.reasons
    assert match["match_evidence"] == chain.match.evidence

    score = body["score"]
    assert score["total"] == chain.alert.score
    assert score["level"] == chain.alert.level
    assert score["detail"] == chain.alert.score_detail
    assert score["level_cap"] == "weak_association_max_p2"
    assert score["forced_rule"] == chain.alert.score_detail["forced_rule"]

    sample = body["samples"][0]
    assert sample["id"] == chain.alert.id
    assert sample["supplier_id"] == chain.supplier.id
    assert sample["supplier_name"] == "华东精密制造有限公司"
    assert sample["level"] == "P2"
    assert sample["event_summary"] == "台风逼近华东沿海"
    assert datetime.fromisoformat(sample["updated_at"]) == updated_at


def test_event_without_supporting_signals_returns_null_source(
    client: TestClient, db_session: Session
) -> None:
    """事件没有支持信号时 source_name/published_at 为 null，不报错也不伪造。"""
    _create_chain(db_session, AlertSpec(dimension="natural", signals=[]))

    body = client.get(_url("natural")).json()
    assert body["available"] is True
    assert body["event"]["source_name"] is None
    assert body["event"]["published_at"] is None


# ── 取数口径：与维度卡片计数一致 ─────────────────────────────────────


def test_trace_uses_same_basis_as_dimension_alert_count(
    client: TestClient, db_session: Session
) -> None:
    """取数口径与卡片 active_alerts 一致：status='current' 即计入，不看 expires_at。

    负向对照：若实现改用 current_alert_condition（实时判活），已过 expires_at 但
    status 仍为 current 的较新提醒会被跳过，轨迹会落到另一条 legacy 提醒
    （score=99）而不是本用例断言的 37。
    """
    base = datetime(2026, 3, 1, 0, 0, tzinfo=UTC)
    older = _create_chain(
        db_session,
        AlertSpec(
            dimension="natural",
            score=99,
            level="P1",
            updated_at=base,
            expiry_kind="legacy",
            event_summary="较早但仍有效",
        ),
    )
    assert older.alert.id  # 仅确保链路创建
    expired_by_time = _create_chain(
        db_session,
        AlertSpec(
            dimension="natural",
            score=37,
            level="P4",
            updated_at=base + timedelta(hours=1),
            expiry_kind="finite",
            expires_at=base - timedelta(days=1),
            event_summary="已过 expires_at 但 status 仍为 current",
        ),
    )

    dimensions = client.get("/api/v1/rule-engine/dimensions").json()
    natural = next(item for item in dimensions if item["key"] == "natural")
    assert natural["active_alerts"] == 2  # 卡片计数确实包含该提醒

    body = client.get(_url("natural")).json()
    assert body["available"] is True
    assert body["score"]["total"] == expired_by_time.alert.score
    assert body["score"]["total"] == 37
    assert body["samples"][0]["id"] == expired_by_time.alert.id


# ── 配置漂移：routing 还原提醒保存的历史归属维度 ─────────────────────


def test_routing_uses_stored_dimension_after_config_drift(
    client: TestClient, db_session: Session
) -> None:
    """配置把事件类型改派后，routing 仍取提醒评分时的历史归属维度。

    场景：提醒保存时 weather 归属 natural（``score_detail["dimension"]``）；
    随后 DB 覆盖把 weather 从 natural 移除并改派给 geopolitical。若 routing
    用当前 resolve_dimension 结果填充，会返回 geopolitical 且与
    ``score.detail.dimension == "natural"`` 自相矛盾；修复后 routing.key/label 必须
    恒等于提醒自身保存的历史归属维度（label 为该维度身份标签 ``base.label``）；仅
    ``match_columns`` 因无逐提醒配置快照而取该维度当前合并配置。
    """
    chain = _create_chain(
        db_session,
        AlertSpec(
            dimension="natural",
            event_type="weather",
            score=63,
            level="P2",
            event_summary="漂移前归属 natural 的天气事件",
        ),
    )
    # 制造漂移：weather 不再由 natural 接管，改派给 geopolitical
    db_session.add_all(
        [
            RuleDimensionConfig(
                key="natural",
                label="自然环境",
                enabled=True,
                config={"event_types": ["geological"]},
            ),
            RuleDimensionConfig(
                key="geopolitical",
                label="地缘政治与安全",
                enabled=True,
                config={"event_types": ["geopolitical", "weather"]},
            ),
        ]
    )
    db_session.flush()

    # 经公开接口确认漂移已生效：当前配置中 weather 改由 geopolitical 接管
    dimensions = client.get("/api/v1/rule-engine/dimensions").json()
    natural = next(item for item in dimensions if item["key"] == "natural")
    geopolitical = next(item for item in dimensions if item["key"] == "geopolitical")
    assert "weather" not in natural["event_types"]
    assert "weather" in geopolitical["event_types"]

    response = client.get(_url("natural"))
    print(f"[QA] drift natural trace status={response.status_code} body={response.text}")
    assert response.status_code == 200
    body = response.json()
    assert body["available"] is True
    assert body["score"]["total"] == chain.alert.score
    assert body["score"]["detail"]["dimension"] == "natural"
    # 历史归属优先：routing.key 必须等于提醒保存的维度，而不是当前 resolve 结果
    assert body["routing"]["key"] == "natural"
    assert body["routing"]["key"] == body["score"]["detail"]["dimension"]
    assert body["routing"]["label"] == "自然环境"

    # 默认轨迹仍按存储维度选取：geopolitical 当前接管 weather，但没有该维度的
    # 提醒落库，只能返回 available=false（不得按当前路由去 geopolitical 取数）
    geo_response = client.get(_url("geopolitical"))
    print(
        f"[QA] drift geopolitical trace status={geo_response.status_code}"
        f" body={geo_response.text}"
    )
    assert geo_response.json()["available"] is False

    # ?alert_id= 路径同样返回历史归属维度
    specified_response = client.get(_url("natural", alert_id=chain.alert.id))
    print(
        f"[QA] drift alert_id trace status={specified_response.status_code}"
        f" body={specified_response.text}"
    )
    specified = specified_response.json()
    assert specified["routing"]["key"] == "natural"
    assert specified["routing"]["key"] == specified["score"]["detail"]["dimension"]


# ── samples：最近最多 10 条 current ──────────────────────────────────


def test_samples_latest_ten_current_alerts_of_dimension(
    client: TestClient, db_session: Session
) -> None:
    """samples 按 updated_at DESC 取 10 条，且只包含本维度 current 提醒。"""
    base = datetime(2026, 3, 1, 0, 0, tzinfo=UTC)
    chains = [
        _create_chain(
            db_session,
            AlertSpec(
                dimension="natural",
                score=10 + index,
                level="P3",
                updated_at=base + timedelta(minutes=index),
                event_summary=f"事件-{index:02d}",
                supplier_name=f"供应商-{index:02d}",
            ),
        )
        for index in range(1, 13)
    ]
    # 更晚但已失效 / 属于其它维度的提醒都不得进入样例
    _create_chain(
        db_session,
        AlertSpec(
            dimension="natural",
            status="expired",
            updated_at=base + timedelta(days=1),
            event_summary="已失效提醒",
        ),
    )
    _create_chain(
        db_session,
        AlertSpec(
            dimension="geopolitical",
            event_type="geopolitical",
            updated_at=base + timedelta(days=2),
            event_summary="其它维度提醒",
        ),
    )

    body = client.get(_url("natural")).json()
    assert body["available"] is True
    samples = body["samples"]
    assert len(samples) == 10
    expected_ids = [chain.alert.id for chain in reversed(chains[-10:])]
    assert [sample["id"] for sample in samples] == expected_ids
    assert [sample["event_summary"] for sample in samples] == [
        f"事件-{index:02d}" for index in range(12, 2, -1)
    ]
    assert samples[0]["id"] == chains[-1].alert.id  # 轨迹与样例首条同源
    timestamps = [datetime.fromisoformat(sample["updated_at"]) for sample in samples]
    assert timestamps == sorted(timestamps, reverse=True)
    assert all(sample["supplier_name"].startswith("供应商-") for sample in samples)
    assert all(sample["level"] == "P3" for sample in samples)


# ── ?alert_id= 指定提醒 ──────────────────────────────────────────────


def test_alert_id_selects_specific_alert_and_rejects_cross_dimension(
    client: TestClient, db_session: Session
) -> None:
    """?alert_id= 返回指定提醒；跨维度与不存在 404。"""
    base = datetime(2026, 3, 1, 0, 0, tzinfo=UTC)
    older = _create_chain(
        db_session,
        AlertSpec(
            dimension="natural",
            score=41,
            level="P3",
            updated_at=base,
            event_summary="较早提醒",
        ),
    )
    newer = _create_chain(
        db_session,
        AlertSpec(
            dimension="natural",
            score=88,
            level="P1",
            updated_at=base + timedelta(hours=2),
            event_summary="最新提醒",
        ),
    )
    other = _create_chain(
        db_session,
        AlertSpec(
            dimension="geopolitical",
            event_type="geopolitical",
            updated_at=base + timedelta(hours=3),
        ),
    )

    response = client.get(_url("natural", alert_id=older.alert.id))
    assert response.status_code == 200
    body = response.json()
    assert body["available"] is True
    assert body["score"]["total"] == 41
    assert body["score"]["level"] == "P3"
    assert body["event"]["summary"] == "较早提醒"
    assert body["samples"][0]["id"] == newer.alert.id  # 样例仍是本维度最近 current

    assert client.get(_url("natural", alert_id=other.alert.id)).status_code == 404
    assert client.get(_url("natural", alert_id=10**9)).status_code == 404
    assert client.get(_url("geopolitical", alert_id=older.alert.id)).status_code == 404


def test_alert_id_returns_superseded_alert_not_current_one(
    client: TestClient, db_session: Session
) -> None:
    """stale_state 探针：默认取 current，指定旧提醒时如实返回旧提醒本身。"""
    base = datetime(2026, 3, 1, 0, 0, tzinfo=UTC)
    superseded = _create_chain(
        db_session,
        AlertSpec(
            dimension="natural",
            score=95,
            level="P1",
            status="expired",
            updated_at=base,
            event_summary="已被取代的旧提醒",
        ),
    )
    current = _create_chain(
        db_session,
        AlertSpec(
            dimension="natural",
            score=50,
            level="P3",
            updated_at=base + timedelta(hours=1),
            event_summary="当前提醒",
        ),
    )

    default_body = client.get(_url("natural")).json()
    assert default_body["score"]["total"] == current.alert.score
    assert default_body["event"]["summary"] == "当前提醒"

    specified = client.get(_url("natural", alert_id=superseded.alert.id)).json()
    assert specified["available"] is True
    assert specified["score"]["total"] == superseded.alert.score
    assert specified["event"]["summary"] == "已被取代的旧提醒"


# ── 多信号事件的 source_name 确定性 ──────────────────────────────────


def test_source_name_prefers_latest_effective_signal(
    client: TestClient, db_session: Session
) -> None:
    """多信号事件取 coalesce(published_at, collected_at) 最新者作为来源。"""
    base = datetime(2026, 3, 1, 0, 0, tzinfo=UTC)
    newest = base + timedelta(days=1)
    _create_chain(
        db_session,
        AlertSpec(
            dimension="natural",
            signals=[
                SignalSpec(
                    source_code="trace-src-old",
                    source_name="信源-旧",
                    published_at=base,
                ),
                SignalSpec(
                    source_code="trace-src-new",
                    source_name="信源-新",
                    published_at=newest,
                ),
            ],
        ),
    )

    event = client.get(_url("natural")).json()["event"]
    assert event["source_name"] == "信源-新"
    assert datetime.fromisoformat(event["published_at"]) == newest


def test_source_name_falls_back_to_collected_at_when_published_missing(
    client: TestClient, db_session: Session
) -> None:
    """published_at 为空时用 collected_at 参与比较，仍取最新信号。"""
    base = datetime(2026, 3, 1, 0, 0, tzinfo=UTC)
    collected_newest = base + timedelta(days=1)
    _create_chain(
        db_session,
        AlertSpec(
            dimension="natural",
            signals=[
                SignalSpec(
                    source_code="trace-src-pub",
                    source_name="信源-有发布时间",
                    published_at=base,
                ),
                SignalSpec(
                    source_code="trace-src-collect",
                    source_name="信源-仅采集时间",
                    published_at=None,
                    collected_at=collected_newest,
                ),
            ],
        ),
    )

    event = client.get(_url("natural")).json()["event"]
    assert event["source_name"] == "信源-仅采集时间"
    assert event["published_at"] is None  # 如实返回代表信号的真实发布时间


def test_source_name_tie_break_by_largest_signal_id_and_repeat_stable(
    client: TestClient, db_session: Session
) -> None:
    """发布时间并列时取最大 signal_id，且重复调用结果完全一致。"""
    tie = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
    _create_chain(
        db_session,
        AlertSpec(
            dimension="natural",
            signals=[
                SignalSpec(
                    source_code="trace-tie-a",
                    source_name="信源-A（先创建，id 较小）",
                    published_at=tie,
                ),
                SignalSpec(
                    source_code="trace-tie-b",
                    source_name="信源-B（后创建，id 较大）",
                    published_at=tie,
                ),
            ],
        ),
    )

    bodies = [client.get(_url("natural")).json() for _ in range(3)]
    assert bodies[0] == bodies[1] == bodies[2]
    assert bodies[0]["event"]["source_name"] == "信源-B（后创建，id 较大）"
    assert datetime.fromisoformat(bodies[0]["event"]["published_at"]) == tie


# ── 权限与只读性 ─────────────────────────────────────────────────────


def test_trace_requires_rule_summary_view(
    client: TestClient,
    db_session: Session,
    auth_as: Callable[[str, str], object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """无 rule_summary_view 的角色访问返回 403。"""
    monkeypatch.setitem(ROLE_PERMISSIONS, "viewer", set())
    auth_as("viewer", "viewer-without-rule-summary")
    assert client.get(_url("natural")).status_code == 403


def test_trace_is_read_only(client: TestClient, db_session: Session) -> None:
    """重复请求不写任何业务表（repeated_interruptions：只读端点不改库）。"""
    _create_chain(
        db_session,
        AlertSpec(
            dimension="natural",
            signals=[
                SignalSpec(
                    source_code="trace-ro",
                    source_name="只读信源",
                    published_at=datetime(2026, 3, 1, tzinfo=UTC),
                )
            ],
        ),
    )
    before = _table_counts(db_session)
    for _ in range(3):
        assert client.get(_url("natural")).status_code == 200
        assert client.get(_url("natural", alert_id=10**9)).status_code == 404
    assert _table_counts(db_session) == before


# ── 手动 QA 转录：真实请求的原始 status+body（-s 运行） ──────────────


def test_qa_transcript_trace_status_body_and_db_comparison(
    client: TestClient, db_session: Session
) -> None:
    """打印带数据/空维度/跨维度 alert_id/多源重复调用/非法输入的原始响应，供证据留存。"""
    base = datetime(2026, 3, 1, 0, 0, tzinfo=UTC)
    tie = base + timedelta(days=1)
    chain = _create_chain(
        db_session,
        AlertSpec(
            dimension="natural",
            score=68,
            level="P2",
            updated_at=base + timedelta(hours=5),
            event_summary="QA 台风逼近华东沿海",
            match_type="site_text",
            supplier_name="QA 华东精密制造有限公司",
            signals=[
                SignalSpec(
                    source_code="qa-trace-a",
                    source_name="QA 信源甲",
                    published_at=tie - timedelta(hours=1),
                ),
                SignalSpec(
                    source_code="qa-trace-b",
                    source_name="QA 信源乙",
                    published_at=tie,
                ),
            ],
        ),
    )
    other = _create_chain(
        db_session,
        AlertSpec(
            dimension="geopolitical",
            event_type="geopolitical",
            updated_at=base + timedelta(hours=6),
        ),
    )
    stale = _create_chain(
        db_session,
        AlertSpec(
            dimension="natural",
            score=91,
            level="P1",
            status="expired",
            updated_at=base + timedelta(hours=7),
            event_summary="QA 已被取代的高分旧提醒",
        ),
    )

    print(
        "[QA] DB rows:"
        f" alert.score={chain.alert.score} alert.level={chain.alert.level}"
        f" match.match_type={chain.match.match_type}"
        f" match.id={chain.match.id} alert.match_id={chain.alert.match_id}"
        f" event.summary={chain.event.summary!r}"
        f" stale(expired).score={stale.alert.score} stale.updated_at={stale.alert.updated_at}"
    )

    with_data = client.get(_url("natural"))
    print(f"[QA] with-data status={with_data.status_code} body={with_data.text}")
    body = with_data.json()
    assert body["score"]["total"] == chain.alert.score
    assert body["score"]["level"] == chain.alert.level
    assert body["match"]["match_type"] == chain.match.match_type
    match_row = db_session.get(SupplierEventMatch, chain.alert.match_id)
    assert match_row is not None
    assert body["match"]["match_type"] == match_row.match_type  # 与库中行直接比对
    # stale_state：默认轨迹取 current（68），不取更新的已失效高分提醒（91）
    assert body["score"]["total"] != stale.alert.score

    empty = client.get(_url("industry"))
    print(f"[QA] empty-dimension status={empty.status_code} body={empty.text}")
    assert empty.status_code == 200
    assert empty.json()["available"] is False

    cross = client.get(_url("natural", alert_id=other.alert.id))
    print(f"[QA] cross-dimension alert_id status={cross.status_code} body={cross.text}")

    missing = client.get(_url("natural", alert_id=10**9))
    print(f"[QA] missing-alert status={missing.status_code} body={missing.text}")

    unknown_key = client.get(_url("no-such-dimension"))
    print(f"[QA] unknown-dimension status={unknown_key.status_code} body={unknown_key.text}")

    zero = client.get(_url("natural", alert_id=0))
    print(f"[QA] alert_id=0 status={zero.status_code} body={zero.text}")

    non_int = client.get(f"{TRACE_PATH.format(key='natural')}?alert_id=abc")
    print(f"[QA] alert_id=abc status={non_int.status_code} body={non_int.text}")

    first = client.get(_url("natural"))
    second = client.get(_url("natural"))
    print(f"[QA] multi-source repeat#1 source_name={first.json()['event']['source_name']}")
    print(f"[QA] multi-source repeat#2 source_name={second.json()['event']['source_name']}")
    assert first.text == second.text

