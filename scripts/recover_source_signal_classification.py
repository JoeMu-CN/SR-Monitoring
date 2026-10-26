"""存量信号分类恢复（幂等、默认 dry-run、不产生新的 AI Provider 调用）。

在 app 容器内执行；`app` 包已随镜像安装，不是独立 PEP 723 脚本。

一、用途
    某些信源（如 `sse-shipping` 上海航交所 CCFI 指数）产出的信号只命中 AI 大类
    `event_type`，不产出 `event_subtype`。旧版 `classify_pending_signal` 在
    `event_subtype is None` 时无条件判定分类失败，信号因此长期停在
    `processing_state=classification_failed` / `validity_reason.code=classification_failed`
    （0066 之前是 `validity_state=pending_classification`）；调度器已把该阶段排除出
    分析队列，不会自动重试。

    本脚本在信源已具备显式 `validity_policy.profile`（见
    `scripts/seed_source_validity_policies.py`）的前提下，复用每条信号**最近一次
    可复用的成功 AI 分析**重新走一遍风险管线，使这些信号拿到有效期窗口。

二、边界（与 seed 脚本一致）
    - 不调用 AI Provider：`process_analysis` 只消费已落库的分析结果。
    - 不采集、不发布、不重启任何服务、不改信源策略、不打印任何 .env / Token /
      密码 / 连接串。
    - 默认 dry-run：只读统计候选数与可恢复数，不写库；`--apply` 才逐条处理。
    - 幂等：成功处理的信号不再是 `processing_state=classification_failed`，
      重复执行不会再次命中候选集；dry-run → apply → 再 dry-run 的候选数应单调下降。
    - 可逆：只写信号有效期字段、事件与提醒，不删除任何数据；需要回退时把
      `data_sources.validity_policy` 的 profile 去掉即可（历史快照不回滚）。

三、统计口径
    候选 / 恢复成功 / 跳过 / 失败，另单列「已分类但当前不在有效期窗口」。
    `process_analysis` 在分类成功但信号已超出有效期时会先提交再抛
    `InactiveRiskSignalError`；这属于分类已恢复、只是当前不计入有效记录，
    因此计入「已分类但当前不在有效期窗口」而不是失败。真正的异常计入失败，
    且不中断整批。

四、运行方式（先 dry-run，确认后再 --apply）
    docker compose cp scripts/recover_source_signal_classification.py app:/tmp/recover.py
    docker compose exec -T app python /tmp/recover.py --source-code sse-shipping
    docker compose exec -T app python /tmp/recover.py --source-code sse-shipping --apply

五、退出码
    0：全部候选处理完成（允许存在跳过或已分类但不在有效期窗口的条目）；
    1：存在失败条目或脚本级异常；2：命令行参数错误（argparse 默认行为）。
"""
from __future__ import annotations

import argparse
import sys
import traceback
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Final, Literal, assert_never

from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.ai.models import AIAnalysisRecord
from app.ai.service import latest_reusable_analysis
from app.database import SessionLocal
from app.risks.service import process_analysis
from app.risks.signal_validity import InactiveRiskSignalError
from app.signals.models import DataSource, RawSignal
from app.signals.schemas import SourceValidityPolicy

LOG_PREFIX: Final = "[recover-classification]"
FAILED_STATE: Final = "classification_failed"
FAILED_CODE: Final = "classification_failed"
ItemStatus = Literal["recovered", "inactive", "skipped", "failed"]


class RecoverAbort(Exception):
    """脚本级中止：参数指向的信源不存在，或无法读取当前数据库身份。"""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(code, detail)

    def __str__(self) -> str:
        return f"{self.code}: {self.detail}" if self.detail else self.code


@dataclass(frozen=True, slots=True)
class Outcome:
    candidates: int
    recovered: int = 0
    skipped: int = 0
    failed: int = 0
    inactive: int = 0

    def summary(self) -> str:
        return (
            f"候选={self.candidates} 恢复成功={self.recovered} 跳过={self.skipped} "
            f"失败={self.failed} 已分类但当前不在有效期窗口={self.inactive}"
        )


def _database_name(session: Session) -> str:
    value: object = session.scalar(text("SELECT current_database()"))
    if not isinstance(value, str):
        raise RecoverAbort("database_identity", "current_database() 返回非字符串")
    return value


def _load_source(session: Session, code: str) -> DataSource:
    source = session.scalar(select(DataSource).where(DataSource.code == code))
    if source is None:
        raise RecoverAbort("source_missing", code)
    return source


def _describe_source_policy(source: DataSource) -> str:
    """只回显规范化后的有效期策略摘要，绝不打印凭据或连接配置。"""
    if source.validity_policy is None:
        return "未配置"
    try:
        policy = SourceValidityPolicy.model_validate(source.validity_policy)
    except ValidationError:
        return "存在但无法解析"
    profile = policy.profile.value if policy.profile is not None else "none"
    return f"profile={profile} mode={policy.mode.value}"


def _candidates(session: Session, source: DataSource) -> list[RawSignal]:
    """待分类失败阶段：以 processing_state 为准（0066 起 validity_state 只表达生命周期）。"""
    return list(
        session.scalars(
            select(RawSignal)
            .where(
                RawSignal.source_id == source.id,
                RawSignal.processing_state == FAILED_STATE,
                RawSignal.validity_reason["code"].astext == FAILED_CODE,
            )
            .order_by(RawSignal.id)
        )
    )


def _analysis_for(session: Session, signal: RawSignal) -> AIAnalysisRecord | None:
    return latest_reusable_analysis(session, signal.id)


def _recover_one(session: Session, signal: RawSignal, now_utc: datetime) -> ItemStatus:
    """复用已存分析重跑单条信号；任何失败都不中断整批。"""
    analysis = _analysis_for(session, signal)
    if analysis is None:
        print(f"{LOG_PREFIX} [SKIP] signal={signal.id} 无可复用的成功 AI 分析")
        return "skipped"
    try:
        process_analysis(session, signal, analysis, now_utc=now_utc)
    except InactiveRiskSignalError as error:
        # process_analysis 已先提交分类结果再抛错；非活跃只表示当前不在有效期窗口。
        session.rollback()
        resolved = signal.processing_state != FAILED_STATE
        print(
            f"{LOG_PREFIX} [{'INACTIVE' if resolved else 'FAILED'}] signal={signal.id} "
            f"reason={error.reason} state={signal.validity_state}"
        )
        return "inactive" if resolved else "failed"
    except Exception as error:  # noqa: BLE001 - 单条失败不中断整批
        session.rollback()
        print(f"{LOG_PREFIX} [FAILED] signal={signal.id} error={type(error).__name__}")
        traceback.print_exc()
        return "failed"
    return "recovered"


def _plan_one(session: Session, signal: RawSignal) -> ItemStatus:
    """dry-run 只判断能否恢复，不触碰信号状态。"""
    return "skipped" if _analysis_for(session, signal) is None else "recovered"


def _accumulate(outcome: Outcome, status: ItemStatus) -> Outcome:
    match status:
        case "recovered":
            return replace(outcome, recovered=outcome.recovered + 1)
        case "inactive":
            return replace(
                outcome, recovered=outcome.recovered + 1, inactive=outcome.inactive + 1
            )
        case "skipped":
            return replace(outcome, skipped=outcome.skipped + 1)
        case "failed":
            return replace(outcome, failed=outcome.failed + 1)
        case unreachable:
            assert_never(unreachable)


def run(code: str, *, apply: bool) -> int:
    now_utc = datetime.now(UTC)
    with SessionLocal() as session:
        database = _database_name(session)
        source = _load_source(session, code)
        signals = _candidates(session, source)
        outcome = Outcome(candidates=len(signals))
        print(
            f"{LOG_PREFIX} mode={'apply' if apply else 'dry-run'} database={database} "
            f"source={code}(id={source.id}) policy={_describe_source_policy(source)}"
        )
        print(f"{LOG_PREFIX} 候选={outcome.candidates}（分类失败阶段）")
        if not apply:
            for signal in signals:
                outcome = _accumulate(outcome, _plan_one(session, signal))
            print(f"{LOG_PREFIX} 结果: {outcome.summary()}")
            print(f"{LOG_PREFIX} dry-run 只读统计，未写库；加 --apply 才会真正处理")
            return 0
        for signal in signals:
            outcome = _accumulate(outcome, _recover_one(session, signal, now_utc))
        print(f"{LOG_PREFIX} 结果: {outcome.summary()}")
        if outcome.failed:
            print(f"{LOG_PREFIX} 存在失败条目，请检查上方日志", file=sys.stderr)
            return 1
        return 0


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="recover_source_signal_classification.py",
        description="恢复卡在 classification_failed 的存量信号",
    )
    parser.add_argument(
        "--source-code", required=True, help="目标信源 code，例如 sse-shipping"
    )
    parser.add_argument(
        "--apply", action="store_true", help="真正写库（默认 dry-run，只统计不写库）"
    )
    namespace = parser.parse_args(list(argv[1:]))
    namespace.source_code = str(namespace.source_code).strip()
    return namespace


def main(argv: Sequence[str]) -> int:
    namespace = _parse_args(argv)
    code = str(namespace.source_code)
    if not code:
        raise RecoverAbort("source_code_empty", "--source-code 不可为空")
    return run(code, apply=bool(namespace.apply))


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv))
    except RecoverAbort as exc:
        print(f"ABORT {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    # 顶层脚本边界：任何意外都须非 0 退出并留痕。
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        raise SystemExit(1) from None
