"""可配置风险评分引擎。

D7 将 D5/D6 的固定评分替换为可通过环境变量调整的评分引擎，
支持强制规则、可配置分级阈值和提醒自动失效。
"""

import json
import os
from dataclasses import dataclass, field
from typing import cast


@dataclass(frozen=True)
class ForcedRule:
    """强制规则：满足条件时直接产生指定等级，绕过常规评分。"""

    name: str
    description: str
    event_types: tuple[str, ...]  # 空元组表示匹配所有事件类型
    match_types: tuple[str, ...]  # 空元组表示匹配所有匹配类型
    forced_level: str
    reason: str
    event_subtypes: tuple[str, ...] = ()


@dataclass(frozen=True)
class ScoringSettings:
    """评分配置。所有维度分值、分级阈值和强制规则均可通过配置调整。"""

    rule_version: str = "risk-score-v1"
    severity_scores: dict[str, int] = field(
        default_factory=lambda: {"critical": 35, "high": 28, "medium": 20, "low": 10}
    )
    association_scores: dict[str, int] = field(
        default_factory=lambda: {
            "registry_no": 30,
            "legal_name": 25,
            "alias": 25,
            "site_distance": 20,
            "site_text": 20,
            "product": 12,
        }
    )
    credibility_weight: float = 0.2
    timeliness_with_date: int = 10
    timeliness_without_date: int = 5
    product_relevance_score: int = 5
    p1_min: int = 85
    p2_min: int = 65
    p3_min: int = 40
    strong_match_types: frozenset[str] = field(
        default_factory=lambda: frozenset({"registry_no", "legal_name", "alias"})
    )
    alert_expiry_days: int = 90
    forced_rules: tuple[ForcedRule, ...] = (
        ForcedRule(
            name="sanctions_entity_hit",
            description="供应商主体直接命中制裁或合规事件",
            event_types=("compliance", "judicial"),
            match_types=("registry_no", "legal_name", "alias"),
            forced_level="P1",
            reason="供应商主体直接命中制裁/合规事件，强制提升为 P1",
        ),
    )


_DEFAULT = ScoringSettings()


def load_scoring_settings() -> ScoringSettings:
    """从环境变量加载评分配置，未设置的项使用默认值。

    环境变量 RISK_SCORING_CONFIG 接受 JSON 对象，键与 ScoringSettings
    字段同名。例如：
        {"p1_min": 90, "severity_scores": {"critical": 40}}
    """
    raw = os.getenv("RISK_SCORING_CONFIG", "").strip()
    if not raw:
        return _DEFAULT
    try:
        overrides: dict[str, object] = json.loads(raw)
    except json.JSONDecodeError:
        return _DEFAULT
    if not isinstance(overrides, dict):
        return _DEFAULT

    valid_keys = {
        "rule_version",
        "severity_scores",
        "association_scores",
        "credibility_weight",
        "timeliness_with_date",
        "timeliness_without_date",
        "product_relevance_score",
        "p1_min",
        "p2_min",
        "p3_min",
        "alert_expiry_days",
    }
    kwargs: dict[str, object] = {}
    for key in valid_keys:
        if key in overrides:
            kwargs[key] = overrides[key]

    # 合并 dict 类型字段（未提供的键保留默认值）
    for dict_key in ("severity_scores", "association_scores"):
        if dict_key in kwargs and isinstance(kwargs[dict_key], dict):
            override_dict = cast(dict[str, int], kwargs[dict_key])
            merged: dict[str, int] = dict(getattr(_DEFAULT, dict_key))
            merged.update(override_dict)
            kwargs[dict_key] = merged

    # strong_match_types 特殊处理
    if "strong_match_types" in overrides and isinstance(
        overrides["strong_match_types"], list
    ):
        strong_types = cast(list[str], overrides["strong_match_types"])
        kwargs["strong_match_types"] = frozenset(strong_types)

    # forced_rules 特殊处理
    if "forced_rules" in overrides and isinstance(overrides["forced_rules"], list):
        raw_rules = cast(list[object], overrides["forced_rules"])
        rules: list[ForcedRule] = []
        for item in raw_rules:
            if isinstance(item, dict):
                rules.append(
                    ForcedRule(
                        name=str(item.get("name", "")),
                        description=str(item.get("description", "")),
                        event_types=tuple(item.get("event_types", [])),
                        match_types=tuple(item.get("match_types", [])),
                        forced_level=str(item.get("forced_level", "P1")),
                        reason=str(item.get("reason", "")),
                        event_subtypes=tuple(item.get("event_subtypes", [])),
                    )
                )
        kwargs["forced_rules"] = tuple(rules)

    return ScoringSettings(**kwargs)  # type: ignore[arg-type]


def compute_score(
    settings: ScoringSettings,
    severity: str,
    match_score: int,
    credibility: int,
    has_published_at: bool,
    product_relevant: bool,
) -> tuple[int, dict[str, object]]:
    """计算风险总分和各维度明细。"""
    severity_score = settings.severity_scores.get(severity, 0)
    credibility_score = round(credibility * settings.credibility_weight)
    timeliness = (
        settings.timeliness_with_date
        if has_published_at
        else settings.timeliness_without_date
    )
    product_score = settings.product_relevance_score if product_relevant else 0

    detail: dict[str, object] = {
        "rule_version": settings.rule_version,
        "severity": severity_score,
        "association": match_score,
        "source_credibility": credibility_score,
        "timeliness": timeliness,
        "product_relevance": product_score,
    }
    total = severity_score + match_score + credibility_score + timeliness + product_score
    return min(total, 100), detail


def compute_level(settings: ScoringSettings, score: int) -> str:
    """根据总分计算 P1 至 P4 等级。"""
    if score >= settings.p1_min:
        return "P1"
    if score >= settings.p2_min:
        return "P2"
    if score >= settings.p3_min:
        return "P3"
    return "P4"


def apply_level_cap(
    settings: ScoringSettings,
    level: str,
    match_type: str,
    detail: dict[str, object],
) -> str:
    """对弱关联（无主体精确匹配）设置等级上限。"""
    types = set(match_type.split("+"))
    if types == {"country"} and level != "P4":
        detail["level_cap"] = "country_only_max_p4"
        return "P4"
    if level == "P1" and types.isdisjoint(settings.strong_match_types):
        detail["level_cap"] = "weak_association_max_p2"
        return "P2"
    return level


def apply_forced_rules(
    settings: ScoringSettings,
    event_type: str,
    match_type: str,
    level: str,
    score: int,
    detail: dict[str, object],
    event_subtype: str | None = None,
) -> tuple[str, int]:
    """检查强制规则，命中时直接返回指定等级和满分。"""
    match_types = set(match_type.split("+"))
    for rule in settings.forced_rules:
        if rule.event_types and event_type not in rule.event_types:
            continue
        if rule.event_subtypes and event_subtype not in rule.event_subtypes:
            continue
        if rule.match_types and match_types.isdisjoint(set(rule.match_types)):
            continue
        detail["forced_rule"] = {
            "name": rule.name,
            "description": rule.description,
            "reason": rule.reason,
            "original_level": level,
            "original_score": score,
        }
        return rule.forced_level, 100
    return level, score
