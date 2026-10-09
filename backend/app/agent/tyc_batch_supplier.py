"""单供应商多维度结果 → 计数/报告信号映射（从 ``tyc_batch`` 拆出控制规模）。

- ``record_tools``：把 ``fetch_dimensions`` 合同逐工具计入五态；
- ``persist``：锚定未执行 vs 已执行的分支语义；已执行即构造并写入
  「重点摘要 + 报告 JSON」信号（``tyc_report_storage.store_tyc_report_signal``：
  until_superseded 周内幂等、跨周原子替代），报告构造/入库失败被隔离、不向上抛。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, assert_never

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.agent.tyc_analysis_context import build_analysis_context
from app.agent.tyc_batch_models import BatchAccumulator, ToolOutcome
from app.agent.tyc_gateway import TycGateway
from app.agent.tyc_report import build_risk_report
from app.agent.tyc_report_storage import store_tyc_report_signal
from app.signals.ingestion import SignalIngestionError
from app.suppliers.models import Supplier

logger = logging.getLogger("scheduler")

# 单供应商报告写入的汇总口径（created/duplicate/empty 进入对应的 BatchAccumulator 计数）。
_ReportOutcome = Literal["created", "duplicate", "empty", "failed"]

# 与网关注入的锚定工具一致；锚定调用同样计入逐工具计数。
ANCHOR_TOOL_NAME = "search_companies"

_DIMENSION_OUTCOME: dict[str, ToolOutcome] = {
    "success": "success_with_records",
    "empty": "empty",
    "error": "error",
    "quota_exhausted": "quota_exhausted",
    "busy": "busy",
}
_ANCHOR_OUTCOME: dict[str, ToolOutcome] = {
    **_DIMENSION_OUTCOME,
    "success": "success_with_records",
}


def run_suppliers(
    accumulator: BatchAccumulator,
    session: Session,
    gateway: TycGateway,
    suppliers: Sequence[Supplier],
) -> None:
    """逐供应商执行并累计；额度耗尽置位后立刻停止后续供应商。"""
    runner = SupplierRunner(accumulator=accumulator, session=session, gateway=gateway)
    for supplier in suppliers:
        results = runner.fetch(supplier)
        runner.record_tools(results)
        runner.persist(supplier, results)
        if accumulator.quota_exhausted:
            logger.warning("天眼查额度耗尽，提前停止批量核查")
            break


@dataclass
class SupplierRunner:
    """单供应商结果 → 逐工具计数/报告信号映射（失败被隔离，不向上抛）。"""

    accumulator: BatchAccumulator
    session: Session
    gateway: TycGateway

    def fetch(self, supplier: Supplier) -> Mapping[str, object]:
        return asyncio.run(self.gateway.fetch_dimensions(supplier.legal_name))

    def record_tools(self, results: Mapping[str, object]) -> None:
        dimensions = results.get("dimensions")
        entries = dimensions if isinstance(dimensions, Mapping) else {}
        for tool_name, entry in entries.items():
            status = entry.get("status") if isinstance(entry, Mapping) else None
            outcome = (
                _DIMENSION_OUTCOME.get(status, "error")
                if isinstance(status, str)
                else "error"
            )
            self.accumulator.record_tool(str(tool_name), outcome)
        top_status = results.get("status")
        if entries or top_status == "success":
            anchor_outcome: ToolOutcome = "success_with_records"
        else:
            anchor_outcome = (
                _ANCHOR_OUTCOME.get(top_status, "error")
                if isinstance(top_status, str)
                else "error"
            )
        self.accumulator.record_tool(ANCHOR_TOOL_NAME, anchor_outcome)

    def persist(self, supplier: Supplier, results: Mapping[str, object]) -> None:
        accumulator = self.accumulator
        status = results.get("status")
        dimensions = results.get("dimensions")
        anchor_ran = bool(dimensions) or status == "success"
        if not anchor_ran:
            if status == "quota_exhausted":
                # 锚定被执行器锁内拒绝（远程未调用）：不超发，置位并停止本批。
                accumulator.quota_exhausted = True
                logger.warning("天眼查额度耗尽，提前停止批量核查")
                return
            accumulator.attempted_count += 1
            if status == "empty":
                accumulator.empty_count += 1
            else:  # error / busy：远程失败或额度锁繁忙
                accumulator.failed_count += 1
            logger.warning("天眼查核查 %s 未产生报告：%s", supplier.legal_name, status)
            return
        accumulator.attempted_count += 1
        outcome = self._write_report(supplier, results)
        match outcome:
            case "created":
                accumulator.created_count += 1
            case "duplicate":
                accumulator.duplicate_count += 1
            case "empty":
                accumulator.empty_count += 1
            case "failed":
                accumulator.failed_count += 1
            case unreachable:
                assert_never(unreachable)
        if status == "quota_exhausted":
            accumulator.quota_exhausted = True

    def _write_report(
        self, supplier: Supplier, results: Mapping[str, object]
    ) -> _ReportOutcome:
        """构造并写入报告信号；返回汇总口径（单供应商隔离，不向上抛）。

        私有分析上下文是辅助输入：构造失败不得让本来可用的核查证据整体失败，因此
        上下文缺失时仍按既有报告信号写入。
        """
        try:
            report = build_risk_report(results, supplier_code=supplier.supplier_code)
        except ValueError as exc:
            logger.warning("天眼查报告构造 %s 失败：%s", supplier.legal_name, exc)
            return "failed"
        try:
            context = build_analysis_context(results, report=report)
        except ValueError as exc:
            logger.warning("天眼查分析上下文构造 %s 失败：%s", supplier.legal_name, exc)
            context = None
        try:
            with self.session.begin_nested():
                write = store_tyc_report_signal(
                    self.session,
                    supplier=supplier,
                    report=report,
                    analysis_context=context,
                )
        except (SQLAlchemyError, SignalIngestionError) as exc:
            # 单供应商信号持久化失败隔离：savepoint 已回滚本次写入（额度记账由
            # 执行器独立提交），外层事务与其余供应商信号不受影响。
            logger.warning("天眼查报告入库 %s 失败：%s", supplier.legal_name, exc)
            return "failed"
        self.session.commit()
        return write.outcome
