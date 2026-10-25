"""当前提醒列表默认排序契约：更新时间优先，事件等级次之。

列表此前按等级优先排序，最新发生的 P2/P3 事件会被压在一堆旧 P1 之后，与卡片上
展示的「更新时间」不一致。本文件锁定 GET /api/v1/risk-alerts 的默认顺序：
更新时间倒序 → 等级 P1→P4 → id 倒序（同一时刻、同一等级时最新写入的在前）。
"""

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from risk_validity_fixtures import SignalSpec, linked_risk
from sqlalchemy.orm import Session

NOW_UTC = datetime.now(UTC)


def _seed_current_alert(
    db_session: Session, name: str, *, level: str, updated_at: datetime
) -> None:
    # until_revoked：信号与提醒均无截止时间，稳定的 current 行；各自独立供应商避免被分组聚合。
    linked = linked_risk(
        db_session, (SignalSpec(name, mode="until_revoked"),), now_utc=NOW_UTC
    )
    linked.alert.level = level
    linked.alert.updated_at = updated_at


def test_current_list_orders_by_updated_at_before_level(
    client: TestClient, db_session: Session
) -> None:
    """最新更新的提醒排前面，等级只在同一更新时间内部决定先后。"""
    # Given 最旧的是 P1、最新的是 P3；另有两条同一更新时间（P2 与 P1）
    _seed_current_alert(
        db_session, "order-oldest-p1", level="P1", updated_at=NOW_UTC - timedelta(hours=3)
    )
    _seed_current_alert(
        db_session, "order-middle-p4", level="P4", updated_at=NOW_UTC - timedelta(hours=2)
    )
    _seed_current_alert(
        db_session, "order-newest-p3", level="P3", updated_at=NOW_UTC - timedelta(hours=1)
    )
    _seed_current_alert(db_session, "order-tie-p2", level="P2", updated_at=NOW_UTC)
    _seed_current_alert(db_session, "order-tie-p1", level="P1", updated_at=NOW_UTC)
    db_session.commit()

    # When
    response = client.get("/api/v1/risk-alerts", params={"limit": 100})

    # Then
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total"] == 5
    assert [item["level"] for item in payload["items"]] == ["P1", "P2", "P3", "P4", "P1"]
    updated = [item["updated_at"] for item in payload["items"]]
    assert updated == sorted(updated, reverse=True)
