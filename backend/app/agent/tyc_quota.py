"""天眼查单工具额度执行器（计划 D9 共享通道）。

所有消耗天眼查额度的路径——批量逐工具、网关逐维度、实时核查——统一经
``execute_tyc_tool_with_quota`` 执行**单个**远程工具：自建独立短事务，
``SET LOCAL lock_timeout`` 后在事务级 advisory lock 内**重读**额度（禁止使用
调用方或批内本地余额快照）、执行单次远程调用、按 Todo 1 计费口径写
``TycUsageRecord`` 并**显式提交**后才返回，保证"检查额度 → 调用远程 → 记账"
互斥且真实调用事实不随调用方事务回滚丢失。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from sqlalchemy import func, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.agent.budget import TycUsageSnapshot, get_tyc_usage, record_tyc_usage
from app.database import SessionLocal

logger = logging.getLogger("scheduler")

# 额度锁常量：保护"锁内重读额度 → 单次远程调用 → 记账 → 提交"的短事务。
# 必须与批次互斥锁（Todo 7 的 BATCH_KEY）使用不同常量：批次锁保护整批运行，
# 额度锁保护每一次工具的"检查-调用-记账"，两者语义不同、不得合并。
QUOTA_ADVISORY_LOCK_KEY = 0x54594351  # "TYCQ"，int32 范围内
QUOTA_LOCK_TIMEOUT_MS = 2000

# 锁内重读发现来源停用或运行密钥不可用时的统一中文提示（与 VerifyCompanyTool
# 前置检查同源；公共语义为 ``status == "not_configured"``）。
NOT_CONFIGURED_MESSAGE = "天眼查未启用：请在信息源控制台配置运行密钥并启用"

RemoteCall = Callable[[], Awaitable[dict[str, object]]]
RecordStatus = Literal["success", "empty", "error"]


class TycQuotaOutcome(StrEnum):
    """单工具额度执行结果（与 Todo 1 的 charged_statuses 对齐）。"""

    SUCCESS_WITH_RECORDS = "success_with_records"
    EMPTY = "empty"
    ERROR = "error"
    QUOTA_EXHAUSTED = "quota_exhausted"
    BUSY = "busy"
    NOT_CONFIGURED = "not_configured"


# 远程结果状态 → 记账状态（Todo 1 charged_statuses）：
# 只有 success（有记录的成功结果）计 1 次；empty 计 0；其余（含 param_missing、
# 未知/非法状态）一律按 error 计 0，不写 success 记录。
_RECORD_STATUS_BY_REMOTE_STATUS: dict[str, RecordStatus] = {
    "success": "success",
    "empty": "empty",
}
_OUTCOME_BY_RECORD_STATUS: dict[RecordStatus, TycQuotaOutcome] = {
    "success": TycQuotaOutcome.SUCCESS_WITH_RECORDS,
    "empty": TycQuotaOutcome.EMPTY,
    "error": TycQuotaOutcome.ERROR,
}


@dataclass(frozen=True)
class TycQuotaExecutionResult:
    """一次单工具额度执行的可区分结果。"""

    outcome: TycQuotaOutcome
    tool_name: str
    company_name: str
    payload: dict[str, object] | None = None
    message: str | None = None
    usage: TycUsageSnapshot | None = None

    @property
    def charged(self) -> bool:
        """是否计入额度消耗（仅"有记录的成功结果"计 1 次）。"""
        return self.outcome is TycQuotaOutcome.SUCCESS_WITH_RECORDS


def _record_status(payload: dict[str, object]) -> RecordStatus:
    """按 Todo 1 计费口径把远程结果归类为记账状态。"""
    status = payload.get("status")
    if not isinstance(status, str):
        return "error"
    return _RECORD_STATUS_BY_REMOTE_STATUS.get(status, "error")


def _acquire_quota_lock(session: Session) -> bool:
    """取事务级额度锁；等待超时返回 False（结构化 busy），其余错误原样抛出。"""
    try:
        # lock_timeout 值来自本模块 int 常量，非外部输入。
        session.execute(text(f"SET LOCAL lock_timeout = '{QUOTA_LOCK_TIMEOUT_MS}ms'"))
        session.execute(select(func.pg_advisory_xact_lock(QUOTA_ADVISORY_LOCK_KEY)))
    except OperationalError as exc:
        session.rollback()
        if getattr(exc.orig, "sqlstate", None) == "55P03":
            logger.warning("天眼查额度锁等待超时")
            return False
        raise
    return True


async def execute_tyc_tool_with_quota(
    tool_name: str,
    company_name: str,
    remote_call: RemoteCall,
) -> TycQuotaExecutionResult:
    """在独立短事务与额度锁内执行单个天眼查工具。

    顺序（计划 D9）：

    1. 自建独立 Session（不共享调用方 ORM Session 的事务与锁）；
    2. ``SET LOCAL lock_timeout`` 后 ``pg_advisory_xact_lock(QUOTA_ADVISORY_LOCK_KEY)``；
    3. 锁内重读 ``get_tyc_usage()``（禁止使用批内本地余额快照），**先**检查
       ``usage.enabled``（来源停用或运行密钥不可解密的撤销即时生效），**再**检查余额；
    4. 启用且额度允许才执行一次 ``remote_call``；
    5. 按 Todo 1 计费口径写 ``TycUsageRecord`` 并显式 ``commit`` 后才返回。

    锁等待超时返回 ``busy``；来源停用/密钥撤销返回 ``not_configured``（不调远程、
    不写 usage record）；额度不足返回 ``quota_exhausted``；远程异常按"不计费 error"
    语义记账并返回 ``error``。
    """
    with SessionLocal() as session:
        if not _acquire_quota_lock(session):
            return TycQuotaExecutionResult(
                outcome=TycQuotaOutcome.BUSY,
                tool_name=tool_name,
                company_name=company_name,
                message="天眼查额度繁忙，请重试",
            )

        usage = get_tyc_usage(session)
        if not usage.enabled:
            session.rollback()
            return TycQuotaExecutionResult(
                outcome=TycQuotaOutcome.NOT_CONFIGURED,
                tool_name=tool_name,
                company_name=company_name,
                message=NOT_CONFIGURED_MESSAGE,
                usage=usage,
            )
        if not usage.allowed:
            session.rollback()
            return TycQuotaExecutionResult(
                outcome=TycQuotaOutcome.QUOTA_EXHAUSTED,
                tool_name=tool_name,
                company_name=company_name,
                message=(
                    f"天眼查额度已达上限：今日 {usage.daily_used}/{usage.daily_limit}，"
                    f"本月 {usage.monthly_used}/{usage.monthly_limit}"
                ),
                usage=usage,
            )

        try:
            payload = await remote_call()
        except Exception as exc:  # noqa: BLE001 —— 远程异常转为「不计费 error」结果
            message = f"天眼查调用失败：{exc}"[:500]
            logger.warning("天眼查工具 %s 调用失败：%s", tool_name, exc)
            record_tyc_usage(
                session,
                tool_name=tool_name,
                company_name=company_name,
                status="error",
            )
            session.commit()
            return TycQuotaExecutionResult(
                outcome=TycQuotaOutcome.ERROR,
                tool_name=tool_name,
                company_name=company_name,
                message=message,
                usage=get_tyc_usage(session),
            )

        record_status = _record_status(payload)
        record_tyc_usage(
            session,
            tool_name=tool_name,
            company_name=company_name,
            status=record_status,
        )
        session.commit()
        return TycQuotaExecutionResult(
            outcome=_OUTCOME_BY_RECORD_STATUS[record_status],
            tool_name=tool_name,
            company_name=company_name,
            payload=payload,
            usage=get_tyc_usage(session),
        )
