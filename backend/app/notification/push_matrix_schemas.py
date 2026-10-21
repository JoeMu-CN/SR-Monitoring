"""推送管理矩阵的请求/响应模型（见《风险提醒邮件推送接入方案.md》9.1-B）。

矩阵是用户推送方案的唯一维护入口：行 = 全部用户，列 = P1/P2/P3/月报。
P4 不提供开关（无推送价值），viewer 行照常返回但四列一律 false。
"""

from pydantic import BaseModel, ConfigDict, Field


class PushMatrixRow(BaseModel):
    """矩阵行：用户基本信息 + 四个开关 + 已分配供应商数量。"""

    user_id: int
    username: str
    display_name: str | None = None
    role: str
    email: str | None = None
    push_p1: bool = False
    push_p2: bool = False
    push_p3: bool = False
    push_monthly_report: bool = False
    supplier_count: int = 0


class PushMatrixUpdate(BaseModel):
    """单行更新：未提交的行保持不变，因此四个开关均为可选。"""

    model_config = ConfigDict(extra="forbid")

    user_id: int
    push_p1: bool = False
    push_p2: bool = False
    push_p3: bool = False
    push_monthly_report: bool = False


class PushMatrixUpdateRequest(BaseModel):
    """批量保存请求：只更新提交行，便于前端整表一次提交。"""

    model_config = ConfigDict(extra="forbid")

    items: list[PushMatrixUpdate] = Field(default_factory=list)
