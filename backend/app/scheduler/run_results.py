"""调度任务返回值的受限投影：只接受结构化摘要，不保存原始 retval。

调度 job 的返回值完全由业务代码决定，可能包含正文、联系方式、凭据等任意内容。
本模块不做自由文本透传：只有显式构造的 :class:`RunResult`（或结构等价且通过
校验的 dict）才会被规范化为可持久化的摘要；其他任何值（异常对象、字符串、
数字、任意 dict）一律返回 ``None``，绝不向上抛错。

摘要本身也受硬限制：``summary`` 最多 160 字，``metrics``/``outputs`` 各最多
12 项，每项 label 最多 24 字、value 是非负整数。额外字段按 Pydantic 默认语义
忽略，保证不同版本 job 增加字段时不会破坏写库。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

# 摘要字段的硬上限：足以展示“处理了多少条/产出什么”，又不至于成为日志通道。
MAX_SUMMARY_LENGTH = 160
MAX_RESULT_ITEMS = 12
MAX_METRIC_LABEL_LENGTH = 24
MAX_METRIC_VALUE = 1_000_000


class RunResultMetric(BaseModel):
    """一项计数指标：稳定短标签 + 非负整数，不含任意文本或路径。"""

    model_config = ConfigDict(extra="ignore")

    label: str = Field(min_length=1, max_length=MAX_METRIC_LABEL_LENGTH)
    value: int = Field(ge=0, le=MAX_METRIC_VALUE)


class RunResult(BaseModel):
    """一次调度执行的脱敏结果摘要；绝不承载原始 retval、正文或异常文本。"""

    model_config = ConfigDict(extra="ignore")

    summary: str = Field(min_length=1, max_length=MAX_SUMMARY_LENGTH)
    metrics: list[RunResultMetric] = Field(
        default_factory=list, max_length=MAX_RESULT_ITEMS
    )
    outputs: list[RunResultMetric] = Field(
        default_factory=list, max_length=MAX_RESULT_ITEMS
    )


def normalize_run_result(value: object | None) -> dict[str, object] | None:
    """把 job 返回值收敛为可持久化的脱敏摘要；不可信值一律返回 ``None``。

    接受 ``RunResult`` 实例或能通过 ``RunResult.model_validate`` 的 dict，
    返回 JSON 兼容的普通 dict（供 JSONB 列写入）；其他类型与校验失败
    （超长、负数、超过条数上限等）都不抛异常，直接视为“无摘要”。
    """
    if isinstance(value, RunResult):
        model = value
    elif isinstance(value, dict):
        try:
            model = RunResult.model_validate(value)
        except Exception:
            return None
    else:
        return None
    return model.model_dump(mode="json")
