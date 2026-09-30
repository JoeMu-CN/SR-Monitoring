"""天眼查批量核查共享模型（计划 Todo 7）：错误层级、逐工具计数与稳定汇总。

从 ``tyc_batch`` 拆出以控制模块规模；本模块只依赖 pydantic/标准库，可被
``app.signals.schemas`` 等低层模块安全导入，不引入 signals/scheduler 依赖。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict

# 计划 D8：分片固定为 2 片，不随维度数/供应商数变化。
SHARD_COUNT = 2

# 逐工具五态（与 Todo 5/6 维度状态及额度执行器结果对齐）。
ToolOutcome = Literal["success_with_records", "empty", "error", "quota_exhausted", "busy"]


class TycBatchError(Exception):
    """批量核查错误基类：稳定 ``code`` + 结构化上下文（API 409 载荷使用）。"""

    code = "tyc_batch_error"

    def __init__(self, message: str, **context: object) -> None:
        super().__init__(message)
        self.context = context


class TycBatchNotTianyancha(TycBatchError):
    code = "not_tianyancha"


class TycBatchSourceInactive(TycBatchError):
    code = "source_inactive"


class TycBatchUnavailable(TycBatchError):
    code = "unavailable"


class TycBatchShardError(TycBatchError):
    code = "invalid_shard"


class TycBatchBucketGateRejected(TycBatchError):
    code = "bucket_gate_rejected"


class TycBatchLocked(TycBatchError):
    code = "batch_locked"


class TycBatchSupplierNotFound(TycBatchError):
    code = "supplier_not_found"


class ToolOutcomeCounts(BaseModel):
    """单个工具的逐结果计数（成功有记录/空/错误/额度耗尽/繁忙）。"""

    model_config = ConfigDict(extra="forbid")

    success_with_records: int = 0
    empty: int = 0
    error: int = 0
    quota_exhausted: int = 0
    busy: int = 0


@dataclass(frozen=True)
class TycBatchResult:
    """一次批量/单供应商核查的稳定汇总（API 与调度共用）。"""

    source_id: int
    shard_index: int
    shard_count: int
    supplier_id: int | None
    targeted_count: int
    attempted_count: int
    created_count: int
    duplicate_count: int
    empty_count: int
    failed_count: int
    quota_exhausted: bool
    per_tool_counts: dict[str, ToolOutcomeCounts]


@dataclass
class BatchAccumulator:
    """运行期累计器；逐供应商逐工具累计，最后冻结为 ``TycBatchResult``。"""

    source_id: int
    shard_index: int
    shard_count: int
    targeted_count: int
    supplier_id: int | None = None
    attempted_count: int = 0
    created_count: int = 0
    duplicate_count: int = 0
    empty_count: int = 0
    failed_count: int = 0
    quota_exhausted: bool = False
    per_tool_counts: dict[str, ToolOutcomeCounts] = field(default_factory=dict)

    def record_tool(self, tool_name: str, outcome: ToolOutcome) -> None:
        counts = self.per_tool_counts.get(tool_name)
        if counts is None:
            counts = ToolOutcomeCounts()
            self.per_tool_counts[tool_name] = counts
        setattr(counts, outcome, getattr(counts, outcome) + 1)

    def freeze(self) -> TycBatchResult:
        return TycBatchResult(
            source_id=self.source_id,
            shard_index=self.shard_index,
            shard_count=self.shard_count,
            supplier_id=self.supplier_id,
            targeted_count=self.targeted_count,
            attempted_count=self.attempted_count,
            created_count=self.created_count,
            duplicate_count=self.duplicate_count,
            empty_count=self.empty_count,
            failed_count=self.failed_count,
            quota_exhausted=self.quota_exhausted,
            per_tool_counts=dict(self.per_tool_counts),
        )
