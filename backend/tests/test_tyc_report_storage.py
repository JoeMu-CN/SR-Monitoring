"""Todo 9 天眼查报告信号存储测试（独立文件）。

新契约（替代旧 ``upsert_supplier_tyc_signal`` 时间戳 external_id 行为）：
- 同一 ISO 周内同 supplier_code：external_id 恒为 ``tyc-<code>-<period_key>``，
  相同固定业务字段幂等（同周首次报告为准，不新增、不产生重复 external_id）；
- 跨周产生新 external_id，并由既有 supersession 机制原子替代旧 active
  （旧 signal ``validity_state=superseded``，新 signal ``active``）；
- 指纹 = 明确固定字段 allowlist 的 canonical JSON（含 period_key，排除
  generated_at 等运行时字段），未来新增模型字段不会悄然改变指纹；
- 空报告（无任何维度条目）不落库；维度条目全部失败的非空报告仍如实落库
  （与 Todo 7 的部分报告契约一致）；
- 并发同周写入只保留一行画像，且不会留下重复 external_id。

执行环境：Compose 隔离测试栈，真实 PostgreSQL；MCP 走确定性 stub，零真实外网。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from pytest import MonkeyPatch
from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from tyc_batch_support import MultidimMcpStub, configure_committed_tyc

import app.agent.tyc_batch_supplier as tyc_batch_supplier_module
import app.agent.tyc_gateway as tyc_gateway_module
from app.agent.tyc_batch import run_tyc_supplier
from app.agent.tyc_report import TycRiskReport, build_risk_report, render_key_summary
from app.agent.tyc_report_storage import (
    TycReportWrite,
    report_fingerprint,
    store_tyc_report_signal,
)
from app.database import SessionLocal
from app.signals.models import DataSource, RawSignal
from app.suppliers.models import Supplier

_DIMS = ["get_risk_overview", "get_judicial_case"]
_STORAGE_POLICY = {
    "mode": "until_superseded",
    "fixed_days": 30,
    "review_required": True,
}
# 2026-05-06 09:30 UTC = 北京 17:30（ISO W19）；+7 天为 W20。
_WEEK_1 = datetime(2026, 5, 6, 9, 30, tzinfo=UTC)
_WEEK_2 = _WEEK_1 + timedelta(days=7)
_CODE = "SUP-STORE-001"
_PERIOD_1 = f"tyc:{_CODE}:2026-W19"
_PERIOD_2 = f"tyc:{_CODE}:2026-W20"

# 落库报告顶层字段白名单：多出的键即视为把源配置/运行时内部字段泄漏进 raw_data。
_ALLOWLIST_KEYS = {
    "report_kind",
    "company_name",
    "supplier_code",
    "credit_code",
    "reg_status",
    "generated_at",
    "period_key",
    "dimensions",
}
_RAW_SENTINEL = "RAW_MARKDOWN_SENTINEL_ABC123"


def _fresh_source(session: Session) -> DataSource:
    session.expire_all()
    source = session.scalar(select(DataSource).where(DataSource.code == "tianyancha"))
    assert source is not None, "迁移应已注册 tianyancha 信息源"
    return source


def _supplier(session: Session, code: str = _CODE) -> Supplier:
    supplier = Supplier(
        supplier_code=code,
        legal_name=f"{code} 有限公司",
        country_code="CN",
        enabled=True,
    )
    session.add(supplier)
    session.flush()
    return supplier


def _contract(
    summary: str = "历史欠税公告、开庭公告命中",
    statuses: dict[str, str] | None = None,
) -> dict[str, object]:
    statuses = statuses or {}
    dimensions: dict[str, object] = {}
    for tool in _DIMS:
        status = statuses.get(tool, "success")
        raw = f"# {tool}\n\n> 摘要：{summary}\n" if status == "success" else None
        dimensions[tool] = {"status": status, "raw": raw, "message": None}
    return {
        "status": "success",
        "company_name": "报告存储测试有限公司",
        "credit_code": "91310000STORETEST01",
        "reg_status": "存续",
        "dimensions": dimensions,
    }


def _entry(status: str, raw: str | None, message: str | None = None) -> dict[str, object]:
    return {"status": status, "raw": raw, "message": message}


def _report(
    moment: datetime,
    *,
    code: str = _CODE,
    summary: str = "历史欠税公告、开庭公告命中",
    statuses: dict[str, str] | None = None,
) -> TycRiskReport:
    return build_risk_report(
        _contract(summary, statuses), supplier_code=code, generated_at=moment
    )


def _rows(session: Session, code: str = _CODE) -> list[RawSignal]:
    return list(
        session.scalars(
            select(RawSignal)
            .where(RawSignal.external_id.like(f"tyc-{code}-%"))
            .order_by(RawSignal.collected_at)
        )
    )


@pytest.fixture
def committed_until_superseded() -> Generator[None]:
    """真提交 until_superseded 策略（跨周替代依赖信源策略），用例后还原。"""
    with SessionLocal() as setup:
        source = setup.scalar(select(DataSource).where(DataSource.code == "tianyancha"))
        assert source is not None
        original = source.validity_policy
        source.validity_policy = dict(_STORAGE_POLICY)
        setup.commit()
    try:
        yield
    finally:
        with SessionLocal() as restore:
            source = restore.scalar(
                select(DataSource).where(DataSource.code == "tianyancha")
            )
            assert source is not None
            source.validity_policy = original
            restore.commit()


# ---------------------------------------------------------------------------
# 存储层：同周幂等 / 同周字段变化契约 / 跨周替代 / 指纹 allowlist / 空报告
# ---------------------------------------------------------------------------


def test_same_week_same_fields_is_idempotent(db_session: Session) -> None:
    source = _fresh_source(db_session)
    source.validity_policy = dict(_STORAGE_POLICY)
    supplier = _supplier(db_session)
    first = _report(_WEEK_1)
    second = _report(_WEEK_1 + timedelta(hours=3))

    write_a = store_tyc_report_signal(db_session, supplier=supplier, report=first)
    write_b = store_tyc_report_signal(db_session, supplier=supplier, report=second)

    assert write_a == TycReportWrite(
        external_id=f"tyc-{_CODE}-{_PERIOD_1}", outcome="created"
    )
    assert write_b == TycReportWrite(
        external_id=f"tyc-{_CODE}-{_PERIOD_1}", outcome="duplicate"
    )
    assert report_fingerprint(first) == report_fingerprint(second)
    rows = _rows(db_session)
    assert len(rows) == 1
    signal = rows[0]
    assert signal.fingerprint == report_fingerprint(first)
    assert signal.title == f"天眼查多维度核查：{supplier.legal_name}"
    assert signal.content == render_key_summary(first)
    assert signal.raw_data == first.model_dump(mode="json")
    assert signal.validity_key == f"tyc:{_CODE}"
    assert signal.lifecycle_action == "assert"
    assert signal.validity_state == "active"
    assert signal.validity_mode == "until_superseded"
    assert signal.valid_from == _WEEK_1
    assert signal.validity_reason["code"] == "anchor_fallback"
    assert signal.validity_policy_version is not None


def test_same_week_changed_fields_keeps_first_report(db_session: Session) -> None:
    """同周首次报告保持幂等：字段变化的再次写入不落库、不新增、不替代。"""
    source = _fresh_source(db_session)
    source.validity_policy = dict(_STORAGE_POLICY)
    supplier = _supplier(db_session)
    first = _report(_WEEK_1, summary="首周命中 A")
    changed = _report(_WEEK_1 + timedelta(hours=2), summary="同周更新 B")
    assert report_fingerprint(first) != report_fingerprint(changed)

    store_tyc_report_signal(db_session, supplier=supplier, report=first)
    write = store_tyc_report_signal(db_session, supplier=supplier, report=changed)

    assert write.outcome == "duplicate"
    rows = _rows(db_session)
    assert len(rows) == 1
    assert rows[0].raw_data == first.model_dump(mode="json")
    assert rows[0].content == render_key_summary(first)


def test_persisted_report_excludes_raw_payload_and_sensitive_fields(
    db_session: Session,
) -> None:
    """落库只保留归一化报告：无原始 Markdown 全文、无白名单外维度、无源密钥、
    无自然人电话/证件号、证据有界。"""
    source = _fresh_source(db_session)
    source.validity_policy = dict(_STORAGE_POLICY)
    source.credential_ref = "env:TYC_TEST_SECRET_SENTINEL"
    source.login_config = {
        **source.login_config,
        "secret_marker": "TYC_LOGIN_CONFIG_SENTINEL",
    }
    supplier = _supplier(db_session)
    phone = "13800138000"
    id_number = "110101199001011234"
    huge = "Z" * 5000
    raw_markdown = "\n".join(
        [
            "# 风险总览：最小化持久化测试有限公司",
            "",
            "- tool: `get_risk_overview`",
            "",
            _RAW_SENTINEL,
            "",
            "| 风险类型 | 风险等级 | 风险数量 |",
            "|---|---|---|",
            "| 开庭公告 | 警示 | 1 |",
            "",
            "> 摘要：命中欠税与开庭公告",
            "",
            f"- 联系电话 {phone}",
            f"- 证件号 {id_number}",
            f"- 备注 {huge}",
        ]
    )
    contract = {
        "status": "success",
        "company_name": "最小化持久化测试有限公司",
        "credit_code": "91310000MINIMIZE01",
        "reg_status": "存续",
        "dimensions": {
            "get_risk_overview": _entry("success", raw_markdown),
            # 非白名单维度：即使调用方误传也不得进入落库报告。
            "get_recruitment_info": _entry(
                "success", "# 招聘信息：最小化持久化测试有限公司\n\n| # | 标题 |\n|---|---|\n| 1 | 仓库主管 |"
            ),
        },
    }
    report = build_risk_report(contract, supplier_code=_CODE, generated_at=_WEEK_1)

    store_tyc_report_signal(db_session, supplier=supplier, report=report)
    row = _rows(db_session)[0]
    payload = json.dumps(row.raw_data, ensure_ascii=False)

    assert set(row.raw_data) == _ALLOWLIST_KEYS
    assert "TYC_TEST_SECRET_SENTINEL" not in payload
    assert "TYC_LOGIN_CONFIG_SENTINEL" not in payload
    for banned in (
        _RAW_SENTINEL,
        "招聘",
        "get_recruitment_info",
        phone,
        id_number,
        huge[:50],
    ):
        assert banned not in payload
    assert "[已脱敏]" in payload
    for dimension in row.raw_data["dimensions"]:
        assert len(dimension["evidence_refs"]) <= 5
        assert all(len(item) <= 41 for item in dimension["evidence_refs"])


def test_cross_week_new_signal_supersedes_previous_active(db_session: Session) -> None:
    source = _fresh_source(db_session)
    source.validity_policy = dict(_STORAGE_POLICY)
    supplier = _supplier(db_session)

    first = store_tyc_report_signal(
        db_session, supplier=supplier, report=_report(_WEEK_1)
    )
    second = store_tyc_report_signal(
        db_session, supplier=supplier, report=_report(_WEEK_2)
    )

    assert first.outcome == "created" and second.outcome == "created"
    assert first.external_id == f"tyc-{_CODE}-{_PERIOD_1}"
    assert second.external_id == f"tyc-{_CODE}-{_PERIOD_2}"
    rows = _rows(db_session)
    assert [row.external_id for row in rows] == [first.external_id, second.external_id]
    old, new = rows
    assert old.validity_state == "superseded"
    assert old.valid_until == old.valid_from
    assert old.validity_reason["code"] == "superseded_by_newer_version"
    assert (
        old.validity_reason["details"]["superseding_external_id"] == second.external_id
    )
    assert new.validity_state == "active"
    assert new.validity_mode == "until_superseded"
    assert old.validity_key == new.validity_key == f"tyc:{_CODE}"
    active = list(
        db_session.scalars(
            select(RawSignal).where(
                RawSignal.validity_key == f"tyc:{_CODE}",
                RawSignal.validity_state == "active",
            )
        )
    )
    assert len(active) == 1


def test_report_fingerprint_uses_explicit_fixed_field_allowlist() -> None:
    report = _report(_WEEK_1)
    expected_payload = {
        "report_kind": report.report_kind,
        "company_name": report.company_name,
        "supplier_code": report.supplier_code,
        "credit_code": report.credit_code,
        "reg_status": report.reg_status,
        "period_key": report.period_key,
        "dimensions": [
            {
                "key": finding.key,
                "name": finding.name,
                "status": finding.status,
                "risk_level": finding.risk_level,
                "hit": finding.hit,
                "summary": finding.summary,
                "evidence_refs": finding.evidence_refs,
                "raw_ref": finding.raw_ref,
            }
            for finding in report.dimensions
        ],
    }
    canonical = json.dumps(
        expected_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    expected = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    assert report_fingerprint(report) == expected

    later = report.model_copy(update={"generated_at": _WEEK_1 + timedelta(hours=5)})
    assert report_fingerprint(later) == report_fingerprint(report)
    changed = report.model_copy(
        update={
            "dimensions": [
                report.dimensions[0].model_copy(update={"summary": "指纹应变化"})
            ]
        }
    )
    assert report_fingerprint(changed) != report_fingerprint(report)


def test_empty_report_is_not_persisted(db_session: Session) -> None:
    source = _fresh_source(db_session)
    source.validity_policy = dict(_STORAGE_POLICY)
    supplier = _supplier(db_session)
    empty = build_risk_report(
        {"status": "success", "company_name": "报告存储测试有限公司", "dimensions": {}},
        supplier_code=_CODE,
        generated_at=_WEEK_1,
    )

    write = store_tyc_report_signal(db_session, supplier=supplier, report=empty)

    assert write == TycReportWrite(
        external_id=f"tyc-{_CODE}-{_PERIOD_1}", outcome="empty"
    )
    assert _rows(db_session) == []


def test_report_with_only_failed_dimensions_is_still_stored(
    db_session: Session,
) -> None:
    """维度条目全部失败属于「非空报告」：如实记录失败状态，照常落库。"""
    source = _fresh_source(db_session)
    source.validity_policy = dict(_STORAGE_POLICY)
    supplier = _supplier(db_session)
    failed = _report(
        _WEEK_1,
        statuses={"get_risk_overview": "error", "get_judicial_case": "quota_exhausted"},
    )

    write = store_tyc_report_signal(db_session, supplier=supplier, report=failed)

    assert write.outcome == "created"
    rows = _rows(db_session)
    assert len(rows) == 1
    statuses = [dimension["status"] for dimension in rows[0].raw_data["dimensions"]]
    assert statuses == ["error", "quota_exhausted"]


def test_blank_supplier_code_cannot_reach_storage() -> None:
    with pytest.raises(ValueError):
        build_risk_report(_contract(), supplier_code="   ")


# ---------------------------------------------------------------------------
# 批次路径：同周 duplicate / 跨周 created + supersede / 空报告计 empty
# ---------------------------------------------------------------------------


def _pin_report_time(monkeypatch: MonkeyPatch, moments: list[datetime]) -> None:
    real_build = tyc_batch_supplier_module.build_risk_report
    remaining = list(moments)

    def _build(results: dict[str, object], *, supplier_code: str) -> TycRiskReport:
        return real_build(
            results, supplier_code=supplier_code, generated_at=remaining.pop(0)
        )

    monkeypatch.setattr(tyc_batch_supplier_module, "build_risk_report", _build)


class _ZeroDimensionGateway:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def fetch_dimensions(self, company_name: str) -> dict[str, object]:
        self.calls.append(company_name)
        return {
            "status": "success",
            "company_name": company_name,
            "credit_code": None,
            "reg_status": None,
            "dimensions": {},
        }


def _batch_setup(
    db_session: Session, monkeypatch: MonkeyPatch, *, code: str
) -> tuple[DataSource, Supplier]:
    configure_committed_tyc(daily_limit=100, monthly_limit=1000, dimensions=_DIMS)
    source = _fresh_source(db_session)
    supplier = _supplier(db_session, code)
    stub = MultidimMcpStub()
    monkeypatch.setattr(
        tyc_gateway_module,
        "build_tyc_gateway",
        lambda **kwargs: stub.gateway(_DIMS),
    )
    return source, supplier


def test_batch_cross_week_creates_new_signal_and_supersedes_old(
    db_session: Session,
    committed_tyc_env: None,
    committed_until_superseded: None,
    monkeypatch: MonkeyPatch,
) -> None:
    source, supplier = _batch_setup(db_session, monkeypatch, code="SUP-STORE-BW")
    _pin_report_time(monkeypatch, [_WEEK_1, _WEEK_2])

    first = run_tyc_supplier(db_session, source, supplier_id=supplier.id)
    second = run_tyc_supplier(db_session, source, supplier_id=supplier.id)

    assert (first.created_count, first.duplicate_count) == (1, 0)
    assert (second.created_count, second.duplicate_count) == (1, 0)
    rows = _rows(db_session, "SUP-STORE-BW")
    assert [row.validity_state for row in rows] == ["superseded", "active"]
    assert rows[0].external_id.endswith("2026-W19")
    assert rows[1].external_id.endswith("2026-W20")


def test_batch_same_week_rerun_is_duplicate_with_same_external_id(
    db_session: Session,
    committed_tyc_env: None,
    committed_until_superseded: None,
    monkeypatch: MonkeyPatch,
) -> None:
    source, supplier = _batch_setup(db_session, monkeypatch, code="SUP-STORE-BD")
    _pin_report_time(monkeypatch, [_WEEK_1, _WEEK_1 + timedelta(hours=4)])

    first = run_tyc_supplier(db_session, source, supplier_id=supplier.id)
    second = run_tyc_supplier(db_session, source, supplier_id=supplier.id)

    assert (first.created_count, first.duplicate_count) == (1, 0)
    assert (second.created_count, second.duplicate_count) == (0, 1)
    assert second.attempted_count == 1
    rows = _rows(db_session, "SUP-STORE-BD")
    assert len(rows) == 1
    assert rows[0].external_id.endswith("2026-W19")


def test_batch_empty_report_counts_empty_without_persisting(
    db_session: Session,
    committed_tyc_env: None,
    committed_until_superseded: None,
    monkeypatch: MonkeyPatch,
) -> None:
    configure_committed_tyc(daily_limit=100, monthly_limit=1000, dimensions=_DIMS)
    source = _fresh_source(db_session)
    supplier = _supplier(db_session, "SUP-STORE-BE")
    gateway = _ZeroDimensionGateway()
    monkeypatch.setattr(tyc_gateway_module, "build_tyc_gateway", lambda **kwargs: gateway)

    result = run_tyc_supplier(db_session, source, supplier_id=supplier.id)

    assert (result.attempted_count, result.empty_count) == (1, 1)
    assert (result.created_count, result.failed_count) == (0, 0)
    assert _rows(db_session, "SUP-STORE-BE") == []


# ---------------------------------------------------------------------------
# 并发：同周不同字段并发写入只允许一个 champion，不产生重复 external_id
# ---------------------------------------------------------------------------


def test_concurrent_same_week_writes_leave_single_active_row(
    committed_until_superseded: None,
) -> None:
    code = f"SUP-STORE-CONC-{uuid4().hex[:8]}"
    with SessionLocal.begin() as setup:
        setup.add(
            Supplier(
                supplier_code=code,
                legal_name=f"{code} 有限公司",
                country_code="CN",
                enabled=True,
            )
        )
    barriers = Barrier(2)
    moments = [_WEEK_1, _WEEK_1 + timedelta(hours=1)]

    def _submit(summary: str, moment: datetime) -> TycReportWrite:
        with SessionLocal.begin() as session:
            supplier = session.scalar(
                select(Supplier).where(Supplier.supplier_code == code)
            )
            assert supplier is not None
            report = _report(moment, code=code, summary=summary)
            barriers.wait(timeout=10)
            return store_tyc_report_signal(session, supplier=supplier, report=report)

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(_submit, "并发写入 A", moments[0]),
                pool.submit(_submit, "并发写入 B", moments[1]),
            ]
            outcomes = sorted(future.result(timeout=15).outcome for future in futures)

        assert outcomes == ["created", "duplicate"]
        with SessionLocal() as probe:
            rows = list(
                probe.scalars(
                    select(RawSignal).where(RawSignal.external_id.like(f"tyc-{code}-%"))
                )
            )
        assert len(rows) == 1
        assert rows[0].validity_state == "active"
        assert rows[0].validity_key == f"tyc:{code}"
    finally:
        with SessionLocal.begin() as cleanup:
            cleanup.execute(
                delete(RawSignal).where(RawSignal.external_id.like(f"tyc-{code}-%"))
            )
            cleanup.execute(delete(Supplier).where(Supplier.supplier_code == code))
