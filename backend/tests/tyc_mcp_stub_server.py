"""确定性天眼查 MCP stub 服务（仅隔离测试栈使用，绝不联外网）。

用标准库 ``http.server`` 实现网关所需的最小 JSON-RPC 子集：
``initialize`` / ``notifications/initialized`` / ``tools/call``
（``search_companies`` 返回固定候选表；``call_tool`` 对每个维度返回固定文本）。
本进程只监听容器内端口，不发起任何出站请求，也不读取任何真实密钥。

启动（compose.test.yaml 的 tyc-stub 服务）：

    python tests/tyc_mcp_stub_server.py --port 8081
"""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

SESSION_ID = "tyc-test-stub-session"
EMPTY_CANDIDATES = "未查询到相关记录"


def _candidates_table(company: str) -> str:
    return (
        "| # | 企业名称 | 统一社会信用代码 | 登记状态 |\n"
        "| --- | --- | --- | --- |\n"
        f"| 1 | {company} | 91310000STUB000001 | 存续 |\n"
    )


def _dimension_text(tool_name: str, company: str) -> str:
    return (
        f"# {tool_name}：{company}\n\n"
        f"- tool: `{tool_name}`\n\n"
        f"> 摘要：{company} {tool_name} 确定性记录\n"
    )


def _result(text: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": 2,
        "result": {"content": [{"type": "text", "text": text}], "isError": False},
    }


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:
        del format, args  # 静默：测试 stub 不写访问日志

    def _respond(
        self,
        status: int,
        body: dict[str, Any] | None = None,
        *,
        session: bool = False,
    ) -> None:
        payload = b"" if body is None else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        if session:
            self.send_header("Mcp-Session-Id", SESSION_ID)
        if body is not None:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if payload:
            self.wfile.write(payload)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._respond(200, {"status": "ok", "service": "tyc-mcp-stub"})
            return
        self._respond(404, {"error": "not_found"})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            self._respond(400, {"error": "invalid_json"})
            return
        method = message.get("method")
        if method == "initialize":
            self._respond(
                200,
                {"jsonrpc": "2.0", "id": message.get("id"), "result": {"capabilities": {}}},
                session=True,
            )
            return
        if method == "notifications/initialized":
            self._respond(202)
            return
        if method != "tools/call":
            self._respond(400, {"error": "unsupported_method"})
            return
        params = message.get("params") or {}
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if name == "search_companies":
            self._respond(200, _result(_candidates_table(str(arguments.get("query", "")))))
            return
        tool_name = str(arguments.get("tool_name", ""))
        company = str(arguments.get("company_name", ""))
        self._respond(200, _result(_dimension_text(tool_name, company)))


def main() -> None:
    parser = argparse.ArgumentParser(description="天眼查 MCP 确定性测试 stub")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8081)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), _Handler)
    print(f"tyc-mcp-stub listening on {args.host}:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
