from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.agent.budget import get_tyc_usage
from app.agent.engine import AgentConfigurationError, AgentError, get_agent_llm
from app.agent.models import SourceOnboardingDraft
from app.agent.schemas import (
    AgentStatusRead,
    ChatRequest,
    ChatResponse,
    SourceOnboardingDraftBoxItem,
    SourceOnboardingDraftBoxResponse,
)
from app.agent.service import chat, chat_source_onboarding
from app.config import AGENT_MAX_STEPS, get_ai_settings
from app.database import get_session
from app.security import require_admin
from app.signals.models import DataSource

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
            draft_id=payload.draft_id,
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


@source_agent_router.get(
    "/source-agent/drafts", response_model=SourceOnboardingDraftBoxResponse
)
def list_source_onboarding_drafts(
    session: SessionDependency,
    _admin: Annotated[str, Depends(require_admin)],
) -> SourceOnboardingDraftBoxResponse:
    """统一列出接入中草稿、待发布适配器和待启用数据源。"""
    in_progress = list(
        session.scalars(
            select(SourceOnboardingDraft)
            .where(SourceOnboardingDraft.source_id.is_(None))
            .order_by(SourceOnboardingDraft.updated_at.desc())
        )
    )
    items = [
        SourceOnboardingDraftBoxItem(
            kind="in_progress",
            title=draft.answers.get("source_identity_schedule") or "未命名数据源接入",
            detail=f"接入进度：{_step_label(draft.current_step)}",
            draft_id=draft.id,
            session_id=draft.agent_session_id,
            current_step=draft.current_step,
            updated_at=draft.updated_at,
        )
        for draft in in_progress
    ]
    pending_sources = list(
        session.scalars(
            select(DataSource)
            .where(
                or_(
                    DataSource.adapter_status == "draft",
                    and_(
                        DataSource.adapter_status == "published",
                        DataSource.enabled.is_(False),
                    ),
                )
            )
            .order_by(DataSource.updated_at.desc())
        )
    )
    items.extend(
        SourceOnboardingDraftBoxItem(
            kind=(
                "adapter_draft"
                if source.adapter_status == "draft"
                else "pending_enable"
            ),
            title=source.name,
            detail=(
                "适配器草稿，尚未发布"
                if source.adapter_status == "draft"
                else "适配器已发布，等待管理员启用"
            ),
            source_id=source.id,
            source_code=source.code,
            updated_at=source.updated_at,
        )
        for source in pending_sources
    )
    return SourceOnboardingDraftBoxResponse(items=items)


@source_agent_router.delete(
    "/source-agent/drafts/{draft_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_source_onboarding_draft(
    draft_id: int,
    session: SessionDependency,
    _admin: Annotated[str, Depends(require_admin)],
) -> None:
    """删除接入中草稿；已生成正式数据源的草稿不允许删除。"""
    draft = session.get(SourceOnboardingDraft, draft_id)
    if draft is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="接入草稿不存在"
        )
    if draft.source_id is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="该草稿已生成正式数据源，请在数据源管理页操作",
        )
    session.delete(draft)
    session.commit()


def _step_label(step: str) -> str:
    return {
        "source_url": "等待官方地址",
        "collection_goal": "等待采集字段与业务目标",
        "access_authorization": "等待授权条件",
        "source_identity_schedule": "等待名称、编码和周期",
        "generate_adapter": "等待探测与适配器预览",
    }.get(step, "处理中")


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
