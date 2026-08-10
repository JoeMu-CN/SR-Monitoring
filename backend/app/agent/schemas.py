from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    session_id: int | None = None
    draft_id: int | None = None


class ToolCallInfo(BaseModel):
    name: str
    arguments: dict[str, object]
    result: dict[str, object]


class SourceOnboardingDraftRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    agent_session_id: int | None
    source_id: int | None
    actor_id: str | None
    current_step: str
    answers: dict[str, str]
    created_at: datetime
    updated_at: datetime


class SourceOnboardingDraftBoxItem(BaseModel):
    kind: str
    title: str
    detail: str
    draft_id: int | None = None
    session_id: int | None = None
    source_id: int | None = None
    source_code: str | None = None
    current_step: str | None = None
    updated_at: datetime


class SourceOnboardingDraftBoxResponse(BaseModel):
    items: list[SourceOnboardingDraftBoxItem]


class ChatResponse(BaseModel):
    session_id: int
    answer: str
    tool_calls: list[ToolCallInfo]
    onboarding_draft: SourceOnboardingDraftRead | None = None


class AgentStatusRead(BaseModel):
    llm_configured: bool
    model: str
    tyc_enabled: bool
    max_steps: int
