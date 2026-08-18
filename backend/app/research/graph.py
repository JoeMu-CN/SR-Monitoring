"""研究轨 LangGraph 最小受控图。

本模块只负责步骤编排。任务租约、预算、外部工具、来源和引用仍由调用方的
既有平台服务负责，图状态中不得放入 URL 正文、密钥或数据库会话。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal, cast

from langchain_core.runnables import RunnableLambda
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Checkpointer
from typing_extensions import TypedDict

RESEARCH_GRAPH_VERSION = "research-graph-v2"
MIN_VERIFIED_EVIDENCE = 1
EvidenceOutcome = Literal["evidence_sufficient", "public_evidence_insufficient"]


class ResearchGraphState(TypedDict, total=False):
    """可序列化的图状态；业务事实仍以平台数据库为准。"""

    task_id: int
    graph_version: str
    monitoring_source_count: int
    evidence_count: int
    supplemental_search_used: bool
    outcome: str


def build_topic_source_discovery_graph(
    *,
    load_monitoring_context: Callable[[], int],
    execute_primary_discovery: Callable[[], int],
    execute_supplemental_discovery: Callable[[], int],
    should_run_supplemental_search: Callable[[int], bool],
    compose_report: Callable[[EvidenceOutcome], None],
    checkpointer: Checkpointer = None,
) -> CompiledStateGraph[ResearchGraphState, None, ResearchGraphState, ResearchGraphState]:
    """构建固定的一次补充搜索图，条件仅由平台规则决定。

    未传入 checkpointer 时使用 MemorySaver 供隔离单测；生产 LangGraph 路径会
    注入 PostgreSQL checkpointer。两者均不承担任务、用量或审计的持久化。
    """

    workflow = StateGraph(ResearchGraphState)

    def load_monitoring_node(_: ResearchGraphState) -> ResearchGraphState:
        return {"monitoring_source_count": load_monitoring_context()}

    def primary_discovery_node(_: ResearchGraphState) -> ResearchGraphState:
        return {"evidence_count": execute_primary_discovery()}

    def evaluate_evidence_node(state: ResearchGraphState) -> ResearchGraphState:
        evidence_count = state.get("evidence_count", 0)
        if evidence_count >= MIN_VERIFIED_EVIDENCE:
            return {"outcome": "evidence_sufficient"}
        if not state.get("supplemental_search_used", False) and should_run_supplemental_search(
            evidence_count
        ):
            return {"outcome": "run_supplemental_search"}
        return {"outcome": "public_evidence_insufficient"}

    def route_after_evaluation(state: ResearchGraphState) -> str:
        if state.get("outcome") == "run_supplemental_search":
            return "supplemental_discovery"
        return "compose_report"

    def supplemental_discovery_node(_: ResearchGraphState) -> ResearchGraphState:
        return {
            "evidence_count": execute_supplemental_discovery(),
            "supplemental_search_used": True,
        }

    def compose_report_node(state: ResearchGraphState) -> ResearchGraphState:
        outcome = state.get("outcome")
        if outcome == "evidence_sufficient":
            compose_report(cast(EvidenceOutcome, outcome))
            return {"outcome": outcome}
        if outcome == "public_evidence_insufficient":
            compose_report(cast(EvidenceOutcome, outcome))
            return {"outcome": outcome}
        raise RuntimeError("研究图缺少有效证据评估结果")

    workflow.add_node("monitoring_context", RunnableLambda(load_monitoring_node))
    workflow.add_node("primary_discovery", RunnableLambda(primary_discovery_node))
    workflow.add_node("evaluate_evidence", RunnableLambda(evaluate_evidence_node))
    workflow.add_node("supplemental_discovery", RunnableLambda(supplemental_discovery_node))
    workflow.add_node("compose_report", RunnableLambda(compose_report_node))
    workflow.add_edge(START, "monitoring_context")
    workflow.add_edge("monitoring_context", "primary_discovery")
    workflow.add_edge("primary_discovery", "evaluate_evidence")
    workflow.add_conditional_edges("evaluate_evidence", route_after_evaluation)
    workflow.add_edge("supplemental_discovery", "evaluate_evidence")
    workflow.add_edge("compose_report", END)
    return workflow.compile(checkpointer=checkpointer or MemorySaver())
