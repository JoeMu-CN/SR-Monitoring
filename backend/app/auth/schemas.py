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


class MeResponse(BaseModel):
    user: UserRead
    permissions: list[str]
