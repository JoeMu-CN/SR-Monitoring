"""Todo 10 契约测试：采集记录列表受控摘要 + 单条报告详情端点。

覆盖范围（黑盒 API 契约）：
- 列表新增受控 ``summary``：TYC 报告从 ``raw_data`` 确定性派生重点摘要；
  普通/malformed/空信号回落 content 或稳定占位；长度受控；不泄漏 raw_data。
- 详情端点 ``GET /api/v1/sources/{source_id}/signals/{signal_id}``：
  沿用 ``SourceStatusView`` 权限（401/403/正式角色）、严格限定 signal 属于 source
  （否则 404），正常 TYC 返回完整 ``report`` 与长 content，malformed 回落
  ``report=None``/``report_truncated=false``，>256KB 返回 ``report=None`` +
  ``report_truncated=true`` 且正文安全截断、响应体受控；普通信号 ``report=None``；
  重复查询稳定；untrusted 文本原样返回不执行。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.agent.tyc_report import (
    DimensionFinding,
    DimensionStatus,
    RiskLevel,
    TycRiskReport,
    render_key_summary,
)
from app.auth.permissions import PERM_SOURCE_STATUS_VIEW, ROLE_PERMISSIONS
from app.signals.models import DataSource, RawSignal

SUMMARY_MAX = 240
RAW_DATA_MAX_BYTES = 256 * 1024
DETAIL_CONTENT_MAX = 5000
SENTINEL = "RAW_DATA_SENTINEL_9f3c"

GENERATED_AT = datetime(2026, 9, 29, 4, 30, tzinfo=UTC)
FORMAL_ROLES = ("viewer", "risk_analyst", "risk_admin", "platform_admin")


def _source(session: Session, code: str, validity_days: int | None) -> DataSource:
    source = DataSource(
        code=code,
        name=f"测试信息源 {code}",
        source_type="official_api",
        credibility=90,
        auth_type="none",
        login_config={},
        adapter_config={},
        adapter_status="builtin",
        adapter_version=0,
        enabled=True,
        signal_validity_days=validity_days,
    )
    session.add(source)
    session.flush()
    return source


def _signal(
    session: Session,
    source: DataSource,
    index: int,
    *,
    content: str = "采集记录正文",
    raw_data: object = None,
    title: str | None = None,
) -> RawSignal:
    now = datetime.now(UTC)
    signal = RawSignal(
        source_id=source.id,
        external_id=f"external-{source.code}-{index}",
        title=title or f"采集记录 {index}",
        content=content,
        url=f"https://example.test/signals/{index}",
        published_at=now,
        collected_at=now,
        fingerprint=f"fingerprint-{source.code}-{index}",
        raw_data={"secret": index} if raw_data is None else raw_data,
        validity_state="legacy",
        validity_reason={
            "code": "legacy_unmigrated",
            "anchor_source": "legacy",
            "details": {},
        },
    )
    session.add(signal)
    session.flush()
    return signal


def _report(company: str = "测试企业有限公司") -> TycRiskReport:
    return TycRiskReport(
        company_name=company,
        supplier_code="SUP-0001",
        credit_code="913302127995394959",
        reg_status="存续",
        generated_at=GENERATED_AT,
        period_key="tyc:SUP-0001:2026-W40",
        dimensions=[
            DimensionFinding(
                key="get_risk_overview",
                name="风险总览",
                status=DimensionStatus.SUCCESS,
                risk_level=RiskLevel.ALERT,
                hit=True,
                summary="开庭公告 1 条，法院公告 1 条",
                evidence_refs=["开庭公告", "法院公告"],
                raw_ref="dimensions.get_risk_overview.raw",
            ),
            DimensionFinding(
                key="get_judicial_case",
                name="司法案件",
                status=DimensionStatus.SUCCESS,
                hit=False,
                summary="未发现司法案件记录",
            ),
        ],
    )


def _report_raw() -> dict:
    return _report().model_dump(mode="json")


# --------------------------------------------------------------------------
# 列表受控摘要
# --------------------------------------------------------------------------


def test_list_summary_derives_from_tyc_report(
    client: TestClient, db_session: Session
) -> None:
    source = _source(db_session, "tyc-list-source", None)
    report = _report()
    signal = _signal(
        db_session,
        source,
        1,
        content=render_key_summary(report),
        raw_data=report.model_dump(mode="json"),
    )
    db_session.commit()

    response = client.get(f"/api/v1/sources/{source.id}/signals?scope=all")

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["id"] == signal.id
    assert item["summary"].startswith("重点命中")
    assert "开庭公告" in item["summary"]
    assert len(item["summary"]) <= SUMMARY_MAX
    assert "raw_data" not in item
    assert "fingerprint" not in item


def test_list_summary_falls_back_to_content_and_stable_placeholder(
    client: TestClient, db_session: Session
) -> None:
    source = _source(db_session, "fallback-source", None)
    long_content = "外部风险提示：" + ("细节" * 500)
    long_signal = _signal(db_session, source, 1, content=long_content, raw_data={"secret": 1})
    empty_signal = _signal(db_session, source, 2, content="   ", raw_data={"unexpected": True})
    db_session.commit()

    first = client.get(f"/api/v1/sources/{source.id}/signals?scope=all")
    second = client.get(f"/api/v1/sources/{source.id}/signals?scope=all")

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    items = {item["id"]: item for item in first.json()["items"]}
    long_summary = items[long_signal.id]["summary"]
    assert len(long_summary) <= SUMMARY_MAX
    assert long_content.startswith(long_summary.rstrip("…")[:40])
    assert long_summary != long_content.strip()
    empty_summary = items[empty_signal.id]["summary"]
    assert empty_summary.strip() != ""
    assert len(empty_summary) <= SUMMARY_MAX


def test_list_does_not_leak_raw_data(
    client: TestClient, db_session: Session
) -> None:
    source = _source(db_session, "no-leak-source", None)
    _signal(db_session, source, 1, content="短正文", raw_data=_report_raw())
    _signal(
        db_session,
        source,
        2,
        content="普通正文" * 200,
        raw_data={"sentinel": SENTINEL, "blob": "y" * 5000},
    )
    db_session.commit()

    response = client.get(f"/api/v1/sources/{source.id}/signals?scope=all")

    assert response.status_code == 200
    assert SENTINEL not in response.text
    assert "raw_data" not in response.text


# --------------------------------------------------------------------------
# 详情端点：正常 TYC 报告
# --------------------------------------------------------------------------


def test_detail_returns_tyc_report_and_long_content(
    client: TestClient, db_session: Session
) -> None:
    source = _source(db_session, "detail-source", None)
    long_content = "完整正文：" + ("x" * 3000)
    signal = _signal(
        db_session, source, 1, content=long_content, raw_data=_report_raw()
    )
    db_session.commit()

    response = client.get(f"/api/v1/sources/{source.id}/signals/{signal.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == signal.id
    assert body["content"] == long_content
    assert body["summary"] != ""
    assert body["report"] is not None
    assert body["report"]["report_kind"] == "supplier_profile"
    assert len(body["report"]["dimensions"]) == 2
    assert body["report_truncated"] is False
    assert "raw_data" not in body


@pytest.mark.parametrize("role", FORMAL_ROLES)
def test_detail_readable_by_all_formal_roles(
    client: TestClient, db_session: Session, auth_as, role: str
) -> None:
    source = _source(db_session, f"role-detail-{role}", None)
    signal = _signal(db_session, source, 1, raw_data=_report_raw())
    db_session.commit()
    auth_as(role, f"role-detail-user-{role}")

    response = client.get(f"/api/v1/sources/{source.id}/signals/{signal.id}")

    assert response.status_code == 200
    assert response.json()["report"] is not None


def test_detail_requires_session_and_permission(
    client: TestClient,
    db_session: Session,
    auth_as,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source(db_session, "perm-detail-source", None)
    signal = _signal(db_session, source, 1, raw_data=_report_raw())
    db_session.commit()

    client.cookies.clear()
    client.headers.pop("X-CSRF-Token", None)
    assert (
        client.get(f"/api/v1/sources/{source.id}/signals/{signal.id}").status_code
        == 401
    )

    monkeypatch.setitem(
        ROLE_PERMISSIONS,
        "viewer",
        ROLE_PERMISSIONS["viewer"] - {PERM_SOURCE_STATUS_VIEW},
    )
    auth_as("viewer", "detail-no-permission")
    assert (
        client.get(f"/api/v1/sources/{source.id}/signals/{signal.id}").status_code
        == 403
    )


def test_detail_strictly_scopes_signal_to_source(
    client: TestClient, db_session: Session
) -> None:
    source_a = _source(db_session, "scope-a", None)
    source_b = _source(db_session, "scope-b", None)
    signal_a = _signal(db_session, source_a, 1, raw_data=_report_raw())
    db_session.commit()

    assert (
        client.get(f"/api/v1/sources/{source_b.id}/signals/{signal_a.id}").status_code
        == 404
    )
    assert (
        client.get(f"/api/v1/sources/{source_a.id}/signals/999999999").status_code == 404
    )
    assert client.get("/api/v1/sources/999999999/signals/1").status_code == 404


# --------------------------------------------------------------------------
# 详情端点：回落 / 截断 / 普通信号 / 稳定性 / untrusted 文本
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw_data",
    [
        {},
        [],
        "malformed",
        123,
        {"report_kind": "supplier_profile"},
        {"report_kind": "other", "dimensions": "x"},
        {"report_kind": "supplier_profile", "company_name": "x"},
    ],
    ids=["empty-dict", "list", "string", "int", "missing-fields", "wrong-kind", "partial"],
)
def test_detail_malformed_raw_data_falls_back_to_no_report(
    client: TestClient, db_session: Session, raw_data: object
) -> None:
    source = _source(db_session, f"malformed-{uuid.uuid4().hex[:8]}", None)
    signal = _signal(db_session, source, 1, content="普通正文", raw_data=raw_data)
    db_session.commit()

    response = client.get(f"/api/v1/sources/{source.id}/signals/{signal.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["report"] is None
    assert body["report_truncated"] is False
    assert body["content"] == "普通正文"


def test_detail_truncates_oversized_raw_data(
    client: TestClient, db_session: Session
) -> None:
    source = _source(db_session, "oversized-source", None)
    blob = "x" * (RAW_DATA_MAX_BYTES + 4096)
    raw = {"report_kind": "supplier_profile", "blob": blob, "sentinel": SENTINEL}
    content = "超长正文：" + ("详细" * 6000)
    signal = _signal(db_session, source, 1, content=content, raw_data=raw)
    db_session.commit()

    response = client.get(f"/api/v1/sources/{source.id}/signals/{signal.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["report"] is None
    assert body["report_truncated"] is True
    assert body["content"] != content
    assert len(body["content"]) <= DETAIL_CONTENT_MAX
    # 巨大 raw_data 结构不得进入响应体：哨兵/巨块均不出现，整体响应受控。
    assert SENTINEL not in response.text
    assert blob not in response.text
    assert len(response.content) < 200 * 1024


def test_detail_never_exposes_analysis_context(
    client: TestClient, db_session: Session
) -> None:
    """私有分析上下文只供模型消费：详情报告字段不变，响应体不含上下文内容。"""
    source = _source(db_session, "ctx-hidden-source", None)
    raw = _report_raw()
    raw["analysis_context"] = {
        "context_version": "tyc-analysis-context-v1",
        "company_name": _report().company_name,
        "supplier_code": "SUP-0001",
        "generated_at": GENERATED_AT.isoformat(),
        "query_incomplete": False,
        "incomplete_dimensions": [],
        "dimensions": [
            {
                "key": "get_risk_overview",
                "name": "风险总览",
                "status": "success",
                "risk_level": "警示",
                "hit": True,
                "summary": "开庭公告 1 条",
                "raw_state": "complete",
                "raw_excerpt": f"内部原文片段-{SENTINEL}",
            }
        ],
    }
    signal = _signal(db_session, source, 1, raw_data=raw)
    db_session.commit()

    response = client.get(f"/api/v1/sources/{source.id}/signals/{signal.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["report"] == _report_raw()
    assert "analysis_context" not in response.text
    assert SENTINEL not in response.text
    assert "raw_excerpt" not in response.text


def test_detail_still_rejects_unknown_raw_fields_with_context_present(
    client: TestClient, db_session: Session
) -> None:
    """共享提取只移除已知私有字段：其他未知键仍按 extra=forbid 拒绝。"""
    source = _source(db_session, "ctx-extra-source", None)
    raw = _report_raw()
    raw["analysis_context"] = {"context_version": "tyc-analysis-context-v1"}
    raw["unexpected_field"] = "不应被静默忽略"
    signal = _signal(db_session, source, 1, raw_data=raw)
    db_session.commit()

    response = client.get(f"/api/v1/sources/{source.id}/signals/{signal.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["report"] is None
    assert body["report_truncated"] is False
    assert "不应被静默忽略" not in response.text


def test_detail_ordinary_signal_report_is_none(
    client: TestClient, db_session: Session
) -> None:
    source = _source(db_session, "ordinary-source", None)
    content = "普通来源正文：" + ("内容" * 100)
    signal = _signal(
        db_session, source, 1, content=content, raw_data={"kind": "rss", "items": [1, 2]}
    )
    db_session.commit()

    response = client.get(f"/api/v1/sources/{source.id}/signals/{signal.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["report"] is None
    assert body["report_truncated"] is False
    assert body["content"] == content


def test_detail_repeated_query_is_stable(
    client: TestClient, db_session: Session
) -> None:
    source = _source(db_session, "stable-source", None)
    signal = _signal(db_session, source, 1, raw_data=_report_raw())
    db_session.commit()
    url = f"/api/v1/sources/{source.id}/signals/{signal.id}"

    first = client.get(url)
    second = client.get(url)

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()


def test_untrusted_content_round_trips_as_plain_text(
    client: TestClient, db_session: Session
) -> None:
    source = _source(db_session, "untrusted-source", None)
    payload = '<script>alert(1)</script> **bold** <img src=x onerror=alert(2)>'
    signal = _signal(db_session, source, 1, content=payload, raw_data={"text": payload})
    db_session.commit()

    response = client.get(f"/api/v1/sources/{source.id}/signals/{signal.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["content"] == payload
    assert body["report"] is None
    assert body["report_truncated"] is False
