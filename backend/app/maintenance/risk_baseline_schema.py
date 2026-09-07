"""风险基线重建核心的数据契约：模型、允许的 FK 常量与删除顺序。

本模块只声明数据形状（Pydantic frozen 模型）与策略常量，不含任何 SQL 或 I/O；
数据库读取与删除逻辑见 ``risk_baseline_catalog`` 与 ``risk_baseline_core``。
"""

from __future__ import annotations

from pathlib import Path
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr

# —— 策略常量（与 scripts/lib/reset-risk-baseline-common.ps1 保持一致）——
ALLOWED_DATABASE_NAME: Final = "supplier_risk_reset_test"
RESTORE_DATABASE_PREFIX: Final = "supplier_risk_restore_"
ENVIRONMENT: Final = "test"
PREFLIGHT_SCHEMA: Final = "supplier-risk-monitoring/risk-baseline-preflight/v1"
EXECUTION_RECEIPT_SCHEMA: Final = "supplier-risk-monitoring/risk-baseline-execution-receipt/v1"
RESTORE_RECEIPT_SCHEMA: Final = "supplier-risk-monitoring/risk-baseline-restore-receipt/v1"
FK_ALLOWLIST_POLICY: Final = "supplier-risk-monitoring/risk-baseline-fk-allowlist/v1"
CONFIRMATION_PHRASE: Final = f"RESET RISK BASELINE {ALLOWED_DATABASE_NAME}"

# 全量清空的业务表（按 FK 安全顺序；通知投递无条件全删，含 alert_id IS NULL）。
DELETE_ORDER: Final = (
    "notification_deliveries",
    "risk_alerts",
    "supplier_event_matches",
    "risk_event_signals",
    "event_entities",
    "event_locations",
    "risk_events",
    "ai_analysis_records",
    "raw_signals",
    "collection_runs",
    "source_member_states",
)


class ForeignKeyReference(BaseModel):
    """一条引用被删除父表的外键边。"""

    model_config = ConfigDict(frozen=True)

    child_table: str
    child_column: str
    parent_table: str
    parent_column: str
    on_delete: str


type ForeignKeyParts = tuple[str, str, str, str]


def _fk(parts: ForeignKeyParts) -> ForeignKeyReference:
    child, column, parent, action = parts
    return ForeignKeyReference(
        child_table=child,
        child_column=column,
        parent_table=parent,
        parent_column="id",
        on_delete=action,
    )


# 删除集合内允许的全部外键；任何被删除父表被删除集合外的表引用均为漂移。
EXPECTED_FOREIGN_KEYS: Final = (
    _fk(("ai_analysis_records", "signal_id", "raw_signals", "CASCADE")),
    _fk(("event_entities", "event_id", "risk_events", "CASCADE")),
    _fk(("event_locations", "event_id", "risk_events", "CASCADE")),
    _fk(("notification_deliveries", "alert_id", "risk_alerts", "SET NULL")),
    _fk(("research_claims", "promoted_signal_id", "raw_signals", "SET NULL")),
    _fk(("risk_alerts", "match_id", "supplier_event_matches", "CASCADE")),
    _fk(("risk_event_signals", "event_id", "risk_events", "CASCADE")),
    _fk(("risk_event_signals", "signal_id", "raw_signals", "CASCADE")),
    _fk(("supplier_event_matches", "event_id", "risk_events", "CASCADE")),
)


class BackupReceipt(BaseModel):
    """恢复演练收据摘要（库函数输入契约）。"""

    model_config = ConfigDict(frozen=True)

    restored: bool
    backup_sha256: str
    restored_database_name: str


class RestoreReceiptBackup(BaseModel):
    model_config = ConfigDict(frozen=True)

    sha256: str
    size_bytes: int


class RestoreReceiptTarget(BaseModel):
    model_config = ConfigDict(frozen=True)

    environment: str
    database_name: str
    fingerprint: str


class RestoreReceipt(BaseModel):
    """PowerShell 落盘的恢复演练收据（execute 路径核心重新验证）。"""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    schema_name: str = Field(alias="schema", serialization_alias="schema")
    status: str
    backup: RestoreReceiptBackup
    source_target: RestoreReceiptTarget
    restored_target: RestoreReceiptTarget


class ResetRiskBaselineRequest(BaseModel):
    """由外部适配器解析后传入核心的完整门禁；database_url 以 SecretStr 承载。"""

    model_config = ConfigDict(frozen=True)

    database_url: SecretStr
    target_database_name: str
    environment: Literal["test", "staging", "production"]
    dry_run: bool = True
    confirmation_phrase: str = ""
    backup_path: Path
    backup_sha256: str
    restore_receipt: BackupReceipt
    active_writer_count: int
    approved_foreign_keys: tuple[ForeignKeyReference, ...]
    approved_plan_hash: str | None = None
    fail_after_table: str | None = None


class ResetRiskBaselineResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    dry_run: bool
    database_name: str
    database_fingerprint_sha256: str
    alembic_version: str
    planned_counts: tuple[tuple[str, int], ...]
    deleted_counts: tuple[tuple[str, int], ...]
    claim_count: int
    promoted_claim_count: int
    claim_provenance: tuple[tuple[int, int | None], ...]
    claim_provenance_sha256: str
    preserved_counts: tuple[tuple[str, int], ...]
    plan_sha256: str


class CriticalTableCounts(BaseModel):
    model_config = ConfigDict(frozen=True)

    raw_signals: int
    ai_analysis_records: int
    risk_events: int
    risk_alerts: int
    notification_deliveries: int
    notification_runtime_state: int = 0
    collection_runs: int
    source_member_states: int
    research_claims: int
    research_claims_promoted_nonnull: int


class TargetDescriptor(BaseModel):
    model_config = ConfigDict(frozen=True)

    environment: Literal["test"]
    database_name: str
    fingerprint: str
    content_fingerprint: str
    migration_version: str
    critical_table_counts: CriticalTableCounts


class ForeignKeyInventory(BaseModel):
    model_config = ConfigDict(frozen=True)

    policy: Literal["supplier-risk-monitoring/risk-baseline-fk-allowlist/v1"]
    status: Literal["passed", "failed"]
    observed_edges: tuple[str, ...]
    missing_edges: tuple[str, ...]
    unexpected_edges: tuple[str, ...]


class TargetSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    target: TargetDescriptor
    fk_inventory: ForeignKeyInventory
    plan_sha256: str
    claim_provenance_sha256: str


class CliRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    operation: Literal["preflight", "execute"]
    environment: Literal["test", "staging", "production"]
    database_name: str
    output: Literal["json"]
    expected_fingerprint: str | None = None
    expected_plan_sha256: str | None = None
    confirm_phrase: str | None = None
    backup_path: Path | None = None
    restore_receipt_path: Path | None = None
    active_writer_count: int | None = None


class ExecutionReceipt(BaseModel):
    """execute 成功后输出的机器可读收据（PowerShell 契约）。"""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    schema_name: str = Field(alias="schema", serialization_alias="schema")
    status: Literal["completed"]
    dry_run: bool
    environment: Literal["test"]
    database_name: str
    pre_reset_fingerprint: str
    pre_reset_plan_sha256: str
    deleted_counts: tuple[tuple[str, int], ...]
    preserved_counts: tuple[tuple[str, int], ...]
    claim_count: int
    promoted_claim_count: int
    claim_provenance_sha256: str


class ResetGateError(RuntimeError):
    """携带机器可消费错误码的硬门禁失败。"""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)
