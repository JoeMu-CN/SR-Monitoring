"""两类 Agent 的独立会话编排。"""

import re
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
from app.agent.models import AgentMessage, AgentSession, SourceOnboardingDraft
from app.agent.schemas import ChatResponse, SourceOnboardingDraftRead
from app.agent.source_skill import ONBOARDING_STEPS, STEP_QUESTIONS, build_source_onboarding_skill
from app.agent.source_tools import build_source_onboarding_tools
from app.agent.tools import Tool, build_tools

HISTORY_WINDOW = 8
RISK_QUERY = "risk_query"
SOURCE_ONBOARDING = "source_onboarding"
START_ONBOARDING = "开始新的数据源接入"
RESUME_ONBOARDING = "继续当前数据源接入"
_HTTPS_URL = re.compile(r"https://[^\s<>\"']+", re.IGNORECASE)
_SENSITIVE_TEXT = re.compile(
    r"(?i)(bearer\s+|token\s*[=:]\s*|api[_-]?key\s*[=:]\s*|password\s*[=:]\s*|"
    r"secret\s*[=:]\s*)([^\s,;，；]+)"
)


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
    draft_id: int | None = None,
    llm: AgentLLM | None = None,
    actor_id: str | None = None,
) -> ChatResponse:
    """数据源接入 Agent：仅加载接入工具，发布和采集需要当前消息确认。"""
    active_session, draft = _load_or_create_onboarding_draft(
        session,
        session_id=session_id,
        draft_id=draft_id,
        actor_id=actor_id,
    )
    if question.strip() == START_ONBOARDING and not draft.answers:
        return _record_onboarding_prompt(
            session,
            active_session,
            draft,
            question,
            STEP_QUESTIONS["source_url"],
        )
    if question.strip() == RESUME_ONBOARDING:
        return _record_onboarding_prompt(
            session,
            active_session,
            draft,
            question,
            STEP_QUESTIONS.get(draft.current_step, "请继续当前接入配置。"),
        )
    if not _save_onboarding_answer(draft, question):
        return _record_onboarding_prompt(
            session,
            active_session,
            draft,
            question,
            STEP_QUESTIONS[draft.current_step],
        )
    tools = cast(
        list[Tool],
        build_source_onboarding_tools(
            actor_id=actor_id,
            allow_publish="确认发布" in question,
            allow_run="立即采集" in question,
            onboarding_draft_id=draft.id,
            allow_create=draft.current_step == "generate_adapter",
        ),
    )
    response = await _chat(
        session,
        question,
        session_id=active_session.id,
        llm=llm,
        agent_kind=SOURCE_ONBOARDING,
        tools=tools,
        system_prompt=build_source_onboarding_skill(
            current_step=draft.current_step,
            answers=draft.answers,
        ),
        persisted_question=_redact_sensitive_text(question),
    )
    session.refresh(draft)
    return response.model_copy(
        update={"onboarding_draft": SourceOnboardingDraftRead.model_validate(draft)}
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
    persisted_question: str | None = None,
) -> ChatResponse:
    active_session = _load_or_create_session(session, session_id, agent_kind)
    session.add(
        AgentMessage(
            session_id=active_session.id,
            role="user",
            content=persisted_question or question,
        )
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
            content=_redact_sensitive_text(result.answer),
            tool_calls=tool_records,
        )
    )
    session.commit()
    return ChatResponse(
        session_id=active_session.id,
        answer=_redact_sensitive_text(result.answer),
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


def _load_or_create_onboarding_draft(
    session: Session,
    *,
    session_id: int | None,
    draft_id: int | None,
    actor_id: str | None,
) -> tuple[AgentSession, SourceOnboardingDraft]:
    if draft_id is not None:
        draft = session.get(SourceOnboardingDraft, draft_id)
        if draft is None:
            raise AgentError("数据源接入草稿不存在")
        if draft.source_id is not None:
            raise AgentError("该草稿已生成正式数据源，请在数据源管理页继续操作")
        if draft.agent_session_id is None:
            active_session = _load_or_create_session(session, session_id, SOURCE_ONBOARDING)
            draft.agent_session_id = active_session.id
            session.commit()
            return active_session, draft
        if session_id is not None and session_id != draft.agent_session_id:
            raise AgentError("草稿与会话不匹配")
        return _load_or_create_session(session, draft.agent_session_id, SOURCE_ONBOARDING), draft

    active_session = _load_or_create_session(session, session_id, SOURCE_ONBOARDING)
    draft = session.scalar(
        select(SourceOnboardingDraft).where(
            SourceOnboardingDraft.agent_session_id == active_session.id
        )
    )
    if draft is None:
        draft = SourceOnboardingDraft(
            agent_session_id=active_session.id,
            actor_id=actor_id,
            current_step="source_url",
            answers={},
        )
        session.add(draft)
        session.commit()
        session.refresh(draft)
    return active_session, draft


def _save_onboarding_answer(draft: SourceOnboardingDraft, question: str) -> bool:
    if draft.current_step not in ONBOARDING_STEPS or draft.current_step == "generate_adapter":
        return True
    cleaned = _onboarding_answer(draft.current_step, question)
    if not cleaned:
        return False
    answers = dict(draft.answers)
    answers[draft.current_step] = cleaned
    draft.answers = answers
    draft.current_step = ONBOARDING_STEPS[
        ONBOARDING_STEPS.index(draft.current_step) + 1
    ]
    return True


def _onboarding_answer(step: str, question: str) -> str | None:
    text = _redact_sensitive_text(question).strip()
    if step == "source_url":
        match = _HTTPS_URL.search(question)
        return match.group(0).rstrip("。；，,.)]") if match else None
    return text or None


def _record_onboarding_prompt(
    session: Session,
    active_session: AgentSession,
    draft: SourceOnboardingDraft,
    question: str,
    answer: str,
) -> ChatResponse:
    session.add_all(
        [
            AgentMessage(
                session_id=active_session.id,
                role="user",
                content=_redact_sensitive_text(question),
            ),
            AgentMessage(
                session_id=active_session.id,
                role="assistant",
                content=answer,
            ),
        ]
    )
    session.commit()
    session.refresh(draft)
    return ChatResponse(
        session_id=active_session.id,
        answer=answer,
        tool_calls=[],
        onboarding_draft=SourceOnboardingDraftRead.model_validate(draft),
    )


def _redact_sensitive_text(text: str) -> str:
    return _SENSITIVE_TEXT.sub(lambda match: f"{match.group(1)}***", text)
