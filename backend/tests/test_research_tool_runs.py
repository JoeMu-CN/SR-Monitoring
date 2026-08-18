"""受控研究工具调用账本测试；全部使用 Fake/Mock。"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.ai.providers import FakeAIProvider
from app.research import runner
from app.research.models import ResearchToolRun
from app.research.runner import execute_topic_source_discovery
from app.research.search import FakeSearchProvider, SearchCandidate
from app.research.service import (
    claim_next_task,
    claim_tool_run,
    complete_tool_run,
    create_task,
)
from app.research.web import ResearchPageRead


def test_tool_run_claim_uses_stable_hashed_action_id(db_session, auth_as) -> None:
    user = auth_as("risk_admin", "tool-run-claim")
    task = create_task(
        db_session,
        owner_user_id=user.id,
        task_type="manual",
        topic="工具调用幂等账本",
        supplier_scope=[],
        idempotency_key=None,
    )
    claimed_task = claim_next_task(db_session, worker_id="tool-run-worker", lease_seconds=60)
    assert claimed_task is not None

    first = claim_tool_run(
        db_session,
        task_id=task.id,
        worker_id="tool-run-worker",
        action_type="web_search",
        arguments={"query": "工具调用幂等账本", "max_results": 2},
    )
    assert first is not None and first.claimed is True
    db_session.commit()

    repeated = claim_tool_run(
        db_session,
        task_id=task.id,
        worker_id="tool-run-worker",
        action_type="web_search",
        arguments={"max_results": 2, "query": "工具调用幂等账本"},
    )
    assert repeated is not None and repeated.claimed is False
    assert repeated.tool_run.id == first.tool_run.id
    assert "工具调用幂等账本" not in first.tool_run.action_id
    assert "工具调用幂等账本" not in first.tool_run.arguments_hash

    completed = complete_tool_run(
        db_session,
        tool_run_id=first.tool_run.id,
        task_id=task.id,
        worker_id="tool-run-worker",
        usage_snapshot={"search_queries": 1},
        result_reference={"candidate_count": 2},
    )
    assert completed is not None and completed.status == "succeeded"
    assert db_session.scalars(select(ResearchToolRun)).all() == [completed]


def test_tool_run_claim_extends_active_task_lease(db_session, auth_as) -> None:
    user = auth_as("risk_admin", "tool-run-lease-renew")
    task = create_task(
        db_session,
        owner_user_id=user.id,
        task_type="manual",
        topic="工具动作续租",
        supplier_scope=[],
        idempotency_key=None,
    )
    start = datetime(2026, 8, 11, tzinfo=UTC)
    claimed_task = claim_next_task(
        db_session,
        worker_id="tool-run-lease-worker",
        lease_seconds=60,
        now=start,
    )
    assert claimed_task is not None

    claimed = claim_tool_run(
        db_session,
        task_id=task.id,
        worker_id="tool-run-lease-worker",
        action_type="web_search",
        arguments={"query": "工具动作续租"},
        now=start + timedelta(seconds=30),
    )
    assert claimed is not None and claimed.claimed is True
    db_session.flush()
    db_session.refresh(task)
    assert task.lease_until == start + timedelta(seconds=330)


def test_completed_tool_runs_prevent_repeated_search_read_and_report(
    db_session, auth_as, monkeypatch
) -> None:
    user = auth_as("risk_admin", "tool-run-recovery")
    task = create_task(
        db_session,
        owner_user_id=user.id,
        task_type="manual",
        topic="幂等恢复测试",
        supplier_scope=[],
        idempotency_key=None,
    )
    claimed_task = claim_next_task(db_session, worker_id="tool-run-worker", lease_seconds=60)
    assert claimed_task is not None
    search_provider = FakeSearchProvider(
        responses={
            task.topic: (
                SearchCandidate("https://official.example/one", "公告一", "摘要一"),
                SearchCandidate("https://official.example/two", "公告二", "摘要二"),
            )
        }
    )
    read_urls: list[str] = []

    async def fake_reader(url: str) -> ResearchPageRead:
        read_urls.append(url)
        return ResearchPageRead(
            requested_url=url,
            final_url=url,
            redirect_chain=(),
            status_code=200,
            content_type="text/html",
            excerpt=f"已读取公开来源：{url}",
        )

    class CountingFakeAIProvider(FakeAIProvider):
        def __init__(self) -> None:
            super().__init__()
            self.report_calls = 0

        async def generate_research_report(self, value, *, max_output_tokens):
            self.report_calls += 1
            return await super().generate_research_report(
                value, max_output_tokens=max_output_tokens
            )

    report_provider = CountingFakeAIProvider()
    monkeypatch.setattr(runner, "read_public_page", fake_reader)

    execute_topic_source_discovery(
        db_session,
        claimed_task,
        provider=search_provider,
        report_provider=report_provider,
    )
    execute_topic_source_discovery(
        db_session,
        claimed_task,
        provider=search_provider,
        report_provider=report_provider,
    )

    assert search_provider.calls == [task.topic]
    assert read_urls == ["https://official.example/one", "https://official.example/two"]
    assert report_provider.report_calls == 1
    tool_runs = list(
        db_session.scalars(
            select(ResearchToolRun)
            .where(ResearchToolRun.task_id == task.id)
            .order_by(ResearchToolRun.id)
        )
    )
    assert [item.action_type for item in tool_runs] == [
        "web_search",
        "public_page_read",
        "public_page_read",
        "report_generation",
    ]
    assert all(item.status == "succeeded" for item in tool_runs)
    assert all("official.example" not in item.arguments_hash for item in tool_runs)
