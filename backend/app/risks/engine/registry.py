"""维度注册表与运行时配置合并。

默认维度来自 dimensions/ 各模块的声明式配置；rule_dimension_configs 表
保存用户在可视化工作台的启停与参数覆盖。引擎每次处理事件时调用
load_dimensions 重新合并，因此配置修改即时生效（热更新）。

评分三层叠加：全局默认（含 RISK_SCORING_CONFIG 环境变量）
  → 维度增量（scoring_overrides 键级合并 + forced_rules_add 追加）
  → DB 覆盖（键级合并，forced_rules 整体替换）。
"""

import dataclasses
import hashlib
import json
from dataclasses import dataclass
from typing import cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.risks.engine.config import DimensionConfig
from app.risks.engine.dimensions import default_dimensions
from app.risks.models import RuleDimensionConfig
from app.risks.scoring import (
    ForcedRule,
    ScoringSettings,
    load_scoring_settings,
)

_OVERRIDABLE_SCORING_KEYS = {
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


@dataclass(frozen=True)
class RuntimeDimension:
    """合并 DB 覆盖后的运行时维度：声明 + 最终评分参数。"""

    config: DimensionConfig
    scoring: ScoringSettings

    @property
    def key(self) -> str:
        return self.config.key

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    def handles(self, event_type: str) -> bool:
        return self.config.handles(event_type)


def _apply_scoring_overrides(
    base: ScoringSettings, overrides: dict[str, object]
) -> ScoringSettings:
    """把评分覆盖合并到给定 ScoringSettings（dict 键级合并，forced_rules 替换）。"""
    if not overrides:
        return base
    kwargs: dict[str, object] = {}
    for key in _OVERRIDABLE_SCORING_KEYS:
        if key in overrides:
            kwargs[key] = overrides[key]
    for dict_key in ("severity_scores", "association_scores"):
        if dict_key in kwargs and isinstance(kwargs[dict_key], dict):
            merged: dict[str, int] = dict(getattr(base, dict_key))
            merged.update(cast(dict[str, int], kwargs[dict_key]))
            kwargs[dict_key] = merged
    if "strong_match_types" in overrides and isinstance(
        overrides["strong_match_types"], list
    ):
        kwargs["strong_match_types"] = frozenset(
            cast(list[str], overrides["strong_match_types"])
        )
    if "forced_rules" in overrides and isinstance(overrides["forced_rules"], list):
        rules: list[ForcedRule] = []
        for item in cast(list[object], overrides["forced_rules"]):
            if isinstance(item, ForcedRule):
                rules.append(item)
            elif isinstance(item, dict):
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
    if not kwargs:
        return base
    return dataclasses.replace(base, **kwargs)  # type: ignore[arg-type]


def build_scoring(
    dim: DimensionConfig, db_overrides: dict[str, object]
) -> ScoringSettings:
    """三层叠加合成维度最终评分参数。"""
    scoring = load_scoring_settings()
    scoring = _apply_scoring_overrides(scoring, dim.scoring_overrides)
    if dim.forced_rules_add:
        scoring = dataclasses.replace(
            scoring, forced_rules=(*scoring.forced_rules, *dim.forced_rules_add)
        )
    scoring = _apply_scoring_overrides(scoring, db_overrides)
    payload = {
        "dimension": dim.key,
        "event_types": db_overrides.get("event_types", list(dim.event_types)),
        "match_columns": db_overrides.get("match_columns", list(dim.match_columns)),
        "severity_scores": scoring.severity_scores,
        "association_scores": scoring.association_scores,
        "credibility_weight": scoring.credibility_weight,
        "timeliness_with_date": scoring.timeliness_with_date,
        "timeliness_without_date": scoring.timeliness_without_date,
        "product_relevance_score": scoring.product_relevance_score,
        "p1_min": scoring.p1_min,
        "p2_min": scoring.p2_min,
        "p3_min": scoring.p3_min,
        "strong_match_types": sorted(scoring.strong_match_types),
        "alert_expiry_days": scoring.alert_expiry_days,
        "forced_rules": [dataclasses.asdict(rule) for rule in scoring.forced_rules],
    }
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()[:12]
    return dataclasses.replace(
        scoring, rule_version=f"{scoring.rule_version}-{dim.key}-{digest}"
    )


def merge_dimension(base: DimensionConfig, row: RuleDimensionConfig) -> RuntimeDimension:
    """DB 行覆盖维度默认，返回运行时维度。"""
    overrides = row.config or {}
    scoring = build_scoring(base, overrides)
    match_columns = base.match_columns
    if isinstance(overrides.get("match_columns"), list):
        match_columns = tuple(cast(list[str], overrides["match_columns"]))
    event_types = base.event_types
    if isinstance(overrides.get("event_types"), list):
        event_types = tuple(cast(list[str], overrides["event_types"]))
    config = DimensionConfig(
        key=base.key,
        label=base.label,
        description=base.description,
        event_types=event_types,
        content_items=base.content_items,
        data_sources=base.data_sources,
        match_columns=match_columns,
        enabled=row.enabled,
        scoring_overrides=base.scoring_overrides,
        forced_rules_add=base.forced_rules_add,
    )
    return RuntimeDimension(config=config, scoring=scoring)


def load_dimensions(session: Session) -> list[RuntimeDimension]:
    """加载全部维度并合并 DB 覆盖，返回运行时维度列表（含默认评分）。"""
    rows = {row.key: row for row in session.scalars(select(RuleDimensionConfig))}
    merged: list[RuntimeDimension] = []
    for base in default_dimensions():
        row = rows.get(base.key)
        if row is not None:
            merged.append(merge_dimension(base, row))
        else:
            merged.append(
                RuntimeDimension(config=base, scoring=build_scoring(base, {}))
            )
    return merged
