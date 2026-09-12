"""任务11：隔离备份/恢复演练的独立非 pytest 校验器。

- capture 模式：对源库生成确定性基线（迁移版本、表计数、逐行摘要、
  证据链闭合、暂停供应商、用户角色状态）。
- verify 模式：对恢复库重复采集并与基线逐项比较，输出 passed。
- 不依赖 pytest，不 import app.database，也不使用其 engine/session：
  本脚本用 create_engine(database_url, future=True) 自建连接。
- 不硬编码业务表/列名：覆盖模型显式导入（导入失败则记 skipped），
  列与主键一律来自 SQLAlchemy inspect(Model)。

CLI：
  python tests/verify_hardening_restore.py --mode capture \
      --database-url <url> --output /test-evidence/baseline.json
  python tests/verify_hardening_restore.py --mode verify \
      --database-url <url> --baseline /test-evidence/baseline.json \
      --output /test-evidence/verify.json
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Final
from uuid import UUID

from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.engine import Connection, Engine, make_url

CAPTURE_SCHEMA: Final = "supplier-risk-monitoring/hardening-restore-capture/v1"
VERIFY_SCHEMA: Final = "supplier-risk-monitoring/hardening-restore-verify/v1"

# 覆盖模型清单：模块 + 类名；任一导入失败只记 skipped，不伪造数据。
MODEL_SPECS: Final = (
    ("app.suppliers.models", "Supplier"),
    ("app.suppliers.models", "SupplierSite"),
    ("app.suppliers.models", "SupplierProduct"),
    ("app.suppliers.models", "SupplierAlias"),
    ("app.risks.models", "RiskEvent"),
    ("app.risks.models", "SupplierEventMatch"),
    ("app.risks.models", "RiskAlert"),
    ("app.risks.models", "RiskEventSignal"),
    ("app.signals.models", "RawSignal"),
    ("app.ai.models", "AIAnalysisRecord"),
    ("app.signals.models", "DataSource"),
    ("app.signals.models", "CollectionRun"),
    ("app.auth.models", "User"),
    ("app.notification.models", "NotificationDelivery"),
)

COMPARISON_KEYS: Final = (
    "migration_version",
    "table_counts",
    "row_digests",
    "evidence_closure",
    "paused_suppliers",
    "user_role_status",
)


def _load_models() -> tuple[dict[str, Any], dict[str, str]]:
    """按 MODEL_SPECS 导入模型；失败降级为 skipped，不阻断其余模型。"""
    models: dict[str, Any] = {}
    skipped: dict[str, str] = {}
    modules: dict[str, Any] = {}
    for module_name, class_name in MODEL_SPECS:
        if class_name in models:
            continue
        module = modules.get(module_name)
        if module is None:
            try:
                module = importlib.import_module(module_name)
            except Exception as error:  # noqa: BLE001 - 边界处必须降级为 skipped
                skipped[class_name] = f"import {module_name} failed: {error}"
                continue
            modules[module_name] = module
        model = getattr(module, class_name, None)
        if model is None:
            skipped[class_name] = f"{class_name} not found in {module_name}"
            continue
        models[class_name] = model
    return models, skipped


def _normalize_value(value: Any) -> Any:
    """把数据库值归一化为可稳定序列化的 JSON 结构。"""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, memoryview):
        return bytes(value).hex()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _normalize_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_normalize_value(item) for item in value]
    return str(value)


def _table_snapshot(connection: Connection, model: Any) -> tuple[str, int, list[str]]:
    """返回（表名, 行数, 按主键升序的逐行 SHA-256 列表）。

    列名来自 inspect(Model).columns，天然排除 ctid 等系统列；
    每行按列名排序拼 JSON 后做 SHA-256。
    """
    mapper = inspect(model)
    columns = [column for column in mapper.columns]
    ordered_columns = sorted(columns, key=lambda column: column.key)
    statement = select(*columns)
    primary_key = [column for column in mapper.primary_key]
    if primary_key:
        statement = statement.order_by(*(column.asc() for column in primary_key))
    digests: list[str] = []
    for row in connection.execute(statement):
        mapping = row._mapping
        payload = {
            column.key: _normalize_value(mapping[column.key])
            for column in ordered_columns
        }
        serialized = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )
        digests.append(hashlib.sha256(serialized.encode("utf-8")).hexdigest())
    table_name = str(mapper.local_table.name)
    count = connection.execute(select(func.count()).select_from(model)).scalar_one()
    return table_name, int(count), digests


def _migration_version(connection: Connection) -> str | None:
    inspector = inspect(connection)
    if not inspector.has_table("alembic_version"):
        return None
    value = connection.execute(
        text("SELECT version_num FROM alembic_version")
    ).scalar_one_or_none()
    return None if value is None else str(value)


def _evidence_closure(connection: Connection, models: dict[str, Any]) -> dict[str, Any]:
    """对每个 current alert 校验 alert→match→event→signal 链路非空。"""
    required = (
        "RiskAlert",
        "SupplierEventMatch",
        "RiskEvent",
        "RiskEventSignal",
        "RawSignal",
    )
    missing = sorted(name for name in required if name not in models)
    if missing:
        return {
            "status": "skipped",
            "reason": "missing models: " + ", ".join(missing),
            "checked_alert_ids": [],
            "orphan_alert_ids": [],
        }
    risk_alert = models["RiskAlert"]
    supplier_event_match = models["SupplierEventMatch"]
    risk_event = models["RiskEvent"]
    risk_event_signal = models["RiskEventSignal"]
    raw_signal = models["RawSignal"]

    checked: list[int] = []
    orphans: list[int] = []
    alert_rows = connection.execute(
        select(risk_alert.id, risk_alert.match_id)
        .where(risk_alert.status == "current")
        .order_by(risk_alert.id)
    ).all()
    for alert_id, match_id in alert_rows:
        alert_identifier = int(alert_id)
        checked.append(alert_identifier)
        if match_id is None:
            orphans.append(alert_identifier)
            continue
        event_id = connection.execute(
            select(supplier_event_match.event_id).where(
                supplier_event_match.id == match_id
            )
        ).scalar_one_or_none()
        if event_id is None or (
            connection.execute(
                select(risk_event.id).where(risk_event.id == event_id)
            ).scalar_one_or_none()
            is None
        ):
            orphans.append(alert_identifier)
            continue
        linked_signals = list(
            connection.execute(
                select(risk_event_signal.signal_id).where(
                    risk_event_signal.event_id == event_id
                )
            ).scalars()
        )
        if not linked_signals:
            orphans.append(alert_identifier)
            continue
        resolved_signal = connection.execute(
            select(raw_signal.id).where(raw_signal.id.in_(linked_signals)).limit(1)
        ).scalar_one_or_none()
        if resolved_signal is None:
            orphans.append(alert_identifier)
    return {
        "status": "ok",
        "reason": None,
        "checked_alert_ids": checked,
        "orphan_alert_ids": orphans,
    }


def _paused_suppliers(connection: Connection, models: dict[str, Any]) -> list[int] | None:
    supplier = models.get("Supplier")
    if supplier is None:
        return None
    rows = connection.execute(
        select(supplier.id).where(supplier.enabled.is_(False)).order_by(supplier.id)
    ).scalars()
    return [int(item) for item in rows]


def _user_role_status(
    connection: Connection, models: dict[str, Any]
) -> dict[str, str] | None:
    user = models.get("User")
    if user is None:
        return None
    rows = connection.execute(
        select(user.id, user.role, user.status).order_by(user.id)
    ).all()
    return {str(row[0]): f"{row[1]}/{row[2]}" for row in rows}


def _collect(connection: Connection, database_name: str) -> dict[str, Any]:
    models, skipped = _load_models()
    table_counts: dict[str, int] = {}
    row_digests: dict[str, list[str]] = {}
    for model in models.values():
        table_name, count, digests = _table_snapshot(connection, model)
        table_counts[table_name] = count
        row_digests[table_name] = digests
    return {
        "schema": CAPTURE_SCHEMA,
        "mode": "capture",
        "database": database_name,
        "migration_version": _migration_version(connection),
        "table_counts": table_counts,
        "row_digests": row_digests,
        "evidence_closure": _evidence_closure(connection, models),
        "paused_suppliers": _paused_suppliers(connection, models),
        "user_role_status": _user_role_status(connection, models),
        "skipped": skipped,
    }


def _mismatched_tables(expected: Any, actual: Any) -> list[str]:
    expected_map = expected if isinstance(expected, dict) else {}
    actual_map = actual if isinstance(actual, dict) else {}
    names = set(expected_map) | set(actual_map)
    return sorted(name for name in names if expected_map.get(name) != actual_map.get(name))


def _compare_documents(
    baseline: dict[str, Any], restored: dict[str, Any]
) -> tuple[dict[str, Any], bool]:
    comparisons: dict[str, Any] = {}
    for key in COMPARISON_KEYS:
        expected = baseline.get(key)
        actual = restored.get(key)
        item: dict[str, Any] = {"passed": expected == actual}
        if key in ("table_counts", "row_digests"):
            item["mismatched_tables"] = _mismatched_tables(expected, actual)
        comparisons[key] = item
    passed = all(bool(item["passed"]) for item in comparisons.values())
    return comparisons, passed


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="隔离备份/恢复演练校验器（capture / verify）"
    )
    parser.add_argument("--mode", required=True, choices=("capture", "verify"))
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--baseline")
    arguments = parser.parse_args(argv)
    if arguments.mode == "verify" and not arguments.baseline:
        parser.error("verify 模式必须提供 --baseline。")
    return arguments


def _write_json(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True)
    path.write_text(f"{payload}\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    arguments = _parse_args(argv)
    database_url = str(arguments.database_url)
    database_name = make_url(database_url).database or ""
    engine: Engine = create_engine(database_url, future=True)
    try:
        with engine.connect() as connection:
            restored = _collect(connection, database_name)
    finally:
        engine.dispose()

    if arguments.mode == "capture":
        document = restored
    else:
        baseline_path = Path(str(arguments.baseline))
        baseline_document = json.loads(baseline_path.read_text(encoding="utf-8"))
        comparisons, passed = _compare_documents(baseline_document, restored)
        document = {
            "schema": VERIFY_SCHEMA,
            "mode": "verify",
            "baseline": {
                "path": str(baseline_path),
                "database": baseline_document.get("database"),
                "migration_version": baseline_document.get("migration_version"),
            },
            "restored": restored,
            "comparisons": comparisons,
            "passed": passed,
        }
    _write_json(Path(str(arguments.output)), document)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as error:  # noqa: BLE001 - CLI 边界统一转为退出码 1
        print(f"verify_hardening_restore 执行失败：{error}", file=sys.stderr)
        raise SystemExit(1) from error
