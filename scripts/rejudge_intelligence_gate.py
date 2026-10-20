"""情报门控重判（幂等、默认 dry-run、只做筛选不生成报告）。

在 app 容器内执行；`app` 包已随镜像安装，不是独立 PEP 723 脚本。

一、用途
    情报门控（两段式：门控 → 报告）上线后，历史报告与已跳过行都带着当时的
    `gate_prompt_version`。当筛选口径调整（`GATE_PROMPT_VERSION` 变化）或需要回看
    历史噪声时，本脚本用当前口径重新判定这些行：
    - `ready` 行判为不相关 → 改为 `skipped` 并写 `gate_reason`，**保留原报告正文**，
      便于人工核对后手工回滚（把 status 与 gate_reason 改回即可）。
    - `ready` 行判为相关 → 保持 `ready`，只刷新 `gate_prompt_version`。
    - `skipped` 行判为相关 → **删除该行**，让事件回到待生成队列，由下一轮调度按
      现行口径重新生成报告（脚本本身不生成报告）。
    - 门控调用失败 → 保持原状态、只记失败数：已有内容的行不因一次调用失败降级。

二、边界
    - 不采集、不重启服务、不改信源配置、不打印任何 .env / Token / 密码 / 连接串。
    - 默认 dry-run：只统计候选与判定分布，不写库；`--apply` 才落库。
    - 幂等：默认只处理 `gate_prompt_version` 与当前口径不同的行；`--apply` 后再跑
      dry-run 候选数应为 0。
    - 与 Scheduler 共存：判定只读，但写库与生成批次可能交错。建议避开情报任务整点后
      第 15 分钟（`SCHEDULER_INTELLIGENCE_CRON`），或先停 Scheduler。

三、运行方式（先 dry-run，确认后再 --apply）
    docker compose cp scripts/rejudge_intelligence_gate.py app:/tmp/rejudge.py
    docker compose exec -T app python /tmp/rejudge.py --category policy
    docker compose exec -T app python /tmp/rejudge.py --category policy --apply
    docker compose exec -T app python /tmp/rejudge.py --category policy --scope skipped --apply

四、退出码
    0：全部候选处理完成（允许存在失败条目以外的跳过项）；1：存在失败条目或脚本级
    异常；2：命令行参数错误（argparse 默认行为）。
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from dataclasses import dataclass
from typing import Final, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.intelligence_providers import (
    IntelligenceProvider,
    build_intelligence_provider,
)
from app.ai.intelligence_schemas import (
    GATE_PROMPT_VERSION,
    IntelligenceCategory,
    IntelligenceGateOutput,
    IntelligenceScope,
)
from app.ai.providers import AIProviderError
from app.config import get_ai_settings
from app.database import SessionLocal
from app.risks.intelligence_models import IntelligenceReport
from app.risks.intelligence_service import (
    build_gate_input,
    load_intelligence_scope,
)
from app.risks.models import RiskEvent

LOG_PREFIX: Final = "[rejudge-intelligence-gate]"
Scope = Literal["ready", "skipped"]

logger = logging.getLogger("scripts.rejudge_intelligence_gate")


@dataclass(slots=True)
class Outcome:
    candidates: int = 0
    kept: int = 0
    skipped: int = 0
    reopened: int = 0
    failed: int = 0

    def summary(self) -> str:
        return (
            f"候选={self.candidates} 保持={self.kept} 改判跳过={self.skipped} "
            f"回到待生成={self.reopened} 失败={self.failed}"
        )


def _candidate_rows(
    session: Session, category: IntelligenceCategory, scope: Scope, limit: int | None
) -> list[tuple[IntelligenceReport, RiskEvent]]:
    """只取口径版本落后于当前门控版本的行（幂等的依据）。"""
    statement = (
        select(IntelligenceReport, RiskEvent)
        .join(RiskEvent, RiskEvent.id == IntelligenceReport.event_id)
        .where(
            IntelligenceReport.category == category,
            IntelligenceReport.status == scope,
            IntelligenceReport.gate_prompt_version.is_distinct_from(
                GATE_PROMPT_VERSION
            ),
        )
        .order_by(IntelligenceReport.id)
    )
    if limit is not None:
        statement = statement.limit(limit)
    return [(report, event) for report, event in session.execute(statement)]


def _judge(
    session: Session,
    event: RiskEvent,
    category: IntelligenceCategory,
    provider: IntelligenceProvider,
    scopes: dict[IntelligenceCategory, IntelligenceScope],
) -> IntelligenceGateOutput | None:
    """返回门控结果；None 表示事件无可用信号或调用失败（由调用方计入失败）。"""
    scope = scopes.get(category)
    if scope is None:
        scope = load_intelligence_scope(session, category)
        scopes[category] = scope
    payload = build_gate_input(session, event, category, scope)
    if payload is None:
        logger.warning("事件无可用信号，保持原状 event=%s", event.id)
        return None
    try:
        return asyncio.run(provider.gate_intelligence(payload))
    except AIProviderError as exc:
        logger.warning("门控调用失败 event=%s: %s", event.id, exc)
        return None


def rejudge(
    *,
    category: IntelligenceCategory,
    scope: Scope,
    apply: bool,
    limit: int | None,
) -> int:
    outcome = Outcome()
    provider = build_intelligence_provider(get_ai_settings())
    scopes: dict[IntelligenceCategory, IntelligenceScope] = {}
    with SessionLocal() as session:
        rows = _candidate_rows(session, category, scope, limit)
        outcome.candidates = len(rows)
        if not rows:
            logger.info("%s 无待重判行", outcome.summary())
            return 0
        for report, event in rows:
            verdict = _judge(session, event, category, provider, scopes)
            if verdict is None:
                outcome.failed += 1
                continue
            if scope == "ready":
                if verdict.relevant:
                    outcome.kept += 1
                    if apply:
                        report.gate_prompt_version = GATE_PROMPT_VERSION
                        session.commit()
                    continue
                outcome.skipped += 1
                if apply:
                    report.status = "skipped"
                    report.gate_reason = verdict.reason_zh
                    report.gate_prompt_version = GATE_PROMPT_VERSION
                    report.error = None
                    session.commit()
                continue
            # scope == "skipped"：判为相关则删行，交回调度重新生成报告
            if verdict.relevant:
                outcome.reopened += 1
                if apply:
                    session.delete(report)
                    session.commit()
                continue
            outcome.kept += 1
            if apply:
                report.gate_reason = verdict.reason_zh
                report.gate_prompt_version = GATE_PROMPT_VERSION
                session.commit()
    logger.info("%s%s", "" if apply else "【dry-run】", outcome.summary())
    return 0 if outcome.failed == 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="按当前门控口径重判历史情报行（默认 dry-run）"
    )
    parser.add_argument("--category", choices=["policy", "industry"], default="policy")
    parser.add_argument("--scope", choices=["ready", "skipped"], default="ready")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--apply", action="store_true", help="实际写库（默认只统计）")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format=f"{LOG_PREFIX} %(message)s")
    category: IntelligenceCategory = args.category
    scope: Scope = args.scope
    return rejudge(
        category=category, scope=scope, apply=bool(args.apply), limit=args.limit
    )


if __name__ == "__main__":
    raise SystemExit(main())
