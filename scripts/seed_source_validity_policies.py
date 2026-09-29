"""信息源有效期策略基线种子（幂等、可重复执行、可版本化）。

在 app 容器内执行；`app` 包已随镜像安装，不是独立 PEP 723 脚本。

一、用途与边界
    本脚本只写 `data_sources.validity_policy` 并追加 `data_source_audit_logs`；
    不触碰 RawSignal、不采集、不发布、不重启任何服务、不重算历史信号，也不打印
    任何 .env / Token / 密码 / 连接串。默认 dry-run 只读比对；`--apply` 才在单事务
    内写库；`--verify` 只读回读并校验 19 条目标策略与版本指纹。

二、「有效期」语义
    validity_policy 是系统判定「一条信号是否仍参与监控」的窗口：过期信号仅留库，
    不再计入有效记录、不再驱动提醒。它是监控口径，不是数据保留期（留库时长由保留
    清理单独决定），也不等于法规或公告的法定有效期。

三、20 源策略表（19 条目标 + nmc-weather 只读核对，逐条理由）
    | code | 策略 | 理由 |
    |---|---|---|
    | nmc-weather | fixed_days 7，review_required=true | 本次不改动：气象预警属短时效事件，保持既有 7 天（只读核对 signal_validity_days=7） |
    | tianyancha | fixed_days 30 | 主体核查结果（工商/司法/经营状态）30 天内视为有效，超期需重新核查 |
    | ofac-sdn | until_revoked，review_required=false | 制裁名单不能靠天数自动解除，只能由撤销信息解除；不设复核期限，仍依赖接收到撤销信息 |
    | mofcom-entity-control | until_revoked，review_required=false | 不可靠实体清单同属制裁/管制名单，撤销前持续有效，不靠天数自动解除 |
    | bis-entity-list | until_revoked，review_required=false | 美国 BIS 实体清单，只能由撤销解除，仍依赖接收到撤销信息 |
    | mofcom-entity-detail | until_revoked，review_required=false | 商务部管控名单明细，撤销前持续有效，不靠天数自动解除 |
    | uflpa-entity-list | until_revoked，review_required=false | UFLPA 实体清单，只能由撤销解除，仍依赖接收到撤销信息 |
    | eu-official-journal | fixed_days 365 | 官方公报为混合公告（含修订草案），365 天是风险关注窗口而非法律期限 |
    | eu-compliance | fixed_days 365 | 合规公告同属混合内容，365 天关注窗口非法定期限 |
    | customs-announcement | fixed_days 365 | 海关公告含政策草案与征求意见稿，365 天关注窗口非法定期限 |
    | mee-announcement | fixed_days 365 | 生态环境部公告混合发布，365 天关注窗口非法定期限 |
    | mem-incident-bulletin | fixed_days 90 | 事故通报 90 天内作为风险关注窗口，超期退出监控 |
    | wto-news | fixed_days 90 | 贸易新闻 90 天内作为风险关注窗口，超期退出监控 |
    | usgs-earthquake-day | fixed_days 7 | 地震日事件为短时效信息，7 天后退出监控 |
    | fmprc-press | fixed_days 30 | 外交表态 30 天关注窗口，超期退出监控 |
    | fx-rates | until_superseded 3 | 汇率行情逐日更新；3 天覆盖周末容差（周末无新报价，避免周一误过期） |
    | commodity-futures | until_superseded 3 | 期货行情逐日更新；3 天覆盖周末容差，避免周末误过期 |
    | sse-shipping | fixed_days 14 | 该采集器不产出 validity_key，设 until_superseded 会导致后续入库失败，故固定 14 天 |
    | pbc-lpr | until_superseded 45 | LPR 月频发布；45 天覆盖发布周期容差，超期或被替代即失效 |
    | stats-pmi | until_superseded 45 | PMI 月频发布；45 天覆盖发布周期容差，超期或被替代即失效 |

四、生效范围
    策略仅对之后新入库的信号生效（applies_to=new_signals_only）；存量信号不回溯、
    不重算，历史 validity_state 保持不变。

五、运行方式（先 dry-run，确认后再 --apply；--verify 可随时只读回读）
    docker compose cp scripts/seed_source_validity_policies.py app:/tmp/seed_validity.py
    docker compose exec -T app python /tmp/seed_validity.py            # dry-run（默认）
    docker compose exec -T app python /tmp/seed_validity.py --apply    # 写入差异项并写审计
    docker compose exec -T app python /tmp/seed_validity.py --verify   # 只读校验 19 条与指纹
"""
from __future__ import annotations

import json
import sys
import traceback
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Literal, assert_never
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.signals.models import DataSource, DataSourceAuditLog
from app.signals.schemas import DataSourceUpdate, SourceValidityPolicy
from app.signals.validity import ValidityMode

RunMode = Literal["dry_run", "apply", "verify"]
PolicyJSON = dict[str, object]

WEATHER_CODE: Final = "nmc-weather"
WEATHER_VALIDITY_DAYS: Final = 7
WEATHER_EXPECTED: Final[PolicyJSON] = {
    "mode": "fixed_days",
    "fixed_days": WEATHER_VALIDITY_DAYS,
    "review_required": True,
}
AUDIT_ACTOR_ROLE: Final = "system"
AUDIT_REASON: Final = "信息源有效期策略基线配置"
AUDIT_EXECUTION: Final = "seed_script"
APPLIES_TO: Final = "new_signals_only"


@dataclass(frozen=True, slots=True)
class SeedAbort(Exception):
    code: str
    detail: str = ""

    def __str__(self) -> str:
        return f"{self.code}: {self.detail}" if self.detail else self.code


@dataclass(frozen=True, slots=True)
class PolicySpec:
    mode: ValidityMode
    fixed_days: int | None = None
    review_required: bool = True


TARGET_POLICIES: Final[Mapping[str, PolicySpec]] = MappingProxyType({
    "tianyancha": PolicySpec(ValidityMode.FIXED_DAYS, 30),
    "ofac-sdn": PolicySpec(ValidityMode.UNTIL_REVOKED, review_required=False),
    "mofcom-entity-control": PolicySpec(ValidityMode.UNTIL_REVOKED, review_required=False),
    "bis-entity-list": PolicySpec(ValidityMode.UNTIL_REVOKED, review_required=False),
    "mofcom-entity-detail": PolicySpec(ValidityMode.UNTIL_REVOKED, review_required=False),
    "uflpa-entity-list": PolicySpec(ValidityMode.UNTIL_REVOKED, review_required=False),
    "eu-official-journal": PolicySpec(ValidityMode.FIXED_DAYS, 365),
    "eu-compliance": PolicySpec(ValidityMode.FIXED_DAYS, 365),
    "customs-announcement": PolicySpec(ValidityMode.FIXED_DAYS, 365),
    "mee-announcement": PolicySpec(ValidityMode.FIXED_DAYS, 365),
    "mem-incident-bulletin": PolicySpec(ValidityMode.FIXED_DAYS, 90),
    "wto-news": PolicySpec(ValidityMode.FIXED_DAYS, 90),
    "usgs-earthquake-day": PolicySpec(ValidityMode.FIXED_DAYS, 7),
    "fmprc-press": PolicySpec(ValidityMode.FIXED_DAYS, 30),
    "fx-rates": PolicySpec(ValidityMode.UNTIL_SUPERSEDED, 3),
    "commodity-futures": PolicySpec(ValidityMode.UNTIL_SUPERSEDED, 3),
    "sse-shipping": PolicySpec(ValidityMode.FIXED_DAYS, 14),
    "pbc-lpr": PolicySpec(ValidityMode.UNTIL_SUPERSEDED, 45),
    "stats-pmi": PolicySpec(ValidityMode.UNTIL_SUPERSEDED, 45),
})


@dataclass(frozen=True, slots=True)
class TargetPlan:
    payload: PolicyJSON
    version: str


@dataclass(frozen=True, slots=True)
class Change:
    code: str
    before: PolicyJSON | None
    after: PolicyJSON


def build_plans() -> dict[str, TargetPlan]:
    """用 SourceValidityPolicy + DataSourceUpdate 预校验每条目标策略。"""
    plans: dict[str, TargetPlan] = {}
    for code, spec in TARGET_POLICIES.items():
        policy = SourceValidityPolicy(
            mode=spec.mode, fixed_days=spec.fixed_days, review_required=spec.review_required
        )
        validated = DataSourceUpdate(validity_policy=policy).validity_policy
        if validated is None:
            raise SeedAbort("normalization_lost_policy", code)
        plans[code] = TargetPlan(payload=_payload(validated), version=validated.fingerprint())
    return plans


def _payload(policy: SourceValidityPolicy) -> PolicyJSON:
    dumped = policy.model_dump(mode="json", exclude_none=True)
    return {str(key): value for key, value in dumped.items()}


def _validate(raw: object, code: str) -> SourceValidityPolicy:
    try:
        return SourceValidityPolicy.model_validate(raw)
    except ValidationError as error:
        detail = f"{code} 有 {error.error_count()} 处校验错误"
        raise SeedAbort("policy_unparseable", detail) from error


def current_policy(source: DataSource) -> SourceValidityPolicy | None:
    """把库中 JSONB 解析为已归一化的策略对象；None 表示从未配置。"""
    raw = source.validity_policy
    return None if raw is None else _validate(raw, source.code)


def load_sources(session: Session) -> dict[str, DataSource]:
    rows = session.scalars(select(DataSource).order_by(DataSource.id)).all()
    return {source.code: source for source in rows}


def require_targets(by_code: Mapping[str, DataSource]) -> None:
    missing = sorted(set(TARGET_POLICIES) - set(by_code))
    if missing:
        raise SeedAbort("target_missing", ",".join(missing))


def require_weather_untouched(session: Session) -> None:
    """nmc-weather 只读核对：策略与 signal_validity_days 必须原样，否则停下。"""
    source = session.scalar(select(DataSource).where(DataSource.code == WEATHER_CODE))
    if source is None:
        raise SeedAbort("weather_missing", WEATHER_CODE)
    policy = current_policy(source)
    actual = None if policy is None else _payload(policy)
    if source.signal_validity_days != WEATHER_VALIDITY_DAYS or actual != WEATHER_EXPECTED:
        raise SeedAbort(
            "weather_policy_changed",
            f"signal_validity_days={source.signal_validity_days} policy={_json(actual)}",
        )


def run_dry_run() -> int:
    plans = build_plans()
    changes: list[Change] = []
    with SessionLocal() as session:
        database = _database_name(session)
        by_code = load_sources(session)
        require_targets(by_code)
        require_weather_untouched(session)
        for code, plan in plans.items():
            policy = current_policy(by_code[code])
            before = None if policy is None else _payload(policy)
            if before != plan.payload:
                changes.append(Change(code=code, before=before, after=plan.payload))
    skipped = len(plans) - len(changes)
    _print_report(database, plans, changes, mode="dry-run")
    print(f"[seed-validity] 结果: {skipped} 条已符合、{len(changes)} 条需变更；dry-run 只读未写库")
    return 0


def run_apply() -> int:
    plans = build_plans()
    batch = uuid4().hex
    with SessionLocal() as session, session.begin():
        database = _database_name(session)
        require_weather_untouched(session)
        rows = list(session.scalars(
            select(DataSource)
            .where(DataSource.code.in_(tuple(plans)))
            .order_by(DataSource.id)
            .with_for_update()
        ).all())
        if len(rows) != len(plans):
            found = {row.code for row in rows}
            raise SeedAbort("target_missing", ",".join(sorted(set(plans) - found)))
        changes: list[Change] = []
        for source in rows:
            plan = plans[source.code]
            policy = current_policy(source)
            before = None if policy is None else _payload(policy)
            if before == plan.payload:
                continue
            changes.append(Change(code=source.code, before=before, after=plan.payload))
            source.validity_policy = plan.payload
            session.add(DataSourceAuditLog(
                source_id=source.id,
                action="updated",
                actor_role=AUDIT_ACTOR_ROLE,
                actor_id=None,
                changes={
                    "validity_policy": "updated",
                    "reason": AUDIT_REASON,
                    "execution": AUDIT_EXECUTION,
                    "applies_to": APPLIES_TO,
                    "validity_policy_before": before,
                    "validity_policy_after": plan.payload,
                    "validity_policy_version": plan.version,
                    "batch": batch,
                },
            ))
    skipped = len(plans) - len(changes)
    _print_report(database, plans, changes, mode="apply")
    if changes:
        print(
            f"[seed-validity] 已单事务提交: batch={batch} "
            f"更新={len(changes)} 审计={len(changes)}"
        )
    else:
        print("[seed-validity] 无需写入：全部目标已符合，未产生更新与审计记录")
    print(f"[seed-validity] 结果: {skipped} 条已符合、{len(changes)} 条需变更")
    return 0


def run_verify() -> int:
    plans = build_plans()
    mismatches: list[str] = []
    with SessionLocal() as session:
        database = _database_name(session)
        by_code = load_sources(session)
        require_targets(by_code)
        require_weather_untouched(session)
        for code, plan in plans.items():
            policy = current_policy(by_code[code])
            if policy is None:
                mismatches.append(f"{code}:missing_policy")
            elif _payload(policy) != plan.payload:
                mismatches.append(f"{code}:policy")
            elif policy.fingerprint() != plan.version:
                mismatches.append(f"{code}:fingerprint")
    print(f"[seed-validity] mode=verify database={database} 目标={len(plans)}")
    print(
        f"[seed-validity] nmc-weather 只读核对通过：fixed_days=7, review_required=true, "
        f"signal_validity_days={WEATHER_VALIDITY_DAYS}（未改动）"
    )
    if mismatches:
        print(f"[seed-validity] 不一致 {len(mismatches)} 条: {', '.join(mismatches)}")
        raise SeedAbort("verify_failed", f"{len(mismatches)} 条策略或指纹不一致")
    print(
        f"[seed-validity] 校验通过: {len(plans)}/{len(plans)} 条；"
        f"结果: {len(plans)} 条已符合、0 条需变更"
    )
    return 0


def _database_name(session: Session) -> str:
    value: object = session.scalar(text("SELECT current_database()"))
    if not isinstance(value, str):
        raise SeedAbort("database_identity", "current_database() 返回非字符串")
    return value


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _print_report(
    database: str,
    plans: Mapping[str, TargetPlan],
    changes: Sequence[Change],
    *,
    mode: str,
) -> None:
    changed = {change.code for change in changes}
    skipped = [code for code in plans if code not in changed]
    print(f"[seed-validity] mode={mode} database={database} 目标={len(plans)}")
    for change in changes:
        print(
            f"[seed-validity] [CHANGE] {change.code} "
            f"before={_json(change.before)} after={_json(change.after)}"
        )
    print(f"[seed-validity] 已符合({len(skipped)}): {', '.join(skipped) if skipped else '无'}")
    if changes:
        print(f"[seed-validity] 需变更({len(changes)}): {', '.join(sorted(changed))}")
    else:
        print("[seed-validity] 需变更(0): 无")


def _parse_mode(argv: Sequence[str]) -> RunMode | None:
    match list(argv[1:]):
        case [] | ["--dry-run"]:
            return "dry_run"
        case ["--apply"]:
            return "apply"
        case ["--verify"]:
            return "verify"
        case _:
            return None


def main(argv: Sequence[str]) -> int:
    mode = _parse_mode(argv)
    if mode is None:
        print(
            "usage: seed_source_validity_policies.py [--dry-run|--apply|--verify]",
            file=sys.stderr,
        )
        return 2
    match mode:
        case "dry_run":
            return run_dry_run()
        case "apply":
            return run_apply()
        case "verify":
            return run_verify()
        case unreachable:
            assert_never(unreachable)


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv))
    except SeedAbort as exc:
        print(f"ABORT {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    # 顶层脚本边界：任何意外都须非 0 退出并留痕。
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        raise SystemExit(1) from None
