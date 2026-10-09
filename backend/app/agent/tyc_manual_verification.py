"""风险助手手动实时天眼查完整核查（清单内供应商）。

与定时批次的分工：
- 定时批次（``tyc_batch``）按周度分片写入报告信号，等待既有待处理队列解析；
- 本模块供风险助手「手动实时完整核查」：清单内供应商不再回读历史报告替代实时
  结果，而是 ``await gateway.fetch_dimensions`` → 构造报告 → 以**显式手动观察
  身份**入库 → 只对该新信号 ``await analyze_raw_signal`` → ``process_analysis``，
  达到条件即创建/更新正式告警。

不变量：
- 全程 async：无 ``asyncio.run``、无跨线程共享 ORM Session；额度记账仍由既有单
  工具执行器在独立短事务内完成（``fetch_dimensions`` 内部逐工具调用）；
- 只有**有效主体证据**（主体锚定成功且解析出企业名）才允许持久化与解析：额度
  耗尽、来源停用/密钥撤销、锚定远程失败一律不落库、不解析；
- 部分维度额度耗尽/失败显式记为 ``partial`` 并列出未完成维度，不冒充完整核查；
- AI 失败保留已入库证据（既有待处理队列可重试），返回明确未完成状态；
- 不调用 ``scheduler.jobs._process_pending_signals`` 等全局队列。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy.orm import Session

from app.agent.budget import TycUsageSnapshot, get_tyc_usage
from app.agent.tyc_analysis_context import TycAnalysisContext, build_analysis_context
from app.agent.tyc_gateway import TycGateway
from app.agent.tyc_report import TycRiskReport, build_risk_report
from app.agent.tyc_report_storage import ManualObservation, store_tyc_report_signal
from app.ai.providers import AIProviderError
from app.ai.service import analyze_raw_signal
from app.risks.service import InactiveRiskSignalError, process_analysis
from app.signals.models import RawSignal
from app.suppliers.models import Supplier

logger = logging.getLogger(__name__)

# 整体核查状态：completed/partial 表示证据已处理；其余均为明确未完成，任何一种
# 都不得被解读为「无风险」。
VerificationStatus = Literal[
    "completed",
    "partial",
    "empty",
    "not_configured",
    "quota_exhausted",
    "busy",
    "error",
    "analysis_failed",
    "inactive_signal",
]
AnalysisStatus = Literal["succeeded", "failed", "skipped"]
ProcessingStatus = Literal["processed", "skipped"]

# fetch_dimensions 顶层状态（主体锚定结果）→ 未完成状态与提示。
_ANCHOR_FAILURE: dict[str, tuple[VerificationStatus, str]] = {
    "not_configured": ("not_configured", "天眼查未启用或运行密钥不可用，本次未完成核查"),
    "quota_exhausted": ("quota_exhausted", "天眼查调用额度已耗尽，本次未完成核查"),
    "busy": ("busy", "天眼查额度锁繁忙，本次未完成核查"),
    "empty": ("empty", "天眼查未检索到该企业主体记录，本次未产生有效证据"),
}
_DEFAULT_ANCHOR_FAILURE = ("error", "天眼查主体锚定失败，本次未完成核查")

# 已完整取数的维度状态；其余（error/quota_exhausted/busy/not_configured）计入
# 未完成维度。
_DIMENSION_DONE_STATUSES = frozenset({"success", "empty"})


@dataclass(frozen=True, slots=True)
class ManualVerificationResult:
    """一次手动完整核查的机器可读结论。"""

    status: VerificationStatus
    message: str
    supplier_id: int
    supplier_code: str
    company_name: str | None = None
    credit_code: str | None = None
    reg_status: str | None = None
    signal_id: int | None = None
    external_id: str | None = None
    analysis_status: AnalysisStatus = "skipped"
    processing_status: ProcessingStatus = "skipped"
    event_id: int | None = None
    event_created: bool = False
    alert_ids: tuple[int, ...] = ()
    complete: bool = False
    incomplete_dimensions: tuple[str, ...] = ()
    title: str | None = None
    content: str | None = None
    usage: dict[str, object] | None = None

    def to_payload(self) -> dict[str, object]:
        """助手工具响应体：机器可读结论（signal_id / analysis / event / alert）。"""
        payload: dict[str, object] = {
            # 助手/前端既有渲染按 status==="success" 判定成功，故完整核查对外仍为
            # success；机器可读完成度由 complete/analysis_status/processing_status
            # 承载，partial 也如实暴露（complete=false + incomplete_dimensions）。
            "status": "success" if self.status == "completed" else self.status,
            "verification_status": self.status,
            "source": "tianyancha_realtime",
            "message": self.message,
            "supplier_id": self.supplier_id,
            "supplier_code": self.supplier_code,
            "company_name": self.company_name,
            "credit_code": self.credit_code,
            "reg_status": self.reg_status,
            "signal_id": self.signal_id,
            "external_id": self.external_id,
            "analysis_status": self.analysis_status,
            "processing_status": self.processing_status,
            "event_id": self.event_id,
            "event_created": self.event_created,
            "alert_ids": list(self.alert_ids),
            "complete": self.complete,
            "incomplete_dimensions": list(self.incomplete_dimensions),
            "title": self.title,
            "content": self.content,
            "usage": self.usage,
        }
        return payload


async def verify_supplier_realtime(
    session: Session,
    *,
    supplier: Supplier,
    gateway: TycGateway | None = None,
    now_utc: datetime | None = None,
) -> ManualVerificationResult:
    """实时完整核查单个清单内供应商，并把新证据推进到正式告警判定。"""
    if not supplier.enabled:
        raise ValueError("仅允许核查已启用的供应商")
    observed_at = now_utc or datetime.now(UTC)
    active_gateway = gateway if gateway is not None else _build_gateway(session)
    results = await active_gateway.fetch_dimensions(supplier.legal_name)
    return await _process_fetch_result(
        session,
        supplier=supplier,
        results=results,
        observed_at=observed_at,
        usage=get_tyc_usage(session),
    )


async def _process_fetch_result(
    session: Session,
    *,
    supplier: Supplier,
    results: Mapping[str, object],
    observed_at: datetime,
    usage: TycUsageSnapshot,
) -> ManualVerificationResult:
    """按 fetch 合同分流：无有效主体证据直接返回，落库路径继续解析与判定。"""
    base = _base_result(supplier, usage)
    dimensions = _dimension_entries(results)
    if not dimensions and results.get("status") != "success":
        return _anchor_failure(base, results)
    company_name = _resolved_company_name(results)
    if company_name is None:
        return replace(base, status="error", message=_DEFAULT_ANCHOR_FAILURE[1])
    return await _persist_and_analyze(
        session,
        base=base,
        supplier=supplier,
        results=results,
        observed_at=observed_at,
        company_name=company_name,
        incomplete=_incomplete_dimensions(dimensions),
    )


async def _persist_and_analyze(
    session: Session,
    *,
    base: ManualVerificationResult,
    supplier: Supplier,
    results: Mapping[str, object],
    observed_at: datetime,
    company_name: str,
    incomplete: tuple[str, ...],
) -> ManualVerificationResult:
    """入库 → 只对本信号 AI 解析 → 规则判定；任一环节失败都显式返回未完成。

    私有分析上下文是辅助输入：构造失败只记日志，仍然完成证据入库与解析判定。
    """
    report = build_risk_report(
        results, supplier_code=supplier.supplier_code, generated_at=observed_at
    )
    write = store_tyc_report_signal(
        session,
        supplier=supplier,
        report=report,
        observation=ManualObservation(observed_at=observed_at),
        analysis_context=_analysis_context(results, report, supplier),
    )
    if write.outcome == "empty" or write.signal_id is None:
        return replace(
            base,
            status="empty",
            message="天眼查未返回任何维度结果，本次未产生有效证据",
            company_name=company_name,
            external_id=write.external_id,
        )
    signal = session.get(RawSignal, write.signal_id)
    if signal is None:
        return replace(
            base,
            status="error",
            message="核查证据写入后不可读，本次未完成判定",
            company_name=company_name,
            external_id=write.external_id,
        )
    persisted = replace(
        base,
        company_name=company_name,
        credit_code=report.credit_code,
        reg_status=report.reg_status,
        signal_id=write.signal_id,
        external_id=write.external_id,
        complete=not incomplete,
        incomplete_dimensions=incomplete,
        title=signal.title,
        content=signal.content,
    )
    try:
        analysis = await analyze_raw_signal(session, signal)
    except AIProviderError as exc:
        logger.warning("天眼查手动核查 %s 的 AI 解析失败：%s", supplier.legal_name, exc)
        return replace(
            persisted,
            status="analysis_failed",
            message=f"核查证据已入库但 AI 解析失败，等待既有队列重试：{exc}",
            analysis_status="failed",
        )
    try:
        processed = process_analysis(session, signal, analysis, now_utc=observed_at)
    except InactiveRiskSignalError as exc:
        logger.warning("天眼查手动核查 %s 的信号当前无有效证据：%s", supplier.legal_name, exc)
        return replace(
            persisted,
            status="inactive_signal",
            message=f"核查证据当前无有效证据，未生成正式告警：{exc}",
            analysis_status="succeeded",
        )
    return replace(
        persisted,
        status="partial" if incomplete else "completed",
        message=_completion_message(incomplete, processed.alert_ids),
        analysis_status="succeeded",
        processing_status="processed",
        event_id=processed.event_id,
        event_created=processed.event_created,
        alert_ids=tuple(processed.alert_ids),
    )


def _analysis_context(
    results: Mapping[str, object], report: TycRiskReport, supplier: Supplier
) -> TycAnalysisContext | None:
    """构造私有上下文；失败只记日志，不影响本次核查的证据与判定。"""
    try:
        return build_analysis_context(results, report=report)
    except ValueError as exc:
        logger.warning("天眼查分析上下文构造 %s 失败：%s", supplier.legal_name, exc)
        return None


def _completion_message(incomplete: tuple[str, ...], alert_ids: list[int]) -> str:
    """完成口径：partial 必带未完成维度；无告警只说明规则未触发，不等于无风险。"""
    parts: list[str] = []
    if incomplete:
        parts.append(f"部分维度未完成（{'、'.join(incomplete)}）")
    if alert_ids:
        parts.append(f"已创建或更新 {len(alert_ids)} 条正式告警")
    else:
        parts.append("规则引擎本次未触发正式告警，不等于无风险")
    return "；".join(parts)


def _anchor_failure(
    base: ManualVerificationResult, results: Mapping[str, object]
) -> ManualVerificationResult:
    raw_status = results.get("status")
    status, message = (
        _ANCHOR_FAILURE[raw_status]
        if isinstance(raw_status, str) and raw_status in _ANCHOR_FAILURE
        else _DEFAULT_ANCHOR_FAILURE
    )
    return replace(base, status=status, message=message)


def _dimension_entries(results: Mapping[str, object]) -> Mapping[str, object]:
    raw = results.get("dimensions")
    return raw if isinstance(raw, Mapping) else {}


def _incomplete_dimensions(dimensions: Mapping[str, object]) -> tuple[str, ...]:
    return tuple(
        str(tool_name)
        for tool_name, entry in dimensions.items()
        if not _is_dimension_done(entry)
    )


def _is_dimension_done(entry: object) -> bool:
    status = entry.get("status") if isinstance(entry, Mapping) else None
    return isinstance(status, str) and status in _DIMENSION_DONE_STATUSES


def _resolved_company_name(results: Mapping[str, object]) -> str | None:
    """主体锚定解析出的企业名；缺失或空白视为无有效主体证据。"""
    name = results.get("company_name")
    if not isinstance(name, str):
        return None
    return name.strip() or None


def _base_result(
    supplier: Supplier, usage: TycUsageSnapshot
) -> ManualVerificationResult:
    return ManualVerificationResult(
        status="error",
        message="",
        supplier_id=supplier.id,
        supplier_code=supplier.supplier_code,
        usage=usage.to_dict(),
    )


def _build_gateway(session: Session) -> TycGateway:
    # 延迟导入以便测试替换网关工厂（与既有调度任务同一策略）。
    from app.agent.tyc_gateway import build_tyc_gateway

    return build_tyc_gateway(session=session)