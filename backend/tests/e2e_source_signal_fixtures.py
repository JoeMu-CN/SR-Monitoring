from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.signals.models import RawSignal


def add_source_signal_fixtures(
    session: Session,
    source_id: int,
    fixed_now: datetime,
) -> None:
    """Add deterministic valid and expired records for source-list E2E coverage."""
    for index in range(1, 24):
        is_expired = index > 18
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
            )
        )
