"""两类 Agent 的独立会话编排。"""

from typing import cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.engine import (
    AGENT_SYSTEM_PROMPT,
    AgentError,
    AgentLLM,
    get_agent_llm,
    run_agent,
)
from app.agent.models import AgentMessage, AgentSession
from app.agent.schemas import ChatResponse
from app.agent.source_skill import SOURCE_ONBOARDING_SKILL
from app.agent.source_tools import build_source_onboarding_tools
from app.agent.tools import Tool, build_tools

HISTORY_WINDOW = 8
RISK_QUERY = "risk_query"
SOURCE_ONBOARDING = "source_onboarding"


async def chat(
    session: Session,
    question: str,
    *,
    session_id: int | None = None,
    llm: AgentLLM | None = None,
) -> ChatResponse:
    """风险查询 Agent：永久只读，不加载数据源写入工具。"""
    return await _chat(
        session,
        question,
        session_id=session_id,
        llm=llm,
        agent_kind=RISK_QUERY,
        tools=build_tools(),
        system_prompt=AGENT_SYSTEM_PROMPT,
    )


async def chat_source_onboarding(
    session: Session,
    question: str,
    *,
    session_id: int | None = None,
    llm: AgentLLM | None = None,
    actor_id: str | None = None,
) -> ChatResponse:
    """数据源接入 Agent：仅加载接入工具，发布和采集需要当前消息确认。"""
    tools = cast(
        list[Tool],
        build_source_onboarding_tools(
            actor_id=actor_id,
            allow_publish="确认发布" in question,
            allow_run="立即采集" in question,
        ),
    )
    return await _chat(
        session,
        question,
        session_id=session_id,
        llm=llm,
        agent_kind=SOURCE_ONBOARDING,
        tools=tools,
        system_prompt=SOURCE_ONBOARDING_SKILL,
    )


async def _chat(
    session: Session,
    question: str,
    *,
    session_id: int | None,
    llm: AgentLLM | None,
    agent_kind: str,
    tools: list[Tool],
    system_prompt: str,
) -> ChatResponse:
    active_session = _load_or_create_session(session, session_id, agent_kind)
    session.add(
        AgentMessage(session_id=active_session.id, role="user", content=question)
    )
    session.commit()

    history = _recent_history(session, active_session.id)
    active_llm = llm or get_agent_llm()
    result = await run_agent(
        session,
        question,
        history,
        llm=active_llm,
        tools=tools,
        system_prompt=system_prompt,
    )

    tool_records = [
        {
            "name": call.name,
            "arguments": call.arguments,
            "result": call.result,
        }
        for call in result.tool_calls
    ]
    session.add(
        AgentMessage(
            session_id=active_session.id,
            role="assistant",
            content=result.answer,
            tool_calls=tool_records,
        )
    )
    session.commit()
    return ChatResponse(
        session_id=active_session.id,
        answer=result.answer,
        tool_calls=result.tool_calls,
    )


def _load_or_create_session(
    session: Session, session_id: int | None, agent_kind: str
) -> AgentSession:
    if session_id is not None:
        existing = session.get(AgentSession, session_id)
        if existing is not None:
            if existing.agent_kind != agent_kind:
                raise AgentError("会话属于另一 Agent，不能跨类型复用")
            return existing
    created = AgentSession(agent_kind=agent_kind)
    session.add(created)
    session.commit()
    session.refresh(created)
    return created


def _recent_history(session: Session, session_id: int) -> list[dict[str, str]]:
    rows = session.scalars(
        select(AgentMessage)
        .where(AgentMessage.session_id == session_id)
        .order_by(AgentMessage.id.desc())
        .limit(HISTORY_WINDOW)
    ).all()
    return [
        {"role": message.role, "content": message.content}
        for message in reversed(rows)
    ]
