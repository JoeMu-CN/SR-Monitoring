"""维度注册表与运行时配置合并。

默认维度来自 dimensions/ 各模块的声明式配置；rule_dimension_configs 表
保存用户在可视化工作台的启停与参数覆盖。引擎每次处理事件时调用
load_dimensions 重新合并，因此配置修改即时生效（热更新）。

评分五层叠加（在 build_scoring 中合成）：
  1. 代码默认 load_scoring_settings()（含 RISK_SCORING_CONFIG 环境变量）
  2. 全局 DB 行（key=__global_scoring__，仅 enabled 为真时生效）
  3. 维度增量 dim.scoring_overrides（dict 键级合并）
  4. 维度追加 dim.forced_rules_add（追加到强制规则列表）
  5. 维度 DB 行 rule_dimension_configs.config（键级合并，forced_rules 整体替换）

强制规则优先级：全局行的 config 只要包含 forced_rules 键，第 2 层即整体替换
强制规则列表，且第 4 层的 dim.forced_rules_add 追加被跳过（整体替换，避免
双重生效）；全局行不含该键时保持现行行为（维度追加照常生效）。

前向兼容边界（有意取舍，必须在测试中锁定）：一旦全局行写入了 forced_rules
键，今后代码新增的 dim.forced_rules_add 会被静默压制——全局自定义即接管
强制规则列表。仅写 p1_min 等不含 forced_rules 的键不会触发该状态。
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
    coerce_llm_adopt_threshold,
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
    "llm_adopt_threshold",
    "alert_expiry_days",
}

# 全局覆盖行在 rule_dimension_configs 中的固定 key 与 label（label NOT NULL）。
GLOBAL_SCORING_CONFIG_KEY = "__global_scoring__"
GLOBAL_SCORING_LABEL = "全局评分配置"


def merge_scoring_config(
    base: dict[str, object], patch: dict[str, object]
) -> dict[str, object]:
    """键级深合并评分配置：子字典按键合并，其余键整体替换。

    - severity_scores / association_scores 逐键合并（部分 diff 不丢已有键）；
    - forced_rules / strong_match_types 等列表键整体替换（语义上必须整体生效）。

    返回新字典，不修改 base 与 patch。全局 PUT、维度 PUT 与草稿预览共用本
    函数，确保"保存"与"预览"的合并语义完全一致。
    """
    merged: dict[str, object] = dict(base)
    for key, value in patch.items():
        if key in ("severity_scores", "association_scores") and isinstance(value, dict):
            existing = merged.get(key)
            if isinstance(existing, dict):
                combined = dict(cast(dict[str, object], existing))
                combined.update(cast(dict[str, object], value))
                merged[key] = combined
                continue
        merged[key] = value
    return merged


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
        if key not in overrides:
            continue
        if key == "llm_adopt_threshold":
            # 类型安全合并：仅接受 [0, 1] 数值，非法值忽略并保持 base。
            threshold = coerce_llm_adopt_threshold(overrides[key])
            if threshold is not None:
                kwargs[key] = threshold
            continue
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


def load_global_scoring_config(session: Session) -> dict[str, object] | None:
    """读取全局覆盖行（key=__global_scoring__）的 config。

    行不存在或 enabled 非真时返回 None（回退代码默认）。只查一次库，由调用方
    显式传入 build_scoring，避免逐维度 N 次查询与在 build_scoring 内隐式查库。
    """
    row = session.scalar(
        select(RuleDimensionConfig).where(
            RuleDimensionConfig.key == GLOBAL_SCORING_CONFIG_KEY
        )
    )
    if row is None or not row.enabled:
        return None
    return dict(row.config or {})


@dataclass(frozen=True)
class GlobalForcedRuleDefaults:
    """全局层强制规则默认值：可全局化的规则 + 被排除的维度规则披露。"""

    rules: tuple[ForcedRule, ...]
    dropped: tuple[dict[str, object], ...]


def global_forced_rule_defaults() -> GlobalForcedRuleDefaults:
    """代码默认（含环境变量）∪ 各维度 forced_rules_add，作为全局层默认列表。

    维度规则的空 event_types 物化为该维度声明的事件类型（空元组在
    apply_forced_rules 中表示匹配所有事件类型，直接全局化会造成过度升级）；
    物化后仍为空（维度本身未声明事件类型，如 policy）的规则被排除，通过
    dropped 披露（含 dimension 字段），供 UI 提示保存后不再生效。

    名称去重策略：按名称**确定性去重、保留首次出现**，代码默认（含
    RISK_SCORING_CONFIG 环境变量）优先于维度 forced_rules_add。PUT 拒绝重复
    名称，GET 返回的默认列表必须自身无重复，用户才能将其原样 PUT 回存。
    """
    rules: list[ForcedRule] = []
    dropped: list[dict[str, object]] = []
    seen: set[str] = set()
    for rule in load_scoring_settings().forced_rules:
        if rule.name in seen:
            continue
        seen.add(rule.name)
        rules.append(rule)
    for dim in default_dimensions():
        for rule in dim.forced_rules_add:
            if rule.name in seen:
                continue
            seen.add(rule.name)
            event_types = rule.event_types or dim.event_types
            if not event_types:
                dropped.append({"dimension": dim.key, **dataclasses.asdict(rule)})
                continue
            rules.append(dataclasses.replace(rule, event_types=tuple(event_types)))
    return GlobalForcedRuleDefaults(rules=tuple(rules), dropped=tuple(dropped))


def effective_global_forced_rules(
    global_config: dict[str, object] | None,
) -> tuple[ForcedRule, ...]:
    """全局层当前生效的强制规则（不含维度 DB 行第五层覆盖）。

    global_config 来自 load_global_scoring_config；含 forced_rules 键时以它为
    整体替换结果，否则回退 global_forced_rule_defaults（含维度追加默认）。
    """
    if global_config is not None and "forced_rules" in global_config:
        return _apply_scoring_overrides(
            load_scoring_settings(), global_config
        ).forced_rules
    return global_forced_rule_defaults().rules


def build_scoring(
    dim: DimensionConfig,
    db_overrides: dict[str, object],
    global_overrides: dict[str, object] | None = None,
) -> ScoringSettings:
    """五层叠加合成维度最终评分参数（见模块 docstring 的合成顺序）。

    全局行含 forced_rules 键时整体替换并跳过 dim.forced_rules_add 追加；
    全局行不含该键时保持现行行为（维度追加照常生效）。
    """
    scoring = load_scoring_settings()
    global_replaces_forced_rules = (
        global_overrides is not None and "forced_rules" in global_overrides
    )
    if global_overrides:
        scoring = _apply_scoring_overrides(scoring, global_overrides)
    scoring = _apply_scoring_overrides(scoring, dim.scoring_overrides)
    if dim.forced_rules_add and not global_replaces_forced_rules:
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
        "llm_adopt_threshold": scoring.llm_adopt_threshold,
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


def _merge_dimension_config(
    base: DimensionConfig,
    overrides: dict[str, object],
    global_overrides: dict[str, object] | None,
    enabled: bool,
) -> RuntimeDimension:
    """把配置覆盖合并到维度声明，返回运行时维度（保存与草稿预览共用）。"""
    scoring = build_scoring(base, overrides, global_overrides)
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
        enabled=enabled,
        scoring_overrides=base.scoring_overrides,
        forced_rules_add=base.forced_rules_add,
    )
    return RuntimeDimension(config=config, scoring=scoring)


def merge_dimension(
    base: DimensionConfig,
    row: RuleDimensionConfig,
    global_overrides: dict[str, object] | None = None,
) -> RuntimeDimension:
    """DB 行覆盖维度默认，返回运行时维度（全局覆盖显式传入）。"""
    return _merge_dimension_config(
        base, dict(row.config or {}), global_overrides, row.enabled
    )


def build_draft_dimension(
    base: DimensionConfig,
    *,
    stored_overrides: dict[str, object] | None,
    draft_overrides: dict[str, object] | None,
    global_overrides: dict[str, object] | None,
    enabled: bool,
) -> RuntimeDimension:
    """构造草稿运行时维度：已存 DB 覆盖 → 草稿覆盖（与保存路径同构）。

    与 merge_dimension 共用同一合并实现：先对已存行 config 与草稿做键级深
    合并（merge_scoring_config，浅合并会整体替换 severity/association 子
    字典），再走同一个 build_scoring（含全局覆盖、维度 scoring_overrides
    与 forced_rules_add）。纯函数：不写库、不依赖维度行对象。
    """
    overrides = merge_scoring_config(stored_overrides or {}, draft_overrides or {})
    return _merge_dimension_config(base, overrides, global_overrides, enabled)


def _global_overrides_from_rows(
    rows: dict[str, RuleDimensionConfig],
) -> dict[str, object] | None:
    """从全量行映射中提取生效的全局覆盖（复用一次查询，避免重复查库）。"""
    row = rows.get(GLOBAL_SCORING_CONFIG_KEY)
    if row is None or not row.enabled:
        return None
    return dict(row.config or {})


def load_dimensions(session: Session) -> list[RuntimeDimension]:
    """加载全部维度并合并 DB 覆盖，返回运行时维度列表（含默认评分）。

    全量行一次查出后提取全局覆盖并显式传入 build_scoring；全局行不是维度
    （key 不在 default_dimensions 中），不会被当作维度返回或启用。
    """
    rows = {row.key: row for row in session.scalars(select(RuleDimensionConfig))}
    global_overrides = _global_overrides_from_rows(rows)
    merged: list[RuntimeDimension] = []
    for base in default_dimensions():
        row = rows.get(base.key)
        if row is not None:
            merged.append(merge_dimension(base, row, global_overrides))
        else:
            merged.append(
                RuntimeDimension(
                    config=base, scoring=build_scoring(base, {}, global_overrides)
                )
            )
    return merged
