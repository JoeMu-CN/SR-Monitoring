"""天眼查批量核查服务（计划 Todo 7/8：多维度 + 分片 + 桶门禁 + 批次互斥）。

定时路径 ``run_tyc_batch``：按启用供应商全集的稳定 SHA-256 分片（固定 2 片）
取子集，经**真实桶门禁**（``max_bucket × (1+len(dimensions)) ≤ daily_limit``）
后在专用连接 session 级 advisory lock 内逐供应商执行；手动路径
``run_tyc_supplier`` 只核查指定启用供应商、不套全量桶门禁。两者共用
``tyc_batch_supplier.run_suppliers``（逐工具计数 + 报告信号映射）。

每个供应商：``gateway.fetch_dimensions``（内部每工具经 D9 单工具额度执行器）→
``build_risk_report`` → ``tyc_report_storage.store_tyc_report_signal`` 写「重点摘要 +
报告 JSON」（until_superseded 周内幂等、跨周替代；本模块不写原始 Markdown）。
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.budget import TycUsageSnapshot, get_tyc_usage
from app.agent.tyc_batch_lock import batch_advisory_lock
from app.agent.tyc_batch_models import (
    SHARD_COUNT,
    BatchAccumulator,
    TycBatchBucketGateRejected,
    TycBatchError,
    TycBatchLocked,
    TycBatchNotTianyancha,
    TycBatchResult,
    TycBatchShardError,
    TycBatchSourceInactive,
    TycBatchSupplierNotFound,
    TycBatchUnavailable,
)
from app.agent.tyc_batch_supplier import run_suppliers
from app.agent.tyc_dimensions import resolve_tyc_dimensions
from app.agent.tyc_gateway import TYC_SOURCE_CODE, TycGateway
from app.database import engine
from app.signals.models import DataSource
from app.suppliers.models import Supplier

logger = logging.getLogger("scheduler")

__all__ = [
    "SHARD_COUNT",
    "TycBatchBucketGateRejected",
    "TycBatchError",
    "TycBatchLocked",
    "TycBatchNotTianyancha",
    "TycBatchResult",
    "TycBatchShardError",
    "TycBatchSourceInactive",
    "TycBatchSupplierNotFound",
    "TycBatchUnavailable",
    "bucket_index",
    "run_tyc_batch",
    "run_tyc_supplier",
]


def bucket_index(supplier_code: str, shard_count: int = SHARD_COUNT) -> int:
    """计划 D8 分片公式：``int(sha256(code).hexdigest(), 16) % shard_count``。

    稳定性优先：禁用 Python 内建 ``hash()``（带随机盐、跨进程不稳定）。
    """
    digest = hashlib.sha256(supplier_code.encode()).hexdigest()
    return int(digest, 16) % shard_count


def run_tyc_batch(
    session: Session,
    source: DataSource,
    *,
    shard_index: int,
    shard_count: int = SHARD_COUNT,
) -> TycBatchResult:
    """定时路径：分片 + 真实桶门禁 + 批次互斥 + 逐供应商多维度核查。

    ``shard_index`` 为必填关键字参数（不设兼容性默认，避免调用者遗漏分片）。
    """
    _validate_shard(shard_index, shard_count)
    usage, dimensions = _preflight(session, source)
    with batch_advisory_lock(engine) as acquired:
        if not acquired:
            raise TycBatchLocked("天眼查批量核查已在运行，请稍后重试")
        suppliers = _enabled_suppliers(session)
        _enforce_bucket_gate(
            suppliers, shard_count, 1 + len(dimensions), usage.daily_limit
        )
        subset = [
            supplier
            for supplier in suppliers
            if bucket_index(supplier.supplier_code, shard_count) == shard_index
        ]
        accumulator = BatchAccumulator(
            source_id=source.id,
            shard_index=shard_index,
            shard_count=shard_count,
            targeted_count=len(subset),
        )
        run_suppliers(accumulator, session, _build_gateway(session), subset)
        return accumulator.freeze()


def run_tyc_supplier(
    session: Session, source: DataSource, *, supplier_id: int
) -> TycBatchResult:
    """手动路径：只核查指定启用供应商，不套全量桶门禁。

    额度按当前维度数 C=1+len(dimensions) 受约束：剩余额度不足 C 时仍允许启动
    （避免丢弃用户显式请求），由执行器逐工具在锁内拦截并返回 ``quota_exhausted``；
    完全无可用额度时由 ``_preflight`` 结构化拒绝（API 409）。
    """
    _, dimensions = _preflight(session, source)
    supplier = session.get(Supplier, supplier_id)
    if supplier is None or not supplier.enabled:
        raise TycBatchSupplierNotFound("供应商不存在或未启用")
    logger.info(
        "天眼查单供应商核查：%s（C=%d）", supplier.supplier_code, 1 + len(dimensions)
    )
    with batch_advisory_lock(engine) as acquired:
        if not acquired:
            raise TycBatchLocked("天眼查批量核查已在运行，请稍后重试")
        accumulator = BatchAccumulator(
            source_id=source.id,
            shard_index=bucket_index(supplier.supplier_code, SHARD_COUNT),
            shard_count=SHARD_COUNT,
            targeted_count=1,
            supplier_id=supplier.id,
        )
        run_suppliers(accumulator, session, _build_gateway(session), [supplier])
        return accumulator.freeze()


def _validate_shard(shard_index: int, shard_count: int) -> None:
    if shard_count != SHARD_COUNT:
        raise TycBatchShardError(
            f"分片数固定为 {SHARD_COUNT}（收到 {shard_count}）", shard_count=shard_count
        )
    if not 0 <= shard_index < shard_count:
        raise TycBatchShardError(
            f"非法分片序号 {shard_index}（合法范围 0..{shard_count - 1}）",
            shard_index=shard_index,
        )


def _preflight(
    session: Session, source: DataSource
) -> tuple[TycUsageSnapshot, tuple[str, ...]]:
    """信息源/额度前置校验；返回（额度快照，当前生效维度清单）。"""
    if source.code != TYC_SOURCE_CODE:
        raise TycBatchNotTianyancha("仅支持天眼查信息源")
    if not source.enabled:
        raise TycBatchSourceInactive("信息源已停用")
    usage = get_tyc_usage(session)
    if not usage.enabled:
        raise TycBatchUnavailable("天眼查运行密钥不可用", reason="key_unavailable")
    if not usage.allowed:
        raise TycBatchUnavailable(
            f"天眼查额度不足（今日 {usage.daily_used}/{usage.daily_limit}，"
            f"本月 {usage.monthly_used}/{usage.monthly_limit}）",
            reason="quota_exhausted",
            **usage.to_dict(),
        )
    login_config = source.login_config if isinstance(source.login_config, dict) else {}
    return usage, resolve_tyc_dimensions(login_config.get("tyc_dimensions"))


def _enabled_suppliers(session: Session) -> list[Supplier]:
    return list(
        session.scalars(
            select(Supplier)
            .where(Supplier.enabled.is_(True))
            .order_by(Supplier.supplier_code)
        )
    )


def _enforce_bucket_gate(
    suppliers: Sequence[Supplier],
    shard_count: int,
    calls_per_supplier: int,
    daily_limit: int,
) -> None:
    """真实桶门禁：启用全集按 SHA-256 分桶，最大桶 × 每供应商调用数 ≤ 日额度。"""
    buckets = [0] * shard_count
    for supplier in suppliers:
        buckets[bucket_index(supplier.supplier_code, shard_count)] += 1
    actual_max_bucket = max(buckets, default=0)
    if actual_max_bucket * calls_per_supplier > daily_limit:
        raise TycBatchBucketGateRejected(
            f"天眼查分片门禁拒绝：单桶最多 {actual_max_bucket} 家 × 每供应商 "
            f"{calls_per_supplier} 次 = {actual_max_bucket * calls_per_supplier} 次 "
            f"> 日额度 {daily_limit}；请上调每日额度或减少维度",
            actual_max_bucket=actual_max_bucket,
            calls_per_supplier=calls_per_supplier,
            daily_limit=daily_limit,
            bucket_counts=tuple(buckets),
        )


def _build_gateway(session: Session) -> TycGateway:
    # 延迟导入以便测试替换网关工厂（与既有调度任务同一策略）。
    from app.agent.tyc_gateway import build_tyc_gateway

    return build_tyc_gateway(session=session)
