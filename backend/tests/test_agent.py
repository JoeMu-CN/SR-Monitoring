"""Agent 模块测试：FakeAgentLLM 全链路，不访问真实模型接口。"""

import asyncio
import hashlib
import json
from collections.abc import Callable, Generator
from datetime import UTC, datetime, timedelta, tzinfo
from typing import ClassVar
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from pytest import MonkeyPatch
from risk_validity_fixtures import SignalSpec, linked_risk
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

import app.agent.budget as budget_module
from app.agent.budget import get_tyc_usage, record_tyc_usage
from app.agent.engine import AgentError, FakeAgentLLM, LLMResponse, ToolCallSpec, run_agent
from app.agent.models import (
    AgentMessage,
    AgentSession,
    SourceOnboardingDraft,
    TycUsageRecord,
)
from app.agent.service import (
    RESUME_ONBOARDING,
    RISK_QUERY,
    SOURCE_ONBOARDING,
    START_ONBOARDING,
    chat,
    chat_source_onboarding,
)
from app.agent.tools import (
    GetBudgetTool,
    QueryCurrentAlertsTool,
    QuerySuppliersTool,
    VerifyCompanyTool,
    build_tools,
)
from app.agent.tyc_gateway import (
    McpTycGateway,
    TycGatewayError,
    UnconfiguredTycGateway,
    build_tyc_gateway,
)
from app.auth.models import User
from app.database import engine
from app.signals.models import DataSource, RawSignal
from app.suppliers.models import Supplier


@pytest.fixture
def clean_agent_tables(db_session: Session) -> Session:
    db_session.execute(delete(SourceOnboardingDraft))
    db_session.execute(delete(AgentMessage))
    db_session.execute(delete(AgentSession))
    db_session.execute(delete(TycUsageRecord))
    configured_tyc = db_session.scalar(
        select(DataSource).where(DataSource.code == "tianyancha")
    )
    if configured_tyc is not None:
        configured_tyc.enabled = False
        configured_tyc.api_key_hash = None
        configured_tyc.api_key_last4 = None
        configured_tyc.api_key_encrypted = None
    if db_session.get(User, 1) is None:
        db_session.add(
            User(
                id=1,
                username="agent-service-owner",
                password_hash="not-used",
                role="risk_admin",
                status="active",
            )
        )
    db_session.flush()
    return db_session


class FakeTycGateway:
    """测试用网关：status 可配置，模拟天眼查返回。"""

    def __init__(self, status: str = "success") -> None:
        self.status = status
        self.calls: list[str] = []
        self.dimension_calls: list[str] = []

    async def verify(self, company_name: str) -> dict[str, object]:
        self.calls.append(company_name)
        if self.status == "success":
            return {
                "status": "success",
                "company_name": company_name,
                "reg_status": "存续",
                "lawsuits": 0,
            }
        if self.status == "empty":
            return {"status": "empty", "message": "无该企业相关记录"}
        if self.status == "error":
            raise RuntimeError("天眼查服务暂时不可用")
        return {"status": "error", "message": "未知错误"}

    async def fetch_dimensions(self, company_name: str) -> dict[str, object]:
        """清单内实时完整核查入口：记录调用并返回确定性 fetch 合同。"""
        self.dimension_calls.append(company_name)
        if self.status == "not_configured":
            return {
                "status": "not_configured",
                "company_name": company_name,
                "credit_code": None,
                "reg_status": None,
                "dimensions": {},
            }
        return {
            "status": "success",
            "company_name": company_name,
            "credit_code": "91310000FAKE00001",
            "reg_status": "存续",
            "dimensions": {},
        }


@pytest.fixture
def enable_tyc(db_session: Session, monkeypatch: MonkeyPatch) -> None:
    from app.signals.secret_store import encrypt_secret

    source = db_session.scalar(select(DataSource).where(DataSource.code == "tianyancha"))
    if source is None:
        source = DataSource(
            code="tianyancha",
            name="天眼查企业核查",
            source_type="external_tool",
            credibility=90,
            schedule=None,
            endpoint_url="https://mcp.tianyancha.com/v1",
            auth_type="api_key",
            login_config={},
            credential_ref=None,
            description="测试信息源",
            enabled=True,
        )
        db_session.add(source)
    # 运行密钥改为控制台加密存库，不再依赖环境变量
    source.enabled = True
    source.api_key_encrypted = encrypt_secret("tyc_test_key")
    source.api_key_hash = hashlib.sha256(b"tyc_test_key").hexdigest()
    source.api_key_last4 = "key"
    db_session.flush()
    source.login_config = {
        "mode": "on_demand",
        "secret_source": "console",
        "daily_limit": 5,
        "monthly_limit": 50,
    }


def _seed_tyc_signal(
    session: Session,
    supplier: Supplier,
    *,
    title: str,
    content: str,
    raw_payload: dict[str, object],
) -> RawSignal:
    """直接落一条天眼查信号：读取侧用例的前置数据（不经过周度报告写入）。"""
    source = session.scalar(select(DataSource).where(DataSource.code == "tianyancha"))
    assert source is not None
    signal = RawSignal(
        source_id=source.id,
        external_id=f"tyc-{supplier.supplier_code}-{uuid4().hex[:12]}",
        title=title,
        content=content,
        fingerprint=hashlib.sha256(
            f"{supplier.supplier_code}|{title}|{uuid4().hex}".encode()
        ).hexdigest(),
        raw_data=raw_payload,
    )
    session.add(signal)
    session.flush()
    return signal


def test_chat_creates_session_and_persists_messages(
    clean_agent_tables: Session,
) -> None:
    response = asyncio.run(
        chat(
            clean_agent_tables,
            "今天有什么风险？",
            llm=FakeAgentLLM(),
            owner_user_id=1,
        )
    )
    assert response.session_id > 0
    assert "Fake" in response.answer
    assert [call.name for call in response.tool_calls] == ["query_current_alerts"]

    messages = list(
        clean_agent_tables.scalars(
            select(AgentMessage)
            .where(AgentMessage.session_id == response.session_id)
            .order_by(AgentMessage.id)
        )
    )
    assert [message.role for message in messages] == ["user", "assistant"]
    assistant = messages[1]
    assert assistant.tool_calls[0]["name"] == "query_current_alerts"
    assert assistant.tool_calls[0]["result"]["total"] == 0
    assert clean_agent_tables.get(AgentSession, response.session_id).agent_kind == RISK_QUERY


def test_chat_continues_existing_session(clean_agent_tables: Session) -> None:
    first = asyncio.run(
        chat(
            clean_agent_tables,
            "今天有什么风险？",
            llm=FakeAgentLLM(),
            owner_user_id=1,
        )
    )
    second = asyncio.run(
        chat(
            clean_agent_tables,
            "再帮我查一下供应商",
            session_id=first.session_id,
            llm=FakeAgentLLM(),
            owner_user_id=1,
        )
    )
    assert second.session_id == first.session_id
    count = len(
        clean_agent_tables.scalars(
            select(AgentMessage).where(AgentMessage.session_id == first.session_id)
        ).all()
    )
    assert count == 4


def test_chat_without_risk_keyword_calls_no_tool(
    clean_agent_tables: Session,
) -> None:
    response = asyncio.run(
        chat(
            clean_agent_tables, "你好", llm=FakeAgentLLM(), owner_user_id=1
        )
    )
    assert response.tool_calls == []


def test_two_agents_use_disjoint_tools_and_sessions(
    clean_agent_tables: Session,
) -> None:
    class CapturingLLM(FakeAgentLLM):
        def __init__(self) -> None:
            self.tool_names: set[str] = set()

        async def respond(
            self,
            messages: list[dict[str, object]],
            tools: list[dict[str, object]],
        ) -> LLMResponse:
            del messages
            self.tool_names = {
                str(tool["function"]["name"])
                for tool in tools
                if isinstance(tool.get("function"), dict)
            }
            return LLMResponse(content="隔离测试完成")

    risk_llm = CapturingLLM()
    risk = asyncio.run(
        chat(
            clean_agent_tables,
            "接入信息源并确认发布立即采集",
            llm=risk_llm,
            owner_user_id=1,
        )
    )
    source_llm = CapturingLLM()
    started = asyncio.run(
        chat_source_onboarding(
            clean_agent_tables,
            START_ONBOARDING,
            llm=source_llm,
            owner_user_id=1,
        )
    )
    source = asyncio.run(
        chat_source_onboarding(
            clean_agent_tables,
            "https://official.example/notices",
            session_id=started.session_id,
            draft_id=started.onboarding_draft.id if started.onboarding_draft else None,
            llm=source_llm,
            owner_user_id=1,
        )
    )

    assert risk_llm.tool_names == {
        "query_suppliers",
        "query_current_alerts",
        "verify_company",
        "get_budget",
    }
    assert source_llm.tool_names == {
        "inspect_source_url",
        "preview_source_adapter",
    }
    assert risk_llm.tool_names.isdisjoint(source_llm.tool_names)
    assert clean_agent_tables.get(AgentSession, risk.session_id).agent_kind == RISK_QUERY
    assert (
        clean_agent_tables.get(AgentSession, source.session_id).agent_kind
        == SOURCE_ONBOARDING
    )

    with pytest.raises(AgentError, match="不能跨类型复用"):
        asyncio.run(
            chat_source_onboarding(
                clean_agent_tables,
                "继续",
                session_id=risk.session_id,
                llm=source_llm,
                owner_user_id=1,
            )
        )
    with pytest.raises(AgentError, match="不能跨类型复用"):
        asyncio.run(
            chat(
                clean_agent_tables,
                "继续",
                session_id=source.session_id,
                llm=risk_llm,
                owner_user_id=1,
            )
        )


def test_source_onboarding_saves_one_step_and_redacts_secret(
    clean_agent_tables: Session,
) -> None:
    started = asyncio.run(
        chat_source_onboarding(
            clean_agent_tables,
            START_ONBOARDING,
            llm=FakeAgentLLM(),
            owner_user_id=1,
        )
    )
    assert started.onboarding_draft is not None
    assert started.onboarding_draft.current_step == "source_url"

    url_step = asyncio.run(
        chat_source_onboarding(
            clean_agent_tables,
            "请接入 https://official.example/notices",
            session_id=started.session_id,
            draft_id=started.onboarding_draft.id,
            llm=FakeAgentLLM(),
            owner_user_id=1,
        )
    )
    goal_step = asyncio.run(
        chat_source_onboarding(
            clean_agent_tables,
            "采集标题、正文、发布时间和详情链接，用于识别自然灾害风险",
            session_id=url_step.session_id,
            draft_id=url_step.onboarding_draft.id if url_step.onboarding_draft else None,
            llm=FakeAgentLLM(),
            owner_user_id=1,
        )
    )
    auth_step = asyncio.run(
        chat_source_onboarding(
            clean_agent_tables,
            "Bearer token=super-secret-value，正式接入使用 env:OFFICIAL_TOKEN",
            session_id=goal_step.session_id,
            draft_id=goal_step.onboarding_draft.id if goal_step.onboarding_draft else None,
            llm=FakeAgentLLM(),
            owner_user_id=1,
        )
    )
    assert auth_step.onboarding_draft is not None
    assert auth_step.onboarding_draft.current_step == "source_identity_schedule"
    assert "super-secret-value" not in auth_step.onboarding_draft.answers[
        "access_authorization"
    ]
    assert "***" in auth_step.onboarding_draft.answers["access_authorization"]
    contents = list(
        clean_agent_tables.scalars(
            select(AgentMessage.content).where(
                AgentMessage.session_id == started.session_id
            )
        )
    )
    assert all("super-secret-value" not in content for content in contents)


def test_source_onboarding_resume_restores_step_without_advancing(
    clean_agent_tables: Session,
) -> None:
    started = asyncio.run(
        chat_source_onboarding(
            clean_agent_tables,
            START_ONBOARDING,
            llm=FakeAgentLLM(),
            owner_user_id=1,
        )
    )
    assert started.onboarding_draft is not None
    draft_id = started.onboarding_draft.id
    answered = asyncio.run(
        chat_source_onboarding(
            clean_agent_tables,
            "https://official.example/notices",
            session_id=started.session_id,
            draft_id=draft_id,
            llm=FakeAgentLLM(),
            owner_user_id=1,
        )
    )
    assert answered.onboarding_draft is not None
    assert answered.onboarding_draft.current_step == "collection_goal"

    # 中途退出后仅凭 draft_id 恢复：重新提出当前问题且不推进步骤
    resumed = asyncio.run(
        chat_source_onboarding(
            clean_agent_tables,
            RESUME_ONBOARDING,
            draft_id=draft_id,
            llm=FakeAgentLLM(),
            owner_user_id=1,
        )
    )
    assert resumed.onboarding_draft is not None
    assert resumed.onboarding_draft.current_step == "collection_goal"
    assert "第 2 步" in resumed.answer

    # 草稿与会话不匹配被拒绝
    other = asyncio.run(
        chat_source_onboarding(
            clean_agent_tables,
            START_ONBOARDING,
            llm=FakeAgentLLM(),
            owner_user_id=1,
        )
    )
    with pytest.raises(AgentError, match="草稿与会话不匹配"):
        asyncio.run(
            chat_source_onboarding(
                clean_agent_tables,
                RESUME_ONBOARDING,
                session_id=started.session_id,
                draft_id=other.onboarding_draft.id if other.onboarding_draft else None,
                llm=FakeAgentLLM(),
                owner_user_id=1,
            )
        )


def test_source_onboarding_completed_draft_cannot_resume(
    clean_agent_tables: Session,
) -> None:
    started = asyncio.run(
        chat_source_onboarding(
            clean_agent_tables,
            START_ONBOARDING,
            llm=FakeAgentLLM(),
            owner_user_id=1,
        )
    )
    assert started.onboarding_draft is not None
    source = DataSource(
        code="completed-draft-source",
        name="已生成信息源的草稿",
        source_type="official_api",
        credibility=80,
        endpoint_url="https://official.example/events",
        enabled=False,
    )
    clean_agent_tables.add(source)
    clean_agent_tables.flush()
    draft = clean_agent_tables.get(SourceOnboardingDraft, started.onboarding_draft.id)
    assert draft is not None
    draft.source_id = source.id
    clean_agent_tables.flush()

    with pytest.raises(AgentError, match="已生成正式信息源"):
        asyncio.run(
            chat_source_onboarding(
                clean_agent_tables,
                RESUME_ONBOARDING,
                draft_id=draft.id,
                llm=FakeAgentLLM(),
                owner_user_id=1,
            )
        )


def test_run_agent_max_steps_guard(clean_agent_tables: Session) -> None:
    class LoopingLLM(FakeAgentLLM):
        async def respond(
            self,
            messages: list[dict[str, object]],
            tools: list[dict[str, object]],
        ) -> LLMResponse:
            return LLMResponse(tool_calls=[ToolCallSpec("get_budget", {})])

    with pytest.raises(AgentError, match="最大步数"):
        asyncio.run(
            run_agent(
                clean_agent_tables,
                "今天有什么风险？",
                [],
                llm=LoopingLLM(),
                tools=build_tools(),
                max_steps=2,
            )
        )


def test_query_suppliers_tool(clean_agent_tables: Session) -> None:
    clean_agent_tables.add(
        Supplier(
            supplier_code="SUP-0001",
            legal_name="测试供应商有限公司",
            country_code="CN",
            registry_no="91310000TEST00001",
        )
    )
    clean_agent_tables.flush()
    result = asyncio.run(QuerySuppliersTool().execute({"keyword": "测试"}, clean_agent_tables))
    assert result["total"] == 1
    assert result["items"][0]["legal_name"] == "测试供应商有限公司"  # type: ignore[index]


def test_query_alerts_tool_returns_empty(clean_agent_tables: Session) -> None:
    result = asyncio.run(QueryCurrentAlertsTool().execute({}, clean_agent_tables))
    assert result["total"] == 0


def test_verify_company_defaults_to_not_configured(
    clean_agent_tables: Session,
) -> None:
    result = asyncio.run(
        VerifyCompanyTool().execute({"company_name": "某科技有限公司"}, clean_agent_tables)
    )
    assert result["status"] == "not_configured"


def test_verify_company_charges_on_success(
    db_session: Session, committed_tyc_source: None
) -> None:
    """实时核查经共享执行器记账：提交态配置，独立连接可见扣费事实。"""
    _configure_committed_tyc(daily_limit=5, monthly_limit=50)
    gateway = FakeTycGateway(status="success")
    tool = VerifyCompanyTool(gateway=gateway)
    result = asyncio.run(
        tool.execute({"company_name": "某科技有限公司"}, db_session)
    )
    assert result["status"] == "success"
    assert result["company_name"] == "某科技有限公司"
    usage = result["usage"]
    assert isinstance(usage, dict)
    assert usage["daily_used"] == 1
    assert usage["daily_limit"] == 5
    assert gateway.calls == ["某科技有限公司"]
    assert _committed_tyc_rows() == [("verify_company", "某科技有限公司", "success")]
    assert _committed_tyc_daily_used() == 1


def test_verify_company_quota_exhausted_blocks_call(
    db_session: Session, committed_tyc_source: None
) -> None:
    _configure_committed_tyc(daily_limit=5, monthly_limit=50)
    # 先塞满当日额度（真提交，执行器锁内重读可见）
    with Session(engine) as seeded:
        for i in range(5):
            seeded.add(
                TycUsageRecord(
                    tool_name="verify_company",
                    company_name=f"C{i}",
                    status="success",
                )
            )
        seeded.commit()
    gateway = FakeTycGateway(status="success")
    tool = VerifyCompanyTool(gateway=gateway)
    result = asyncio.run(
        tool.execute({"company_name": "新公司"}, db_session)
    )
    assert result["status"] == "quota_exhausted"
    assert "天眼查额度已达上限" in str(result["message"])
    usage = result["usage"]
    assert isinstance(usage, dict)
    assert usage["daily_used"] == 5
    assert usage["daily_remaining"] == 0
    assert gateway.calls == []  # 超额不调远程
    assert _committed_tyc_daily_used() == 5


def test_verify_company_error_does_not_charge(
    db_session: Session, committed_tyc_source: None
) -> None:
    _configure_committed_tyc(daily_limit=5, monthly_limit=50)
    gateway = FakeTycGateway(status="error")
    tool = VerifyCompanyTool(gateway=gateway)
    result = asyncio.run(
        tool.execute({"company_name": "某公司"}, db_session)
    )
    # 网关异常按不计费 error 记账并独立提交
    assert result["status"] == "error"
    assert "天眼查服务暂时不可用" in str(result["message"])
    assert _committed_tyc_rows() == [("verify_company", "某公司", "error")]
    assert _committed_tyc_daily_used() == 0


def test_verify_company_empty_does_not_charge(
    db_session: Session, committed_tyc_source: None
) -> None:
    _configure_committed_tyc(daily_limit=5, monthly_limit=50)
    gateway = FakeTycGateway(status="empty")
    tool = VerifyCompanyTool(gateway=gateway)
    result = asyncio.run(
        tool.execute({"company_name": "某公司"}, db_session)
    )
    assert result["status"] == "empty"
    assert _committed_tyc_rows() == [("verify_company", "某公司", "empty")]
    assert _committed_tyc_daily_used() == 0


def test_get_budget_returns_real_counts(
    clean_agent_tables: Session, enable_tyc: None
) -> None:
    record_tyc_usage(
        clean_agent_tables, tool_name="verify_company", company_name="C1", status="success"
    )
    clean_agent_tables.flush()
    result = asyncio.run(GetBudgetTool().execute({}, clean_agent_tables))
    assert result["daily_used"] == 1
    assert result["daily_remaining"] == 4  # type: ignore[index]
    assert result["monthly_remaining"] == 49  # type: ignore[index]


def test_tyc_usage_ignores_env_key(
    clean_agent_tables: Session,
    monkeypatch: MonkeyPatch,
) -> None:
    """环境变量不构成可用密钥：无库内密文时用量快照保持停用。"""
    source = clean_agent_tables.scalar(
        select(DataSource).where(DataSource.code == "tianyancha")
    )
    assert source is not None
    source.enabled = True
    source.api_key_encrypted = None
    monkeypatch.setenv("TYC_API_KEY", "tyc_env_key")

    assert get_tyc_usage(clean_agent_tables).enabled is False


def test_tyc_usage_reads_console_limits(
    clean_agent_tables: Session,
) -> None:
    """控制台 login_config 的额度优先于常量默认值。"""
    source = clean_agent_tables.scalar(
        select(DataSource).where(DataSource.code == "tianyancha")
    )
    assert source is not None
    source.login_config = {"daily_limit": 17, "monthly_limit": 123}
    clean_agent_tables.flush()

    usage = get_tyc_usage(clean_agent_tables)
    assert usage.daily_limit == 17
    assert usage.monthly_limit == 123


def test_tyc_usage_uses_constant_default_limits(
    clean_agent_tables: Session,
) -> None:
    """控制台未配置额度时使用常量默认值 1000/10000（与账户口径一致），不再读取环境变量。"""
    source = clean_agent_tables.scalar(
        select(DataSource).where(DataSource.code == "tianyancha")
    )
    assert source is not None
    source.login_config = {}
    clean_agent_tables.flush()

    usage = get_tyc_usage(clean_agent_tables)
    assert usage.daily_limit == 1000
    assert usage.monthly_limit == 10000


class _FrozenDateTime(datetime):
    """冻结 ``budget`` 模块内 ``datetime.now()`` 的测试时钟（边界确定性）。"""

    frozen_at: ClassVar[datetime] = datetime(2026, 1, 1, tzinfo=UTC)

    @classmethod
    def now(cls, tz: tzinfo | None = None) -> datetime:
        if tz is None:
            return cls.frozen_at.replace(tzinfo=None)
        return cls.frozen_at.astimezone(tz)


@pytest.fixture
def frozen_budget_now(monkeypatch: MonkeyPatch) -> Callable[[datetime], None]:
    """把 ``budget.get_tyc_usage`` 的当前时刻固定为指定 UTC 时间。"""
    monkeypatch.setattr(budget_module, "datetime", _FrozenDateTime)

    def freeze(moment: datetime) -> None:
        _FrozenDateTime.frozen_at = moment

    return freeze


def _add_tyc_usage(
    session: Session, *, called_at: datetime, status: str = "success"
) -> None:
    session.add(
        TycUsageRecord(
            tool_name="verify_company",
            company_name="固定时钟记录",
            status=status,
            called_at=called_at,
        )
    )


def test_baseline_tyc_usage_charges_only_success_records(
    clean_agent_tables: Session,
) -> None:
    """基线特征：计费口径 = 仅有记录的成功结果计 1 次（Todo 1 charged_statuses）。"""
    for status in ("success", "empty", "error", "not_configured"):
        record_tyc_usage(
            clean_agent_tables,
            tool_name="verify_company",
            company_name=f"状态-{status}",
            status=status,
        )
    clean_agent_tables.flush()

    usage = get_tyc_usage(clean_agent_tables)
    assert usage.daily_used == 1
    assert usage.monthly_used == 1


def test_baseline_tyc_usage_daily_window_follows_beijing(
    clean_agent_tables: Session,
    frozen_budget_now: Callable[[datetime], None],
) -> None:
    """基线特征：日窗口按北京时间 00:00 边界；北京今日 00:00 前不计入。"""
    frozen_budget_now(datetime(2026, 8, 31, 16, 30, tzinfo=UTC))  # 北京 9/1 00:30
    beijing_day_start_utc = datetime(2026, 8, 31, 16, 0, tzinfo=UTC)
    _add_tyc_usage(
        clean_agent_tables, called_at=beijing_day_start_utc - timedelta(seconds=1)
    )
    _add_tyc_usage(clean_agent_tables, called_at=beijing_day_start_utc)
    clean_agent_tables.flush()

    assert get_tyc_usage(clean_agent_tables).daily_used == 1


def test_tyc_usage_month_window_uses_beijing_boundary(
    clean_agent_tables: Session,
    frozen_budget_now: Callable[[datetime], None],
) -> None:
    """月窗口按北京时间月首：北京 9/1 00:00 = UTC 8/31 16:00。

    基线（修改前，见 Todo 4 证据）：按 UTC 月首（8/1 00:00）统计，三条全部计入 → 3。
    目标行为：北京 8 月的记录（8/31 15:00 UTC）不计入 9 月 → 2。
    """
    frozen_budget_now(datetime(2026, 8, 31, 16, 30, tzinfo=UTC))  # 北京 9/1 00:30
    _add_tyc_usage(clean_agent_tables, called_at=datetime(2026, 8, 31, 15, 0, tzinfo=UTC))
    _add_tyc_usage(clean_agent_tables, called_at=datetime(2026, 8, 31, 16, 0, tzinfo=UTC))
    _add_tyc_usage(clean_agent_tables, called_at=datetime(2026, 9, 15, 12, 0, tzinfo=UTC))
    clean_agent_tables.flush()

    usage = get_tyc_usage(clean_agent_tables)
    assert usage.monthly_used == 2
    assert usage.daily_used == 2


CANDIDATES_MD = (
    "| # | 企业名称 | 统一社会信用代码 | 登记状态 | 法定代表人 |\n"
    "| --- | --- | --- | --- | --- |\n"
    "| 1 | 测试科技有限公司 | 91310000TEST00001 | 存续 | 张三 |\n"
    "| 2 | 测试科技有限公司(上海) | 91310000TEST00002 | 注销 | 李四 |\n"
)


def _sse_response(body: dict[str, object]) -> httpx.Response:
    data = json.dumps(body, ensure_ascii=False)
    return httpx.Response(
        200,
        headers={"Content-Type": "text/event-stream"},
        text=f"event: message\ndata: {data}\n\n",
    )


def _mcp_handler() -> tuple[object, list[str]]:
    captured: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request.headers.get("Authorization", ""))
        payload = json.loads(request.content)
        method = payload.get("method")
        if method == "initialize":
            return _sse_response(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "serverInfo": {"name": "tyc-mcp", "version": "2.2.0"},
                    },
                }
            )
        if method == "notifications/initialized":
            return httpx.Response(202)
        if method == "tools/call":
            name = payload["params"]["name"]  # type: ignore[index]
            assert name == "search_companies"
            return _sse_response(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "result": {
                        "content": [{"type": "text", "text": CANDIDATES_MD}],
                        "isError": False,
                    },
                }
            )
        return httpx.Response(400)

    return handler, captured


def test_mcp_gateway_sse_success() -> None:
    handler, captured = _mcp_handler()
    gateway = McpTycGateway("tyc_test_key", transport=httpx.MockTransport(handler))
    result = asyncio.run(gateway.verify("测试科技有限公司"))
    assert result["status"] == "success"
    assert result["company_name"] == "测试科技有限公司"
    assert result["credit_code"] == "91310000TEST00001"
    assert result["reg_status"] == "存续"
    assert all(header == "tyc_test_key" for header in captured)


def test_mcp_gateway_json_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        method = payload.get("method")
        if method == "initialize":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {"protocolVersion": "2025-06-18", "capabilities": {}},
                },
            )
        if method == "tools/call":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "result": {
                        "content": [{"type": "text", "text": CANDIDATES_MD}],
                        "isError": False,
                    },
                },
            )
        return httpx.Response(202)

    gateway = McpTycGateway("tyc_test_key", transport=httpx.MockTransport(handler))
    result = asyncio.run(gateway.verify("测试科技有限公司"))
    assert result["status"] == "success"


def test_mcp_gateway_empty_result() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if payload.get("method") == "initialize":
            return _sse_response(
                {"jsonrpc": "2.0", "id": 1, "result": {"capabilities": {}}}
            )
        if payload.get("method") == "tools/call":
            return _sse_response(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "result": {
                        "content": [{"type": "text", "text": "未查询到相关记录"}],
                        "isError": False,
                    },
                }
            )
        return httpx.Response(202)

    gateway = McpTycGateway("tyc_test_key", transport=httpx.MockTransport(handler))
    result = asyncio.run(gateway.verify("不存在的公司"))
    assert result["status"] == "empty"


def test_mcp_gateway_auth_failure_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if payload.get("method") == "initialize":
            return _sse_response(
                {"jsonrpc": "2.0", "id": 1, "result": {"capabilities": {}}}
            )
        return httpx.Response(401)

    gateway = McpTycGateway("bad_key", transport=httpx.MockTransport(handler))
    with pytest.raises(TycGatewayError, match="鉴权失败"):
        asyncio.run(gateway.verify("测试公司"))


def test_build_tyc_gateway_defaults_to_unconfigured() -> None:
    gateway = build_tyc_gateway()
    assert isinstance(gateway, UnconfiguredTycGateway)
    result = asyncio.run(gateway.verify("测试公司"))
    assert result["status"] == "not_configured"


def test_build_tyc_gateway_reads_console_key_from_db(
    db_session: Session,
) -> None:
    """网关构建只认信息源控制台加密存库的运行密钥。"""
    from app.signals.secret_store import encrypt_secret

    source = db_session.scalar(select(DataSource).where(DataSource.code == "tianyancha"))
    assert source is not None
    source.enabled = True
    source.api_key_encrypted = encrypt_secret("tyc_db_key")
    source.api_key_last4 = "key"
    source.endpoint_url = "https://console.example/mcp"
    db_session.flush()

    gateway = build_tyc_gateway(session=db_session)
    assert isinstance(gateway, McpTycGateway)
    assert gateway.api_key == "tyc_db_key"
    assert gateway.endpoint == "https://console.example/mcp"


def test_build_tyc_gateway_ignores_env_key(
    db_session: Session,
    committed_tyc_env: None,
    monkeypatch: MonkeyPatch,
) -> None:
    """环境变量完全不参与密钥解析：即使存在 TYC_API_KEY，无库内密文即未配置。"""
    _configure_committed_tyc(
        daily_limit=100, monthly_limit=1000, enabled=False, api_key=None
    )
    db_session.expire_all()
    source = db_session.scalar(select(DataSource).where(DataSource.code == "tianyancha"))
    assert source is not None
    assert source.api_key_encrypted is None
    monkeypatch.setenv("TYC_API_KEY", "tyc_env_key")

    gateway = build_tyc_gateway(session=db_session)
    assert isinstance(gateway, UnconfiguredTycGateway)
    result = asyncio.run(gateway.verify("测试公司"))
    assert result["status"] == "not_configured"


def test_chat_endpoint(
    client: TestClient, clean_agent_tables: Session, monkeypatch: MonkeyPatch
) -> None:
    import app.agent.service as agent_service

    # 固定使用 Fake 引擎，避免依赖真实模型网络（测试环境无关）
    monkeypatch.setattr(agent_service, "get_agent_llm", lambda: FakeAgentLLM())
    response = client.post("/api/v1/chat", json={"question": "今天有什么风险？"})
    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] > 0
    assert body["answer"]
    assert body["tool_calls"][0]["name"] == "query_current_alerts"


def test_chat_endpoint_persists_alert_tool_calls_as_json(
    client: TestClient, clean_agent_tables: Session, monkeypatch: MonkeyPatch
) -> None:
    """真实存在有效告警时：/chat 返回 200 且 assistant 工具记录以 JSON 落库。"""
    import app.agent.service as agent_service

    # Given
    now_utc = datetime.now(UTC)
    deadline = now_utc + timedelta(days=1)
    risk = linked_risk(
        clean_agent_tables,
        (SignalSpec("agent-chat-json", valid_until=deadline),),
        now_utc=now_utc,
    )
    risk.event.valid_until = deadline
    risk.event.review_due_at = deadline
    risk.alert.expiry_kind = "finite"
    risk.alert.expires_at = deadline
    clean_agent_tables.commit()
    monkeypatch.setattr(agent_service, "get_agent_llm", lambda: FakeAgentLLM())

    # When
    response = client.post("/api/v1/chat", json={"question": "今天有什么风险？"})

    # Then
    assert response.status_code == 200
    body = response.json()
    assert body["tool_calls"][0]["name"] == "query_current_alerts"
    assert body["tool_calls"][0]["result"]["total"] >= 1
    stored = clean_agent_tables.scalar(
        select(AgentMessage).where(
            AgentMessage.session_id == body["session_id"],
            AgentMessage.role == "assistant",
        )
    )
    assert stored is not None
    assert stored.tool_calls[0]["result"]["total"] >= 1
    assert stored.tool_calls[0]["result"]["items"][0]["expires_at"] == deadline.isoformat()


def test_chat_endpoint_validation(client: TestClient) -> None:
    response = client.post("/api/v1/chat", json={"question": ""})
    assert response.status_code == 422


def test_agent_endpoints_have_separate_openapi_groups(client: TestClient) -> None:
    paths = client.get("/api/openapi.json").json()["paths"]
    assert paths["/api/v1/chat"]["post"]["tags"] == ["风险查询助手"]
    assert paths["/api/v1/source-agent/chat"]["post"]["tags"] == ["信息源接入助手"]


def test_agent_status_endpoint(
    client: TestClient,
    db_session: Session,
    committed_tyc_env: None,
    monkeypatch: MonkeyPatch,
) -> None:
    import app.agent.router as agent_router
    from app.config import AISettings

    _configure_committed_tyc(
        daily_limit=100, monthly_limit=1000, enabled=False, api_key=None
    )
    db_session.expire_all()
    monkeypatch.setattr(
        agent_router,
        "get_ai_settings",
        lambda: AISettings(
            provider="fake",
            base_url="",
            model="",
            api_key="",
            timeout_seconds=30,
            max_retries=2,
        ),
    )
    response = client.get("/api/v1/agent/status")
    assert response.status_code == 200
    body = response.json()
    assert body["llm_configured"] is False  # Fake 引擎视为未配置真实模型
    assert body["tyc_enabled"] is False
    assert body["max_steps"] >= 1


def test_verify_company_no_longer_serves_history_for_registered_supplier(
    clean_agent_tables: Session, enable_tyc: None
) -> None:
    """清单内供应商：即使已有历史信号也不回读历史替代实时核查。

    回归锁定：清单内路径必须发起实时多维核查（这里网关未配置密钥，故返回
    not_configured 而不是历史信号内容）。
    """
    supplier = Supplier(
        supplier_code="SUP-001",
        legal_name="上海华美精密机械有限公司",
        country_code="CN",
        enabled=True,
    )
    clean_agent_tables.add(supplier)
    clean_agent_tables.flush()

    signal = _seed_tyc_signal(
        clean_agent_tables,
        supplier,
        title="天眼查核查：上海华美精密机械有限公司",
        content="企业：上海华美精密机械有限公司；登记状态：存续",
        raw_payload={"status": "success", "company_name": "上海华美精密机械有限公司"},
    )
    clean_agent_tables.flush()

    gateway = FakeTycGateway(status="not_configured")
    tool = VerifyCompanyTool(gateway=gateway)
    result = asyncio.run(
        tool.execute({"company_name": "上海华美精密机械有限公司"}, clean_agent_tables)
    )
    assert result["status"] == "not_configured"
    assert result["source"] == "tianyancha_realtime"
    assert result["signal_id"] is None
    assert result["alert_ids"] == []
    # 走的是实时多维核查，不是回读历史信号。
    assert gateway.dimension_calls == ["上海华美精密机械有限公司"]
    assert gateway.calls == []
    assert signal.external_id not in str(result["message"])
    stored = clean_agent_tables.scalar(
        select(RawSignal).where(RawSignal.external_id == signal.external_id)
    )
    assert stored is not None


def test_format_tyc_signal_result_prefers_raw_data_and_falls_back_to_content(
    clean_agent_tables: Session, enable_tyc: None
) -> None:
    """回归：清单内供应商卡片的结构化字段（信用代码/登记状态）必须透出。

    raw_data 结构化字段优先；缺失时回退解析 content 正文；确实缺失返回 None。
    """
    from app.agent.supplier_tyc import format_tyc_signal_result

    supplier = Supplier(
        supplier_code="SUP-TYC-FORMAT",
        legal_name="格式回归测试有限公司",
        country_code="CN",
        enabled=True,
    )
    clean_agent_tables.add(supplier)
    clean_agent_tables.flush()

    # Given：raw_data 携带 verify() 原始结构化字段，且与正文值刻意不同以证明优先级
    raw_title = "天眼查核查：格式回归测试有限公司"
    raw_signal = _seed_tyc_signal(
        clean_agent_tables,
        supplier,
        title=raw_title,
        content=(
            "企业：格式回归测试有限公司；统一社会信用代码：91110000MA01OLDX1；"
            "登记状态：注销；候选1：旧正文候选"
        ),
        raw_payload={
            "status": "success",
            "company_name": "格式回归测试有限公司",
            "credit_code": "91310000MA1K3XYZ8N",
            "reg_status": "存续",
            "candidates": [
                {
                    "name": "格式回归测试有限公司",
                    "credit_code": "91310000MA1K3XYZ8N",
                    "reg_status": "存续",
                },
                {
                    "name": "格式回归测试（北京）有限公司",
                    "credit_code": "91110000MA01ABCD2X",
                    "reg_status": "注销",
                },
            ],
        },
    )
    raw_stored = clean_agent_tables.scalar(
        select(RawSignal).where(
            RawSignal.external_id == raw_signal.external_id,
            RawSignal.title == raw_title,
        )
    )
    assert raw_stored is not None

    # When
    result = format_tyc_signal_result(raw_stored)

    # Then：结构化字段以 raw_data 为准，既有字段保持不变
    assert result["status"] == "success"
    assert result["source"] == "database"
    assert result["title"] == raw_title
    assert result["company_name"] == "格式回归测试有限公司"
    assert result["credit_code"] == "91310000MA1K3XYZ8N"
    assert result["reg_status"] == "存续"
    assert result["candidates"] == [
        {
            "name": "格式回归测试有限公司",
            "credit_code": "91310000MA1K3XYZ8N",
            "reg_status": "存续",
        },
        {
            "name": "格式回归测试（北京）有限公司",
            "credit_code": "91110000MA01ABCD2X",
            "reg_status": "注销",
        },
    ]

    # Given：历史信号 raw_data 缺少结构化字段，仅正文含可回溯文本
    fallback_title = "天眼查核查：格式回归测试有限公司（历史）"
    fallback_signal = _seed_tyc_signal(
        clean_agent_tables,
        supplier,
        title=fallback_title,
        content=(
            "企业：格式回归测试有限公司；统一社会信用代码：91310000MA1K3XYZ8N；"
            "登记状态：存续；候选1：格式回归测试有限公司；候选2：格式回归测试（北京）有限公司"
        ),
        raw_payload={"status": "success"},
    )
    fallback_stored = clean_agent_tables.scalar(
        select(RawSignal).where(
            RawSignal.external_id == fallback_signal.external_id,
            RawSignal.title == fallback_title,
        )
    )
    assert fallback_stored is not None

    # When
    fallback = format_tyc_signal_result(fallback_stored)

    # Then：缺失字段从 content 正文解析补齐
    assert fallback["company_name"] == "格式回归测试有限公司"
    assert fallback["credit_code"] == "91310000MA1K3XYZ8N"
    assert fallback["reg_status"] == "存续"
    assert fallback["candidates"] == [
        {"name": "格式回归测试有限公司", "credit_code": None, "reg_status": None},
        {"name": "格式回归测试（北京）有限公司", "credit_code": None, "reg_status": None},
    ]

    # Given：raw_data 与正文均无信用代码/登记状态
    sparse_title = "天眼查核查：格式回归测试有限公司（稀疏）"
    sparse_signal = _seed_tyc_signal(
        clean_agent_tables,
        supplier,
        title=sparse_title,
        content="企业：格式回归测试有限公司",
        raw_payload={},
    )
    sparse_stored = clean_agent_tables.scalar(
        select(RawSignal).where(
            RawSignal.external_id == sparse_signal.external_id,
            RawSignal.title == sparse_title,
        )
    )
    assert sparse_stored is not None

    # When
    sparse = format_tyc_signal_result(sparse_stored)

    # Then：确实缺失的字段为 None，后端不得输出「未披露」
    assert sparse["credit_code"] is None
    assert sparse["reg_status"] is None
    assert sparse["candidates"] == []


def test_verify_company_registered_supplier_never_reports_database_source(
    clean_agent_tables: Session, enable_tyc: None
) -> None:
    """回归：清单内路径不再返回 source=database（历史回读路径已移除）。"""
    supplier = Supplier(
        supplier_code="SUP-002",
        legal_name="宁波鸿腾精密有限公司",
        country_code="CN",
        enabled=True,
    )
    clean_agent_tables.add(supplier)
    clean_agent_tables.flush()

    gateway = FakeTycGateway(status="not_configured")
    tool = VerifyCompanyTool(gateway=gateway)
    result = asyncio.run(
        tool.execute({"company_name": "宁波鸿腾精密有限公司"}, clean_agent_tables)
    )
    assert result["status"] == "not_configured"
    assert result["source"] == "tianyancha_realtime"
    assert result["verification_status"] == "not_configured"
    assert gateway.dimension_calls == ["宁波鸿腾精密有限公司"]
    assert gateway.calls == []
    assert get_tyc_usage(clean_agent_tables).daily_used == 0


# ---------------------------------------------------------------------------
# 天眼查单工具额度执行器（tyc_quota）：独立短事务 + advisory lock + 锁内重读
# ---------------------------------------------------------------------------


def _truncate_committed_tyc_usage() -> None:
    """真提交清空计费表：执行器使用独立 Session，测试场景必须跨连接可见。"""
    with engine.begin() as connection:
        connection.execute(delete(TycUsageRecord))


def _configure_committed_tyc(
    *,
    daily_limit: int,
    monthly_limit: int,
    enabled: bool = True,
    api_key: str | None = "tyc_quota_test_key",
) -> None:
    """真提交地启用天眼查测试源并写入额度（执行器自建 Session 可见）。"""
    from app.signals.secret_store import encrypt_secret

    with Session(engine) as setup:
        source = setup.scalar(select(DataSource).where(DataSource.code == "tianyancha"))
        assert source is not None, "迁移应已注册 tianyancha 信息源"
        source.enabled = enabled
        if api_key is None:
            source.api_key_encrypted = None
            source.api_key_hash = None
            source.api_key_last4 = None
        else:
            source.api_key_encrypted = encrypt_secret(api_key)
            source.api_key_hash = hashlib.sha256(api_key.encode()).hexdigest()
            source.api_key_last4 = "key"
        source.login_config = {
            "mode": "on_demand",
            "secret_source": "console",
            "daily_limit": daily_limit,
            "monthly_limit": monthly_limit,
        }
        setup.commit()


def _committed_tyc_rows() -> list[tuple[str, str, str]]:
    with Session(engine) as probe:
        rows = probe.execute(
            select(
                TycUsageRecord.tool_name,
                TycUsageRecord.company_name,
                TycUsageRecord.status,
            ).order_by(TycUsageRecord.id)
        ).all()
    return [(row.tool_name, row.company_name, row.status) for row in rows]


def _committed_tyc_daily_used() -> int:
    with Session(engine) as probe:
        return get_tyc_usage(probe).daily_used


@pytest.fixture
def committed_tyc_source() -> Generator[None]:
    """为执行器准备跨连接可见的天眼查配置；测试后恢复原值并清空计费表。"""
    with Session(engine) as snapshot:
        source = snapshot.scalar(
            select(DataSource).where(DataSource.code == "tianyancha")
        )
        assert source is not None
        original = (
            source.enabled,
            source.api_key_encrypted,
            source.api_key_hash,
            source.api_key_last4,
            source.login_config,
        )
    _truncate_committed_tyc_usage()
    yield
    with Session(engine) as restore:
        source = restore.scalar(
            select(DataSource).where(DataSource.code == "tianyancha")
        )
        assert source is not None
        (
            source.enabled,
            source.api_key_encrypted,
            source.api_key_hash,
            source.api_key_last4,
            source.login_config,
        ) = original
        restore.commit()
    _truncate_committed_tyc_usage()


class _RecordingRemoteCall:
    """可断言的异步 fake remote_call：记录调用次数，可返回结果或抛异常。"""

    def __init__(
        self,
        *,
        result: dict[str, object] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.result = result if result is not None else {"status": "success"}
        self.error = error
        self.calls: list[int] = []

    async def __call__(self) -> dict[str, object]:
        self.calls.append(len(self.calls) + 1)
        if self.error is not None:
            raise self.error
        return self.result


def test_quota_executor_stops_sixth_call_when_daily_limit_five(
    committed_tyc_source: None,
) -> None:
    """Manual QA 数据面：daily_limit=5 时连续 6 次，DB 恰 5 条且第 6 次不调远程。"""
    from app.agent.tyc_quota import TycQuotaOutcome, execute_tyc_tool_with_quota

    _configure_committed_tyc(daily_limit=5, monthly_limit=50)
    remote_call = _RecordingRemoteCall(
        result={"status": "success", "company_name": "额度测试公司"}
    )

    outcomes = [
        asyncio.run(
            execute_tyc_tool_with_quota(
                "verify_company", f"额度测试公司{index}", remote_call
            )
        ).outcome
        for index in range(6)
    ]

    assert outcomes == [TycQuotaOutcome.SUCCESS_WITH_RECORDS] * 5 + [
        TycQuotaOutcome.QUOTA_EXHAUSTED
    ]
    assert len(remote_call.calls) == 5
    rows = _committed_tyc_rows()
    assert len(rows) == 5
    assert {status for _, _, status in rows} == {"success"}
    assert _committed_tyc_daily_used() == 5


def test_quota_executor_commits_independently_of_caller_session(
    committed_tyc_source: None,
) -> None:
    """执行器自建独立短事务：调用方事务回滚，记账事实仍已真实提交。"""
    from app.agent.tyc_quota import TycQuotaOutcome, execute_tyc_tool_with_quota

    _configure_committed_tyc(daily_limit=5, monthly_limit=50)
    remote_call = _RecordingRemoteCall(
        result={"status": "success", "company_name": "独立事务公司"}
    )

    caller = Session(engine)
    try:
        result = asyncio.run(
            execute_tyc_tool_with_quota("verify_company", "独立事务公司", remote_call)
        )
        assert result.outcome is TycQuotaOutcome.SUCCESS_WITH_RECORDS
        assert result.payload == {"status": "success", "company_name": "独立事务公司"}
        assert result.usage is not None
        assert result.usage.daily_used == 1

        # 另一条连接立刻可见：说明执行器在返回前已显式提交。
        assert _committed_tyc_rows() == [("verify_company", "独立事务公司", "success")]

        # 调用方（模拟 Agent 请求会话）回滚自己的事务，不丢失记账事实。
        caller.rollback()
        assert _committed_tyc_daily_used() == 1
    finally:
        caller.close()


def test_quota_executor_rereads_quota_inside_lock_after_external_consumption(
    committed_tyc_source: None,
) -> None:
    """外部消费真提交后，执行器锁内重读余额，下一次调用不再放行（不超发）。"""
    from app.agent.tyc_quota import TycQuotaOutcome, execute_tyc_tool_with_quota

    _configure_committed_tyc(daily_limit=2, monthly_limit=50)
    remote_call = _RecordingRemoteCall()

    first = asyncio.run(
        execute_tyc_tool_with_quota("verify_company", "序列公司A", remote_call)
    )
    assert first.outcome is TycQuotaOutcome.SUCCESS_WITH_RECORDS

    # 模拟实时路径：外部连接真提交一条消费（剩余额度变为 0）。
    with Session(engine) as external:
        external.add(
            TycUsageRecord(
                tool_name="verify_company", company_name="外部消费", status="success"
            )
        )
        external.commit()

    second = asyncio.run(
        execute_tyc_tool_with_quota("verify_company", "序列公司B", remote_call)
    )
    assert second.outcome is TycQuotaOutcome.QUOTA_EXHAUSTED
    assert len(remote_call.calls) == 1
    assert _committed_tyc_daily_used() == 2


def test_quota_executor_rejects_deactivated_source_inside_lock(
    committed_tyc_source: None,
) -> None:
    """来源停用真提交后，执行器锁内重读 enabled=False：不调远程、不记账、不计费。"""
    from app.agent.tyc_quota import (
        NOT_CONFIGURED_MESSAGE,
        TycQuotaOutcome,
        execute_tyc_tool_with_quota,
    )

    _configure_committed_tyc(daily_limit=5, monthly_limit=50)
    _configure_committed_tyc(daily_limit=5, monthly_limit=50, enabled=False)
    remote_call = _RecordingRemoteCall()

    result = asyncio.run(
        execute_tyc_tool_with_quota("verify_company", "停用公司", remote_call)
    )

    assert result.outcome is TycQuotaOutcome.NOT_CONFIGURED
    assert result.message == NOT_CONFIGURED_MESSAGE
    assert result.payload is None
    assert result.usage is not None
    assert result.usage.enabled is False
    assert remote_call.calls == []
    assert _committed_tyc_rows() == []
    assert _committed_tyc_daily_used() == 0


def test_quota_executor_rejects_revoked_key_inside_lock(
    committed_tyc_source: None,
) -> None:
    """加密密钥删除真提交后，执行器锁内重读 enabled=False：不调远程、不记账、不计费。"""
    from app.agent.tyc_quota import (
        NOT_CONFIGURED_MESSAGE,
        TycQuotaOutcome,
        execute_tyc_tool_with_quota,
    )

    _configure_committed_tyc(daily_limit=5, monthly_limit=50)
    _configure_committed_tyc(daily_limit=5, monthly_limit=50, api_key=None)
    remote_call = _RecordingRemoteCall()

    result = asyncio.run(
        execute_tyc_tool_with_quota("verify_company", "撤销密钥公司", remote_call)
    )

    assert result.outcome is TycQuotaOutcome.NOT_CONFIGURED
    assert result.message == NOT_CONFIGURED_MESSAGE
    assert result.payload is None
    assert result.usage is not None
    assert result.usage.enabled is False
    assert remote_call.calls == []
    assert _committed_tyc_rows() == []
    assert _committed_tyc_daily_used() == 0


def test_quota_executor_records_error_without_charging(
    committed_tyc_source: None,
) -> None:
    """远程异常：按不计费 error 记账并显式提交，不写 success 记录。"""
    from app.agent.tyc_quota import TycQuotaOutcome, execute_tyc_tool_with_quota

    _configure_committed_tyc(daily_limit=5, monthly_limit=50)
    remote_call = _RecordingRemoteCall(error=RuntimeError("天眼查服务暂时不可用"))

    result = asyncio.run(
        execute_tyc_tool_with_quota("verify_company", "异常公司", remote_call)
    )

    assert result.outcome is TycQuotaOutcome.ERROR
    assert result.payload is None
    assert result.message is not None
    assert "天眼查服务暂时不可用" in result.message
    assert _committed_tyc_rows() == [("verify_company", "异常公司", "error")]
    assert _committed_tyc_daily_used() == 0


@pytest.mark.parametrize(
    ("remote_result", "expected_outcome", "expected_record_status"),
    [
        ({"status": "empty"}, "empty", "empty"),
        ({"status": "param_missing"}, "error", "error"),
        ({}, "error", "error"),
        ({"status": 123}, "error", "error"),
    ],
)
def test_quota_executor_classifies_remote_status_without_charging(
    committed_tyc_source: None,
    remote_result: dict[str, object],
    expected_outcome: str,
    expected_record_status: str,
) -> None:
    """空结果与非法/缺失状态按 Todo 1 口径分类：empty 或 error 均不计费。"""
    from app.agent.tyc_quota import TycQuotaOutcome, execute_tyc_tool_with_quota

    _configure_committed_tyc(daily_limit=5, monthly_limit=50)
    remote_call = _RecordingRemoteCall(result=remote_result)

    result = asyncio.run(
        execute_tyc_tool_with_quota("verify_company", "状态公司", remote_call)
    )

    assert result.outcome is TycQuotaOutcome(expected_outcome)
    assert len(remote_call.calls) == 1
    assert _committed_tyc_rows() == [
        ("verify_company", "状态公司", expected_record_status)
    ]
    assert _committed_tyc_daily_used() == 0


def test_quota_executor_returns_busy_on_lock_timeout(
    committed_tyc_source: None,
    monkeypatch: MonkeyPatch,
) -> None:
    """额度锁被占用超过 lock_timeout：结构化 busy，不调用远程、不记账。"""
    import app.agent.tyc_quota as tyc_quota_module

    _configure_committed_tyc(daily_limit=5, monthly_limit=50)
    monkeypatch.setattr(tyc_quota_module, "QUOTA_LOCK_TIMEOUT_MS", 100)
    remote_call = _RecordingRemoteCall()

    holder = engine.connect()
    holder_transaction = holder.begin()
    try:
        holder.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": tyc_quota_module.QUOTA_ADVISORY_LOCK_KEY},
        )
        result = asyncio.run(
            tyc_quota_module.execute_tyc_tool_with_quota(
                "verify_company", "锁竞争公司", remote_call
            )
        )
    finally:
        holder_transaction.rollback()
        holder.close()

    assert result.outcome is tyc_quota_module.TycQuotaOutcome.BUSY
    assert remote_call.calls == []
    assert _committed_tyc_rows() == []


# ---------------------------------------------------------------------------
# Todo 13：实时核查 VerifyCompanyTool 与批量路径共享 D9 额度执行器
# ---------------------------------------------------------------------------


def test_verify_company_realtime_shares_quota_with_batch_without_overrun(
    committed_tyc_source: None,
) -> None:
    """实时与批量并发共享额度锁：合计不超日限，恰好一方消耗、另一方被拒。"""
    from concurrent.futures import ThreadPoolExecutor

    from app.agent.tyc_quota import (
        TycQuotaExecutionResult,
        TycQuotaOutcome,
        execute_tyc_tool_with_quota,
    )

    _configure_committed_tyc(daily_limit=1, monthly_limit=50)
    gateway = FakeTycGateway(status="success")
    batch_call = _RecordingRemoteCall(
        result={"status": "success", "company_name": "批量公司"}
    )

    def run_realtime() -> dict[str, object]:
        with Session(engine) as caller:
            return asyncio.run(
                VerifyCompanyTool(gateway=gateway).execute(
                    {"company_name": "实时公司"}, caller
                )
            )

    def run_batch() -> TycQuotaExecutionResult:
        return asyncio.run(
            execute_tyc_tool_with_quota("verify_company", "批量公司", batch_call)
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        realtime_future = pool.submit(run_realtime)
        batch_future = pool.submit(run_batch)
        realtime_result = realtime_future.result()
        batch_result = batch_future.result()

    # 不超发：日限 1，两路合计恰好一次远程调用、一条计费行
    assert len(gateway.calls) + len(batch_call.calls) == 1
    assert _committed_tyc_daily_used() == 1
    assert len(_committed_tyc_rows()) == 1

    if realtime_result["status"] == "success":
        assert batch_result.outcome in {
            TycQuotaOutcome.QUOTA_EXHAUSTED,
            TycQuotaOutcome.BUSY,
        }
    else:
        assert batch_result.outcome is TycQuotaOutcome.SUCCESS_WITH_RECORDS
        assert realtime_result["status"] in {"quota_exhausted", "busy"}


def test_verify_company_realtime_rechecks_quota_inside_lock(
    committed_tyc_source: None,
) -> None:
    """批量已提交消费后，实时工具在锁内重读余额并结构化拒绝，不调远程。"""
    _configure_committed_tyc(daily_limit=1, monthly_limit=50)
    with Session(engine) as batch:
        batch.add(
            TycUsageRecord(
                tool_name="verify_company", company_name="批量消费", status="success"
            )
        )
        batch.commit()
    gateway = FakeTycGateway(status="success")

    with Session(engine) as caller:
        result = asyncio.run(
            VerifyCompanyTool(gateway=gateway).execute(
                {"company_name": "实时公司"}, caller
            )
        )

    assert result["status"] == "quota_exhausted"
    assert "天眼查额度已达上限" in str(result["message"])
    usage = result["usage"]
    assert isinstance(usage, dict)
    assert usage["daily_used"] == 1
    assert gateway.calls == []
    assert len(_committed_tyc_rows()) == 1


def test_verify_company_revoked_between_precheck_and_lock_returns_not_configured(
    committed_tyc_source: None,
) -> None:
    """竞态：调用方会话仍持有启用视图（未提交），提交态已停用。

    前置检查放行后，执行器在额度锁内重读提交态 ``enabled=False``，返回既有
    ``not_configured`` 公共语义：不调用远程、不写 usage record、不计费。
    """
    from app.agent.tyc_quota import NOT_CONFIGURED_MESSAGE

    _configure_committed_tyc(daily_limit=5, monthly_limit=50, enabled=False)
    gateway = FakeTycGateway(status="success")

    caller = Session(engine)
    try:
        source = caller.scalar(
            select(DataSource).where(DataSource.code == "tianyancha")
        )
        assert source is not None
        source.enabled = True  # 调用方未提交视图：仅该会话的前置检查可见
        caller.flush()

        result = asyncio.run(
            VerifyCompanyTool(gateway=gateway).execute(
                {"company_name": "撤销竞态公司"}, caller
            )
        )
    finally:
        caller.rollback()
        caller.close()

    assert result["status"] == "not_configured"
    assert result["message"] == NOT_CONFIGURED_MESSAGE
    usage = result["usage"]
    assert isinstance(usage, dict)
    assert usage["enabled"] is False
    assert gateway.calls == []
    assert _committed_tyc_rows() == []
    assert _committed_tyc_daily_used() == 0


def test_verify_company_realtime_commit_survives_later_agent_failure(
    committed_tyc_source: None,
) -> None:
    """实时成功即独立提交：调用方（Agent）后续失败回滚不丢失记账记录。"""
    _configure_committed_tyc(daily_limit=5, monthly_limit=50)
    gateway = FakeTycGateway(status="success")
    caller = Session(engine)
    try:
        result = asyncio.run(
            VerifyCompanyTool(gateway=gateway).execute(
                {"company_name": "实时提交公司"}, caller
            )
        )
        assert result["status"] == "success"
        # 执行器在返回前已显式提交：另一条连接立即可见
        assert _committed_tyc_rows() == [
            ("verify_company", "实时提交公司", "success")
        ]
        # 模拟 Agent 循环后续失败：调用方事务回滚不影响已提交记账
        caller.rollback()
        assert _committed_tyc_daily_used() == 1
    finally:
        caller.close()


def test_verify_company_realtime_returns_busy_on_lock_timeout(
    committed_tyc_source: None,
    monkeypatch: MonkeyPatch,
) -> None:
    """额度锁被占用超过 lock_timeout：结构化 busy，不调远程、不记账。"""
    import app.agent.tyc_quota as tyc_quota_module

    _configure_committed_tyc(daily_limit=5, monthly_limit=50)
    monkeypatch.setattr(tyc_quota_module, "QUOTA_LOCK_TIMEOUT_MS", 100)
    gateway = FakeTycGateway(status="success")

    holder = engine.connect()
    holder_transaction = holder.begin()
    try:
        holder.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": tyc_quota_module.QUOTA_ADVISORY_LOCK_KEY},
        )
        with Session(engine) as caller:
            result = asyncio.run(
                VerifyCompanyTool(gateway=gateway).execute(
                    {"company_name": "锁竞争公司"}, caller
                )
            )
    finally:
        holder_transaction.rollback()
        holder.close()

    assert result["status"] == "busy"
    assert result["message"] == "天眼查额度繁忙，请重试"
    assert gateway.calls == []
    assert _committed_tyc_rows() == []
