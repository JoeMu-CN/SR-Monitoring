"""D10 端到端验收场景（技术方案 13.3 八条）。

每条场景有独立测试函数，直接对应验收清单：
1. 多地点多产品供应商导入与页面展示
2. 天气事件覆盖生产地点生成高等级提醒并展示地理匹配理由
3. 企业公告提及法人主体按名称/注册编号生成关联
4. 仅提及国家的弱信号不能生成 P1
5. 相同公告被两个来源转载只形成一个事件和一条当前提醒
6. AI 超时重试、最终失败不生成无依据提醒
7. 非法 Excel 返回逐行错误且不破坏已有数据
8. 风险详情可打开原始来源并查看评分明细
"""

from io import BytesIO

from fastapi.testclient import TestClient
from openpyxl import load_workbook
from pytest import MonkeyPatch
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai import service as ai_service
from app.ai.schemas import SignalAnalysisResult
from app.risks.models import (
    RiskAlert,
    RiskEvent,
    RiskEventSignal,
    SupplierEventMatch,
)
from app.signals.models import RawSignal
from app.suppliers.importer import (
    SHEET_PRODUCTS,
    SHEET_SITES,
    SHEET_SUPPLIERS,
    create_template,
)


class AcceptanceProvider:
    """可控的确定性 AI 提供者，供验收场景注入。"""

    provider_name = "acceptance-test"
    model = "acceptance-v1"

    def __init__(self, result: SignalAnalysisResult | None = None) -> None:
        self.result = result
        self.fail_first = 0

    async def analyze_signal(self, _value: object) -> SignalAnalysisResult:
        if self.fail_first > 0:
            self.fail_first -= 1
            raise ai_service.AIProviderError("模拟模型超时")  # type: ignore[attr-defined]
        return self.result or SignalAnalysisResult(
            event_type="weather",
            suggested_severity="high",
            organizations=[{"name": "上海华辰精密制造有限公司", "aliases": []}],
            locations=[{"name": "上海市", "country_code": "CN", "city": "上海市"}],
            affected_activities=["production"],
            affected_products=[],
            start_at="2026-08-18T08:00:00+08:00",
            end_at="2026-08-19T08:00:00+08:00",
            summary_zh="台风影响上海华辰生产活动",
            evidence_sentences=["受台风影响，上海华辰精密制造有限公司暂停生产。"],
            confidence=0.9,
        )


def _build_workbook(
    *,
    supplier_code: str = "ACC-001",
    legal_name: str = "上海华辰精密制造有限公司",
    registry_no: str = "91310000ACCEPTANCE01",
) -> bytes:
    workbook = load_workbook(BytesIO(create_template()))
    workbook[SHEET_SUPPLIERS].append(
        [
            supplier_code,
            legal_name,
            "CN",
            registry_no,
            "上海市浦东新区华辰登记路1号",
            None,
            None,
            "",
            True,
        ]
    )
    workbook[SHEET_SITES].append(
        [
            supplier_code,
            "上海浦东工厂",
            "CN",
            "上海市",
            "上海市",
            "浦东新区",
            "上海市浦东新区华辰路1号",
            31.2304,
            121.4737,
        ]
    )
    workbook[SHEET_SITES].append(
        [
            supplier_code,
            "苏州昆山工厂",
            "CN",
            "江苏省",
            "苏州市",
            "昆山市",
            "昆山市华辰工业园2号",
            31.385,
            120.981,
        ]
    )
    workbook[SHEET_PRODUCTS].append([supplier_code, "精密轴承", "轴承;精密加工"])
    workbook[SHEET_PRODUCTS].append([supplier_code, "减速机", "减速机;传动"])
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def _import_workbook(client: TestClient, content: bytes) -> object:
    return client.post(
        "/api/v1/suppliers/import",
        files={"file": ("suppliers.xlsx", content)},
    )


def _import_signal(
    client: TestClient,
    *,
    external_id: str,
    title: str,
    content: str,
    url: str | None = None,
    published_at: str = "2026-08-18T08:00:00+08:00",
) -> None:
    import json

    document = {
        "version": "1.0",
        "signals": [
            {
                "external_id": external_id,
                "title": title,
                "content": content,
                "url": url,
                "published_at": published_at,
            }
        ],
    }
    response = client.post(
        "/api/v1/signals/import",
        files={
            "file": (
                "signals.json",
                json.dumps(document, ensure_ascii=False).encode(),
                "application/json",
            )
        },
    )
    assert response.status_code == 200


# ── 场景 1：多地点多产品供应商导入与展示 ──────────────────────────────
def test_acceptance_1_multi_site_product_import_and_query(
    client: TestClient, db_session: Session
) -> None:
    response = _import_workbook(client, _build_workbook())
    assert response.status_code == 200
    assert response.json()["created_suppliers"] == 1

    suppliers = client.get("/api/v1/suppliers", params={"keyword": "ACC-001"})
    assert suppliers.status_code == 200
    assert suppliers.json()["total"] == 1
    item = suppliers.json()["items"][0]
    assert item["legal_name"] == "上海华辰精密制造有限公司"
    assert len(item["sites"]) == 2
    assert len(item["products"]) == 2
    assert {site["city"] for site in item["sites"]} == {"上海市", "苏州市"}


# ── 场景 2：天气事件覆盖生产地点，高等级提醒 + 地理匹配理由 ────────────
def test_acceptance_2_weather_event_geographic_match_high_level(
    client: TestClient, db_session: Session, monkeypatch: MonkeyPatch
) -> None:
    provider = AcceptanceProvider()
    monkeypatch.setattr(ai_service, "get_ai_provider", lambda _settings: provider)
    _import_workbook(client, _build_workbook())
    _import_signal(
        client,
        external_id="ACC-SIG-WEATHER",
        title="台风影响上海浦东",
        content="台风中心位于上海浦东附近，上海华辰精密制造有限公司所在区域受影响。",
        url="https://example.com/acceptance/weather",
    )
    signal_id = db_session.scalar(select(RawSignal.id).order_by(RawSignal.id))
    assert signal_id is not None

    response = client.post(f"/api/v1/signals/{signal_id}/process")

    assert response.status_code == 200
    assert len(response.json()["alert_ids"]) >= 1
    alert = db_session.scalar(select(RiskAlert).order_by(RiskAlert.id))
    match = db_session.scalar(select(SupplierEventMatch))
    assert alert is not None and match is not None
    assert alert.level in {"P1", "P2"}
    assert "上海" in " ".join(match.reasons)
    assert any("距离" in str(item) or "site" in str(item).lower() for item in match.evidence)


# ── 场景 3：企业公告提及法人主体，按名称/注册编号关联 ──────────────────
def test_acceptance_3_legal_name_and_registry_no_match(
    client: TestClient, db_session: Session, monkeypatch: MonkeyPatch
) -> None:
    _import_workbook(client, _build_workbook())
    _import_signal(
        client,
        external_id="ACC-SIG-NOTICE",
        title="企业公告",
        content=(
            "上海华辰精密制造有限公司（统一社会信用代码 91310000ACCEPTANCE01）"
            "发布停业整顿公告。"
        ),
        url="https://example.com/acceptance/notice",
    )
    provider = AcceptanceProvider(
        SignalAnalysisResult(
            event_type="corporate",
            suggested_severity="critical",
            organizations=[
                {
                    "name": "上海华辰精密制造有限公司",
                    "aliases": [],
                    "registry_no": "91310000ACCEPTANCE01",
                }
            ],
            locations=[],
            affected_activities=["operations"],
            affected_products=[],
            summary_zh="上海华辰精密制造有限公司停业整顿",
            evidence_sentences=["上海华辰精密制造有限公司发布停业整顿公告。"],
            confidence=0.95,
        )
    )
    monkeypatch.setattr(ai_service, "get_ai_provider", lambda _settings: provider)
    signal_id = db_session.scalar(select(RawSignal.id).order_by(RawSignal.id))
    assert signal_id is not None

    response = client.post(f"/api/v1/signals/{signal_id}/process")

    assert response.status_code == 200
    alert = db_session.scalar(select(RiskAlert))
    match = db_session.scalar(select(SupplierEventMatch))
    assert alert is not None and match is not None
    assert match.match_type in {"registry_no", "legal_name", "registry_no+legal_name"}
    assert alert.level == "P1"


# ── 场景 4：仅提及国家的弱信号不能生成 P1 ─────────────────────────────
def test_acceptance_4_country_only_signal_not_p1(
    client: TestClient, db_session: Session, monkeypatch: MonkeyPatch
) -> None:
    _import_workbook(client, _build_workbook())
    provider = AcceptanceProvider(
        SignalAnalysisResult(
            event_type="geopolitical",
            suggested_severity="low",
            organizations=[],
            locations=[{"name": "中国", "country_code": "CN"}],
            affected_activities=["operations"],
            affected_products=[],
            summary_zh="中国区域出现一般性贸易波动",
            evidence_sentences=["中国区域出现一般性贸易波动，未涉及具体企业或地点。"],
            confidence=0.6,
        )
    )
    monkeypatch.setattr(ai_service, "get_ai_provider", lambda _settings: provider)
    _import_signal(
        client,
        external_id="ACC-SIG-COUNTRY",
        title="区域贸易波动",
        content="中国区域出现一般性贸易波动，未涉及具体企业或地点。",
    )
    signal_id = db_session.scalar(select(RawSignal.id).order_by(RawSignal.id))
    assert signal_id is not None

    response = client.post(f"/api/v1/signals/{signal_id}/process")

    assert response.status_code == 200
    alerts = list(db_session.scalars(select(RiskAlert)))
    assert all(alert.level != "P1" for alert in alerts)


# ── 场景 5：相同公告两个来源转载，只形成一个事件和一条当前提醒 ─────────
def test_acceptance_5_duplicate_articles_single_event_single_alert(
    client: TestClient, db_session: Session, monkeypatch: MonkeyPatch
) -> None:
    _import_workbook(client, _build_workbook())
    provider = AcceptanceProvider()
    monkeypatch.setattr(ai_service, "get_ai_provider", lambda _settings: provider)
    _import_signal(
        client,
        external_id="ACC-SIG-A",
        title="台风公告（官方）",
        content="上海华辰精密制造有限公司受台风影响暂停生产。",
        url="https://example.com/official",
        published_at="2026-08-18T08:00:00+08:00",
    )
    _import_signal(
        client,
        external_id="ACC-SIG-B",
        title="台风公告（转载）",
        content="上海华辰精密制造有限公司受同一台风事件影响。",
        url="https://example.com/rewrite",
        published_at="2026-08-18T09:00:00+08:00",
    )
    signal_ids = list(db_session.scalars(select(RawSignal.id).order_by(RawSignal.id)))
    assert len(signal_ids) == 2

    assert client.post(f"/api/v1/signals/{signal_ids[0]}/process").status_code == 200
    assert client.post(f"/api/v1/signals/{signal_ids[1]}/process").status_code == 200

    assert db_session.scalar(select(func.count()).select_from(RiskEvent)) == 1
    assert db_session.scalar(select(func.count()).select_from(RiskAlert)) == 1
    assert db_session.scalar(select(func.count()).select_from(RiskEventSignal)) == 2


# ── 场景 6：AI 超时重试、最终失败不生成无依据提醒 ─────────────────────
def test_acceptance_6_ai_retry_then_fail_no_unfounded_alert(
    client: TestClient, db_session: Session, monkeypatch: MonkeyPatch
) -> None:
    _import_workbook(client, _build_workbook())
    _import_signal(
        client,
        external_id="ACC-SIG-FAIL",
        title="无法解析的信号",
        content="内容无法被 AI 解析。",
    )
    provider = AcceptanceProvider()
    provider.fail_first = 2  # 连续失败直到超过重试上限
    monkeypatch.setattr(ai_service, "get_ai_provider", lambda _settings: provider)
    signal_id = db_session.scalar(select(RawSignal.id).order_by(RawSignal.id))
    assert signal_id is not None

    response = client.post(f"/api/v1/signals/{signal_id}/process")

    assert response.status_code == 502
    assert db_session.scalar(select(func.count()).select_from(RiskEvent)) == 0
    assert db_session.scalar(select(func.count()).select_from(RiskAlert)) == 0
    from app.ai.models import AIAnalysisRecord

    record = db_session.scalar(select(AIAnalysisRecord).order_by(AIAnalysisRecord.id.desc()))
    assert record is not None and record.status == "failed"


# ── 场景 7：非法 Excel 返回逐行错误且不破坏已有数据 ────────────────────
def test_acceptance_7_invalid_excel_reports_row_errors_keeps_data(
    client: TestClient, db_session: Session
) -> None:
    first = _import_workbook(client, _build_workbook())
    assert first.status_code == 200
    assert first.json()["created_suppliers"] == 1

    broken = _build_workbook(supplier_code="ACC-BAD", legal_name="", registry_no="")
    response = _import_workbook(client, broken)
    assert response.status_code == 422
    assert len(response.json()["detail"]["errors"]) >= 1

    suppliers = client.get("/api/v1/suppliers", params={"keyword": "ACC-"})
    assert suppliers.status_code == 200
    assert suppliers.json()["total"] >= 1
    assert any(item["supplier_code"] == "ACC-001" for item in suppliers.json()["items"])


# ── 场景 8：风险详情可打开原始来源并查看评分明细 ──────────────────────
def test_acceptance_8_detail_has_source_url_and_score_breakdown(
    client: TestClient, db_session: Session, monkeypatch: MonkeyPatch
) -> None:
    _import_workbook(client, _build_workbook())
    provider = AcceptanceProvider()
    monkeypatch.setattr(ai_service, "get_ai_provider", lambda _settings: provider)
    _import_signal(
        client,
        external_id="ACC-SIG-DETAIL",
        title="台风影响生产",
        content="上海华辰精密制造有限公司受台风影响暂停生产。",
        url="https://example.com/acceptance/detail",
    )
    signal_id = db_session.scalar(select(RawSignal.id).order_by(RawSignal.id))
    assert signal_id is not None
    assert client.post(f"/api/v1/signals/{signal_id}/process").status_code == 200

    alert_id = db_session.scalar(select(RiskAlert.id))
    assert alert_id is not None
    response = client.get(f"/api/v1/risk-alerts/{alert_id}")
    assert response.status_code == 200
    detail = response.json()
    assert detail["source_url"] == "https://example.com/acceptance/detail"
    assert "rule_version" in detail["score_detail"]
    assert detail["match_reasons"]
    assert detail["match_evidence"]
