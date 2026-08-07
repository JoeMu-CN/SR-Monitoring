from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    session_id: int | None = None


class ToolCallInfo(BaseModel):
    name: str
    arguments: dict[str, object]
    result: dict[str, object]


class ChatResponse(BaseModel):
    session_id: int
    answer: str
    tool_calls: list[ToolCallInfo]


class AgentStatusRead(BaseModel):
    llm_configured: bool
    model: str
    tyc_enabled: bool
    max_steps: int
