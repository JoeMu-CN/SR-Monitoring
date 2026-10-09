"""持久化的 Scheduler 排期快照与每次调度执行历史。

两张表只记录调度进程自我观测的排期与执行元数据（稳定状态与 UTC 时间戳），
供只读健康聚合接口消费；它们不参与任何业务事务，写入必须走独立短事务。

- ``scheduler_job_registry``：真实排期快照。``next_run_at`` 来自 Scheduler 进程的
  ``Job.next_run_time``（Web 不推算）；job 被移除时同步删除该行。
- ``scheduler_job_run``：每次调度执行历史。事件幂等键为
  ``(job_id, scheduled_run_at UTC)``；``status`` 仅允许
  ``running/completed/error/missed/max_instances`` —— ``completed`` 只表示
  callable 正常返回，绝不命名为 ``succeeded``。绝不保存 exception、traceback、
  retval、信号标题正文、凭据或其他敏感数据。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, get_args

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Index,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

# 稳定状态枚举（tuple 由 Literal 派生，避免 DB 约束与 API 契约双份声明漂移）。
JobRunStatus = Literal["running", "completed", "error", "missed", "max_instances"]
JOB_RUN_STATUS_VALUES: tuple[str, ...] = get_args(JobRunStatus)
_JOB_RUN_STATUS_SQL = ", ".join(f"'{value}'" for value in JOB_RUN_STATUS_VALUES)


class SchedulerJobRegistry(Base):
    __tablename__ = "scheduler_job_registry"

    # job_id 作为主键：与 APScheduler ``Job.id`` 一一对应。
    job_id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    # 时间列一律 UTC：无下一次触发（暂停等）时保持 NULL，不推算。
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SchedulerJobRun(Base):
    __tablename__ = "scheduler_job_run"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({_JOB_RUN_STATUS_SQL})",
            name="ck_scheduler_job_run_status",
        ),
        # 事件幂等键：同一 (job_id, scheduled_run_at) 只允许一行，并发由 DB 兜底。
        UniqueConstraint(
            "job_id", "scheduled_run_at", name="uq_scheduler_job_run_job_scheduled"
        ),
        Index("ix_scheduler_job_run_scheduled_run_at", "scheduled_run_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    scheduled_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # 从未登记开始/结束的终态（missed/max_instances、乱序先到）保持 NULL，不编造。
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
