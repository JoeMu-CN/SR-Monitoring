"""LangGraph 研究图的隔离测试；不访问数据库、网页或模型。"""

from app.research.graph import RESEARCH_GRAPH_VERSION, build_topic_source_discovery_graph


def test_topic_source_discovery_graph_runs_nodes_in_controlled_order() -> None:
    observed: list[str] = []

    def load_monitoring_context() -> int:
        observed.append("monitoring_context")
        return 2

    def execute_primary_discovery() -> int:
        observed.append("primary_discovery")
        return 1

    def execute_supplemental_discovery() -> int:
        observed.append("supplemental_discovery")
        return 0

    def compose_report(outcome: str) -> None:
        observed.append(f"compose:{outcome}")

    graph = build_topic_source_discovery_graph(
        load_monitoring_context=load_monitoring_context,
        execute_primary_discovery=execute_primary_discovery,
        execute_supplemental_discovery=execute_supplemental_discovery,
        should_run_supplemental_search=lambda _: True,
        compose_report=compose_report,
    )
    result = graph.invoke(
        {"task_id": 42, "graph_version": RESEARCH_GRAPH_VERSION},
        config={"configurable": {"thread_id": "research-task-42"}},
    )

    assert observed == ["monitoring_context", "primary_discovery", "compose:evidence_sufficient"]
    assert result["monitoring_source_count"] == 2
    assert result["evidence_count"] == 1
    assert result["outcome"] == "evidence_sufficient"


def test_topic_source_discovery_graph_runs_one_supplemental_search_then_stops() -> None:
    observed: list[str] = []

    graph = build_topic_source_discovery_graph(
        load_monitoring_context=lambda: 0,
        execute_primary_discovery=lambda: observed.append("primary") or 0,
        execute_supplemental_discovery=lambda: observed.append("supplemental") or 0,
        should_run_supplemental_search=lambda _: True,
        compose_report=lambda outcome: observed.append(f"compose:{outcome}"),
    )

    result = graph.invoke(
        {"task_id": 43, "graph_version": RESEARCH_GRAPH_VERSION},
        config={"configurable": {"thread_id": "research-task-43"}},
    )

    assert observed == ["primary", "supplemental", "compose:public_evidence_insufficient"]
    assert result["supplemental_search_used"] is True
    assert result["outcome"] == "public_evidence_insufficient"
