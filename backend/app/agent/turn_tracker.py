"""Agent 单次运行的步骤追踪（进程内内存存储）。

前端在等待回答期间用 ``run_token`` 轮询真实执行步骤——只记录确实发生的模型调用
与工具执行，不采集也不展示模型隐藏思维链。部署为单进程 uvicorn，因此普通
``dict`` 即可，无需加锁；每次访问惰性清理过期项。
"""

import time
from dataclasses import dataclass, field

from app.agent.schemas import AgentRunStepsRead, AgentStepRead

#: 步骤存活上限（秒），自最近一次更新起算。
RUN_TTL_SECONDS = 600.0

STEP_ANALYZING = "analyzing"
STEP_TOOL_START = "tool_start"
STEP_TOOL_DONE = "tool_done"
STEP_FINALIZING = "finalizing"

#: 步骤类型白名单，供契约校验与文档使用。
STEP_KINDS = frozenset({STEP_ANALYZING, STEP_TOOL_START, STEP_TOOL_DONE, STEP_FINALIZING})


@dataclass
class _RunState:
    owner_user_id: int
    updated_at: float
    finished: bool = False
    steps: list[AgentStepRead] = field(default_factory=list)


_RUNS: dict[str, _RunState] = {}


def _purge_expired(now: float) -> None:
    expired = [
        token
        for token, state in list(_RUNS.items())
        if now - state.updated_at > RUN_TTL_SECONDS
    ]
    for token in expired:
        del _RUNS[token]


def start_run(run_token: str, owner_user_id: int) -> None:
    """登记一次新运行，记录归属用户以便后续做 owner 校验。"""
    now = time.monotonic()
    _purge_expired(now)
    _RUNS[run_token] = _RunState(owner_user_id=owner_user_id, updated_at=now)


def record_step(run_token: str, kind: str, tool: str | None = None) -> None:
    """追加一条步骤；index 从 1 递增。未知 token 静默忽略，绝不抛异常。"""
    state = _RUNS.get(run_token)
    if state is None:
        return
    state.steps.append(
        AgentStepRead(index=len(state.steps) + 1, kind=kind, tool=tool, detail=None)
    )
    state.updated_at = time.monotonic()


def finish_run(run_token: str) -> None:
    """标记运行结束；未知 token 静默忽略。"""
    state = _RUNS.get(run_token)
    if state is None:
        return
    state.finished = True
    state.updated_at = time.monotonic()


def get_run(run_token: str, owner_user_id: int) -> AgentRunStepsRead | None:
    """读取步骤快照；不存在、已过期或 owner 不匹配时返回 None。"""
    now = time.monotonic()
    _purge_expired(now)
    state = _RUNS.get(run_token)
    if state is None or state.owner_user_id != owner_user_id:
        return None
    return AgentRunStepsRead(
        run_token=run_token,
        status="done" if state.finished else "running",
        steps=list(state.steps),
    )
