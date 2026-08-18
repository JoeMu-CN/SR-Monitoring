"""LangGraph PostgreSQL checkpoint 隔离测试；不调用真实研究工具。"""

from uuid import uuid4

from langgraph.checkpoint.postgres import PostgresSaver

from app.research.graph import RESEARCH_GRAPH_VERSION, build_topic_source_discovery_graph
from app.research.runner import postgres_checkpoint_connection_string


def test_postgres_checkpoint_persists_controlled_graph_thread() -> None:
    observed: list[str] = []

    def load_monitoring_context() -> int:
        observed.append("monitoring_context")
        return 3

    def execute_primary_discovery() -> int:
        observed.append("primary_discovery")
        return 3

    def execute_supplemental_discovery() -> int:
        observed.append("supplemental_discovery")
        return 0

    def compose_report(outcome: str) -> None:
        observed.append(f"compose:{outcome}")

    config = {"configurable": {"thread_id": f"checkpoint-test-task-{uuid4()}"}}
    with PostgresSaver.from_conn_string(postgres_checkpoint_connection_string()) as checkpointer:
        checkpointer.setup()
        graph = build_topic_source_discovery_graph(
            load_monitoring_context=load_monitoring_context,
            execute_primary_discovery=execute_primary_discovery,
            execute_supplemental_discovery=execute_supplemental_discovery,
            should_run_supplemental_search=lambda _: True,
            compose_report=compose_report,
            checkpointer=checkpointer,
        )
        graph.invoke(
            {"task_id": 42, "graph_version": RESEARCH_GRAPH_VERSION},
            config=config,
            interrupt_after=["primary_discovery"],
        )
        stored = checkpointer.get_tuple(config)
        result = graph.invoke(None, config=config)

    assert observed == ["monitoring_context", "primary_discovery", "compose:evidence_sufficient"]
    assert result["monitoring_source_count"] == 3
    assert stored is not None
    assert stored.checkpoint["channel_values"]["task_id"] == 42


def test_postgres_checkpoint_does_not_repeat_completed_supplemental_node() -> None:
    observed: list[str] = []
    config = {"configurable": {"thread_id": f"checkpoint-supplemental-task-{uuid4()}"}}

    with PostgresSaver.from_conn_string(postgres_checkpoint_connection_string()) as checkpointer:
        checkpointer.setup()
        graph = build_topic_source_discovery_graph(
            load_monitoring_context=lambda: observed.append("monitoring_context") or 0,
            execute_primary_discovery=lambda: observed.append("primary_discovery") or 0,
            execute_supplemental_discovery=lambda: observed.append("supplemental_discovery") or 1,
            should_run_supplemental_search=lambda _: True,
            compose_report=lambda outcome: observed.append(f"compose:{outcome}"),
            checkpointer=checkpointer,
        )
        graph.invoke(
            {"task_id": 43, "graph_version": RESEARCH_GRAPH_VERSION},
            config=config,
            interrupt_after=["supplemental_discovery"],
        )
        result = graph.invoke(None, config=config)

    assert observed == [
        "monitoring_context",
        "primary_discovery",
        "supplemental_discovery",
        "compose:evidence_sufficient",
    ]
    assert result["supplemental_search_used"] is True
