from datetime import datetime, timedelta
from typing import Final

from sqlalchemy.orm import Session

from app.signals.models import RawSignal
from app.signals.validity import ValidityState

LEGACY_VALIDITY_REASON_JSON: Final = {
    "code": "legacy_unmigrated",
    "anchor_source": "legacy",
    "details": {},
}


def add_source_signal_fixtures(
    session: Session,
    source_id: int,
    fixed_now: datetime,
) -> None:
    """Add deterministic valid and expired records for source-list E2E coverage.

    规模契约：123 条有效 + 5 条已过期；叠加 seed_e2e 的 2 条证据信号后，
    该来源为「有效 125 / 全部 130」。有效条数必须 ≥125，采集记录页按 100 条/页
    切分时第二页才是真实的 offset=100（25 条），而不是永远只有一页的空验证。
    """
    for index in range(1, 129):
        is_expired = index > 123
        occurred_at = (
            fixed_now - timedelta(days=20 + index)
            if is_expired
            else fixed_now - timedelta(hours=index)
        )
        published_at = None if index in {18, 23} else occurred_at
        content = (
            "E2E long source signal content. " * 12
            if index == 1
            else f"E2E source signal content {index:02d}"
        )
        session.add(
            RawSignal(
                id=94_100 + index,
                source_id=source_id,
                external_id=f"E2E-SOURCE-SIGNAL-{index:02d}",
                title=f"E2E Source Signal {index:02d}",
                content=content,
                url=f"https://example.test/source-signals/{index}",
                published_at=published_at,
                collected_at=occurred_at,
                fingerprint=f"e2e-source-signal-fingerprint-{index:02d}",
                raw_data={"fixture": True, "source_signal_sequence": index},
                validity_state=ValidityState.LEGACY,
                validity_reason=LEGACY_VALIDITY_REASON_JSON,
            )
        )
