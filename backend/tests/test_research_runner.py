"""本地 research-worker 生命周期与受控单页读取测试。"""

import pytest
from sqlalchemy import select

from app.ai.models import AIAnalysisRecord
from app.ai.providers import FakeAIProvider, GeneratedResearchReport
from app.auth.models import SecurityAuditEvent
from app.research import runner
from app.research.models import (
    ResearchCitation,
    ResearchReport,
    ResearchSource,
    ResearchTaskEvent,
    ResearchToolRun,
)
from app.research.reporting import ResearchClaimDraft, ResearchReportDraft
from app.research.runner import (
    LOCAL_LIFECYCLE_TEST_TOPIC_PREFIX,
    load_monitoring_context,
    run_controlled_single_page_once,
    run_local_lifecycle_test_once,
    run_topic_source_discovery_once,
)
from app.research.search import FakeSearchProvider, SearchCandidate
from app.research.service import create_task, request_controlled_execution
from app.research.web import ResearchPageRead
from app.risks.models import (
    EventLocation,
    RiskAlert,
    RiskEvent,
    RiskEventSignal,
    SupplierEventMatch,
)
from app.signals.models import DataSource, RawSignal
from app.signals.request_control import SourceRequestFailed
from app.suppliers.models import Supplier, SupplierSite


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


def test_research_monitoring_context_filters_legacy_cross_district_weather_alert(
    db_session, auth_as
) -> None:
    user = auth_as("risk_admin", "research-district-filter")
    supplier = Supplier(
        supplier_code="DISTRICT-FILTER",
        legal_name="松江供应商",
        country_code="CN",
        sites=[
            SupplierSite(
                site_name="上海工厂",
                country_code="CN",
                region="上海市",
                city="松江区",
                district=None,
                address="上海市松江区测试路1号",
            )
        ],
    )
    db_session.add(supplier)
    db_session.flush()
    task = create_task(
        db_session,
        owner_user_id=user.id,
        task_type="manual",
        topic="区级天气风险过滤",
        supplier_scope=[supplier.id],
        idempotency_key=None,
    )
    source = DataSource(
        code="nmc-weather-test",
        name="气象测试源",
        source_type="weather",
        credibility=95,
    )
    signal = RawSignal(
        source=source,
        external_id="weather-district-1",
        title="上海市宝山区大风预警",
        content="上海市宝山区发布大风预警。",
        url="https://weather.example/alerts/1",
        fingerprint="weather-district-1-fingerprint",
        raw_data={},
    )
    event = RiskEvent(
        dedup_key="weather-district-event-1",
        event_type="weather",
        severity="high",
        summary="上海市宝山区大风预警",
        confidence=0.9,
        facts={},
    )
    db_session.add_all([source, signal, event])
    db_session.flush()
    db_session.add_all(
        [
            EventLocation(
                event_id=event.id,
                name="上海市宝山区",
                normalized_name="上海市宝山区",
                country_code="CN",
                region="上海市",
                city="上海市",
                district=None,
            ),
            RiskEventSignal(event_id=event.id, signal_id=signal.id),
            AIAnalysisRecord(
                signal_id=signal.id,
                provider="test",
                model="test",
                prompt_version="test-v1",
                status="succeeded",
                result={},
                needs_review=False,
            ),
        ]
    )
    db_session.flush()
    match = SupplierEventMatch(
        supplier_id=supplier.id,
        event_id=event.id,
        match_type="site_text",
        score=20,
        reasons=["历史匹配"],
        evidence=[],
    )
    db_session.add(match)
    db_session.flush()
    db_session.add(
        RiskAlert(
            match_id=match.id,
            level="P2",
            score=70,
            score_detail={},
            status="current",
        )
    )
    db_session.commit()

    assert load_monitoring_context(db_session, task) == 0
    assert (
        db_session.scalar(select(ResearchSource).where(ResearchSource.task_id == task.id))
        is None
    )
    filtered = db_session.scalar(
        select(ResearchTaskEvent).where(
            ResearchTaskEvent.task_id == task.id,
            ResearchTaskEvent.event_type == "monitoring_signal_filtered",
        )
    )
    assert filtered is None
    completed = db_session.scalar(
        select(ResearchTaskEvent).where(
            ResearchTaskEvent.task_id == task.id,
            ResearchTaskEvent.event_type == "monitoring_context_completed",
        )
    )
    assert completed is not None
    assert completed.detail["filtered_location_count"] == 1


@pytest.mark.parametrize("orchestrator", ["legacy", "langgraph"])
def test_topic_worker_searches_and_reads_sources_without_user_urls(
    db_session, client, auth_as, monkeypatch, orchestrator
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
        orchestrator=orchestrator,
    )

    assert completed is not None
    assert completed.status == "succeeded"
    assert completed.search_queries_used == 1
    assert completed.search_results_used == 2
    if orchestrator == "legacy":
        assert completed.current_step == "report_draft_created:2/2"
        assert completed.graph_version is None
        assert completed.checkpoint_thread_id is None
    else:
        assert completed.current_step == "research_report_generated"
        assert completed.graph_version == "research-graph-v2"
        assert completed.checkpoint_thread_id == (
            f"research-task-{task.id}-research-graph-v2"
        )
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
    event_types = list(
        db_session.scalars(
            select(ResearchTaskEvent.event_type)
            .where(ResearchTaskEvent.task_id == task.id)
            .order_by(ResearchTaskEvent.id)
        )
    )
    if orchestrator == "langgraph":
        assert "agent_graph_started" in event_types
        assert "agent_graph_completed" in event_types
    else:
        assert "agent_graph_started" not in event_types


def test_supplier_scoped_search_uses_legal_name_and_filters_wrong_entity(
    db_session, auth_as, monkeypatch
) -> None:
    user = auth_as("risk_admin", "supplier-entity-filter")
    supplier = Supplier(
        supplier_code="SUP-ENTITY-001",
        legal_name="上海华美电梯装饰有限公司",
        country_code="CN",
        registry_no="91310117674635790W",
    )
    db_session.add(supplier)
    db_session.commit()
    task = create_task(
        db_session,
        owner_user_id=user.id,
        task_type="manual",
        topic="查询上海华美最近30天的风险情况",
        supplier_scope=[supplier.id],
        idempotency_key=None,
    )
    effective_query = f'"{supplier.legal_name}" {task.topic}'
    provider = FakeSearchProvider(
        responses={
            effective_query: (
                SearchCandidate(
                    "https://finance.example/wrong",
                    "盛美上海季度机构持股变化",
                    "盛美上海（688082）披露季度数据",
                ),
                SearchCandidate(
                    "https://official.example/right",
                    "上海华美行政处罚公告",
                    "上海华美风险信息更新",
                ),
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
            excerpt=f"{supplier.legal_name}公开风险公告。",
        )

    monkeypatch.setattr(runner, "build_configured_search_provider", lambda: provider)
    monkeypatch.setattr(runner, "read_public_page", fake_reader)
    assert request_controlled_execution(
        db_session,
        task_id=task.id,
        owner_user_id=user.id,
        role="risk_admin",
    ) is not None

    completed = run_topic_source_discovery_once(
        db_session,
        worker_id="supplier-entity-worker",
        lease_seconds=60,
        report_provider=FakeAIProvider(),
        orchestrator="langgraph",
    )

    assert completed is not None and completed.status == "succeeded"
    assert provider.calls == [effective_query]
    assert read_urls == ["https://official.example/right"]
    sources = list(
        db_session.scalars(
            select(ResearchSource)
            .where(ResearchSource.task_id == task.id)
            .order_by(ResearchSource.id)
        )
    )
    assert [source.title for source in sources] == ["上海华美行政处罚公告"]
    search_completed = db_session.scalar(
        select(ResearchTaskEvent).where(
            ResearchTaskEvent.task_id == task.id,
            ResearchTaskEvent.event_type == "web_search_completed",
        )
    )
    assert search_completed is not None
    assert search_completed.detail == {
        "provider": "fake",
        "query": effective_query,
        "provider_candidate_count": 2,
        "candidate_count": 1,
        "entity_filtered_count": 1,
    }


def test_report_validation_failure_finishes_tool_run_and_records_usage(
    db_session, auth_as, monkeypatch
) -> None:
    user = auth_as("risk_admin", "report-validation-failure")
    task = create_task(
        db_session,
        owner_user_id=user.id,
        task_type="manual",
        topic="报告引用校验失败",
        supplier_scope=[],
        idempotency_key=None,
    )
    provider = FakeSearchProvider(
        responses={
            task.topic: (
                SearchCandidate(
                    "https://official.example/report",
                    "供应商风险公告",
                    "供应商风险公告摘要",
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
            excerpt="供应商风险公告正文。",
        )

    class InvalidReportProvider(FakeAIProvider):
        provider_name = "invalid-report-test"
        model = "invalid-report-model"

        async def generate_research_report(self, value, *, max_output_tokens):
            del value, max_output_tokens
            return GeneratedResearchReport(
                draft=ResearchReportDraft(
                    title="错误引用报告",
                    disclaimer="AI 生成，仅供参考。",
                    facts=[
                        ResearchClaimDraft(
                            claim_id="fact-1",
                            claim_type="fact",
                            text="无法通过引用校验的结论",
                            citation_ids=["citation-not-provided"],
                        )
                    ],
                ),
                input_tokens=12,
                output_tokens=34,
            )

    monkeypatch.setattr(runner, "build_configured_search_provider", lambda: provider)
    monkeypatch.setattr(runner, "read_public_page", fake_reader)
    assert request_controlled_execution(
        db_session,
        task_id=task.id,
        owner_user_id=user.id,
        role="risk_admin",
    ) is not None

    completed = run_topic_source_discovery_once(
        db_session,
        worker_id="report-validation-worker",
        lease_seconds=60,
        report_provider=InvalidReportProvider(),
        orchestrator="legacy",
    )

    assert completed is not None and completed.status == "failed"
    assert completed.input_tokens_used == 12
    assert completed.output_tokens_used == 34
    report_run = db_session.scalar(
        select(ResearchToolRun).where(
            ResearchToolRun.task_id == task.id,
            ResearchToolRun.action_type == "report_generation",
        )
    )
    assert report_run is not None
    assert report_run.status == "failed"
    assert report_run.error_category == "report_validation_failed"
    assert report_run.usage_snapshot == {"input_tokens": 12, "output_tokens": 34}
    failure_event = db_session.scalar(
        select(ResearchTaskEvent).where(
            ResearchTaskEvent.task_id == task.id,
            ResearchTaskEvent.event_type == "report_generation_failed",
        )
    )
    assert failure_event is not None
    assert failure_event.status == "failed"
    assert failure_event.detail["model"] == "invalid-report-model"
    assert failure_event.detail["error_kind"] == "report_validation_failed"


@pytest.mark.parametrize("orchestrator", ["legacy", "langgraph"])
def test_topic_worker_records_candidate_read_failures_and_finishes_with_insufficient_evidence(
    db_session, auth_as, monkeypatch, orchestrator
) -> None:
    user = auth_as("risk_admin", f"topic-source-failure-{orchestrator}")
    task = create_task(
        db_session,
        owner_user_id=user.id,
        task_type="manual",
        topic="候选来源读取失败诊断",
        supplier_scope=[],
        idempotency_key=None,
        budget_snapshot={
            "max_queries": 1,
            "max_results": 10,
            "max_pages": 5,
            "max_input_tokens": 20_000,
            "max_output_tokens": 5_000,
        },
    )
    provider = FakeSearchProvider(
        responses={
            task.topic: (
                SearchCandidate("https://blocked.example/a", "被阻断来源", "摘要"),
                SearchCandidate("https://invalid.example/b", "无效来源", "摘要"),
            )
        }
    )

    async def fake_reader(url: str) -> ResearchPageRead:
        if url.endswith("/a"):
            raise SourceRequestFailed(
                "来源被 robots 阻断",
                error_kind="robots",
                status_code=403,
            )
        raise ValueError("候选 URL 不可读取")

    monkeypatch.setattr(runner, "build_configured_search_provider", lambda: provider)
    monkeypatch.setattr(runner, "read_public_page", fake_reader)
    assert request_controlled_execution(
        db_session, task_id=task.id, owner_user_id=user.id, role="risk_admin"
    ) is not None

    completed = run_topic_source_discovery_once(
        db_session,
        worker_id=f"topic-failure-worker-{orchestrator}",
        lease_seconds=60,
        report_provider=FakeAIProvider(),
        orchestrator=orchestrator,
    )

    assert completed is not None and completed.status == "succeeded"
    assert provider.calls == [task.topic]
    assert db_session.scalar(
        select(ResearchSource).where(ResearchSource.task_id == task.id)
    ) is None
    report = db_session.scalar(select(ResearchReport).where(ResearchReport.task_id == task.id))
    assert report is not None
    assert report.model_version == "system-insufficient-evidence-v1"

    events = list(
        db_session.scalars(
            select(ResearchTaskEvent)
            .where(ResearchTaskEvent.task_id == task.id)
            .order_by(ResearchTaskEvent.id)
        )
    )
    failures = [event for event in events if event.event_type == "source_read_failed"]
    assert [event.detail["error_kind"] for event in failures] == ["robots", "invalid_source"]
    assert failures[0].detail["status_code"] == 403
    assert "message" not in failures[0].detail
    summary = next(event for event in events if event.event_type == "source_read_summary")
    assert summary.detail == {
        "candidate_count": 2,
        "read_success_count": 0,
        "read_failure_count": 2,
        "duplicate_count": 0,
    }


def test_langgraph_worker_runs_one_supplemental_search_after_empty_primary_result(
    db_session, auth_as, monkeypatch
) -> None:
    user = auth_as("risk_admin", "langgraph-supplemental-success")
    task = create_task(
        db_session,
        owner_user_id=user.id,
        task_type="manual",
        topic="补充搜索成功",
        supplier_scope=[],
        idempotency_key=None,
        budget_snapshot={
            "max_queries": 2,
            "max_results": 10,
            "max_pages": 5,
            "max_input_tokens": 20_000,
            "max_output_tokens": 5_000,
        },
    )
    supplemental_query = f"{task.topic} 风险 公告"
    provider = FakeSearchProvider(
        responses={
            task.topic: (),
            supplemental_query: (
                SearchCandidate("https://official.example/supplement", "补充公告", "摘要"),
            ),
        }
    )

    async def fake_reader(url: str) -> ResearchPageRead:
        return ResearchPageRead(
            requested_url=url,
            final_url=url,
            redirect_chain=(),
            status_code=200,
            content_type="text/html",
            excerpt="补充搜索找到可回验的公开证据。",
        )

    monkeypatch.setattr(runner, "build_configured_search_provider", lambda: provider)
    monkeypatch.setattr(runner, "read_public_page", fake_reader)
    assert request_controlled_execution(
        db_session, task_id=task.id, owner_user_id=user.id, role="risk_admin"
    ) is not None

    completed = run_topic_source_discovery_once(
        db_session,
        worker_id="langgraph-supplemental-worker",
        lease_seconds=60,
        report_provider=FakeAIProvider(),
        orchestrator="langgraph",
    )

    assert completed is not None and completed.status == "succeeded"
    assert provider.calls == [task.topic, supplemental_query]
    assert completed.search_queries_used == 2
    events = list(
        db_session.scalars(
            select(ResearchTaskEvent.event_type)
            .where(ResearchTaskEvent.task_id == task.id)
            .order_by(ResearchTaskEvent.id)
        )
    )
    assert "supplemental_search_started" in events
    assert "report_generated" in events


@pytest.mark.parametrize("max_queries", [1, 2])
def test_langgraph_worker_stops_with_public_evidence_insufficient_draft(
    db_session, auth_as, monkeypatch, max_queries
) -> None:
    user = auth_as("risk_admin", f"langgraph-insufficient-{max_queries}")
    task = create_task(
        db_session,
        owner_user_id=user.id,
        task_type="manual",
        topic="无有效公开证据",
        supplier_scope=[],
        idempotency_key=None,
        budget_snapshot={
            "max_queries": max_queries,
            "max_results": 10,
            "max_pages": 5,
            "max_input_tokens": 20_000,
            "max_output_tokens": 5_000,
        },
    )
    provider = FakeSearchProvider()
    monkeypatch.setattr(runner, "build_configured_search_provider", lambda: provider)
    assert request_controlled_execution(
        db_session, task_id=task.id, owner_user_id=user.id, role="risk_admin"
    ) is not None

    completed = run_topic_source_discovery_once(
        db_session,
        worker_id=f"langgraph-insufficient-worker-{max_queries}",
        lease_seconds=60,
        report_provider=FakeAIProvider(),
        orchestrator="langgraph",
    )

    expected_calls = [task.topic]
    if max_queries == 2:
        expected_calls.append(f"{task.topic} 风险 公告")
    assert completed is not None and completed.status == "succeeded"
    assert provider.calls == expected_calls
    report = db_session.scalar(select(ResearchReport).where(ResearchReport.task_id == task.id))
    assert report is not None
    assert report.model_version == "system-insufficient-evidence-v1"
    assert report.draft_payload["facts"] == []
    assert completed.current_step == "public_evidence_insufficient"


def test_topic_worker_merges_monitoring_signal_and_skips_duplicate_search_url(
    db_session, auth_as, monkeypatch
) -> None:
    user = auth_as("risk_admin", "topic-monitoring-context")
    data_source = db_session.scalar(select(DataSource).where(DataSource.code == "nmc-weather"))
    assert data_source is not None
    supplier = Supplier(
        supplier_code="SUP-MONITORING-001",
        legal_name="监控上下文供应商",
        country_code="CN",
    )
    db_session.add(supplier)
    db_session.flush()
    signal = RawSignal(
        source_id=data_source.id,
        title="供应商所在地发布风险公告",
        content="官方公告显示该地区出现供应链中断风险，需要持续观察。",
        url="https://official.example/monitoring-notice",
        fingerprint="monitoring-context-signal",
        raw_data={},
    )
    db_session.add(signal)
    db_session.flush()
    assert signal.url is not None
    db_session.add(
        AIAnalysisRecord(
            signal_id=signal.id,
            provider="fake",
            model="fake",
            prompt_version="test",
            status="succeeded",
            result={"event_type": "weather"},
            needs_review=False,
        )
    )
    event = RiskEvent(
        dedup_key="monitoring-context-event",
        event_type="weather",
        severity="high",
        summary="供应链中断风险",
        confidence=0.9,
        facts={},
    )
    db_session.add(event)
    db_session.flush()
    db_session.add(RiskEventSignal(event_id=event.id, signal_id=signal.id))
    match = SupplierEventMatch(
        supplier_id=supplier.id,
        event_id=event.id,
        match_type="entity",
        score=90,
        reasons=["供应商主体匹配"],
        evidence=[],
    )
    db_session.add(match)
    db_session.flush()
    db_session.add(
        RiskAlert(
            match_id=match.id,
            level="P1",
            score=90,
            score_detail={},
            status="current",
        )
    )
    db_session.commit()
    task = create_task(
        db_session,
        owner_user_id=user.id,
        task_type="manual",
        topic="监控上下文融合",
        supplier_scope=[supplier.id],
        idempotency_key=None,
    )
    effective_query = f'"{supplier.legal_name}" {task.topic}'
    provider = FakeSearchProvider(
        responses={
            effective_query: (
                SearchCandidate(
                    signal.url,
                    f"{supplier.legal_name}重复公告",
                    "重复摘要",
                ),
                SearchCandidate(
                    "https://media.example/new",
                    f"{supplier.legal_name}补充报道",
                    "补充摘要",
                ),
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
            excerpt="补充报道提供了新的公开证据。",
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
        worker_id="topic-monitoring-worker",
        lease_seconds=60,
        report_provider=FakeAIProvider(),
    )

    assert completed is not None and completed.status == "succeeded"
    assert provider.calls == [effective_query]
    assert read_urls == ["https://media.example/new"]
    sources = list(
        db_session.scalars(
            select(ResearchSource)
            .where(ResearchSource.task_id == task.id)
            .order_by(ResearchSource.id)
        )
    )
    assert [source.source_type for source in sources] == [
        "monitoring_signal",
        "web_search",
    ]
    assert sources[0].source_metadata["signal_id"] == signal.id
    events = list(
        db_session.scalars(
            select(ResearchTaskEvent)
            .where(ResearchTaskEvent.task_id == task.id)
            .order_by(ResearchTaskEvent.id)
        )
    )
    assert "monitoring_signal_added" in [item.event_type for item in events]
    assert "source_duplicate_skipped" in [item.event_type for item in events]
    assert "report_generated" in [item.event_type for item in events]


def test_monthly_child_task_is_claimed_by_same_topic_worker(
    db_session, auth_as, monkeypatch
) -> None:
    user = auth_as("risk_admin", "monthly-topic-worker")
    task = create_task(
        db_session,
        owner_user_id=user.id,
        task_type="monthly",
        topic="月报子任务公开风险",
        supplier_scope=[9999],
        idempotency_key="scheduled:monthly:2026-08:9999",
    )
    provider = FakeSearchProvider(
        responses={
            task.topic: (
                SearchCandidate("https://official.example/monthly", "月报公告", "摘要"),
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
            excerpt="月报子任务读取到公开证据。",
        )

    monkeypatch.setattr(runner, "build_configured_search_provider", lambda: provider)
    monkeypatch.setattr(runner, "read_public_page", fake_reader)

    completed = run_topic_source_discovery_once(
        db_session,
        worker_id="monthly-topic-worker",
        lease_seconds=60,
        report_provider=FakeAIProvider(),
        orchestrator="legacy",
    )

    assert completed is not None
    assert completed.id == task.id
    assert completed.status == "succeeded"
    assert task.execution_requested_at is not None
    assert provider.calls == [task.topic]
