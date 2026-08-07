"""Agent 对话编排：会话持久化 + 推理循环 + 审计记录。"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.engine import AgentLLM, get_agent_llm, run_agent
from app.agent.models import AgentMessage, AgentSession
from app.agent.schemas import ChatResponse
from app.agent.tools import build_tools

HISTORY_WINDOW = 8


async def chat(
    session: Session,
    question: str,
    *,
    session_id: int | None = None,
    llm: AgentLLM | None = None,
) -> ChatResponse:
    active_session = _load_or_create_session(session, session_id)
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
        tools=build_tools(),
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


def _load_or_create_session(session: Session, session_id: int | None) -> AgentSession:
    if session_id is not None:
        existing = session.get(AgentSession, session_id)
        if existing is not None:
            return existing
    created = AgentSession()
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
