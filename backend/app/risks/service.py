"""风险管线服务（兼容层）。

匹配、评分与提醒的核心逻辑已迁移至 app.risks.engine（模块化维度引擎）。
本模块仅保留原公开接口的再导出，保证既有调用方（router、scheduler）与
测试（test_scoring）兼容。

process_analysis 转调引擎 process_event；scoring 形参为兼容保留、已不生效
（评分参数由维度配置三层叠加决定：全局默认 + 维度增量 + DB 覆盖）。
"""

from sqlalchemy.orm import Session

from app.ai.models import AIAnalysisRecord
from app.risks.engine.engine import (
    _compute_expires_at,
    event_dedup_key,
    expire_alerts,
    match_suppliers,
    process_event,
)
from app.risks.engine.matching import MATCH_ORDER, MatchCandidate
from app.risks.schemas import RiskProcessResult
from app.risks.scoring import ScoringSettings
from app.signals.models import RawSignal

__all__ = [
    "MATCH_ORDER",
    "MatchCandidate",
    "_compute_expires_at",
    "event_dedup_key",
    "expire_alerts",
    "match_suppliers",
    "process_analysis",
    "process_event",
]


def process_analysis(
    session: Session,
    signal: RawSignal,
    analysis: AIAnalysisRecord,
    scoring: ScoringSettings | None = None,
) -> RiskProcessResult:
    """处理 AI 分析结果：归并事件、维度匹配、评分并生成提醒。

    scoring 形参为兼容旧调用保留，已不再生效（评分由维度配置决定）。
    """
    return process_event(session, signal, analysis)
