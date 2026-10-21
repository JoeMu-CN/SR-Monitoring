"""认证相关 Pydantic 模型。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=128)


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    email: str | None = None
    display_name: str | None = None
    role: str
    status: str
    last_login_at: datetime | None = None
    created_at: datetime
    # 已分配负责供应商数量（仅列表页展示，完整列表走 /users/{id}/suppliers）。
    supplier_count: int = 0


class UserSupplierRead(BaseModel):
    """负责供应商条目：只暴露列表页需要的三个字段。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    supplier_code: str
    legal_name: str


class UserSupplierUpdate(BaseModel):
    """全量替换负责供应商；空数组表示清空。"""

    model_config = ConfigDict(extra="forbid")

    supplier_ids: list[int] = Field(default_factory=list)


class UserCreate(BaseModel):
    username: str = Field(min_length=2, max_length=128)
    password: str = Field(min_length=8, max_length=128)
    display_name: str | None = Field(default=None, max_length=128)
    email: str | None = Field(default=None, max_length=256)
    role: str = Field(default="viewer", pattern="^(viewer|risk_analyst|risk_admin|platform_admin)$")


class UserUpdate(BaseModel):
    role: str | None = Field(
        default=None, pattern="^(viewer|risk_analyst|risk_admin|platform_admin)$"
    )
    status: str | None = Field(default=None, pattern="^(pending|active|disabled)$")
    display_name: str | None = Field(default=None, max_length=128)
    email: str | None = Field(default=None, max_length=256)


class PasswordChange(BaseModel):
    old_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class PasswordResetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    new_password: str = Field(min_length=8, max_length=128)


class MeResponse(BaseModel):
    user: UserRead
    permissions: list[str]
