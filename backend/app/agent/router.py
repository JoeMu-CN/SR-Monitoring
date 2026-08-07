from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.agent.engine import AgentConfigurationError, AgentError, get_agent_llm
from app.agent.schemas import AgentStatusRead, ChatRequest, ChatResponse
from app.agent.service import chat
from app.config import AGENT_MAX_STEPS, AGENT_TYC_ENABLED, get_ai_settings
from app.database import get_session

router = APIRouter(prefix="/api/v1", tags=["风险查询助手"])
SessionDependency = Annotated[Session, Depends(get_session)]


@router.post("/chat", response_model=ChatResponse)
async def chat_endpoint(
    payload: ChatRequest, session: SessionDependency
) -> ChatResponse:
    try:
        return await chat(session, payload.question, session_id=payload.session_id)
    except AgentConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except AgentError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"风险查询助手暂时不可用：{exc}",
        ) from exc


@router.get("/agent/status", response_model=AgentStatusRead)
def agent_status() -> AgentStatusRead:
    settings = get_ai_settings()
    llm = get_agent_llm(settings)
    configured = settings.provider != "fake"
    return AgentStatusRead(
        llm_configured=configured,
        model=llm.model,
        tyc_enabled=AGENT_TYC_ENABLED,
        max_steps=AGENT_MAX_STEPS,
    )
