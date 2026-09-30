"""Todo 5 天眼查多维度网关测试（独立文件，避免与并行任务共享写点）。

执行环境：Compose 隔离测试栈（``scripts/test-backend.ps1``），真实 PostgreSQL
测试库 + ``httpx.MockTransport`` 确定性 MCP stub；**不调用真实天眼查**。

覆盖：
- 兼容基线：``verify()`` / ``search_companies()`` / ``UnconfiguredTycGateway``；
- 维度方案：``DEFAULT_TYC_DIMENSIONS`` 精确等于 Todo 1 的 final_dimension_set，
  已知候选池 17 项，``login_config.tyc_dimensions`` 覆盖非法即整体回落；
- ``fetch_dimensions``：1 次锚定 + N 次维度调用全部经 ``execute_tyc_tool_with_quota``，
  list 工具显式分页，单维失败隔离，quota/busy/not_configured（来源撤销）阻断不超发，
  独立连接查账。
"""

import asyncio
import hashlib
import json
import logging
from collections.abc import Generator

import httpx
import pytest
from pytest import LogCaptureFixture, MonkeyPatch
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

import app.agent.tyc_dimensions as tyc_dimensions_module
import app.agent.tyc_quota as tyc_quota_module
from app.agent.budget import get_tyc_usage
from app.agent.models import TycUsageRecord
from app.agent.tyc_dimensions import (
    DEFAULT_TYC_DIMENSIONS,
    KNOWN_TYC_DIMENSIONS,
    is_empty_dimension_text,
    resolve_tyc_dimensions,
)
from app.agent.tyc_gateway import (
    McpTycGateway,
    UnconfiguredTycGateway,
    build_tyc_gateway,
)
from app.agent.tyc_quota import (
    NOT_CONFIGURED_MESSAGE,
    RemoteCall,
    TycQuotaExecutionResult,
    TycQuotaOutcome,
)
from app.agent.tyc_report import DimensionStatus, build_risk_report
from app.database import engine
from app.signals.models import DataSource

# Todo 1 证据（.omo/evidence/tyc-multidim-risk-llm/todo1.md）§3：final_dimension_set。
_TODO1_FINAL_DIMENSION_SET = [
    "get_risk_overview",
    "get_judicial_case",
    "get_default_event_info",
    "get_hearing_notice",
    "get_court_notice",
    "get_administrative_license",
    "get_random_check",
    "get_spot_check_info",
    "get_credit_evaluation",
    "get_shell_company_check",
    "get_change_records",
    "get_actual_controller",
]

# Todo 1 §3：未入选 12 维的 5 个实测可用候选（扩展池）。
_TODO1_EXTRA_TOOLS = (
    "get_beneficial_owners",
    "get_external_investments",
    "get_shareholder_info",
    "get_company_registration_info",
    "get_equity_ratio",
)

# Todo 1 §3「需 page/page_size = 是」的 11 个 list 工具。
_TODO1_LIST_TOOLS = frozenset(
    {
        "get_judicial_case",
        "get_default_event_info",
        "get_hearing_notice",
        "get_court_notice",
        "get_administrative_license",
        "get_random_check",
        "get_credit_evaluation",
        "get_change_records",
        "get_beneficial_owners",
        "get_external_investments",
        "get_shareholder_info",
    }
)

_CANDIDATES_MD = (
    "| # | 企业名称 | 统一社会信用代码 | 登记状态 | 法定代表人 |\n"
    "| --- | --- | --- | --- | --- |\n"
    "| 1 | 维度测试科技有限公司 | 91310000DIM000001 | 存续 | 王五 |\n"
)

_DIMENSION_TEXT = (
    "# 风险总览：维度测试科技有限公司\n"
    "\n"
    "- tool: `get_risk_overview`\n"
    "\n"
    "| 风险类型 | 风险等级 | 风险数量 |\n"
    "| --- | --- | --- |\n"
    "| 开庭公告 | 警示 | 1 |\n"
)

_EMPTY_DIMENSION_TEXT = "> 空结果：未发现该维度记录"

# Todo 1 真实样本（tyc_raw_result.md，行号见各常量注释）；F1 混合判定回归专用。

# tyc_raw_result.md L153-L181

_REAL_CREDIT_EVALUATION_MIXED = """\
# 经营与公示：宁波鸿腾精密制造股份有限公司

- tool: `get_credit_evaluation`

### 税务评级

#### 概览字段

| 字段 | 值 |
|---|---|
| 总数 | 6 |
| pageNum | 1 |
| pageSize | 20 |
| 状态 | ok |

#### 明细（6 条）

| # | 名称 | 年份 | creditLevel | evaluationOrg | taxType | 纳税人识别号 |
|---|---|---|---|---|---|---|
| 1 | 宁波鸿腾精密制造股份有限公司 | 2021 | A | 国家税务总局宁波市鄞州区税务局百丈税务所 | \
国税 | 913302127995394959 |
| 2 | 宁波鸿腾精密制造股份有限公司 | 2020 | A | 国家税务总局 | 国税 | 913302127995394959 |
| 3 | 宁波鸿腾精密制造股份有限公司 | 2019 | A | 国家税务总局 | 国税 | 913302127995394959 |
| 4 | 宁波鸿腾精密制造股份有限公司 | 2018 | A | 国家税务总局 | 国税 | 913302127995394959 |
| 5 | 宁波鸿腾精密制造股份有限公司 | 2017 | A | 国家税务总局 | 国税 | 913302127995394959 |
| 6 | 宁波市鸿腾机电有限公司 | 2016 | A | 国家税务总局 | 国税 | 913302127995394959 |

### 企业信用评级

> 空结果：未发现企业信用评级记录"""

# tyc_raw_result.md L216-L275

_REAL_ADMINISTRATIVE_LICENSE_MIXED = """\
# 行政许可：宁波鸿腾精密制造股份有限公司

- tool: `get_administrative_license`

> 摘要：该查询实体共有19条行政许可记录。

### 行政许可

#### 概览字段

| 字段 | 值 |
|---|---|
| 总数 | 8 |
| pageNum | 1 |
| pageSize | 20 |
| 状态 | ok |

#### 明细（8 条）

| # | 结束日期 | 来源 | 决定日期 | licenceDepartment | licenceName | licenceContent | licens\
eNumber |
|---|---|---|---|---|---|---|---|
| 1 | 2099-12-31 | 国家市场监督管理总局 | 2021-10-27 | 11330200MB15150223 | 对纳税人延期缴纳\
税款的核准 | FORM0=ffswjgmc=国家税务总局宁波市税务局；nowY=2021；nsrsbh=(913302127995394959)\
；nowD=22；nowM=10；TABLE0=yqjnse=567022.64元；yqjkqx=2022-01-26；zsxmDm=企业所得税 | - |
| 2 | 2099-12-31 | 国家市场监督管理总局 | 2021-03-29 | 宁波人社一体化经办平台 | 330203210329\
850212780 | 企业实行综合计算工时工作制审批 | 330203210329850212780 |
| 3 | 2099-12-31 | 国家市场监督管理总局 | 2021-03-29 | 宁波人社业务协同平台1 | 3302032103298\
50116805 | 企业实行不定时工作制审批 | 330203210329850116805 |
| 4 | 2099-12-31 | 国家市场监督管理总局 | 2020-08-19 | 海曙区税务局 | 330203200819854307244 \
| 对纳税人延期缴纳税款的核准 | 330203200819854307244 |
| 5 | 2099-12-31 | 国家市场监督管理总局 | 2020-07-21 | 浙江省工商行政管理局 | 33020120072152\
5482901 | 宁波鸿腾精密制造股份有限公司的变更登记 | 330201200721525482901 |
| 6 | 2099-12-31 | 国家市场监督管理总局 | 2020-03-20 | 浙江省宁波市人力资源和社会保障局 | 综\
合计算工时工作制审批 | 关于申请综合计算工时工作制审批 | 无 |
| 7 | 2099-12-31 | 国家市场监督管理总局 | 2019-01-15 | 宁波市海曙区道路运输管理所 | 关于宁波\
鸿腾精密制造股份有限公司的许可决定 | 货运企业宁波鸿腾精密制造股份有限公司(原有效期2015-05-13\
至2019-05-13)申请续营 | ZJ0203201901150019 |
| 8 | - | 中国人民银行 | 2018-12-07 | 中国人民银行宁波市中心支行 | 基本存款账户开户许可证 | \
账户变更 | J3320009937203 |

### 行政许可-其他来源

> 空结果：未发现行政许可-其他来源记录

### 行政许可-工商局

#### 概览字段

| 字段 | 值 |
|---|---|
| 总数 | 11 |
| pageSize | 20 |
| pageNum | 1 |
| 状态 | ok |

#### 明细（11 条）

| # | 部门 | 有效期起 | 许可名称 | 许可证编号 | 许可范围 | 有效期止 |
|---|---|---|---|---|---|---|
| 1 | 11330200MB15150223 | 2021-10-27 | 对纳税人延期缴纳税款的核准 | - | FORM0=ffswjgmc=国家\
税务总局宁波市税务局；nowD=22；nsrsbh=(913302127995394959)；nowY=2021；nowM=10；TABLE0=yqjns\
e=567022.64元；yqjkqx=2022-01-26；zsxmDm=企业所得税 | 2099-12-31 |
| 2 | 海曙区税务局 | 2021-10-27 | 330203211027884000758 | (甬税)许准字﹝2021﹞第(10064)号 | \
FORM0=ffswjgmc=国家税务总局宁波市税务局；nsrsbh=(913302127995394959)；nowM=10；nowD=22；nowY\
=2021；TABLE0=yqjnse=567022.64元；yqjkqx=2022-01-26；zsxmDm=企业所得税 | 2099-12-31 |
| 3 | 宁波人社业务协同平台1 | 2021-03-29 | 330203210329850116805 | 330203210329850116805 | \
企业实行不定时工作制审批 | 2099-12-31 |
| 4 | 宁波人社一体化经办平台 | 2021-03-29 | 330203210329850212780 | 330203210329850212780 | \
企业实行综合计算工时工作制审批 | 2099-12-31 |
| 5 | 宁波人社业务协同平台1 | 2021-03-29 | 企业实行不定时工作制审批 | - | 企业实行不定时工作\
制审批 | 2099-12-31 |
| 6 | 宁波人社一体化经办平台 | 2021-03-29 | 企业实行综合计算工时工作制审批 | - | 企业实行综\
合计算工时工作制审批 | 2099-12-31 |
| 7 | 海曙区税务局 | 2020-08-19 | 330203200819854307244 | 330203200819854307244 | 对纳税人延\
期缴纳税款的核准 | 2099-12-31 |
| 8 | 浙江省工商行政管理局 | 2020-07-21 | 330201200721525482901 | 330201200721525482901 | 宁\
波鸿腾精密制造股份有限公司的变更登记 | 2099-12-31 |
| 9 | 浙江省宁波市人力资源和社会保障局 | 2020-03-20 | 不定时工作制审批 | 无 | 关于申请不定时\
工作制审批 | 2099-12-31 |
| 10 | 浙江省宁波市人力资源和社会保障局 | 2020-03-20 | 综合计算工时工作制审批 | 无 | 关于申\
请综合计算工时工作制审批 | 2099-12-31 |
| 11 | 宁波市海曙区道路运输管理所 | 2019-01-15 | 关于宁波鸿腾精密制造股份有限公司的许可决定 \
| ZJ0203201901150019 | 货运企业宁波鸿腾精密制造股份有限公司(原有效期2015-05-13至2019-05-13)\
申请续营 | 2099-12-31 |"""

# tyc_raw_result.md L640-L644

_REAL_DEFAULT_EVENT_EMPTY = """\
# 风险合规：宁波鸿腾精密制造股份有限公司

- tool: `get_default_event_info`

> 空结果：未发现该维度记录"""

# Todo 1 §3 实测文案：get_actual_controller 对旗舰主体返回空（「未发现任何记录」形态）。
_REAL_ACTUAL_CONTROLLER_EMPTY = (
    "# 实际控制人：宁德时代新能源科技股份有限公司\n"
    "\n"
    "- tool: `get_actual_controller`\n"
    "\n"
    "> 空结果：未发现任何记录"
)

_SKIPPED_QUOTA_MESSAGE = "天眼查额度耗尽，该维度未调用"
_SKIPPED_BUSY_MESSAGE = "天眼查额度繁忙，该维度未调用"
_SKIPPED_NOT_CONFIGURED_MESSAGE = "天眼查未启用，该维度未调用"


def _sse_response(body: dict[str, object]) -> httpx.Response:
    data = json.dumps(body, ensure_ascii=False)
    return httpx.Response(
        200,
        headers={"Content-Type": "text/event-stream"},
        text=f"event: message\ndata: {data}\n\n",
    )


def _sse_result(text: str, *, is_error: bool = False) -> httpx.Response:
    return _sse_response(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "result": {
                "content": [{"type": "text", "text": text}],
                "isError": is_error,
            },
        }
    )


class _MCPToolStub:
    """确定性天眼查 MCP stub：记录 tools/call 契约，可配置候选/维度文本与错误。"""

    def __init__(
        self,
        *,
        candidates_text: str = _CANDIDATES_MD,
        dimension_texts: dict[str, str] | None = None,
        error_tools: frozenset[str] = frozenset(),
        http_error_tools: frozenset[str] = frozenset(),
        search_error: bool = False,
    ) -> None:
        self.candidates_text = candidates_text
        self.dimension_texts = dimension_texts or {}
        self.error_tools = error_tools
        self.http_error_tools = http_error_tools
        self.search_error = search_error
        self.calls: list[tuple[str, dict[str, object]]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        method = payload.get("method")
        if method == "initialize":
            return _sse_response(
                {"jsonrpc": "2.0", "id": 1, "result": {"capabilities": {}}}
            )
        if method == "notifications/initialized":
            return httpx.Response(202)
        if method != "tools/call":
            return httpx.Response(400)
        params = payload.get("params", {})
        name = params.get("name")
        arguments = params.get("arguments", {})
        assert isinstance(name, str)
        assert isinstance(arguments, dict)
        if name == "search_companies":
            self.calls.append(("search_companies", arguments))
            if self.search_error:
                return _sse_result("搜索参数不足", is_error=True)
            return _sse_result(self.candidates_text)
        assert name == "call_tool"
        tool_name = arguments.get("tool_name")
        assert isinstance(tool_name, str)
        self.calls.append((tool_name, arguments))
        if tool_name in self.error_tools:
            return _sse_result("工具调用参数不足：缺少显式分页字段", is_error=True)
        if tool_name in self.http_error_tools:
            return httpx.Response(500)
        return _sse_result(self.dimension_texts.get(tool_name, _DIMENSION_TEXT))


def _gateway(stub: _MCPToolStub) -> McpTycGateway:
    return McpTycGateway("tyc_test_key", transport=httpx.MockTransport(stub.handler))


def _dimensions_of(result: dict[str, object]) -> dict[str, object]:
    dimensions = result["dimensions"]
    assert isinstance(dimensions, dict)
    return dimensions


def _entry_of(result: dict[str, object], tool_name: str) -> dict[str, object]:
    entry = _dimensions_of(result)[tool_name]
    assert isinstance(entry, dict)
    return entry


# ---------------------------------------------------------------------------
# 执行器（独立 Session，真提交）所需的跨连接测试环境
# ---------------------------------------------------------------------------


def _truncate_committed_tyc_usage() -> None:
    with engine.begin() as connection:
        connection.execute(delete(TycUsageRecord))


def _configure_committed_tyc(
    *,
    daily_limit: int,
    monthly_limit: int,
    dimensions: object = None,
    enabled: bool = True,
) -> None:
    """真提交地启用天眼查测试源并写入额度与可选维度覆盖（执行器跨连接可见）。"""
    from app.signals.secret_store import encrypt_secret

    with Session(engine) as setup:
        source = setup.scalar(select(DataSource).where(DataSource.code == "tianyancha"))
        assert source is not None, "迁移应已注册 tianyancha 信息源"
        source.enabled = enabled
        source.api_key_encrypted = encrypt_secret("tyc_multidim_test_key")
        source.api_key_hash = hashlib.sha256(b"tyc_multidim_test_key").hexdigest()
        source.api_key_last4 = "key"
        source.login_config = {
            "mode": "on_demand",
            "secret_source": "console",
            "daily_limit": daily_limit,
            "monthly_limit": monthly_limit,
            "tyc_dimensions": dimensions,
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
def committed_tyc_env() -> Generator[None]:
    """快照/恢复 tianyancha 配置，并清空已提交计费表（执行器跨连接可见）。"""
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


# ---------------------------------------------------------------------------
# 兼容基线（改造前绿，改造后必须保持）
# ---------------------------------------------------------------------------


def test_mcp_gateway_verify_success_locked() -> None:
    """基线：锚定命中候选时 verify 返回主体三要素，且 search_companies 参数不变。"""
    stub = _MCPToolStub()
    result = asyncio.run(_gateway(stub).verify("维度测试科技有限公司"))

    assert result["status"] == "success"
    assert result["company_name"] == "维度测试科技有限公司"
    assert result["credit_code"] == "91310000DIM000001"
    assert result["reg_status"] == "存续"
    assert stub.calls == [
        ("search_companies", {"query": "维度测试科技有限公司", "page_size": 5})
    ]


def test_mcp_gateway_verify_empty_result_locked() -> None:
    """基线：候选表解析不到记录时 verify 返回 empty（不抛错）。"""
    stub = _MCPToolStub(candidates_text="未查询到相关记录")
    result = asyncio.run(_gateway(stub).verify("不存在的公司"))

    assert result == {"status": "empty", "message": "未检索到该企业相关记录"}


def test_mcp_gateway_search_companies_parses_candidates_locked() -> None:
    """基线：search_companies 返回解析后的候选列表（列名定位）。"""
    stub = _MCPToolStub()
    candidates = asyncio.run(_gateway(stub).search_companies("维度测试科技有限公司"))

    assert candidates == [
        {
            "name": "维度测试科技有限公司",
            "credit_code": "91310000DIM000001",
            "reg_status": "存续",
        }
    ]


def test_unconfigured_gateway_verify_locked() -> None:
    """基线：无可用密钥时 build_tyc_gateway 返回占位实现，verify 不发起调用。"""
    gateway = build_tyc_gateway()
    assert isinstance(gateway, UnconfiguredTycGateway)

    result = asyncio.run(gateway.verify("测试公司"))
    assert result["status"] == "not_configured"


# ---------------------------------------------------------------------------
# 维度方案常量与覆盖校验
# ---------------------------------------------------------------------------


def test_default_dimensions_exactly_match_todo1_final_set() -> None:
    assert list(DEFAULT_TYC_DIMENSIONS) == _TODO1_FINAL_DIMENSION_SET
    assert len(DEFAULT_TYC_DIMENSIONS) == len(set(DEFAULT_TYC_DIMENSIONS)) == 12


def test_known_pool_covers_todo1_candidates() -> None:
    assert len(KNOWN_TYC_DIMENSIONS) == 17
    assert set(KNOWN_TYC_DIMENSIONS) == set(_TODO1_FINAL_DIMENSION_SET) | set(
        _TODO1_EXTRA_TOOLS
    )


def test_resolve_accepts_valid_override_and_defaults_none() -> None:
    assert resolve_tyc_dimensions(None) == DEFAULT_TYC_DIMENSIONS
    assert (
        resolve_tyc_dimensions(tuple(DEFAULT_TYC_DIMENSIONS[:2]))
        == DEFAULT_TYC_DIMENSIONS[:2]
    )
    override = ["get_actual_controller", "get_risk_overview", "get_equity_ratio"]
    assert resolve_tyc_dimensions(override) == tuple(override)


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param("get_risk_overview", id="string"),
        pytest.param({"name": "get_risk_overview"}, id="dict"),
        pytest.param([], id="zero-items"),
        pytest.param([f"tool-{index}" for index in range(13)], id="thirteen-unknown"),
        pytest.param(
            [*_TODO1_FINAL_DIMENSION_SET, "get_beneficial_owners"], id="thirteen-known"
        ),
        pytest.param(["get_risk_overview", "get_risk_overview"], id="duplicate"),
        pytest.param(["get_risk_overview", "get_unknown_tool"], id="unknown"),
        pytest.param([123], id="non-string"),
        pytest.param([None], id="null-item"),
        pytest.param(["get_risk_overview", ""], id="empty-string"),
        pytest.param([[["nested"]]], id="nested-list"),
    ],
)
def test_resolve_rejects_invalid_override_wholesale(raw: object) -> None:
    """非法覆盖整体回落默认：13 项/重复/未知/错误类型绝不部分接受。"""
    assert resolve_tyc_dimensions(raw) == DEFAULT_TYC_DIMENSIONS


# ---------------------------------------------------------------------------
# fetch_dimensions：默认 13 次调用（Manual QA 数据面核心用例）
# ---------------------------------------------------------------------------


def test_fetch_dimensions_all_success_default_plan(committed_tyc_env: None) -> None:
    """Manual QA：1 锚定 + 12 维 = 13 次；JSON 参数合规；独立连接查账一致。"""
    _configure_committed_tyc(daily_limit=1000, monthly_limit=10000)
    stub = _MCPToolStub()

    result = asyncio.run(_gateway(stub).fetch_dimensions("维度测试科技有限公司"))

    assert set(result) == {
        "status",
        "company_name",
        "credit_code",
        "reg_status",
        "dimensions",
    }
    assert result["status"] == "success"
    assert result["company_name"] == "维度测试科技有限公司"
    assert result["credit_code"] == "91310000DIM000001"
    assert result["reg_status"] == "存续"

    dimensions = _dimensions_of(result)
    assert list(dimensions) == _TODO1_FINAL_DIMENSION_SET
    for tool_name in _TODO1_FINAL_DIMENSION_SET:
        assert _entry_of(result, tool_name) == {
            "status": "success",
            "raw": _DIMENSION_TEXT,
            "message": None,
        }

    # 一次锚定 + 12 次维度 = C = 13，工具名严格按默认顺序。
    assert [name for name, _ in stub.calls] == [
        "search_companies",
        *_TODO1_FINAL_DIMENSION_SET,
    ]
    for tool_name, arguments in stub.calls[1:]:
        assert arguments["company_name"] == "维度测试科技有限公司"
        assert arguments["tool_name"] == tool_name
        inner = arguments["arguments"]
        assert isinstance(inner, dict)
        if tool_name in _TODO1_LIST_TOOLS:
            assert inner == {"page": 1, "page_size": 20}
        else:
            assert inner == {}

    # 独立连接查账：13 条 success 与 daily_used 完全一致。
    rows = _committed_tyc_rows()
    assert len(rows) == 13
    assert [status for _, _, status in rows] == ["success"] * 13
    assert _committed_tyc_daily_used() == 13


def test_fetch_dimensions_empty_dimension_is_not_charged(committed_tyc_env: None) -> None:
    """空结果由真实「无记录」文本判定为 empty，且不计费。"""
    _configure_committed_tyc(daily_limit=1000, monthly_limit=10000)
    empty_tool = "get_default_event_info"
    stub = _MCPToolStub(dimension_texts={empty_tool: _EMPTY_DIMENSION_TEXT})

    result = asyncio.run(_gateway(stub).fetch_dimensions("维度测试科技有限公司"))

    assert _entry_of(result, empty_tool) == {
        "status": "empty",
        "raw": _EMPTY_DIMENSION_TEXT,
        "message": None,
    }
    rows = {tool: status for tool, _, status in _committed_tyc_rows()}
    assert rows[empty_tool] == "empty"
    assert sorted(rows.values()) == ["empty"] + ["success"] * 12
    assert _committed_tyc_daily_used() == 12


def test_fetch_dimensions_error_dimension_is_isolated(committed_tyc_env: None) -> None:
    """单维 isError 隔离为 error 且不计费，其余维度继续执行。"""
    _configure_committed_tyc(daily_limit=1000, monthly_limit=10000)
    error_tool = "get_judicial_case"
    stub = _MCPToolStub(error_tools=frozenset({error_tool}))

    result = asyncio.run(_gateway(stub).fetch_dimensions("维度测试科技有限公司"))

    entry = _entry_of(result, error_tool)
    assert entry["status"] == "error"
    assert entry["raw"] is None
    assert isinstance(entry["message"], str) and "天眼查调用失败" in entry["message"]

    # 失败隔离：13 次调用全部发生，其余 11 维成功。
    assert [name for name, _ in stub.calls] == [
        "search_companies",
        *_TODO1_FINAL_DIMENSION_SET,
    ]
    for tool_name in _TODO1_FINAL_DIMENSION_SET:
        if tool_name != error_tool:
            assert _entry_of(result, tool_name)["status"] == "success"

    rows = {tool: status for tool, _, status in _committed_tyc_rows()}
    assert rows[error_tool] == "error"
    assert _committed_tyc_daily_used() == 12


def test_fetch_dimensions_http_500_dimension_is_isolated(committed_tyc_env: None) -> None:
    """单维 HTTP 500 隔离为 error 且不计费，其余维度继续执行（计划 QA failure）。"""
    _configure_committed_tyc(daily_limit=1000, monthly_limit=10000)
    error_tool = "get_court_notice"
    stub = _MCPToolStub(http_error_tools=frozenset({error_tool}))

    result = asyncio.run(_gateway(stub).fetch_dimensions("维度测试科技有限公司"))

    entry = _entry_of(result, error_tool)
    assert entry["status"] == "error"
    assert entry["raw"] is None
    assert isinstance(entry["message"], str) and "HTTP 500" in entry["message"]

    for tool_name in _TODO1_FINAL_DIMENSION_SET:
        if tool_name != error_tool:
            assert _entry_of(result, tool_name)["status"] == "success"

    rows = {tool: status for tool, _, status in _committed_tyc_rows()}
    assert rows[error_tool] == "error"
    assert _committed_tyc_daily_used() == 12


# ---------------------------------------------------------------------------
# 额度耗尽 / 繁忙 / 外部消费（不超发）
# ---------------------------------------------------------------------------


def test_fetch_dimensions_quota_exhausted_midway_stops_remaining(
    committed_tyc_env: None,
) -> None:
    """daily_limit=3：锚定 + 前 2 维后额度耗尽，第 3 维起不再调用远程。"""
    _configure_committed_tyc(daily_limit=3, monthly_limit=100)
    stub = _MCPToolStub()

    result = asyncio.run(_gateway(stub).fetch_dimensions("维度测试科技有限公司"))

    assert result["status"] == "quota_exhausted"
    assert [name for name, _ in stub.calls] == [
        "search_companies",
        *_TODO1_FINAL_DIMENSION_SET[:2],
    ]
    dimensions = _dimensions_of(result)
    assert len(dimensions) == 12
    assert dimensions[_TODO1_FINAL_DIMENSION_SET[0]]["status"] == "success"
    assert dimensions[_TODO1_FINAL_DIMENSION_SET[1]]["status"] == "success"

    blocked = _entry_of(result, _TODO1_FINAL_DIMENSION_SET[2])
    assert blocked["status"] == "quota_exhausted"
    assert blocked["raw"] is None
    assert isinstance(blocked["message"], str) and "额度已达上限" in blocked["message"]

    for tool_name in _TODO1_FINAL_DIMENSION_SET[3:]:
        assert _entry_of(result, tool_name) == {
            "status": "quota_exhausted",
            "raw": None,
            "message": _SKIPPED_QUOTA_MESSAGE,
        }

    rows = _committed_tyc_rows()
    assert len(rows) == 3
    assert all(status == "success" for _, _, status in rows)
    assert _committed_tyc_daily_used() == 3


def test_fetch_dimensions_busy_on_anchor_calls_nothing(
    committed_tyc_env: None, monkeypatch: MonkeyPatch
) -> None:
    """额度锁被占：锚定即 busy，不调用远程、不记账、不进入维度。"""
    _configure_committed_tyc(daily_limit=1000, monthly_limit=10000)
    monkeypatch.setattr(tyc_quota_module, "QUOTA_LOCK_TIMEOUT_MS", 100)
    stub = _MCPToolStub()

    holder = engine.connect()
    holder_transaction = holder.begin()
    try:
        holder.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": tyc_quota_module.QUOTA_ADVISORY_LOCK_KEY},
        )
        result = asyncio.run(_gateway(stub).fetch_dimensions("维度测试科技有限公司"))
    finally:
        holder_transaction.rollback()
        holder.close()

    assert result["status"] == "busy"
    assert _dimensions_of(result) == {}
    assert stub.calls == []
    assert _committed_tyc_rows() == []


def test_fetch_dimensions_busy_midway_stops_remaining(
    committed_tyc_env: None, monkeypatch: MonkeyPatch
) -> None:
    """受控执行器 seam：第 2 个维度返回 busy 后，其余维度不再发起调用。"""
    _configure_committed_tyc(daily_limit=1000, monthly_limit=10000)
    real_execute = tyc_quota_module.execute_tyc_tool_with_quota
    call_count = {"value": 0}

    async def _execute_with_busy_on_second_dimension(
        tool_name: str, company_name: str, remote_call: RemoteCall
    ) -> TycQuotaExecutionResult:
        call_count["value"] += 1
        if call_count["value"] == 3:  # 锚定(1) + 第 1 维(2) 之后
            return TycQuotaExecutionResult(
                outcome=TycQuotaOutcome.BUSY,
                tool_name=tool_name,
                company_name=company_name,
                message="天眼查额度繁忙，请重试",
            )
        return await real_execute(tool_name, company_name, remote_call)

    monkeypatch.setattr(
        tyc_dimensions_module,
        "execute_tyc_tool_with_quota",
        _execute_with_busy_on_second_dimension,
    )
    stub = _MCPToolStub()

    result = asyncio.run(_gateway(stub).fetch_dimensions("维度测试科技有限公司"))

    assert result["status"] == "busy"
    assert [name for name, _ in stub.calls] == [
        "search_companies",
        _TODO1_FINAL_DIMENSION_SET[0],
    ]
    dimensions = _dimensions_of(result)
    assert len(dimensions) == 12
    assert dimensions[_TODO1_FINAL_DIMENSION_SET[0]]["status"] == "success"
    assert dimensions[_TODO1_FINAL_DIMENSION_SET[1]]["status"] == "busy"
    for tool_name in _TODO1_FINAL_DIMENSION_SET[2:]:
        assert _entry_of(result, tool_name) == {
            "status": "busy",
            "raw": None,
            "message": _SKIPPED_BUSY_MESSAGE,
        }
    assert _committed_tyc_daily_used() == 2


def test_fetch_dimensions_sees_external_consumption_before_next_call(
    committed_tyc_env: None, monkeypatch: MonkeyPatch
) -> None:
    """stale_state：中途外部真提交消费后，下一维度被锁内重读阻断（不超发）。"""
    _configure_committed_tyc(daily_limit=3, monthly_limit=100)
    real_execute = tyc_quota_module.execute_tyc_tool_with_quota
    call_count = {"value": 0}

    async def _execute_injecting_external_usage(
        tool_name: str, company_name: str, remote_call: RemoteCall
    ) -> TycQuotaExecutionResult:
        call_count["value"] += 1
        if call_count["value"] == 3:  # 第 2 个维度调用前插入外部消费
            with Session(engine) as external:
                external.add(
                    TycUsageRecord(
                        tool_name="verify_company",
                        company_name="外部实时消费",
                        status="success",
                    )
                )
                external.commit()
        return await real_execute(tool_name, company_name, remote_call)

    monkeypatch.setattr(
        tyc_dimensions_module,
        "execute_tyc_tool_with_quota",
        _execute_injecting_external_usage,
    )
    stub = _MCPToolStub()

    result = asyncio.run(_gateway(stub).fetch_dimensions("维度测试科技有限公司"))

    # 锚定 + 第 1 维 = 2 次远程；外部消费后第 2 维被阻断（远程未调用）。
    assert result["status"] == "quota_exhausted"
    assert [name for name, _ in stub.calls] == [
        "search_companies",
        _TODO1_FINAL_DIMENSION_SET[0],
    ]
    dimensions = _dimensions_of(result)
    assert dimensions[_TODO1_FINAL_DIMENSION_SET[0]]["status"] == "success"
    assert dimensions[_TODO1_FINAL_DIMENSION_SET[1]]["status"] == "quota_exhausted"
    for tool_name in _TODO1_FINAL_DIMENSION_SET[2:]:
        assert _entry_of(result, tool_name) == {
            "status": "quota_exhausted",
            "raw": None,
            "message": _SKIPPED_QUOTA_MESSAGE,
        }
    assert _committed_tyc_daily_used() == 3


def test_fetch_dimensions_revoked_before_anchor_returns_not_configured(
    committed_tyc_env: None,
) -> None:
    """来源停用后锚定即被拒：顶层 not_configured、零远程调用、零记账。"""
    _configure_committed_tyc(daily_limit=1000, monthly_limit=10000)
    _configure_committed_tyc(daily_limit=1000, monthly_limit=10000, enabled=False)
    stub = _MCPToolStub()

    result = asyncio.run(_gateway(stub).fetch_dimensions("维度测试科技有限公司"))

    assert result == {
        "status": "not_configured",
        "company_name": "维度测试科技有限公司",
        "credit_code": None,
        "reg_status": None,
        "dimensions": {},
    }
    assert stub.calls == []
    assert _committed_tyc_rows() == []
    assert _committed_tyc_daily_used() == 0


def test_fetch_dimensions_revocation_midway_stops_remaining_calls(
    committed_tyc_env: None, monkeypatch: MonkeyPatch
) -> None:
    """中途撤销（外部真提交停用）后执行器锁内重读拒绝：后续维度零远程、零记账。

    注入点与 ``test_fetch_dimensions_sees_external_consumption_before_next_call``
    相同：第 3 次执行器调用前真提交停用，再由真实执行器取锁后重读决定。
    """
    _configure_committed_tyc(daily_limit=1000, monthly_limit=10000)
    real_execute = tyc_quota_module.execute_tyc_tool_with_quota
    call_count = {"value": 0}

    async def _execute_revoking_source_on_third_call(
        tool_name: str, company_name: str, remote_call: RemoteCall
    ) -> TycQuotaExecutionResult:
        call_count["value"] += 1
        if call_count["value"] == 3:  # 锚定 + 第 1 维之后：提交撤销
            _configure_committed_tyc(
                daily_limit=1000, monthly_limit=10000, enabled=False
            )
        return await real_execute(tool_name, company_name, remote_call)

    monkeypatch.setattr(
        tyc_dimensions_module,
        "execute_tyc_tool_with_quota",
        _execute_revoking_source_on_third_call,
    )
    stub = _MCPToolStub()

    result = asyncio.run(_gateway(stub).fetch_dimensions("维度测试科技有限公司"))

    assert result["status"] == "not_configured"
    assert [name for name, _ in stub.calls] == [
        "search_companies",
        _TODO1_FINAL_DIMENSION_SET[0],
    ]
    dimensions = _dimensions_of(result)
    assert len(dimensions) == 12
    assert dimensions[_TODO1_FINAL_DIMENSION_SET[0]]["status"] == "success"

    blocked = _entry_of(result, _TODO1_FINAL_DIMENSION_SET[1])
    assert blocked["status"] == "not_configured"
    assert blocked["raw"] is None
    assert blocked["message"] == NOT_CONFIGURED_MESSAGE

    for tool_name in _TODO1_FINAL_DIMENSION_SET[2:]:
        assert _entry_of(result, tool_name) == {
            "status": "not_configured",
            "raw": None,
            "message": _SKIPPED_NOT_CONFIGURED_MESSAGE,
        }

    # 撤销前已提交的 2 次 success 保留；撤销后零新增远程调用与记账。
    assert _committed_tyc_rows() == [
        ("search_companies", "维度测试科技有限公司", "success"),
        (_TODO1_FINAL_DIMENSION_SET[0], "维度测试科技有限公司", "success"),
    ]
    assert _committed_tyc_daily_used() == 2


# ---------------------------------------------------------------------------
# 锚定失败与覆盖接线
# ---------------------------------------------------------------------------


def test_fetch_dimensions_anchor_empty_skips_dimensions(committed_tyc_env: None) -> None:
    """锚定空候选：顶层 empty、dimensions 为空、不进入维度调用、不计费。"""
    _configure_committed_tyc(daily_limit=1000, monthly_limit=10000)
    stub = _MCPToolStub(candidates_text="未查询到相关记录")

    result = asyncio.run(_gateway(stub).fetch_dimensions("不存在的公司"))

    assert result == {
        "status": "empty",
        "company_name": "不存在的公司",
        "credit_code": None,
        "reg_status": None,
        "dimensions": {},
    }
    assert [name for name, _ in stub.calls] == ["search_companies"]
    assert _committed_tyc_rows() == [("search_companies", "不存在的公司", "empty")]
    assert _committed_tyc_daily_used() == 0


def test_fetch_dimensions_anchor_error_returns_error(committed_tyc_env: None) -> None:
    """锚定 isError：顶层 error、不进入维度调用、按不计费 error 记账。"""
    _configure_committed_tyc(daily_limit=1000, monthly_limit=10000)
    stub = _MCPToolStub(search_error=True)

    result = asyncio.run(_gateway(stub).fetch_dimensions("测试公司"))

    assert result == {
        "status": "error",
        "company_name": "测试公司",
        "credit_code": None,
        "reg_status": None,
        "dimensions": {},
    }
    assert [name for name, _ in stub.calls] == ["search_companies"]
    assert _committed_tyc_rows() == [("search_companies", "测试公司", "error")]
    assert _committed_tyc_daily_used() == 0


def test_build_tyc_gateway_reads_dimension_override_and_fetches(
    committed_tyc_env: None,
) -> None:
    """build_tyc_gateway 读取 login_config.tyc_dimensions 覆盖并驱动取数。"""
    override = ["get_actual_controller", "get_equity_ratio"]
    _configure_committed_tyc(daily_limit=1000, monthly_limit=10000, dimensions=override)
    stub = _MCPToolStub()

    with Session(engine) as session:
        gateway = build_tyc_gateway(
            session=session, transport=httpx.MockTransport(stub.handler)
        )
    assert isinstance(gateway, McpTycGateway)
    assert list(gateway.dimensions) == override

    result = asyncio.run(gateway.fetch_dimensions("维度测试科技有限公司"))

    assert list(_dimensions_of(result)) == override
    assert [name for name, _ in stub.calls] == ["search_companies", *override]
    assert _committed_tyc_daily_used() == 3


def test_build_tyc_gateway_invalid_override_falls_back_with_warning(
    committed_tyc_env: None, caplog: LogCaptureFixture
) -> None:
    """13 项非法覆盖：记录警告并整体回落默认 12 维（绝不部分接受）。"""
    invalid = [*_TODO1_FINAL_DIMENSION_SET, "get_beneficial_owners"]
    _configure_committed_tyc(daily_limit=1000, monthly_limit=10000, dimensions=invalid)

    with caplog.at_level(logging.WARNING, logger="scheduler"):
        with Session(engine) as session:
            gateway = build_tyc_gateway(session=session)

    assert isinstance(gateway, McpTycGateway)
    assert gateway.dimensions == DEFAULT_TYC_DIMENSIONS
    assert any("维度覆盖" in record.message for record in caplog.records)


def test_unconfigured_gateway_fetch_dimensions_returns_not_configured() -> None:
    gateway = build_tyc_gateway()
    assert isinstance(gateway, UnconfiguredTycGateway)

    result = asyncio.run(gateway.fetch_dimensions("测试公司"))
    assert result == {
        "status": "not_configured",
        "company_name": "测试公司",
        "credit_code": None,
        "reg_status": None,
        "dimensions": {},
    }


# ---------------------------------------------------------------------------
# F1 回归（对抗验证修复）：真实混合响应（有记录 + 子段空结果）不得判 empty
# ---------------------------------------------------------------------------

_F1_MIXED_TOOLS = ("get_credit_evaluation", "get_administrative_license")
_F1_EMPTY_TOOLS = ("get_default_event_info", "get_actual_controller")


def _f1_stub() -> _MCPToolStub:
    """真实样本混注 stub：2 个混合维（有记录+空结果子段）+ 2 个纯空维。"""
    return _MCPToolStub(
        dimension_texts={
            "get_credit_evaluation": _REAL_CREDIT_EVALUATION_MIXED,
            "get_administrative_license": _REAL_ADMINISTRATIVE_LICENSE_MIXED,
            "get_default_event_info": _REAL_DEFAULT_EVENT_EMPTY,
            "get_actual_controller": _REAL_ACTUAL_CONTROLLER_EMPTY,
        }
    )


@pytest.mark.parametrize(
    ("text", "expected_empty"),
    [
        pytest.param(_REAL_CREDIT_EVALUATION_MIXED, False, id="credit-evaluation-mixed"),
        pytest.param(
            _REAL_ADMINISTRATIVE_LICENSE_MIXED, False, id="administrative-license-mixed"
        ),
        pytest.param(_REAL_DEFAULT_EVENT_EMPTY, True, id="default-event-pure-empty"),
        pytest.param(
            _REAL_ACTUAL_CONTROLLER_EMPTY, True, id="actual-controller-pure-empty"
        ),
    ],
)
def test_is_empty_dimension_text_matches_real_sample_structure(
    text: str, expected_empty: bool
) -> None:
    """真实样本：表格数据行构成记录；仅无记录且命中空结果文案才判 empty。"""
    assert is_empty_dimension_text(text) is expected_empty


def test_is_empty_dimension_text_ignores_headers_separators_and_markers() -> None:
    """仅有表头/分隔行与空结果说明 → 仍判 empty（不因表格结构误判 success）。"""
    text = (
        "# 风险合规：示例公司\n\n- tool: `get_example`\n\n"
        "| 字段 | 值 |\n|---|---|\n\n> 空结果：未发现该维度记录"
    )
    assert is_empty_dimension_text(text) is True


def test_is_empty_dimension_text_counts_bullet_and_summary_records() -> None:
    """项目符号条目与「共有 N>0 条」摘要均视为有效记录。"""
    bullet = (
        "- tool: `get_example`\n\n"
        "- 开庭公告 · 警示 · 1 条：示例记录\n\n"
        "> 空结果：未发现其他记录"
    )
    summary = (
        "- tool: `get_example`\n\n"
        "> 摘要：该查询实体共有19条记录。\n"
        "> 空结果：未发现其他来源记录"
    )
    assert is_empty_dimension_text(bullet) is False
    assert is_empty_dimension_text(summary) is False


def test_fetch_dimensions_real_mixed_samples_charge_as_records(
    committed_tyc_env: None,
) -> None:
    """Manual QA：真实混合作文本 → success 计 1 次；纯空文本 → empty 不计费。"""
    _configure_committed_tyc(daily_limit=1000, monthly_limit=10000)
    stub = _f1_stub()

    result = asyncio.run(_gateway(stub).fetch_dimensions("宁波鸿腾精密制造股份有限公司"))

    for tool_name in _F1_MIXED_TOOLS:
        entry = _entry_of(result, tool_name)
        assert entry["status"] == "success"
        assert isinstance(entry["raw"], str) and "明细" in entry["raw"]
    for tool_name in _F1_EMPTY_TOOLS:
        assert _entry_of(result, tool_name)["status"] == "empty"

    rows = {tool: status for tool, _, status in _committed_tyc_rows()}
    assert [rows[tool] for tool in _F1_MIXED_TOOLS] == ["success", "success"]
    assert [rows[tool] for tool in _F1_EMPTY_TOOLS] == ["empty", "empty"]
    # 锚定 1 + 有记录维 10（2 混合 + 8 默认文本）= 11 次计费；纯空 2 维不计费。
    assert _committed_tyc_daily_used() == 11


def test_real_mixed_dimensions_keep_success_findings_through_report_builder(
    committed_tyc_env: None,
) -> None:
    """Manual QA：混合维经 build_risk_report 保留 success finding（不虚报未发现）。"""
    _configure_committed_tyc(daily_limit=1000, monthly_limit=10000)
    results = asyncio.run(
        _gateway(_f1_stub()).fetch_dimensions("宁波鸿腾精密制造股份有限公司")
    )

    report = build_risk_report(results, supplier_code="SUP-F1-FIX-001")
    findings = {finding.key: finding for finding in report.dimensions}

    for tool_name in _F1_MIXED_TOOLS:
        finding = findings[tool_name]
        assert finding.status is DimensionStatus.SUCCESS
        assert finding.summary != f"未发现{finding.name}记录"
        assert finding.raw_ref == f"dimensions.{tool_name}.raw"
    empty_finding = findings["get_default_event_info"]
    assert empty_finding.status is DimensionStatus.EMPTY
    assert empty_finding.hit is False
