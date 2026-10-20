"""情报门控（两段式：门控 → 报告）的行为锁定。

锁定的行为：
- 门控判定不相关：落 skipped + gate_reason，且**不发起报告调用**；再次调度不重复
  门控（skipped 是已判定的幂等标记），列表默认不出现，可用 status=skipped 抽查。
- 门控判定相关：正常生成 ready，gate_prompt_version 记录本次口径，gate_reason 为空。
- 门控调用失败：落 failed + 「门控失败」前缀，走既有冷却期，不阻塞同批其它事件。
- 关注域（维度主题 + 启用供应商行业/原材料/产品关键词/国家）注入门控输入，且
  同样的画像生成同样的提示词（确定性排序）。
- 关闭门控或分类不在门控范围内时，保持原有「直接生成报告」语义。

离线 Fake Provider 的门控固定放行（见 FakeIntelligenceProvider 文档），因此过滤
路径统一由本文件的 GatedProvider 覆盖。
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from pytest import MonkeyPatch
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_intelligence import (
    NOW_UTC,
    POLICY_SOURCE_CODE,
    _event,
    _use_test_session,
)

from app.ai.intelligence_prompt import intelligence_gate_system_prompt
from app.ai.intelligence_providers import FakeIntelligenceProvider
from app.ai.intelligence_schemas import (
    GATE_PROMPT_VERSION,
    INTELLIGENCE_PROMPT_VERSION,
    IntelligenceGateInput,
    IntelligenceGateOutput,
)
from app.ai.providers import AIProviderError
from app.config import INTELLIGENCE_GATE_MAX_CONTENT_CHARS
from app.risks import intelligence_batch, intelligence_service
from app.risks.intelligence_models import IntelligenceReport
from app.risks.models import RiskEventSignal
from app.signals.models import RawSignal
from app.suppliers.models import Supplier, SupplierProduct

MEANINGFUL_TITLE = "欧盟对钢制货架发起反倾销登记"


class GatedProvider(FakeIntelligenceProvider):
    """可编排门控结果的离线 Provider：记录门控与报告各被调用多少次。"""

    def __init__(
        self,
        *,
        relevant: bool,
        reason: str = "测试判定依据",
        fail_gate: bool = False,
    ) -> None:
        self.relevant = relevant
        self.reason = reason
        self.fail_gate = fail_gate
        self.gate_calls: list[IntelligenceGateInput] = []
        self.report_calls = 0

    async def gate_intelligence(
        self, value: IntelligenceGateInput
    ) -> IntelligenceGateOutput:
        self.gate_calls.append(value)
        if self.fail_gate:
            raise AIProviderError("门控模型不可用")
        return IntelligenceGateOutput(relevant=self.relevant, reason_zh=self.reason)

    async def generate_intelligence(self, value):  # type: ignore[override]
        self.report_calls += 1
        return await super().generate_intelligence(value)


def _use_provider(monkeypatch: MonkeyPatch, provider: GatedProvider) -> None:
    monkeypatch.setenv("AI_PROVIDER", "fake")
    monkeypatch.setattr(
        intelligence_batch, "build_intelligence_provider", lambda _settings: provider
    )


def _set_gate(
    monkeypatch: MonkeyPatch,
    *,
    enabled: bool = True,
    categories: tuple[str, ...] = ("policy",),
) -> None:
    monkeypatch.setattr(intelligence_batch, "INTELLIGENCE_GATE_ENABLED", enabled)
    monkeypatch.setattr(
        intelligence_batch, "INTELLIGENCE_GATE_CATEGORIES", frozenset(categories)
    )


def _row(session: Session, event_id: int) -> IntelligenceReport:
    row = session.scalar(
        select(IntelligenceReport).where(IntelligenceReport.event_id == event_id)
    )
    assert row is not None
    return row


def _add_supplier(session: Session, *, code: str = "SUP-GATE") -> Supplier:
    supplier = Supplier(
        supplier_code=code,
        legal_name="门控测试供应商有限公司",
        country_code="CN",
        industry="塑料制品#电梯配件",
        raw_materials=["钢材", "化工原料"],
    )
    session.add(supplier)
    session.flush()
    session.add(
        SupplierProduct(
            supplier_id=supplier.id,
            name="冷轧板",
            keywords=["冷轧板", "电梯部件", "添加剂"],
        )
    )
    session.flush()
    return supplier


def test_gate_rejection_skips_report_and_is_idempotent(
    db_session: Session, monkeypatch: MonkeyPatch
) -> None:
    # Given：门控判不相关的事件
    provider = GatedProvider(relevant=False, reason="勘误类文书，未改变规则内容")
    _use_provider(monkeypatch, provider)
    _set_gate(monkeypatch)
    _use_test_session(monkeypatch, db_session)
    event = _event(db_session, POLICY_SOURCE_CODE, "gate-skip")

    # When：批量生成
    generated = intelligence_batch.generate_pending_intelligence_reports(
        now_utc=NOW_UTC
    )

    # Then：不生成报告，落 skipped + 依据 + 口径版本
    assert generated == 0
    assert provider.report_calls == 0
    assert len(provider.gate_calls) == 1
    row = _row(db_session, event.id)
    assert row.status == "skipped"
    assert row.gate_reason == "勘误类文书，未改变规则内容"
    assert row.gate_prompt_version == GATE_PROMPT_VERSION
    assert row.report_zh is None
    assert row.suggestions == []
    assert row.generated_at is None

    # When：再次调度
    second = intelligence_batch.generate_pending_intelligence_reports(now_utc=NOW_UTC)

    # Then：skipped 视为已判定，不重复消耗门控调用，候选粗筛也不再选中它
    assert second == 0
    assert len(provider.gate_calls) == 1
    coarse_ids = {
        event_id
        for event_id, _ in db_session.execute(
            intelligence_batch._candidate_event_select()
        ).all()
    }
    assert event.id not in coarse_ids


def test_gate_rejection_hides_row_from_default_list(
    client: TestClient, db_session: Session, monkeypatch: MonkeyPatch
) -> None:
    # Given：一条 skipped 与一条 ready 的政策情报
    _use_provider(monkeypatch, GatedProvider(relevant=False, reason="与关注域无交集"))
    _set_gate(monkeypatch)
    _use_test_session(monkeypatch, db_session)
    skipped_event = _event(db_session, POLICY_SOURCE_CODE, "list-skip")
    intelligence_batch.generate_pending_intelligence_reports(now_utc=NOW_UTC)
    ready_event = _event(db_session, POLICY_SOURCE_CODE, "list-ready")
    db_session.add(
        IntelligenceReport(
            event_id=ready_event.id,
            category="policy",
            report_zh="正文",
            suggestions=["建议"],
            provider="fake",
            model="fake-deterministic-v1",
            prompt_version=INTELLIGENCE_PROMPT_VERSION,
            status="ready",
            generated_at=NOW_UTC,
        )
    )
    db_session.commit()

    default = client.get("/api/v1/intelligence", params={"tab": "policy"})
    skipped = client.get(
        "/api/v1/intelligence", params={"tab": "policy", "status": "skipped"}
    )
    failed = client.get(
        "/api/v1/intelligence", params={"tab": "policy", "status": "failed"}
    )

    # Then：默认只列 ready；skipped 可单独抽查且带判定依据；failed 不是可查询状态
    assert default.status_code == 200
    assert default.json()["total"] == 1
    assert default.json()["items"][0]["event_id"] == ready_event.id
    assert default.json()["items"][0]["gate_reason"] is None
    assert skipped.status_code == 200
    assert skipped.json()["total"] == 1
    assert skipped.json()["items"][0]["event_id"] == skipped_event.id
    assert skipped.json()["items"][0]["gate_reason"] == "与关注域无交集"
    assert failed.status_code == 422

    detail = client.get(f"/api/v1/intelligence/{skipped.json()['items'][0]['id']}")
    assert detail.status_code == 200
    assert detail.json()["gate_reason"] == "与关注域无交集"


def test_gate_allow_generates_ready_and_records_gate_version(
    db_session: Session, monkeypatch: MonkeyPatch
) -> None:
    # Given：门控放行
    provider = GatedProvider(relevant=True)
    _use_provider(monkeypatch, provider)
    _set_gate(monkeypatch)
    _use_test_session(monkeypatch, db_session)
    event = _event(db_session, POLICY_SOURCE_CODE, "gate-allow")

    # When
    generated = intelligence_batch.generate_pending_intelligence_reports(
        now_utc=NOW_UTC
    )

    # Then：报告正常生成，gate_prompt_version 标记已过门控，gate_reason 为空
    assert generated == 1
    assert provider.report_calls == 1
    row = _row(db_session, event.id)
    assert row.status == "ready"
    assert row.report_zh
    assert row.gate_reason is None
    assert row.gate_prompt_version == GATE_PROMPT_VERSION
    assert row.generated_at is not None


def test_gate_failure_records_failed_and_obeys_cooldown(
    db_session: Session, monkeypatch: MonkeyPatch
) -> None:
    # Given：门控调用持续失败
    provider = GatedProvider(relevant=True, fail_gate=True)
    _use_provider(monkeypatch, provider)
    _set_gate(monkeypatch)
    _use_test_session(monkeypatch, db_session)
    event = _event(db_session, POLICY_SOURCE_CODE, "gate-fail")

    # When
    generated = intelligence_batch.generate_pending_intelligence_reports(
        now_utc=NOW_UTC
    )

    # Then：不生成报告，落 failed + 「门控失败」前缀
    assert generated == 0
    assert provider.report_calls == 0
    row = _row(db_session, event.id)
    assert row.status == "failed"
    assert row.error is not None and row.error.startswith("门控失败")
    assert row.gate_prompt_version == GATE_PROMPT_VERSION

    # When：冷却期内再次调度（updated_at 以测试时间为准，避免真实时钟干扰）
    row.updated_at = NOW_UTC
    db_session.flush()
    inside = intelligence_batch.generate_pending_intelligence_reports(
        now_utc=NOW_UTC + timedelta(hours=1)
    )
    # Then：不重复消耗门控调用
    assert inside == 0
    assert len(provider.gate_calls) == 1

    # When：冷却期后再调度
    after = intelligence_batch.generate_pending_intelligence_reports(
        now_utc=NOW_UTC + timedelta(hours=25)
    )
    # Then：允许重试
    assert after == 0
    assert len(provider.gate_calls) == 2


def test_gate_disabled_for_category_keeps_direct_report(
    db_session: Session, monkeypatch: MonkeyPatch
) -> None:
    # Given：门控只对行业开放，但事件属于政策情报
    provider = GatedProvider(relevant=False)
    _use_provider(monkeypatch, provider)
    _set_gate(monkeypatch, categories=("industry",))
    _use_test_session(monkeypatch, db_session)
    event = _event(db_session, POLICY_SOURCE_CODE, "gate-off")

    # When
    generated = intelligence_batch.generate_pending_intelligence_reports(
        now_utc=NOW_UTC
    )

    # Then：不经门控，直接生成报告
    assert generated == 1
    assert provider.gate_calls == []
    assert provider.report_calls == 1
    assert _row(db_session, event.id).status == "ready"


@pytest.mark.parametrize("category", ["policy", "industry"])
def test_scope_contains_dimension_themes_and_supplier_profile(
    db_session: Session, category: str
) -> None:
    # Given：一个启用供应商（行业用 # 连接、含原材料与产品关键词）与一个停用供应商
    _add_supplier(db_session)
    db_session.add(
        Supplier(
            supplier_code="SUP-DISABLED",
            legal_name="停用供应商",
            country_code="JP",
            industry="半导体",
            enabled=False,
        )
    )
    db_session.flush()

    # When
    scope = intelligence_service.load_intelligence_scope(db_session, category)
    again = intelligence_service.load_intelligence_scope(db_session, category)

    # Then：维度主题来自维度声明；画像只取启用供应商，且拆分 # 连接的多值行业
    expected_theme = {
        "policy": "行业监管",
        "industry": "关键原材料与零部件短缺",
    }[category]
    assert expected_theme in scope.themes
    assert scope.industries == ["塑料制品", "电梯配件"]
    assert scope.product_keywords == sorted(["冷轧板", "电梯部件", "钢材"])
    assert scope.countries == ["CN"]
    assert scope.supplier_count == 1
    # 同样画像必须生成同样关注域（提示词稳定，便于缓存与回溯）
    assert again == scope


def test_generic_scope_keywords_are_excluded_before_limit(
    db_session: Session, monkeypatch: MonkeyPatch
) -> None:
    # Given：通用品类词（添加剂/化工原料/工业耗材）出现次数高于具体品类词
    monkeypatch.setattr(
        intelligence_service, "INTELLIGENCE_GATE_SCOPE_KEYWORD_LIMIT", 2
    )
    for index in range(3):
        session_supplier = Supplier(
            supplier_code=f"SUP-BROAD-{index}",
            legal_name=f"通用词供应商{index}",
            country_code="CN",
            raw_materials=["添加剂", "化工原料", "工业耗材"],
        )
        db_session.add(session_supplier)
    supplier = Supplier(
        supplier_code="SUP-SPECIFIC",
        legal_name="具体品类供应商",
        country_code="CN",
    )
    db_session.add(supplier)
    db_session.flush()
    db_session.add(
        SupplierProduct(
            supplier_id=supplier.id,
            name="冷轧板",
            keywords=["冷轧板", "电梯部件"],
        )
    )
    db_session.flush()

    # When
    scope = intelligence_service.load_intelligence_scope(db_session, "policy")

    # Then：通用词先被剔除，再参与截断——不能占用 2 个名额
    assert scope.product_keywords == sorted(["冷轧板", "电梯部件"])
    for generic in intelligence_service.GENERIC_SCOPE_KEYWORDS:
        assert generic not in scope.product_keywords


def test_gate_input_carries_scope_facts_and_truncated_content(
    db_session: Session,
) -> None:
    # Given：超长正文与 AI 已提取的产品/行业/国家
    event = _event(db_session, POLICY_SOURCE_CODE, "gate-input", event_type="trade_policy")
    signal = db_session.scalar(
        select(RawSignal)
        .join(RiskEventSignal, RiskEventSignal.signal_id == RawSignal.id)
        .where(RiskEventSignal.event_id == event.id)
    )
    assert signal is not None
    signal.content = "公告正文" * 2000
    signal.title = MEANINGFUL_TITLE
    event.facts = {
        "affected_products": ["钢制货架"],
        "affected_industries": ["metal furniture and shelving manufacturing"],
        "affected_countries": ["CN"],
    }
    db_session.flush()
    scope = intelligence_service.load_intelligence_scope(db_session, "policy")

    # When
    payload = intelligence_service.build_gate_input(
        db_session, event, "policy", scope
    )

    # Then：正文按门控上限截断，关注域与事件事实一并进入输入
    assert payload is not None
    assert len(payload.content) == INTELLIGENCE_GATE_MAX_CONTENT_CHARS
    assert payload.title == MEANINGFUL_TITLE
    assert payload.affected_products == ["钢制货架"]
    assert payload.affected_industries == [
        "metal furniture and shelving manufacturing"
    ]
    assert payload.affected_countries == ["CN"]
    assert payload.scope == scope


def test_fake_provider_gate_allows_everything() -> None:
    # 离线 Provider 不得静默丢数据：门控固定放行，过滤路径由测试子类覆盖。
    result = asyncio.run(
        FakeIntelligenceProvider().gate_intelligence(
            IntelligenceGateInput(
                event_id=1,
                category="policy",
                source_code=POLICY_SOURCE_CODE,
                source_name="测试信源",
                title="标题",
                summary="摘要",
                content="正文",
                event_type="trade_policy",
            )
        )
    )
    assert result.relevant is True
    assert result.reason_zh


def test_gate_output_truncates_overlong_reason() -> None:
    # 依据过长只截断，不让整条判定因字数不合法作废（与 suggestions 的降级口径一致）。
    result = IntelligenceGateOutput(relevant=False, reason_zh="不" * 300)
    assert len(result.reason_zh) == 200
    assert result.relevant is False


def test_gate_prompt_covers_scope_and_category() -> None:
    policy_prompt = intelligence_gate_system_prompt("policy")
    industry_prompt = intelligence_gate_system_prompt("industry")

    # 判定口径、保守放行、关注域与机器契约都必须出现在提示词里
    assert "relevant" in policy_prompt
    assert "拿不准" in policy_prompt
    assert "scope" in policy_prompt
    assert "政策与法规类情报" in policy_prompt
    assert "产业与供应市场类情报" in industry_prompt
    assert "政策与法规类情报" not in industry_prompt
