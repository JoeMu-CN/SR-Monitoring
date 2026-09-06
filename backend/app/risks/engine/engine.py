"""模块化规则引擎的稳定公开入口。"""

from app.risks.engine.evaluation import evaluate_event
from app.risks.engine.event_identity import event_dedup_key
from app.risks.engine.processing import match_suppliers, process_event
from app.risks.validity import compute_alert_expires_at, expire_alerts

_compute_expires_at = compute_alert_expires_at

__all__ = [
    "_compute_expires_at",
    "evaluate_event",
    "event_dedup_key",
    "expire_alerts",
    "match_suppliers",
    "process_event",
]
