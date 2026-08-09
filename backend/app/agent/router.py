from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.agent.budget import get_tyc_usage
from app.agent.engine import AgentConfigurationError, AgentError, get_agent_llm
from app.agent.schemas import AgentStatusRead, ChatRequest, ChatResponse
from app.agent.service import chat, chat_source_onboarding
from app.config import AGENT_MAX_STEPS, get_ai_settings
from app.database import get_session
from app.security import require_admin

router = APIRouter(prefix="/api/v1", tags=["风险查询助手"])
source_agent_router = APIRouter(prefix="/api/v1", tags=["数据源接入助手"])
SessionDependency = Annotated[Session, Depends(get_session)]


@router.post("/chat", response_model=ChatResponse)
async def chat_endpoint(
    payload: ChatRequest,
    session: SessionDependency,
) -> ChatResponse:
    try:
        return await chat(
            session,
            payload.question,
            session_id=payload.session_id,
        )
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


@source_agent_router.post("/source-agent/chat", response_model=ChatResponse)
async def source_agent_chat_endpoint(
    payload: ChatRequest,
    session: SessionDependency,
    _admin: Annotated[str, Depends(require_admin)],
    actor_id: Annotated[str | None, Header(alias="X-User-Id")] = None,
) -> ChatResponse:
    try:
        return await chat_source_onboarding(
            session,
            payload.question,
            session_id=payload.session_id,
            actor_id=actor_id,
        )
    except AgentConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except AgentError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"数据源接入助手暂时不可用：{exc}",
        ) from exc


@router.get("/agent/status", response_model=AgentStatusRead)
def agent_status(session: SessionDependency) -> AgentStatusRead:
    settings = get_ai_settings()
    llm = get_agent_llm(settings)
    configured = settings.provider != "fake"
    return AgentStatusRead(
        llm_configured=configured,
        model=llm.model,
        tyc_enabled=get_tyc_usage(session).enabled,
        max_steps=AGENT_MAX_STEPS,
    )
