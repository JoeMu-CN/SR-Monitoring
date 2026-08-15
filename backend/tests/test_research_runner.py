"""本地 research-worker 生命周期与受控单页读取测试。"""

from sqlalchemy import select

from app.ai.providers import FakeAIProvider
from app.auth.models import SecurityAuditEvent
from app.research import runner
from app.research.models import ResearchCitation, ResearchReport, ResearchSource
from app.research.runner import (
    LOCAL_LIFECYCLE_TEST_TOPIC_PREFIX,
    run_controlled_single_page_once,
    run_local_lifecycle_test_once,
    run_topic_source_discovery_once,
)
from app.research.search import FakeSearchProvider, SearchCandidate
from app.research.service import create_task, request_controlled_execution
from app.research.web import ResearchPageRead


def test_local_worker_leaves_regular_research_task_queued(db_session, auth_as) -> None:
    user = auth_as("risk_admin", "local-worker-regular")
    task = create_task(
        db_session,
        owner_user_id=user.id,
        task_type="manual",
        topic="普通研究任务不得被本地测试 Worker 消费",
        supplier_scope=[],
        idempotency_key=None,
    )

    assert (
        run_local_lifecycle_test_once(
            db_session,
            worker_id="local-worker",
            lease_seconds=60,
        )
        is None
    )
    db_session.refresh(task)
    assert task.status == "queued"
    assert task.attempts == 0


def test_local_worker_completes_only_explicit_lifecycle_test(db_session, auth_as) -> None:
    user = auth_as("risk_admin", "local-worker-test")
    task = create_task(
        db_session,
        owner_user_id=user.id,
        task_type="manual",
        topic=f"{LOCAL_LIFECYCLE_TEST_TOPIC_PREFIX} 验证任务认领与完成",
        supplier_scope=[],
        idempotency_key=None,
    )

    completed = run_local_lifecycle_test_once(
        db_session,
        worker_id="local-worker",
        lease_seconds=60,
    )

    assert completed is not None
    assert completed.id == task.id
    assert completed.status == "succeeded"
    assert completed.current_step == "local_lifecycle_test_completed"
    assert completed.search_queries_used == 0
    assert completed.search_results_used == 0
    assert completed.input_tokens_used == 0
    assert completed.output_tokens_used == 0
    assert completed.cost_amount == 0

    events = list(
        db_session.scalars(
            select(SecurityAuditEvent)
            .where(
                SecurityAuditEvent.resource_type == "research_task",
                SecurityAuditEvent.resource_id == str(task.id),
            )
            .order_by(SecurityAuditEvent.id)
        )
    )
    assert [event.action for event in events] == [
        "research_task_claimed",
        "research_task_succeeded",
    ]


def test_controlled_worker_reads_only_explicit_task_urls_and_persists_evidence(
    db_session, auth_as, monkeypatch
) -> None:
    user = auth_as("risk_admin", "controlled-page-worker")
    task = create_task(
        db_session,
        owner_user_id=user.id,
        task_type="manual",
        topic="公开页面读取",
        supplier_scope=[],
        source_urls=["https://official.example/notice"],
        idempotency_key=None,
    )
    observed_urls: list[str] = []

    async def fake_reader(url: str) -> ResearchPageRead:
        observed_urls.append(url)
        return ResearchPageRead(
            requested_url=url,
            final_url=url,
            redirect_chain=(),
            status_code=200,
            content_type="text/html",
            excerpt="官方公告：供应链信息正常。",
        )

    monkeypatch.setattr(runner, "read_public_page", fake_reader)
    started = request_controlled_execution(
        db_session,
        task_id=task.id,
        owner_user_id=user.id,
        role="risk_admin",
    )
    assert started is not None
    completed = run_controlled_single_page_once(
        db_session,
        worker_id="controlled-worker",
        lease_seconds=60,
    )

    assert completed is not None
    assert completed.id == task.id
    assert completed.status == "succeeded"
    assert completed.search_results_used == 1
    assert completed.current_step == "controlled_page_read_complete:1/1"
    assert observed_urls == ["https://official.example/notice"]
    source = db_session.scalar(select(ResearchSource).where(ResearchSource.task_id == task.id))
    assert source is not None
    assert source.url == "https://official.example/notice"
    citation = db_session.scalar(
        select(ResearchCitation).where(ResearchCitation.source_id == source.id)
    )
    assert citation is not None
    assert citation.verified is True


def test_controlled_worker_leaves_task_without_source_urls_queued(db_session, auth_as) -> None:
    user = auth_as("risk_admin", "controlled-page-empty")
    task = create_task(
        db_session,
        owner_user_id=user.id,
        task_type="manual",
        topic="没有允许来源的任务",
        supplier_scope=[],
        idempotency_key=None,
    )

    assert (
        run_controlled_single_page_once(
            db_session,
            worker_id="controlled-worker",
            lease_seconds=60,
        )
        is None
    )
    db_session.refresh(task)
    assert task.status == "queued"
    assert task.attempts == 0


def test_topic_worker_searches_and_reads_sources_without_user_urls(
    db_session, client, auth_as, monkeypatch
) -> None:
    user = auth_as("risk_admin", "topic-source-worker")
    task = create_task(
        db_session,
        owner_user_id=user.id,
        task_type="manual",
        topic="某供应商近期公开风险",
        supplier_scope=[],
        idempotency_key=None,
    )
    provider = FakeSearchProvider(
        responses={
            task.topic: (
                SearchCandidate(
                    "https://official.example/a", "官方公告", "公告摘要"
                ),
                SearchCandidate(
                    "https://media.example/b", "媒体报道", "报道摘要"
                ),
            )
        }
    )

    async def fake_reader(url: str) -> ResearchPageRead:
        return ResearchPageRead(
            requested_url=url,
            final_url=url,
            redirect_chain=(),
            status_code=200,
            content_type="text/html",
            excerpt=f"已读取公开来源：{url}",
        )

    monkeypatch.setattr(runner, "build_configured_search_provider", lambda: provider)
    monkeypatch.setattr(runner, "read_public_page", fake_reader)
    started = request_controlled_execution(
        db_session,
        task_id=task.id,
        owner_user_id=user.id,
        role="risk_admin",
    )
    assert started is not None

    completed = run_topic_source_discovery_once(
        db_session,
        worker_id="topic-worker",
        lease_seconds=60,
        report_provider=FakeAIProvider(),
    )

    assert completed is not None
    assert completed.status == "succeeded"
    assert completed.search_queries_used == 1
    assert completed.search_results_used == 2
    assert completed.current_step == "report_draft_created:2/2"
    assert provider.calls == [task.topic]
    sources = list(
        db_session.scalars(
            select(ResearchSource)
            .where(ResearchSource.task_id == task.id)
            .order_by(ResearchSource.id)
        )
    )
    assert [source.title for source in sources] == ["官方公告", "媒体报道"]
    response = client.get(f"/api/v1/research/tasks/{task.id}/sources")
    assert response.status_code == 200
    assert len(response.json()["items"]) == 2
    report = db_session.scalar(select(ResearchReport).where(ResearchReport.task_id == task.id))
    assert report is not None
    assert report.model_version == "fake-deterministic-v1"
    draft = report.draft_payload
    assert draft["disclaimer"] == "AI 生成，仅供参考。"
    assert draft["facts"][0]["citation_ids"] == [draft["citations"][0]["citation_id"]]
    assert draft["citations"][0]["verified"] is True
