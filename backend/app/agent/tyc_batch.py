"""天眼查批量核查服务。

每日定时任务与手动批量刷新 API 共用的结果返回型实现：查询全部
``enabled=true`` 供应商，逐供应商在调用前复查额度；每次调用结果独立写
``TycUsageRecord`` 并先提交，再写成功信号；仅 success 入信号池，重复信号计入
``duplicate_count``；单个供应商失败被隔离。前置条件不满足时抛出类型化异常，
由调用方决定 HTTP 状态映射；本模块不吞异常、不做事务回滚伪装。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.agent.budget import get_tyc_usage, record_tyc_usage
from app.agent.supplier_tyc import upsert_supplier_tyc_signal
from app.agent.tyc_gateway import TYC_SOURCE_CODE
from app.signals.ingestion import SignalIngestionError
from app.signals.models import DataSource
from app.suppliers.models import Supplier

logger = logging.getLogger("scheduler")

# 与 VerifyCompanyTool.name 一致：批量与实时核查共用同一计费口径和工具标识。
TYC_VERIFY_TOOL_NAME = "verify_company"
_TYC_USAGE_STATUSES: frozenset[str] = frozenset(
    {"success", "empty", "error", "not_configured"}
)


class TycBatchError(Exception):
    """天眼查批量核查的前置条件错误基类。"""


class TycBatchNotTianyancha(TycBatchError):
    """目标数据源不是天眼查（API 映射为 422）。"""


class TycBatchSourceInactive(TycBatchError):
    """目标数据源未启用（API 映射为 409）。"""


class TycBatchUnavailable(TycBatchError):
    """运行密钥或起始额度不可用（API 映射为 409）。"""


@dataclass(frozen=True)
class TycBatchResult:
    """一次批量核查的稳定汇总。"""

    source_id: int
    targeted_count: int
    attempted_count: int
    created_count: int
    duplicate_count: int
    empty_count: int
    failed_count: int
    quota_exhausted: bool


def run_tyc_batch(session: Session, source: DataSource) -> TycBatchResult:
    """执行一次天眼查批量核查并返回汇总。

    - 只处理 ``suppliers.enabled=true``，按 ``supplier_code`` 稳定排序；
    - 前置校验（数据源类型/启用状态/密钥/起始额度）不满足时抛出类型化异常；
    - 逐供应商调用前复查额度，中途耗尽即停止并置 ``quota_exhausted``；
    - 每次调用结果独立写 ``TycUsageRecord`` 并先提交（真实调用已发生，计费
      事实不因后续失败回滚），success 才写信号，重复信号计入 ``duplicate``；
    - 单个供应商调用异常、非 success 或信号持久化失败（回滚当前失败事务后）
      不阻塞其余供应商。
    """
    if source.code != TYC_SOURCE_CODE:
        raise TycBatchNotTianyancha("仅支持天眼查数据源")
    if not source.enabled:
        raise TycBatchSourceInactive("数据源已停用")
    usage = get_tyc_usage(session)
    if not usage.enabled:
        raise TycBatchUnavailable("天眼查运行密钥不可用")
    if not usage.allowed:
        raise TycBatchUnavailable(
            f"天眼查额度不足（今日 {usage.daily_used}/{usage.daily_limit}，"
            f"本月 {usage.monthly_used}/{usage.monthly_limit}）"
        )

    # 延迟导入以便测试替换网关工厂（与既有调度任务同一策略）。
    from app.agent.tyc_gateway import build_tyc_gateway

    suppliers = list(
        session.scalars(
            select(Supplier)
            .where(Supplier.enabled.is_(True))
            .order_by(Supplier.supplier_code)
        )
    )
    gateway = build_tyc_gateway(session=session)
    created = 0
    duplicate = 0
    empty = 0
    failed = 0
    attempted = 0
    quota_exhausted = False
    for supplier in suppliers:
        if not get_tyc_usage(session).allowed:
            quota_exhausted = True
            logger.warning("天眼查额度耗尽，提前停止供应商批量核查")
            break
        attempted += 1
        try:
            result = asyncio.run(gateway.verify(supplier.legal_name))
        except Exception as exc:  # noqa: BLE001 —— 单供应商失败隔离，不阻塞其余
            failed += 1
            # 调用已发生（网络/鉴权/服务异常）：按 error 记账并立即提交，
            # 不因后续流程中断而丢失真实调用事实。
            record_tyc_usage(
                session,
                tool_name=TYC_VERIFY_TOOL_NAME,
                company_name=supplier.legal_name,
                status="error",
            )
            session.commit()
            logger.warning("天眼查核查 %s 失败: %s", supplier.legal_name, exc)
            continue
        call_status = _tyc_usage_status(result)
        record_tyc_usage(
            session,
            tool_name=TYC_VERIFY_TOOL_NAME,
            company_name=supplier.legal_name,
            status=call_status,
        )
        # 记账独立提交：真实调用已计入额度，信号入库失败也不回滚计费事实。
        session.commit()
        if call_status != "success":
            if call_status == "empty":
                empty += 1
            else:
                # error / not_configured：调用未能正常完成，计入失败。
                failed += 1
            continue
        title = f"天眼查核查：{supplier.legal_name}"
        content = _format_tyc_content(result)
        try:
            _, is_created = upsert_supplier_tyc_signal(
                session,
                supplier=supplier,
                title=title,
                content=content,
                url=None,
                raw_payload=result,
            )
            session.commit()
        except (SQLAlchemyError, SignalIngestionError) as exc:
            # 单供应商信号持久化失败隔离：回滚当前失败事务（usage 已先提交，
            # 不受影响），计入失败并继续其余供应商；不捕获任意 Exception。
            session.rollback()
            failed += 1
            logger.warning("天眼查信号入库 %s 失败: %s", supplier.legal_name, exc)
            continue
        if is_created:
            created += 1
        else:
            duplicate += 1
    return TycBatchResult(
        source_id=source.id,
        targeted_count=len(suppliers),
        attempted_count=attempted,
        created_count=created,
        duplicate_count=duplicate,
        empty_count=empty,
        failed_count=failed,
        quota_exhausted=quota_exhausted,
    )


def _tyc_usage_status(result: dict[str, object]) -> str:
    """按天眼查计费口径归类 verify 结果：仅四态合法，其余一律按 error 记账。"""
    status = result.get("status")
    if isinstance(status, str) and status in _TYC_USAGE_STATUSES:
        return status
    return "error"


def _format_tyc_content(result: dict[str, object]) -> str:
    """把天眼查 verify 结果转成信号正文（含可回溯字段）。"""
    parts = [f"企业：{result.get('company_name', '')}"]
    if result.get("credit_code"):
        parts.append(f"统一社会信用代码：{result['credit_code']}")
    if result.get("reg_status"):
        parts.append(f"登记状态：{result['reg_status']}")
    candidates = result.get("candidates") or []
    if isinstance(candidates, list):
        for idx, cand in enumerate(candidates[:3], start=1):
            if isinstance(cand, dict) and cand.get("name"):
                parts.append(f"候选{idx}：{cand.get('name')}")
    return "；".join(parts)
