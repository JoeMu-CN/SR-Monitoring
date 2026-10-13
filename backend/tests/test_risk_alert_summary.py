"""当前提醒全量汇总契约（GET /api/v1/risk-alerts/summary）。

风险监控页的概览卡此前直接用列表前 100 行推导分级计数，导致它与 /dashboard/summary
的全库当前统计互相矛盾。本文件锁定：汇总接口按全库口径聚合、与列表分页无关、
且与总览的当前统计完全一致。
"""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient
from risk_validity_fixtures import SignalSpec, linked_risk
from sqlalchemy.orm import Session

CURRENT_LEVELS = ("P1", "P2", "P3", "P4")


def _level_counts(level_counts: Sequence[dict[str, Any]]) -> dict[str, int]:
    """把响应的 level_counts 折叠成 level→count，便于与总览口径直接对比。"""
    return {item["level"]: item["count"] for item in level_counts}


def _seed_current_alert(
    db_session: Session, name: str, *, level: str, now_utc: datetime
) -> None:
    # until_revoked：信号与提醒都是无截止时间的当前有效行，避免固定天数模式要求 valid_until。
    linked = linked_risk(
        db_session, (SignalSpec(name, mode="until_revoked"),), now_utc=now_utc
    )
    linked.alert.level = level


def test_summary_requires_session_returns_401_without_counts(
    client: TestClient,
) -> None:
    # Given
    client.cookies.clear()
    client.headers.pop("X-CSRF-Token", None)

    # When
    response = client.get("/api/v1/risk-alerts/summary")

    # Then
    assert response.status_code == 401
    assert "total_current" not in response.json()


def test_summary_counts_every_current_alert_beyond_the_list_page_limit(
    client: TestClient, db_session: Session
) -> None:
    """列表只回 100 行但 total 为全量；汇总必须等于全量而不是本页行数。"""
    # Given 105 条 current 提醒，超过列表 limit=100 的单页上限
    now_utc = datetime.now(UTC)
    plan = [("P1", 3), ("P2", 4), ("P3", 5), ("P4", 93)]
    index = 0
    for level, count in plan:
        for _ in range(count):
            _seed_current_alert(
                db_session, f"summary-page-{level}-{index:03d}", level=level, now_utc=now_utc
            )
            index += 1
    db_session.commit()

    # When
    summary_response = client.get("/api/v1/risk-alerts/summary")
    list_response = client.get("/api/v1/risk-alerts", params={"status": "current", "limit": 100})
    dashboard_response = client.get("/api/v1/dashboard/summary", params={"days": 30})

    # Then 列表口径：只回一页，但 total 已是全量
    assert list_response.status_code == 200, list_response.text
    assert list_response.json()["total"] == 105
    assert len(list_response.json()["items"]) == 100
    # 汇总口径：恒含四档且顺序固定，计数等于全量而不是本页
    assert summary_response.status_code == 200, summary_response.text
    summary = summary_response.json()
    assert [item["level"] for item in summary["level_counts"]] == list(CURRENT_LEVELS)
    assert _level_counts(summary["level_counts"]) == {"P1": 3, "P2": 4, "P3": 5, "P4": 93}
    assert summary["total_current"] == 105
    # 与风险总览的当前统计完全一致（两个页面不得再互相矛盾）
    assert dashboard_response.status_code == 200, dashboard_response.text
    assert _level_counts(dashboard_response.json()["level_counts"]) == _level_counts(summary["level_counts"])
    assert dashboard_response.json()["total_current"] == summary["total_current"]


def test_summary_excludes_alerts_past_their_deadline(
    client: TestClient, db_session: Session
) -> None:
    # Given 一条有效提醒 + 一条 finite 截止时间已过的 current 提醒
    now_utc = datetime.now(UTC)
    _seed_current_alert(db_session, "summary-live", level="P1", now_utc=now_utc)
    stale = linked_risk(
        db_session, (SignalSpec("summary-stale", mode="until_revoked"),), now_utc=now_utc
    )
    stale.alert.level = "P1"
    stale.alert.expiry_kind = "finite"
    stale.alert.expires_at = now_utc - timedelta(days=1)
    db_session.commit()

    # When
    response = client.get("/api/v1/risk-alerts/summary")

    # Then
    assert response.status_code == 200, response.text
    assert response.json()["total_current"] == 1
    assert _level_counts(response.json()["level_counts"]) == {"P1": 1, "P2": 0, "P3": 0, "P4": 0}


def test_summary_returns_fixed_zero_contract_without_current_alerts(
    client: TestClient,
) -> None:
    # Given / When 当前没有任何 current 提醒
    response = client.get("/api/v1/risk-alerts/summary")

    # Then
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total_current"] == 0
    assert [item["level"] for item in payload["level_counts"]] == list(CURRENT_LEVELS)
    assert [item["count"] for item in payload["level_counts"]] == [0, 0, 0, 0]


def test_summary_is_not_swallowed_by_the_alert_id_path_route(
    client: TestClient, db_session: Session
) -> None:
    """路由顺序回归：/risk-alerts/summary 必须先于 /risk-alerts/{alert_id} 匹配。

    若被 int 路径参数抢先匹配，FastAPI 会以 422 拒绝，而这里断言的是 200。
    """
    # Given 一条 current 提醒，确保 summary 是真的在执行聚合而不是命中空结果
    now_utc = datetime.now(UTC)
    linked = linked_risk(
        db_session, (SignalSpec("summary-route-order", mode="until_revoked"),), now_utc=now_utc
    )
    linked.alert.level = "P2"
    db_session.commit()

    # When
    summary_response = client.get("/api/v1/risk-alerts/summary")
    detail_response = client.get(f"/api/v1/risk-alerts/{linked.alert.id}")

    # Then 两条路由各自命中自身语义
    assert summary_response.status_code == 200, summary_response.text
    assert _level_counts(summary_response.json()["level_counts"])["P2"] == 1
    assert detail_response.status_code == 200, detail_response.text
    assert detail_response.json()["id"] == linked.alert.id


def test_summary_returns_403_when_viewer_lacks_risk_view(
    client: TestClient, auth_as, monkeypatch
) -> None:
    # Given viewer 的 risk_view 被临时移除
    from app.auth import permissions

    auth_as("viewer", "alert-summary-viewer-403")
    monkeypatch.setitem(
        permissions.ROLE_PERMISSIONS,
        "viewer",
        permissions.ROLE_PERMISSIONS["viewer"] - {permissions.PERM_RISK_VIEW},
    )

    # When
    response = client.get("/api/v1/risk-alerts/summary")

    # Then
    assert response.status_code == 403, response.text
    assert "total_current" not in response.text