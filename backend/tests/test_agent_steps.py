"""风险查询助手「运行步骤」增量轮询测试：全程使用 FakeAgentLLM，不访问真实模型。"""

from collections.abc import Callable

from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from app.agent.engine import FakeAgentLLM
from app.auth.models import User

VALID_KINDS = {"analyzing", "tool_start", "tool_done", "finalizing"}


def _force_fake_llm(monkeypatch: MonkeyPatch) -> None:
    import app.agent.service as agent_service

    monkeypatch.setattr(agent_service, "get_agent_llm", lambda: FakeAgentLLM())


def test_chat_with_run_token_exposes_tool_steps(
    client: TestClient, monkeypatch: MonkeyPatch
) -> None:
    """Given 带 run_token 的风险问题，When chat 完成后轮询，Then 步骤已 done 且含工具执行。"""
    _force_fake_llm(monkeypatch)
    run_token = "steps-toolcase-001"

    # When
    response = client.post(
        "/api/v1/chat",
        json={"question": "今天有什么风险？", "run_token": run_token},
    )

    # Then：chat 响应结构保持既有契约
    assert response.status_code == 200
    body = response.json()
    assert set(body) >= {"session_id", "answer", "tool_calls"}
    assert body["tool_calls"][0]["name"] == "query_current_alerts"

    # When：轮询步骤
    steps_response = client.get(f"/api/v1/chat/steps/{run_token}")

    # Then：已完成，index 连续，含 query_current_alerts 的 tool_start/tool_done
    assert steps_response.status_code == 200
    steps_body = steps_response.json()
    assert steps_body["run_token"] == run_token
    assert steps_body["status"] == "done"
    steps = steps_body["steps"]
    assert [step["index"] for step in steps] == list(range(1, len(steps) + 1))
    kinds = [step["kind"] for step in steps]
    assert kinds[0] == "analyzing"
    assert kinds[-1] == "finalizing"
    assert set(kinds) <= VALID_KINDS
    start = kinds.index("tool_start")
    assert kinds[start + 1] == "tool_done"
    assert steps[start]["tool"] == "query_current_alerts"
    assert steps[start + 1]["tool"] == "query_current_alerts"


def test_unknown_run_token_returns_unknown(client: TestClient) -> None:
    """Given 从未登记的 token，When 轮询，Then unknown 且 steps 为空。"""
    response = client.get("/api/v1/chat/steps/never-started-token-001")

    assert response.status_code == 200
    assert response.json() == {
        "run_token": "never-started-token-001",
        "status": "unknown",
        "steps": [],
    }


def test_chat_without_run_token_keeps_response_shape(
    client: TestClient, monkeypatch: MonkeyPatch
) -> None:
    """Given 不带 run_token，When chat，Then 既有响应结构与语义不变。"""
    _force_fake_llm(monkeypatch)

    response = client.post("/api/v1/chat", json={"question": "今天有什么风险？"})

    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] > 0
    assert body["answer"]
    assert [call["name"] for call in body["tool_calls"]] == ["query_current_alerts"]
    assert "run_token" not in body


def test_run_steps_hidden_from_non_owner(
    client: TestClient,
    auth_as: Callable[[str, str], User],
    monkeypatch: MonkeyPatch,
) -> None:
    """Given 另一用户会话，When 轮询他人 run_token，Then 语义等同未知 token。"""
    _force_fake_llm(monkeypatch)
    run_token = "owner-scoped-token-001"
    posted = client.post(
        "/api/v1/chat",
        json={"question": "今天有什么风险？", "run_token": run_token},
    )
    assert posted.status_code == 200

    # When：切换为另一用户
    auth_as("platform_admin", "another-risk-admin")
    response = client.get(f"/api/v1/chat/steps/{run_token}")

    # Then
    assert response.status_code == 200
    assert response.json() == {
        "run_token": run_token,
        "status": "unknown",
        "steps": [],
    }


def test_chat_rejects_invalid_run_token(client: TestClient) -> None:
    """Given 非法字符集 token，When chat，Then 契约校验 422。"""
    response = client.post(
        "/api/v1/chat",
        json={"question": "今天有什么风险？", "run_token": "bad token!"},
    )

    assert response.status_code == 422
