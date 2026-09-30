"""Todo 7 天眼查多维度批量核查服务测试（独立文件，避免与并行任务共享写点）。

覆盖计划 D8/D9 与 Todo 7 验收：
- 固定 2 片 SHA-256 分片（逐字公式，禁用 Python ``hash()``）：两桶无交集且并集=全集；
- 真实桶门禁：启用供应商全集 × 当前维度数 C，``max_bucket × C ≤ daily_limit``；
- 批次互斥：``engine.connect()`` 专用连接 session 级 advisory lock，未获锁零调用退出，
  异常路径 finally 释放，并发只允许一个持有；
- 每供应商经 ``gateway.fetch_dimensions``（内部逐工具执行器）→ ``build_risk_report``
  → ``store_tyc_report_signal`` 落「摘要 + 报告 JSON」（until_superseded 周内幂等）；
- 逐工具计数 success_with_records/empty/error/quota_exhausted/busy；
- 手动单供应商路径按 C 受额度约束但不套全量桶门禁。

执行环境：Compose 隔离测试栈，真实 PostgreSQL；MockTransport 确定性 stub，
不发起真实网络调用。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import pytest
from pytest import MonkeyPatch
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session
from tyc_batch_support import (
    CompanyStub,
    MultidimMcpStub,
    committed_tyc_daily_used,
    committed_tyc_rows,
    configure_committed_tyc,
    truncate_committed_tyc_usage,
)

import app.agent.tyc_batch_supplier as tyc_batch_supplier_module
import app.agent.tyc_gateway as tyc_gateway_module
from app.agent.models import TycUsageRecord
from app.agent.tyc_batch import (
    SHARD_COUNT,
    TycBatchBucketGateRejected,
    TycBatchLocked,
    TycBatchNotTianyancha,
    TycBatchShardError,
    TycBatchSourceInactive,
    TycBatchSupplierNotFound,
    TycBatchUnavailable,
    bucket_index,
    run_tyc_batch,
    run_tyc_supplier,
)
from app.agent.tyc_batch_lock import BATCH_ADVISORY_LOCK_KEY
from app.agent.tyc_quota import QUOTA_ADVISORY_LOCK_KEY
from app.agent.tyc_report import TycRiskReport
from app.agent.tyc_report_storage import TycReportWrite
from app.database import engine
from app.signals.models import DataSource, RawSignal
from app.suppliers.models import Supplier

_TWO_DIMS = ["get_risk_overview", "get_judicial_case"]
_FIVE_DIMS = [
    "get_risk_overview",
    "get_judicial_case",
    "get_default_event_info",
    "get_hearing_notice",
    "get_court_notice",
]


# ---------------------------------------------------------------------------
# 局部辅助：字面量公式、供应商构造、契约工厂
# ---------------------------------------------------------------------------


def _literal_bucket(code: str, shard_count: int = SHARD_COUNT) -> int:
    """计划 D8 公式的独立字面实现（测试不调用生产函数）。"""
    return int(hashlib.sha256(code.encode()).hexdigest(), 16) % shard_count


def _codes_for_buckets(target: dict[int, int], *, prefix: str = "SUP-MD-GATE") -> list[str]:
    """生成使各桶计数精确等于 target 的 supplier_code（跳过不需要的桶）。"""
    counts = dict.fromkeys(target, 0)
    codes: list[str] = []
    candidate = 0
    while any(counts[bucket] < need for bucket, need in target.items()):
        code = f"{prefix}-{candidate:05d}"
        candidate += 1
        bucket = _literal_bucket(code)
        if bucket in counts and counts[bucket] < target[bucket]:
            counts[bucket] += 1
            codes.append(code)
    return codes


def _fresh_source(session: Session) -> DataSource:
    """过期身份映射后重读天眼查源（提交态配置对调用方会话可见）。"""
    session.expire_all()
    source = session.scalar(select(DataSource).where(DataSource.code == "tianyancha"))
    assert source is not None, "迁移应已注册 tianyancha 信息源"
    return source


def _add_suppliers(session: Session, codes: list[str]) -> list[Supplier]:
    suppliers = [
        Supplier(
            supplier_code=code,
            legal_name=f"{code} 有限公司",
            country_code="CN",
            enabled=True,
        )
        for code in codes
    ]
    session.add_all(suppliers)
    session.flush()
    return suppliers


def _commit_usage(company_name: str, *, status: str = "success") -> None:
    """独立连接真提交一条消费（模拟外部/实时路径已消耗额度）。"""
    with Session(engine) as external:
        external.add(
            TycUsageRecord(
                tool_name="verify_company", company_name=company_name, status=status
            )
        )
        external.commit()


def _dim(status: str) -> dict[str, object]:
    return {"status": status, "raw": None, "message": None}


def _success_contract(
    company: str,
    dims: list[str],
    *,
    statuses: dict[str, str] | None = None,
) -> dict[str, object]:
    statuses = statuses or {}
    return {
        "status": "success",
        "company_name": company,
        "credit_code": "91310000MDTEST0001",
        "reg_status": "存续",
        "dimensions": {tool: _dim(statuses.get(tool, "success")) for tool in dims},
    }


def _empty_anchor(company: str) -> dict[str, object]:
    return {
        "status": "empty",
        "company_name": company,
        "credit_code": None,
        "reg_status": None,
        "dimensions": {},
    }


class ScriptedGateway:
    """按公司名返回固定 fetch 合同；不走额度执行器（映射/分片/锁语义测试用）。"""

    def __init__(self, factory: Callable[[str], dict[str, object]]) -> None:
        self.factory = factory
        self.calls: list[str] = []

    async def fetch_dimensions(self, company_name: str) -> dict[str, object]:
        self.calls.append(company_name)
        return self.factory(company_name)


class _BlockingGateway:
    """首个 fetch 调用阻塞直到释放：用于并发锁竞争。"""

    def __init__(self) -> None:
        self.entered = Event()
        self.release = Event()
        self.calls: list[str] = []

    async def fetch_dimensions(self, company_name: str) -> dict[str, object]:
        self.calls.append(company_name)
        self.entered.set()
        assert self.release.wait(timeout=10), "锁竞争测试未在超时内释放"
        return _empty_anchor(company_name)


class _ExplodingGateway:
    async def fetch_dimensions(self, company_name: str) -> dict[str, object]:
        raise KeyboardInterrupt(f"fatal:{company_name}")


def _patch_gateway(monkeypatch: MonkeyPatch, gateway: object) -> None:
    monkeypatch.setattr(
        tyc_gateway_module, "build_tyc_gateway", lambda **kwargs: gateway
    )


# ---------------------------------------------------------------------------
# 分片：SHA-256 字面公式 / 两条桶无交集且并集为全集 / 固定 shard_count
# ---------------------------------------------------------------------------


def test_bucket_index_matches_literal_sha256_formula() -> None:
    """计划 D8 逐字公式；固定 2 片且结果恒在 [0, 2)。"""
    assert SHARD_COUNT == 2
    for code in ("SUP-001", "供应商-甲", "a" * 64, "ミラー株式会社"):
        assert bucket_index(code) == _literal_bucket(code)
        assert 0 <= bucket_index(code) < SHARD_COUNT


def test_run_tyc_batch_requires_explicit_shard_index(
    db_session: Session, committed_tyc_env: None
) -> None:
    """定时路径不得靠默认分片掩盖调用者遗漏：缺少 shard_index 即类型错误。"""
    _fresh_source(db_session)
    configure_committed_tyc(daily_limit=100, monthly_limit=1000)
    with pytest.raises(TypeError):
        run_tyc_batch(db_session, _fresh_source(db_session))  # type: ignore[call-arg]


def test_run_tyc_batch_rejects_invalid_shard_configuration(
    db_session: Session, committed_tyc_env: None, monkeypatch: MonkeyPatch
) -> None:
    """非法分片/非法 shard_count 结构化拒绝，零远程调用。"""
    configure_committed_tyc(daily_limit=100, monthly_limit=1000, dimensions=_TWO_DIMS)
    source = _fresh_source(db_session)
    gateway = ScriptedGateway(_empty_anchor)
    _patch_gateway(monkeypatch, gateway)

    for shard_index, shard_count in ((2, 2), (-1, 2), (0, 3), (0, 1)):
        with pytest.raises(TycBatchShardError):
            run_tyc_batch(
                db_session, source, shard_index=shard_index, shard_count=shard_count
            )

    assert gateway.calls == []
    assert committed_tyc_rows() == []


def test_shards_are_disjoint_and_cover_all_enabled_suppliers(
    db_session: Session, committed_tyc_env: None, monkeypatch: MonkeyPatch
) -> None:
    """两个哈希桶两次子集无交集且并集=启用供应商全集。"""
    configure_committed_tyc(daily_limit=1000, monthly_limit=10000, dimensions=_TWO_DIMS)
    source = _fresh_source(db_session)
    suppliers = _add_suppliers(db_session, [f"SUP-MD-SHARD-{index:03d}" for index in range(40)])
    gateway = ScriptedGateway(_empty_anchor)
    _patch_gateway(monkeypatch, gateway)

    first = run_tyc_batch(db_session, source, shard_index=0)
    calls0 = list(gateway.calls)
    gateway.calls.clear()
    second = run_tyc_batch(db_session, source, shard_index=1)
    calls1 = list(gateway.calls)

    expected0 = {
        supplier.legal_name
        for supplier in suppliers
        if _literal_bucket(supplier.supplier_code) == 0
    }
    expected1 = {
        supplier.legal_name
        for supplier in suppliers
        if _literal_bucket(supplier.supplier_code) == 1
    }
    assert set(calls0) == expected0
    assert set(calls1) == expected1
    assert expected0 & expected1 == set()
    assert expected0 | expected1 == {supplier.legal_name for supplier in suppliers}
    assert first.shard_index == 0 and first.shard_count == SHARD_COUNT
    assert second.shard_index == 1 and second.shard_count == SHARD_COUNT
    assert first.targeted_count == len(expected0)
    assert first.attempted_count == len(expected0)
    assert first.empty_count == len(expected0)
    assert second.empty_count == len(expected1)
    assert first.supplier_id is None


# ---------------------------------------------------------------------------
# 真实桶门禁：启用全集 × C ≤ daily_limit，否则零调用结构化拒绝
# ---------------------------------------------------------------------------


def test_bucket_gate_rejects_when_max_bucket_exceeds_c13_limit(
    db_session: Session, committed_tyc_env: None, monkeypatch: MonkeyPatch
) -> None:
    """默认 C=13：某桶 77 家 → 77×13=1001>1000，拒绝且零调用。"""
    codes = _codes_for_buckets({0: 77})
    _add_suppliers(db_session, codes)
    configure_committed_tyc(daily_limit=1000, monthly_limit=10000)
    source = _fresh_source(db_session)
    gateway = ScriptedGateway(_empty_anchor)
    _patch_gateway(monkeypatch, gateway)

    with pytest.raises(TycBatchBucketGateRejected) as exc_info:
        run_tyc_batch(db_session, source, shard_index=0)

    context = exc_info.value.context
    assert context["actual_max_bucket"] == 77
    assert context["calls_per_supplier"] == 13
    assert context["daily_limit"] == 1000
    assert gateway.calls == []
    assert committed_tyc_rows() == []


def test_bucket_gate_threshold_is_exact_at_daily_limit_500(
    db_session: Session, committed_tyc_env: None, monkeypatch: MonkeyPatch
) -> None:
    """daily_limit=500、C=13：max_bucket=38 → 494≤500 放行；追加到 39 → 507>500 才拒绝。"""
    codes = _codes_for_buckets({0: 38, 1: 38})
    _add_suppliers(db_session, codes)
    configure_committed_tyc(daily_limit=500, monthly_limit=5000)
    source = _fresh_source(db_session)
    gateway = ScriptedGateway(_empty_anchor)
    _patch_gateway(monkeypatch, gateway)

    allowed = run_tyc_batch(db_session, source, shard_index=0)
    assert allowed.targeted_count == 38
    assert allowed.empty_count == 38

    extra_code = next(
        code
        for code in (f"SUP-MD-EXTRA-{i:05d}" for i in range(1000))
        if _literal_bucket(code) == 0
    )
    _add_suppliers(db_session, [extra_code])
    gateway.calls.clear()

    with pytest.raises(TycBatchBucketGateRejected) as exc_info:
        run_tyc_batch(db_session, source, shard_index=0)

    assert exc_info.value.context["actual_max_bucket"] == 39
    assert gateway.calls == []


def test_bucket_gate_allows_default_135_suppliers(
    db_session: Session, committed_tyc_env: None, monkeypatch: MonkeyPatch
) -> None:
    """135 家确定性 supplier_code、默认 C=13、daily_limit=1000：真实桶在门禁内。"""
    codes = [f"SUP-MD-135-{index:03d}" for index in range(135)]
    _add_suppliers(db_session, codes)
    counts = [0] * SHARD_COUNT
    for code in codes:
        counts[_literal_bucket(code)] += 1
    assert max(counts) * 13 <= 1000

    configure_committed_tyc(daily_limit=1000, monthly_limit=10000)
    source = _fresh_source(db_session)
    gateway = ScriptedGateway(_empty_anchor)
    _patch_gateway(monkeypatch, gateway)

    result = run_tyc_batch(db_session, source, shard_index=0)

    assert result.targeted_count == counts[0]
    assert result.empty_count == counts[0]
    assert result.quota_exhausted is False


# ---------------------------------------------------------------------------
# 批次互斥：专用连接 session 级 advisory lock
# ---------------------------------------------------------------------------


def test_batch_lock_key_is_distinct_from_quota_lock() -> None:
    assert BATCH_ADVISORY_LOCK_KEY != QUOTA_ADVISORY_LOCK_KEY


def test_lock_busy_second_run_exits_before_any_call(
    db_session: Session, committed_tyc_env: None, monkeypatch: MonkeyPatch
) -> None:
    """持锁时第二次调用在任何 gateway/MCP 调用前退出（零记账、零信号）。"""
    configure_committed_tyc(daily_limit=100, monthly_limit=1000, dimensions=_TWO_DIMS)
    source = _fresh_source(db_session)
    codes = _codes_for_buckets({0: 1})
    _add_suppliers(db_session, codes)
    gateway = ScriptedGateway(_empty_anchor)
    _patch_gateway(monkeypatch, gateway)

    holder = engine.connect()
    try:
        acquired = holder.scalar(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": BATCH_ADVISORY_LOCK_KEY}
        )
        assert acquired is True
        with pytest.raises(TycBatchLocked):
            run_tyc_batch(db_session, source, shard_index=0)
    finally:
        holder.scalar(
            text("SELECT pg_advisory_unlock(:key)"), {"key": BATCH_ADVISORY_LOCK_KEY}
        )
        holder.close()

    assert gateway.calls == []
    assert committed_tyc_rows() == []
    assert db_session.scalar(select(RawSignal)) is None


def test_concurrent_runs_only_one_acquires_batch_lock(
    db_session: Session, committed_tyc_env: None, monkeypatch: MonkeyPatch
) -> None:
    """两个独立 Session 并发：恰好一个持锁执行，另一个结构化拒绝（重复 3 轮防 flake）。"""
    configure_committed_tyc(daily_limit=1000, monthly_limit=10000, dimensions=_TWO_DIMS)
    # 提交态供应商必须跨连接可见；code 用随机后缀避免与残留唯一键冲突。
    code = f"SUP-MD-CONC-{uuid4().hex[:10]}"
    name = f"{code} 有限公司"
    with Session(engine) as seed:
        seed.add(
            Supplier(
                supplier_code=code,
                legal_name=name,
                country_code="CN",
                enabled=True,
            )
        )
        seed.commit()
    with Session(engine) as probe:
        source_id = _fresh_source(probe).id
    bucket = _literal_bucket(code)

    try:
        for _round in range(3):
            gateway = _BlockingGateway()
            _patch_gateway(monkeypatch, gateway)

            def _run_holder() -> object:
                with Session(engine) as session:
                    return run_tyc_batch(
                        session, session.get(DataSource, source_id), shard_index=bucket
                    )

            with ThreadPoolExecutor(max_workers=1) as pool:
                holder_future = pool.submit(_run_holder)
                assert gateway.entered.wait(timeout=5), "持锁方未进入远程调用"
                calls_before_contender = len(gateway.calls)
                with Session(engine) as contender:
                    with pytest.raises(TycBatchLocked):
                        run_tyc_batch(
                            contender,
                            contender.get(DataSource, source_id),
                            shard_index=bucket,
                        )
                # 未获锁方在任何 gateway/MCP 调用前退出。
                assert len(gateway.calls) == calls_before_contender
                gateway.release.set()
                holder_result = holder_future.result(timeout=15)

            assert holder_result.attempted_count >= 1  # type: ignore[attr-defined]
            assert name in gateway.calls
    finally:
        with Session(engine) as cleanup:
            cleanup.execute(delete(Supplier).where(Supplier.supplier_code == code))
            cleanup.commit()


def test_lock_is_released_when_runner_raises_base_exception(
    db_session: Session, committed_tyc_env: None, monkeypatch: MonkeyPatch
) -> None:
    """异常路径（BaseException）经 finally 释放批次锁，锁可被下一次获取。"""
    configure_committed_tyc(daily_limit=100, monthly_limit=1000, dimensions=_TWO_DIMS)
    source = _fresh_source(db_session)
    _add_suppliers(db_session, _codes_for_buckets({0: 1}))
    _patch_gateway(monkeypatch, _ExplodingGateway())

    with pytest.raises(KeyboardInterrupt):
        run_tyc_batch(db_session, source, shard_index=0)

    probe = engine.connect()
    try:
        reacquired = probe.scalar(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": BATCH_ADVISORY_LOCK_KEY}
        )
        assert reacquired is True
        probe.scalar(
            text("SELECT pg_advisory_unlock(:key)"), {"key": BATCH_ADVISORY_LOCK_KEY}
        )
    finally:
        probe.close()
    assert db_session.scalar(select(RawSignal)) is None


# ---------------------------------------------------------------------------
# 多维度报告落库 + 逐工具计数 + 额度执行器真提交
# ---------------------------------------------------------------------------


def test_multi_dim_success_writes_report_signal_and_per_tool_usage(
    db_session: Session, committed_tyc_env: None, monkeypatch: MonkeyPatch
) -> None:
    """默认 2 维覆盖：锚定+维度逐工具提交；信号只含摘要+报告 JSON（无原始 Markdown）。"""
    configure_committed_tyc(daily_limit=100, monthly_limit=1000, dimensions=_TWO_DIMS)
    source = _fresh_source(db_session)
    code = _codes_for_buckets({0: 1})[0]
    supplier = _add_suppliers(db_session, [code])[0]
    stub = MultidimMcpStub()
    _patch_gateway(monkeypatch, stub.gateway(_TWO_DIMS))

    result = run_tyc_batch(db_session, source, shard_index=0)

    assert result.targeted_count == 1
    assert result.attempted_count == 1
    assert result.created_count == 1
    assert (result.duplicate_count, result.empty_count, result.failed_count) == (0, 0, 0)
    assert result.quota_exhausted is False
    assert result.supplier_id is None
    assert set(result.per_tool_counts) == {"search_companies", *_TWO_DIMS}
    assert all(
        counts.success_with_records == 1
        for counts in result.per_tool_counts.values()
    )
    assert [tuple(row) for row in committed_tyc_rows()] == [
        ("search_companies", supplier.legal_name, "success"),
        (_TWO_DIMS[0], supplier.legal_name, "success"),
        (_TWO_DIMS[1], supplier.legal_name, "success"),
    ]
    assert committed_tyc_daily_used() == 3

    signal = db_session.scalar(
        select(RawSignal).where(RawSignal.external_id.like(f"tyc-{code}-%"))
    )
    assert signal is not None
    assert signal.title == f"天眼查多维度核查：{supplier.legal_name}"
    raw_data = signal.raw_data
    assert raw_data["report_kind"] == "supplier_profile"
    assert raw_data["supplier_code"] == code
    assert [dimension["key"] for dimension in raw_data["dimensions"]] == _TWO_DIMS
    serialized = json.dumps(raw_data, ensure_ascii=False)
    assert '"raw"' not in serialized and "tool:" not in serialized
    assert "重点命中" in signal.content


def test_rerun_same_week_counts_duplicate_without_new_signal(
    db_session: Session, committed_tyc_env: None, monkeypatch: MonkeyPatch
) -> None:
    configure_committed_tyc(daily_limit=100, monthly_limit=1000, dimensions=_TWO_DIMS)
    source = _fresh_source(db_session)
    _add_suppliers(db_session, _codes_for_buckets({0: 1}))
    stub = MultidimMcpStub()
    _patch_gateway(monkeypatch, stub.gateway(_TWO_DIMS))

    first = run_tyc_batch(db_session, source, shard_index=0)
    second = run_tyc_batch(db_session, source, shard_index=0)

    assert (first.created_count, first.duplicate_count) == (1, 0)
    assert (second.created_count, second.duplicate_count) == (0, 1)
    assert second.attempted_count == 1
    assert len(list(db_session.scalars(select(RawSignal)))) == 1


def test_bucket_gate_rejects_when_daily_limit_below_single_supplier_calls(
    db_session: Session, committed_tyc_env: None, monkeypatch: MonkeyPatch
) -> None:
    """C=3、daily_limit=2：即使仅 1 家供应商也无法完成完整核查 → 门禁拒绝、零调用。"""
    configure_committed_tyc(daily_limit=2, monthly_limit=50, dimensions=_TWO_DIMS)
    source = _fresh_source(db_session)
    _add_suppliers(db_session, _codes_for_buckets({0: 1}))
    gateway = ScriptedGateway(lambda company: _success_contract(company, _TWO_DIMS))
    _patch_gateway(monkeypatch, gateway)

    with pytest.raises(TycBatchBucketGateRejected) as exc_info:
        run_tyc_batch(db_session, source, shard_index=0)

    assert exc_info.value.context["actual_max_bucket"] == 1
    assert exc_info.value.context["calls_per_supplier"] == 3
    assert exc_info.value.context["daily_limit"] == 2
    assert gateway.calls == []
    assert committed_tyc_rows() == []


def test_anchor_rejected_by_quota_stops_without_any_attempt(
    db_session: Session, committed_tyc_env: None, monkeypatch: MonkeyPatch
) -> None:
    """锚定即被执行器锁内拒绝（远程未调用）→ attempted_count=0 并停止。"""
    configure_committed_tyc(daily_limit=100, monthly_limit=1000, dimensions=_TWO_DIMS)
    source = _fresh_source(db_session)
    codes = _codes_for_buckets({0: 2})
    suppliers = _add_suppliers(db_session, codes)
    rejected = suppliers[0].legal_name

    def factory(company: str) -> dict[str, object]:
        if company == rejected:
            return {
                "status": "quota_exhausted",
                "company_name": company,
                "credit_code": None,
                "reg_status": None,
                "dimensions": {},
            }
        return _success_contract(company, _TWO_DIMS)

    gateway = ScriptedGateway(factory)
    _patch_gateway(monkeypatch, gateway)

    result = run_tyc_batch(db_session, source, shard_index=0)

    assert result.attempted_count == 0
    assert result.quota_exhausted is True
    assert result.created_count == 0
    assert gateway.calls == [rejected]
    assert committed_tyc_rows() == []


def test_busy_anchor_counts_failed_and_continues(
    db_session: Session, committed_tyc_env: None, monkeypatch: MonkeyPatch
) -> None:
    """锚定额度锁繁忙（远程未调用）→ 计失败并继续后续供应商。"""
    configure_committed_tyc(daily_limit=100, monthly_limit=1000, dimensions=_TWO_DIMS)
    source = _fresh_source(db_session)
    suppliers = _add_suppliers(db_session, _codes_for_buckets({0: 2}))
    busy, ok = suppliers[0].legal_name, suppliers[1].legal_name

    def factory(company: str) -> dict[str, object]:
        if company == busy:
            return {
                "status": "busy",
                "company_name": company,
                "credit_code": None,
                "reg_status": None,
                "dimensions": {},
            }
        return _success_contract(company, _TWO_DIMS)

    gateway = ScriptedGateway(factory)
    _patch_gateway(monkeypatch, gateway)

    result = run_tyc_batch(db_session, source, shard_index=0)

    assert result.attempted_count == 2
    assert result.failed_count == 1
    assert result.created_count == 1
    assert result.quota_exhausted is False
    assert gateway.calls == [busy, ok]


def test_per_tool_counts_cover_all_five_outcomes(
    db_session: Session, committed_tyc_env: None, monkeypatch: MonkeyPatch
) -> None:
    """逐工具计数五态聚合：success_with_records/empty/error/quota_exhausted/busy。"""
    configure_committed_tyc(daily_limit=100, monthly_limit=1000, dimensions=_FIVE_DIMS)
    source = _fresh_source(db_session)
    code = _codes_for_buckets({0: 1})[0]
    _add_suppliers(db_session, [code])
    statuses = dict(
        zip(
            _FIVE_DIMS,
            ["success", "empty", "error", "quota_exhausted", "busy"],
            strict=True,
        )
    )
    gateway = ScriptedGateway(
        lambda company: _success_contract(company, _FIVE_DIMS, statuses=statuses)
    )
    _patch_gateway(monkeypatch, gateway)

    result = run_tyc_batch(db_session, source, shard_index=0)

    assert result.per_tool_counts["search_companies"].success_with_records == 1
    assert result.per_tool_counts[_FIVE_DIMS[0]].success_with_records == 1
    assert result.per_tool_counts[_FIVE_DIMS[1]].empty == 1
    assert result.per_tool_counts[_FIVE_DIMS[2]].error == 1
    assert result.per_tool_counts[_FIVE_DIMS[3]].quota_exhausted == 1
    assert result.per_tool_counts[_FIVE_DIMS[4]].busy == 1


def test_dimension_remote_error_is_isolated_and_supplier_still_created(
    db_session: Session, committed_tyc_env: None, monkeypatch: MonkeyPatch
) -> None:
    """单个维度 isError 不阻断供应商报告：error 计 0 费，其余仍入报告与计数。"""
    configure_committed_tyc(daily_limit=100, monthly_limit=1000, dimensions=_TWO_DIMS)
    source = _fresh_source(db_session)
    code = _codes_for_buckets({0: 1})[0]
    name = f"{code} 有限公司"
    _add_suppliers(db_session, [code])
    stub = MultidimMcpStub(
        per_company={name: CompanyStub(error_tools=frozenset({_TWO_DIMS[1]}))}
    )
    _patch_gateway(monkeypatch, stub.gateway(_TWO_DIMS))

    result = run_tyc_batch(db_session, source, shard_index=0)

    assert result.created_count == 1
    assert result.failed_count == 0
    assert result.per_tool_counts[_TWO_DIMS[1]].error == 1
    assert committed_tyc_rows() == [
        ("search_companies", name, "success"),
        (_TWO_DIMS[0], name, "success"),
        (_TWO_DIMS[1], name, "error"),
    ]
    assert committed_tyc_daily_used() == 2


def test_signal_persistence_failure_is_isolated(
    db_session: Session, committed_tyc_env: None, monkeypatch: MonkeyPatch
) -> None:
    """单供应商信号入库失败被隔离：计 failed、其余继续、执行器记账不丢。"""
    from app.signals.ingestion import SignalIngestionError

    configure_committed_tyc(daily_limit=100, monthly_limit=1000, dimensions=_TWO_DIMS)
    source = _fresh_source(db_session)
    suppliers = _add_suppliers(db_session, _codes_for_buckets({0: 2}))
    failing_code = suppliers[0].supplier_code
    stub = MultidimMcpStub()
    _patch_gateway(monkeypatch, stub.gateway(_TWO_DIMS))

    real_store = tyc_batch_supplier_module.store_tyc_report_signal

    def _failing_store(
        session: Session,
        *,
        supplier: Supplier,
        report: TycRiskReport,
    ) -> TycReportWrite:
        if supplier.supplier_code == failing_code:
            raise SignalIngestionError("validity_conflict")
        return real_store(session, supplier=supplier, report=report)

    monkeypatch.setattr(
        tyc_batch_supplier_module, "store_tyc_report_signal", _failing_store
    )

    result = run_tyc_batch(db_session, source, shard_index=0)

    assert result.attempted_count == 2
    assert result.created_count == 1
    assert result.failed_count == 1
    assert len(committed_tyc_rows()) == 6  # 两家 ×（锚定+2 维）均真提交
    signals = list(db_session.scalars(select(RawSignal)))
    assert len(signals) == 1
    assert suppliers[1].supplier_code in signals[0].external_id


def test_external_consumption_is_seen_by_executor_between_calls(
    db_session: Session, committed_tyc_env: None, monkeypatch: MonkeyPatch
) -> None:
    """stale_state：外部已提交 1 次消费 → 执行器锁内重读，后续维度被拦截。"""
    configure_committed_tyc(daily_limit=3, monthly_limit=50, dimensions=_TWO_DIMS)
    source = _fresh_source(db_session)
    code = _codes_for_buckets({0: 1})[0]
    name = f"{code} 有限公司"
    _add_suppliers(db_session, [code])
    _commit_usage("外部消费")
    stub = MultidimMcpStub()
    _patch_gateway(monkeypatch, stub.gateway(_TWO_DIMS))

    result = run_tyc_batch(db_session, source, shard_index=0)

    assert result.quota_exhausted is True
    assert result.attempted_count == 1
    assert result.created_count == 1  # 部分报告：已执行维度仍落库，不丢弃已付额度
    assert result.per_tool_counts[_TWO_DIMS[1]].quota_exhausted == 1
    assert stub.calls == [(name, "search_companies"), (name, _TWO_DIMS[0])]
    assert committed_tyc_rows() == [
        ("verify_company", "外部消费", "success"),
        ("search_companies", name, "success"),
        (_TWO_DIMS[0], name, "success"),
    ]


def test_anchor_error_is_isolated_and_batch_continues(
    db_session: Session, committed_tyc_env: None, monkeypatch: MonkeyPatch
) -> None:
    """第 1 家锚定远程失败 → 计 failed；第 2 家继续成功。"""
    configure_committed_tyc(daily_limit=100, monthly_limit=1000, dimensions=_TWO_DIMS)
    source = _fresh_source(db_session)
    suppliers = _add_suppliers(db_session, _codes_for_buckets({0: 2}))
    failed_name = suppliers[0].legal_name
    stub = MultidimMcpStub(
        per_company={failed_name: CompanyStub(anchor_error=True)}
    )
    _patch_gateway(monkeypatch, stub.gateway(_TWO_DIMS))

    result = run_tyc_batch(db_session, source, shard_index=0)

    assert result.attempted_count == 2
    assert result.failed_count == 1
    assert result.created_count == 1
    assert committed_tyc_rows()[0] == ("search_companies", failed_name, "error")
    assert committed_tyc_daily_used() == 3  # 仅第二家的锚定+2 维计费


def test_preflight_rejections_are_typed(
    db_session: Session, committed_tyc_env: None
) -> None:
    """前置校验：非天眼查 422、停用 409、密钥/起始额度不可用 409（均零调用）。"""
    manual = db_session.scalar(select(DataSource).where(DataSource.code == "manual-json"))
    assert manual is not None
    with pytest.raises(TycBatchNotTianyancha):
        run_tyc_batch(db_session, manual, shard_index=0)

    configure_committed_tyc(daily_limit=100, monthly_limit=1000, enabled=False)
    with pytest.raises(TycBatchSourceInactive):
        run_tyc_batch(db_session, _fresh_source(db_session), shard_index=0)

    configure_committed_tyc(daily_limit=100, monthly_limit=1000, api_key=None)
    with pytest.raises(TycBatchUnavailable) as key_error:
        run_tyc_batch(db_session, _fresh_source(db_session), shard_index=0)
    assert key_error.value.context["reason"] == "key_unavailable"

    configure_committed_tyc(daily_limit=1, monthly_limit=50)
    _commit_usage("预占额度")
    with pytest.raises(TycBatchUnavailable) as quota_error:
        run_tyc_batch(db_session, _fresh_source(db_session), shard_index=0)
    assert quota_error.value.context["reason"] == "quota_exhausted"
    assert quota_error.value.context["daily_remaining"] == 0


# ---------------------------------------------------------------------------
# 手动单供应商：只核查指定启用供应商，按 C 受额度约束但不套全量桶门禁
# ---------------------------------------------------------------------------


def test_manual_single_supplier_targets_only_requested_supplier(
    db_session: Session, committed_tyc_env: None, monkeypatch: MonkeyPatch
) -> None:
    configure_committed_tyc(daily_limit=100, monthly_limit=1000, dimensions=_TWO_DIMS)
    source = _fresh_source(db_session)
    suppliers = _add_suppliers(db_session, _codes_for_buckets({0: 2, 1: 1}))
    target, other = suppliers[0], suppliers[1]
    stub = MultidimMcpStub()
    _patch_gateway(monkeypatch, stub.gateway(_TWO_DIMS))

    result = run_tyc_supplier(db_session, source, supplier_id=target.id)

    assert result.supplier_id == target.id
    assert result.shard_index == _literal_bucket(target.supplier_code)
    assert result.shard_count == SHARD_COUNT
    assert result.targeted_count == 1
    assert result.attempted_count == 1
    assert result.created_count == 1
    assert set(result.per_tool_counts) == {"search_companies", *_TWO_DIMS}
    assert {company for company, _tool in stub.calls} == {target.legal_name}
    assert other.legal_name not in {company for company, _tool in stub.calls}
    signals = list(db_session.scalars(select(RawSignal)))
    assert len(signals) == 1
    assert signals[0].external_id.startswith(f"tyc-{target.supplier_code}-")


def test_manual_single_supplier_is_not_blocked_by_full_bucket_gate(
    db_session: Session, committed_tyc_env: None, monkeypatch: MonkeyPatch
) -> None:
    """39 家同桶在定时路径会被 500 额度门禁拒绝；手动单供应商仍可执行。"""
    codes = _codes_for_buckets({0: 39})
    suppliers = _add_suppliers(db_session, codes)
    configure_committed_tyc(daily_limit=500, monthly_limit=5000)
    source = _fresh_source(db_session)
    gateway = ScriptedGateway(_empty_anchor)
    _patch_gateway(monkeypatch, gateway)

    result = run_tyc_supplier(db_session, source, supplier_id=suppliers[0].id)

    assert result.targeted_count == 1
    assert result.empty_count == 1
    assert result.quota_exhausted is False
    assert gateway.calls == [suppliers[0].legal_name]


def test_manual_single_supplier_rejects_unknown_or_disabled(
    db_session: Session, committed_tyc_env: None
) -> None:
    configure_committed_tyc(daily_limit=100, monthly_limit=1000)
    source = _fresh_source(db_session)
    with pytest.raises(TycBatchSupplierNotFound):
        run_tyc_supplier(db_session, source, supplier_id=999_999)

    disabled = Supplier(
        supplier_code="SUP-MD-DISABLED",
        legal_name="停用供应商",
        country_code="CN",
        enabled=False,
    )
    db_session.add(disabled)
    db_session.flush()
    with pytest.raises(TycBatchSupplierNotFound):
        run_tyc_supplier(db_session, source, supplier_id=disabled.id)


def test_manual_single_supplier_quota_exhausted_midway(
    db_session: Session, committed_tyc_env: None, monkeypatch: MonkeyPatch
) -> None:
    """手动单供应商同样按 C 受额度约束：锚定后耗尽 → 部分报告 + quota_exhausted。"""
    configure_committed_tyc(daily_limit=1, monthly_limit=50)
    source = _fresh_source(db_session)
    code = _codes_for_buckets({0: 1})[0]
    name = f"{code} 有限公司"
    supplier = _add_suppliers(db_session, [code])[0]
    stub = MultidimMcpStub()
    _patch_gateway(monkeypatch, stub.gateway(_TWO_DIMS))

    result = run_tyc_supplier(db_session, source, supplier_id=supplier.id)

    assert result.attempted_count == 1
    assert result.created_count == 1
    assert result.quota_exhausted is True
    assert stub.calls == [(name, "search_companies")]
    assert committed_tyc_daily_used() == 1
    assert result.per_tool_counts["get_risk_overview"].quota_exhausted == 1


def test_manual_single_supplier_missing_key_still_structured(
    db_session: Session, committed_tyc_env: None
) -> None:
    configure_committed_tyc(daily_limit=100, monthly_limit=1000, api_key=None)
    source = _fresh_source(db_session)
    supplier = _add_suppliers(db_session, ["SUP-MD-NOKEY-00001"])[0]
    with pytest.raises(TycBatchUnavailable):
        run_tyc_supplier(db_session, source, supplier_id=supplier.id)


@pytest.fixture(autouse=True)
def _cleanup_committed_usage() -> None:
    """双保险：任何用例结束后清空跨连接计费行（committed_tyc_env 之外）。"""
    yield
    truncate_committed_tyc_usage()
