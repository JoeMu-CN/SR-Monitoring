"""持久化的 Scheduler 运行时观测状态。

本表只记录调度进程自我观测的运行元数据（心跳、上一轮结论、稳定错误码），
供只读健康聚合接口消费。它不参与任何业务事务，写入必须走独立短事务。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, get_args

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Integer, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

# status 只允许这四个稳定取值：idle=已注册未运行 / running=本轮进行中 /
# succeeded=上一轮业务结论成功 / failed=上一轮业务结论失败。
RUNTIME_STATUS_VALUES: tuple[str, ...] = ("idle", "running", "succeeded", "failed")
_RUNTIME_STATUS_SQL = ", ".join(f"'{value}'" for value in RUNTIME_STATUS_VALUES)

# 当前工作枚举（只读健康聚合展示“调度器正在做什么”）：
# source_collection=单源采集 / pending_signal_processing=待处理信号批次。
# tuple 由 Literal 派生，避免 DB 约束与 API 契约出现双份声明漂移。
CurrentWorkKind = Literal["source_collection", "pending_signal_processing"]
CurrentWorkStage = Literal["collecting", "processing_signal"]
CURRENT_WORK_KIND_VALUES: tuple[str, ...] = get_args(CurrentWorkKind)
CURRENT_WORK_STAGE_VALUES: tuple[str, ...] = get_args(CurrentWorkStage)
_CURRENT_WORK_KIND_SQL = ", ".join(f"'{value}'" for value in CURRENT_WORK_KIND_VALUES)
_CURRENT_WORK_STAGE_SQL = ", ".join(f"'{value}'" for value in CURRENT_WORK_STAGE_VALUES)


class SchedulerRuntimeState(Base):
    __tablename__ = "scheduler_runtime_state"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({_RUNTIME_STATUS_SQL})",
            name="ck_scheduler_runtime_state_status",
        ),
        CheckConstraint(
            "processed_count >= 0 AND filtered_count >= 0 AND failed_count >= 0",
            name="ck_scheduler_runtime_state_counts_non_negative",
        ),
        CheckConstraint(
            "error_code IS NULL OR error_code ~ '^[a-z][a-z0-9_]{0,63}$'",
            name="ck_scheduler_runtime_state_error_code_stable",
        ),
        CheckConstraint(
            f"current_kind IS NULL OR current_kind IN ({_CURRENT_WORK_KIND_SQL})",
            name="ck_scheduler_runtime_state_current_kind",
        ),
        CheckConstraint(
            f"current_stage IS NULL OR current_stage IN ({_CURRENT_WORK_STAGE_SQL})",
            name="ck_scheduler_runtime_state_current_stage",
        ),
        CheckConstraint(
            "current_item_id IS NULL OR current_item_id > 0",
            name="ck_scheduler_runtime_state_current_item_positive",
        ),
    )

    # job_key 作为主键：固定观测点 scheduler / pending_signals / collect:<source_id>。
    job_key: Mapped[str] = mapped_column(Text, primary_key=True)
    status: Mapped[str] = mapped_column(Text, server_default=text("'idle'"))
    # 时间列一律 UTC、可空：从未运行时保持 NULL 而非填入占位时间。
    last_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 成功时间仅在该轮业务无处理异常后更新；失败不改动它。
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processed_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    filtered_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    failed_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    # 仅允许稳定脱敏码（正则同迁移约束）；绝不写入异常文本或堆栈。
    error_code: Mapped[str | None] = mapped_column(Text)
    # 当前工作观测：任务开始时登记，结束（result/neutral）时清空；只存标识与枚举。
    current_kind: Mapped[str | None] = mapped_column(Text)
    current_stage: Mapped[str | None] = mapped_column(Text)
    current_item_id: Mapped[int | None] = mapped_column(BigInteger)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
