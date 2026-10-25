"""清理旧版国家柱匹配留下的误匹配（事件涉及国 → 同国全部供应商）。

在 app 容器内执行；`app` 包已随镜像安装，不是独立 PEP 723 脚本。

一、用途
    旧版 `match_countries` 读 `locations[].country_code`（事件发生地/公告涉及国），
    一旦政策公告涉及 CN，就会把全部中国供应商拉进匹配，匹配理由文案为
    「事件涉及国家与供应商国别/生产地一致：CN」。现版本只读 AI 判定的
    `affected_countries`（国家级系统性风险），文案改为「事件受影响国家范围…」。

    这批旧匹配在 2026-10-13 的历史政策回填中一次性产出 1089 条 match 与 1089 条
    `risk_alerts` 行（rule_version `risk-score-v1-policy-*`），随后全部置为
    `expired`。按当前规则重算这些信号，匹配数为 0——它们不是漏报，而是旧规则的
    误匹配残留。

二、为什么要清
    - `risk_alerts.expires_at` 是事件有效期（2026-12 至 2027-10），保留清理以
      `expires_at` 为删除锚点（见 `app/scheduler/retention_queries.py`），
      这批 `expired` 行永远满足不了 `anchor < cutoff`，会长期滞留；
    - `retained_alert_condition` 因此持续把这些事件与信号纳入受保护闭包；
    - 供应商删除影响面统计计入全部状态的 match，历史数字被虚增。

三、边界与安全
    - 只匹配旧版文案「事件涉及国家与供应商国别/生产地一致」；
    - 只删除「全部提醒都是 expired」的 match，出现任何 current 提醒即跳过并计数；
    - 事件（`risk_events`）、信号（`raw_signals`）、供应商均不动；
    - 默认 dry-run 只读统计；`--apply` 才删除，删除 match 时由外键
      `ON DELETE CASCADE` 一并删除其 expired 提醒；
    - 幂等：删除后不再命中候选集，重复 dry-run 候选数应为 0；
    - 不采集、不调用 AI、不重启服务、不打印任何 .env / 连接串 / 密码。

四、运行方式（先 dry-run，确认后再 --apply）
    docker compose cp scripts/cleanup_legacy_country_matches.py app:/tmp/cleanup.py
    docker compose exec -T app python /tmp/cleanup.py
    docker compose exec -T app python /tmp/cleanup.py --apply

五、退出码
    0：dry-run 完成，或 apply 全部成功；
    1：存在被跳过的候选（含 current 提醒）或脚本级异常；
    2：命令行参数错误（argparse 默认行为）。
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from collections.abc import Sequence
from typing import Final

from sqlalchemy import Text, cast, delete, func, select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.risks.models import RiskAlert, RiskEvent, SupplierEventMatch

LOG_PREFIX: Final = "[cleanup-legacy-country]"
LEGACY_REASON_FRAGMENT: Final = "事件涉及国家与供应商国别/生产地一致"
BATCH_SIZE: Final = 500


def _legacy_match_condition():
    """旧版国家匹配条件：reasons 数组文本包含旧文案。"""
    return cast(SupplierEventMatch.reasons, Text).like(f"%{LEGACY_REASON_FRAGMENT}%")


def _candidate_rows(session: Session) -> Sequence[SupplierEventMatch]:
    return list(
        session.scalars(
            select(SupplierEventMatch)
            .where(_legacy_match_condition())
            .order_by(SupplierEventMatch.id)
        )
    )


def collect(session: Session) -> tuple[list[SupplierEventMatch], dict[str, int]]:
    """把候选分成可删除与跳过两类，返回 (可删除 match, 摘要计数)。"""
    candidates = _candidate_rows(session)
    deletable: list[SupplierEventMatch] = []
    skipped_with_current = 0
    with_alerts = 0
    orphan = 0
    alert_total = 0
    event_ids: set[int] = set()
    for match in candidates:
        states = list(
            session.scalars(select(RiskAlert.status).where(RiskAlert.match_id == match.id))
        )
        if any(state != "expired" for state in states):
            skipped_with_current += 1
            continue
        if states:
            with_alerts += 1
            alert_total += len(states)
        else:
            orphan += 1
        deletable.append(match)
        event_ids.add(match.event_id)
    summary = {
        "candidates": len(candidates),
        "deletable_matches": len(deletable),
        "deletable_alerts": alert_total,
        "cascaded_orphan_matches": orphan,
        "skipped_current_alerts": skipped_with_current,
        "affected_events": len(event_ids),
    }
    return deletable, summary


def _anchor_range(session: Session, match_ids: Sequence[int]) -> dict[str, str | None]:
    """可删除提醒的有效期范围，用于确认这批数据不会自然到期清理。"""
    if not match_ids:
        return {"expires_at_min": None, "expires_at_max": None}
    row = session.execute(
        select(
            func.min(RiskAlert.expires_at),
            func.max(RiskAlert.expires_at),
        ).where(RiskAlert.match_id.in_(match_ids))
    ).one()
    return {
        "expires_at_min": row[0].isoformat() if row[0] else None,
        "expires_at_max": row[1].isoformat() if row[1] else None,
    }


def _event_state(session: Session, event_ids: Sequence[int]) -> dict[str, int]:
    """删除前后事件本身不变：仅统计其有效性，便于核对未误删事件。"""
    if not event_ids:
        return {"events": 0, "events_active": 0}
    rows = session.execute(
        select(
            func.count(),
            func.count().filter(RiskEvent.validity_state == "active"),
        ).where(RiskEvent.id.in_(event_ids))
    ).one()
    return {"events": int(rows[0]), "events_active": int(rows[1])}


def apply_delete(session: Session, match_ids: Sequence[int]) -> int:
    """分批删除 match；risk_alerts.match_id 的 ON DELETE CASCADE 一并清理提醒。"""
    deleted = 0
    for start in range(0, len(match_ids), BATCH_SIZE):
        batch = list(match_ids[start : start + BATCH_SIZE])
        result = session.execute(delete(SupplierEventMatch).where(SupplierEventMatch.id.in_(batch)))
        session.commit()
        deleted += result.rowcount or 0
        print(
            f"{LOG_PREFIX} 已删除批次 {start // BATCH_SIZE + 1}: match {result.rowcount} 条",
            flush=True,
        )
    return deleted


def run(session: Session, *, apply: bool) -> dict[str, object]:
    deletable, summary = collect(session)
    match_ids = [match.id for match in deletable]
    event_ids = sorted({match.event_id for match in deletable})
    report: dict[str, object] = {
        "mode": "apply" if apply else "dry-run",
        **summary,
        **_anchor_range(session, match_ids),
        **_event_state(session, event_ids),
    }
    if not apply:
        print(f"{LOG_PREFIX} {json.dumps(report, ensure_ascii=False)}", flush=True)
        return report
    report["deleted_matches"] = apply_delete(session, match_ids)
    remaining = session.scalar(
        select(func.count()).select_from(SupplierEventMatch).where(_legacy_match_condition())
    )
    report["remaining_candidates"] = int(remaining or 0)
    report["events_after_delete"] = _event_state(session, event_ids)["events"]
    print(f"{LOG_PREFIX} {json.dumps(report, ensure_ascii=False)}", flush=True)
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="清理旧版国家柱匹配产生的误匹配（默认 dry-run）")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="实际删除可清理的 match 及其 expired 提醒；缺省只统计",
    )
    args = parser.parse_args(argv)

    session = SessionLocal()
    try:
        report = run(session, apply=args.apply)
        return 1 if int(report.get("skipped_current_alerts", 0)) else 0
    except Exception:
        session.rollback()
        print(f"{LOG_PREFIX} 执行失败", file=sys.stderr, flush=True)
        traceback.print_exc()
        return 1
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
