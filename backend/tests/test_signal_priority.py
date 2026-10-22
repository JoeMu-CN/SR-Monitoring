"""信号消费优先级打分测试（纯确定性计算，不联网、不调用 LLM）。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.signals.models import DataSource, RawSignal
from app.signals.priority import compute_priority, source_tier
from app.signals.relevance import FilterRules, _default_rules

NOW = datetime(2026, 10, 21, 8, tzinfo=UTC)


def _source(code: str, source_type: str) -> DataSource:
    return DataSource(
        code=code,
        name=code,
        source_type=source_type,
        credibility=50,
        enabled=True,
    )


def _signal(
    title: str = "", content: str = "", *, valid_until: datetime | None = None
) -> RawSignal:
    return RawSignal(title=title, content=content, valid_until=valid_until)


@pytest.mark.parametrize(
    ("code", "source_type", "expected"),
    [
        ("ofac-sdn", "news", 10),  # 清单类信源 code → 10
        ("gdelt-news", "weather", 20),  # 高频结构化源类型 → 20
        ("gov-policy", "policy", 70),  # 政策/监管类 → 70
        ("gdelt-news", "news", 50),  # 其他 → 50
    ],
)
def test_source_tier(code: str, source_type: str, expected: int) -> None:
    assert source_tier(_source(code, source_type), _default_rules()) == expected


def test_supplier_hit_adds_bonus() -> None:
    rules = _default_rules()
    source = _source("gdelt-news", "news")
    signal = _signal("某企业发布公告", "无地理与关键词线索。")

    assert compute_priority(signal, source, supplier_hit=False, rules=rules) == 50
    assert compute_priority(signal, source, supplier_hit=True, rules=rules) == 70


def test_priority_country_adds_bonus() -> None:
    rules = _default_rules()
    source = _source("gdelt-news", "news")
    # 日本属默认海外供应链重点关注国家（SIGNAL_PRIORITY_COUNTRIES 默认 JP,KR）。
    signal = _signal("日本供货商交货延迟", "该工厂以本地销售为主。")

    assert compute_priority(signal, source, supplier_hit=False, rules=rules) == 60


def test_deadline_within_24_hours_adds_bonus() -> None:
    rules = _default_rules()
    source = _source("gdelt-news", "news")
    soon = _signal("某企业发布公告", valid_until=NOW + timedelta(hours=12))
    later = _signal("另一企业发布公告", valid_until=NOW + timedelta(hours=48))

    assert (
        compute_priority(
            soon, source, supplier_hit=False, rules=rules, now_utc=NOW
        )
        == 60
    )
    assert (
        compute_priority(
            later, source, supplier_hit=False, rules=rules, now_utc=NOW
        )
        == 50
    )


def test_priority_is_capped_at_100() -> None:
    rules: FilterRules = replace(_default_rules(), source_tiers={"news": 95})
    source = _source("gdelt-news", "news")
    # 供应商命中 + 高影响关键词（制裁）+ 重点关注国家（日本）+ 24h 内失效。
    signal = _signal(
        "日本制裁相关公告",
        "供应商命中且临近失效。",
        valid_until=NOW + timedelta(hours=2),
    )

    assert (
        compute_priority(
            signal, source, supplier_hit=True, rules=rules, now_utc=NOW
        )
        == 100
    )
