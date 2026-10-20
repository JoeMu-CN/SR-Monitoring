"""天眼查多维度风险报告（计划 Todo 6）：结构化模型与确定性构造器。

输入合同（与 Todo 5 ``fetch_dimensions`` 对齐，不依赖其具体类）::

    {"status": "success|empty|error|quota_exhausted|busy|not_configured",
     # 顶层锚定结果，不写入报告；锚定失败时附带 "message"（真实失败原因），
     # 锚定成功时无此键
     "company_name": str, "credit_code": str | None, "reg_status": str | None,
     "dimensions": {"<tool>": {"status": ..., "raw": str | None, "message": str | None}}}

只抽取风险相关字段：报告不含完整原始 Markdown，``raw_ref`` 仅为定位引用；摘要与
证据引用长度受限并对电话/证件号脱敏；未知状态或类型校验失败；未知工具名不进入
报告（数据最小化）；``supplier_code`` 非空、``generated_at`` 必须 timezone-aware；
``period_key`` 按北京时间 ISO 周生成，跨年边界与同一瞬时不同时区表达一致。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from enum import StrEnum
from typing import Annotated, Literal, NamedTuple

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints

_BEIJING = timezone(timedelta(hours=8))
_MAX_SUMMARY_CHARS = 200
_MAX_EVIDENCE_ITEMS = 5
_MAX_ITEM_CHARS = 40
# 并列人员/股东变更做集合差时最多比较的条目数；超出时只记「项目+日期」。
_MAX_CHANGE_DIFF_ITEMS = 5
_SHELL_TOOL = "get_shell_company_check"
# 变更记录维度：摘要要给出具体变更事实而不是「共有 N 条」，故单列工具名做专门处理。
_CHANGE_TOOL = "get_change_records"
# 分节式变更记录标题：「#### 第 1 条 · 2026-07-14 · 负责人变更（…）」。
_CHANGE_SECTION_RE = re.compile(r"^#{2,6}\s*第\s*\d+\s*条\s*[·・]\s*(?P<rest>.+)$")
_CHANGE_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# 展示脱敏：手机号 / 座机 / 证件号（18 位或 15 位）；前后数字边界避免误伤长编号。
_SENSITIVE_PATTERNS = (
    re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    re.compile(r"(?<!\d)0\d{2,3}-\d{7,8}(?!\d)"),
    re.compile(r"(?<!\d)(?:\d{17}[\dXx]|\d{15})(?!\d)"),
)
_EMPTY_MARKERS = re.compile("空结果|未发现")
_SHELL_TAG_RE = re.compile(r"(?:hasShellTags|shellTagsCount)\s*\|\s*(\d+)")
_WHITESPACE_RE = re.compile(r"\s+")
_BULLET_SPLIT_RE = re.compile(r"\s*·\s*|：")
_NUMERIC_RE = re.compile(r"[\d\-./]+")


class DimensionStatus(StrEnum):
    SUCCESS = "success"
    EMPTY = "empty"
    ERROR = "error"
    QUOTA_EXHAUSTED = "quota_exhausted"
    BUSY = "busy"
    NOT_CONFIGURED = "not_configured"


class RiskLevel(StrEnum):
    HIGH = "高风险"
    ALERT = "警示"
    MEDIUM = "中风险"
    LOW = "低风险"


@dataclass(frozen=True, slots=True)
class _DimensionSpec:
    name: str
    risk_relevant: bool


# Todo 1 final_dimension_set（严格按序）在前，其后为候选池备用工具（对齐 Todo 5 候选池）。
_DIMENSION_SPECS: dict[str, _DimensionSpec] = {
    "get_risk_overview": _DimensionSpec("风险总览", True),
    "get_judicial_case": _DimensionSpec("司法解析", True),
    "get_default_event_info": _DimensionSpec("失信/被执行", True),
    "get_hearing_notice": _DimensionSpec("开庭公告", True),
    "get_court_notice": _DimensionSpec("法院公告", True),
    "get_administrative_license": _DimensionSpec("行政许可", False),
    "get_random_check": _DimensionSpec("双随机抽查", False),
    "get_spot_check_info": _DimensionSpec("抽查检查", False),
    "get_credit_evaluation": _DimensionSpec("信用评价（税务）", False),
    _SHELL_TOOL: _DimensionSpec("空壳公司识别", False),
    # 历史变更记录是风险画像的关键事实（法定代表人/负责人、股东、注册资本、地址等），
    # 标为风险相关后才能进入重点摘要与前端「命中风险」分组。
    _CHANGE_TOOL: _DimensionSpec("历史变更记录", True),
    "get_actual_controller": _DimensionSpec("实际控制人", False),
    "get_beneficial_owners": _DimensionSpec("受益所有人", False),
    "get_external_investments": _DimensionSpec("对外投资", False),
    "get_shareholder_info": _DimensionSpec("股东信息", False),
    "get_company_registration_info": _DimensionSpec("基础工商登记", False),
    "get_equity_ratio": _DimensionSpec("股权控制结构", False),
}

_NonBlankText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class DimensionFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: _NonBlankText
    name: _NonBlankText
    status: DimensionStatus
    risk_level: RiskLevel | None = None
    hit: bool
    summary: str
    evidence_refs: list[str] = Field(default_factory=list)
    raw_ref: _NonBlankText | None = None


class TycRiskReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report_kind: Literal["supplier_profile"] = "supplier_profile"
    company_name: _NonBlankText
    supplier_code: _NonBlankText
    credit_code: str | None = None
    reg_status: str | None = None
    generated_at: AwareDatetime
    period_key: str = Field(pattern=r"^tyc:.+:\d{4}-W\d{2}$")
    dimensions: list[DimensionFinding]


def build_risk_report(
    results: Mapping[str, object],
    *,
    supplier_code: str,
    generated_at: datetime | None = None,
) -> TycRiskReport:
    """从 Todo 5 的 ``results`` 构造报告；确定性抽取，无外部副作用。"""
    raw_dimensions = results.get("dimensions")
    if not isinstance(raw_dimensions, Mapping):
        raise ValueError("results.dimensions 必须是工具名到结果的映射")
    findings: list[DimensionFinding] = []
    for key, spec in _DIMENSION_SPECS.items():
        entry = raw_dimensions.get(key)
        if entry is None:
            continue
        if not isinstance(entry, Mapping):
            raise ValueError(f"维度 {key} 的结果必须是映射")
        findings.append(_build_finding(key, spec, entry))
    moment = generated_at if generated_at is not None else datetime.now(UTC)
    code = supplier_code.strip()
    return TycRiskReport(
        company_name=_require_text(results.get("company_name"), "company_name"),
        supplier_code=code,
        credit_code=_coerce_text(results.get("credit_code"), "credit_code"),
        reg_status=_coerce_text(results.get("reg_status"), "reg_status"),
        generated_at=moment,
        period_key=_period_key(code, moment),
        dimensions=findings,
    )


# 命中维度展示顺序：风险等级高的在前，无等级的最后。
_RISK_LEVEL_ORDER: dict[RiskLevel, int] = {
    RiskLevel.HIGH: 0,
    RiskLevel.ALERT: 1,
    RiskLevel.MEDIUM: 2,
    RiskLevel.LOW: 3,
}
# 案号（含全角/半角圆括号与方括号）：摘要里只保留案件性质与状态，不堆案号。
_CASE_NO_RE = re.compile(r"[（(〔]\s*\d{4}\s*[）)〕][\u4e00-\u9fa5A-Za-z0-9]{0,24}号")
_SEPARATOR_RUN_RE = re.compile(r"[、；，,]{2,}")
# 风险总览是汇总维度：固定排在具体维度之后，避免其等级较高时抢占具体风险事实。
_OVERVIEW_TOOL_KEY = "get_risk_overview"
# 命中维度剥离过程性说明后没有事实时的如实提示：不谎称无风险，也不静默丢弃维度。
_NO_FACT_HINT = "有风险记录，具体信息未披露"
# 已取得结论（成功或确认为空）的状态；其余状态一律视为「未取得结果」。
_SETTLED_STATUSES: frozenset[DimensionStatus] = frozenset(
    {DimensionStatus.SUCCESS, DimensionStatus.EMPTY}
)
# 分句边界：摘要内的分句即过程性说明与真实事实的分界。
_CLAUSE_SPLIT_RE = re.compile(r"[；;。]+")
# 过程性说明与口径提示的标记词。
_PROCESS_MARKER_RE = re.compile(
    r"已(?:返回|获取|生成|同步|完成|抓取|输出)|详见|见上|如下|以上|口径|为准|请以"
)
# 真实事实标记：数量/金额单位，或欠税、失信、处罚、制裁、诉讼、开庭等具体风险事实词。
_FACT_MARKER_RE = re.compile(
    r"\d+(?:\.\d+)?\s*(?:条|起|项|笔|万|亿|元|次|家|人|户|年|月|日|号)"
    r"|欠税|被执行|失信|处罚|制裁|禁入|冻结|违法|诉讼|开庭|法院|公告|纠纷"
)


def render_key_summary(report: TycRiskReport) -> str:
    """生成列表与正文用的中文重点摘要（确定性、可重复）。

    命中维度按风险等级排序并在名称后标注等级；风险总览作为汇总维度固定排在具体
    维度之后，使「司法解析/开庭公告」等具体事实不被总览的过程性文字抢占；历史变更
    记录是主体身份与经营要素变化的底账，再固定排到具体风险维度之前，使其在摘要里
    最先被读到。摘要去除案号堆砌、压缩重复分隔符，并剥离「已返回…」
    等过程性说明；必要口径提示保留在维度事实之后，同句里的真实事实
    原样保留。命中维度剥离后没有事实时给如实提示，不谎称无风险；无命中时单列未取得
    结果的维度数量，空/失败维度不算作已排除风险。

    ``content`` 在入库时固定，故只对新采集的信号生效；采集记录列表由
    ``signals.tyc_report_views.derive_summary`` 从历史 ``raw_data`` 实时派生，因此本
    规则同样立即作用于既有报告的列表摘要（不改写已落库的 ``content``/``raw_data``）。
    """
    hits = [finding for finding in report.dimensions if finding.hit]
    if not hits:
        return _no_hit_summary(report)
    concrete = sorted(
        (finding for finding in hits if finding.key != _OVERVIEW_TOOL_KEY),
        key=lambda item: (
            0 if item.key == _CHANGE_TOOL else 1,
            _RISK_LEVEL_ORDER.get(item.risk_level, 4),
        ),
    )
    overviews = [finding for finding in hits if finding.key == _OVERVIEW_TOOL_KEY]
    parts = [_dimension_part(finding) for finding in concrete + overviews]
    return _clip("重点命中：" + "；".join(parts), 600)


def _dimension_part(finding: DimensionFinding) -> str:
    """单个维度的摘要片段：``名称（等级）`` 加剥离过程性说明后的事实。"""
    label = (
        f"{finding.name}（{finding.risk_level.value}）"
        if finding.risk_level is not None
        else finding.name
    )
    facts = _drop_procedural(_strip_case_numbers(finding.summary))
    return f"{label}：{facts or _NO_FACT_HINT}"


def _no_hit_summary(report: TycRiskReport) -> str:
    """无命中时的如实措辞：未取得结果的维度单列，不并入「未发现风险」的覆盖面。"""
    incomplete = sum(
        1 for finding in report.dimensions if finding.status not in _SETTLED_STATUSES
    )
    tail = f"，另有 {incomplete} 个维度未取得结果" if incomplete else ""
    return f"未发现重点风险命中（共核查 {len(report.dimensions)} 个维度{tail}）"


def _drop_procedural(summary: str) -> str:
    """剥离纯过程性说明，事实在前、去重后的口径提示在后。"""
    clauses = [clause.strip(" 、；，,:：") for clause in _CLAUSE_SPLIT_RE.split(summary)]
    kept = [
        clause for clause in clauses if "口径" not in clause and _states_fact(clause)
    ]
    kept.extend(dict.fromkeys(clause for clause in clauses if "口径" in clause))
    return "；".join(kept)


def _states_fact(clause: str) -> bool:
    """分句是否陈述事实：无过程性标记，或同时带有数量/金额/风险事实标记。"""
    text = clause.strip()
    if not text:
        return False
    return not _PROCESS_MARKER_RE.search(text) or bool(_FACT_MARKER_RE.search(text))


def _strip_case_numbers(summary: str) -> str:
    """去除摘要中的案号并压缩重复分隔符，留下案件性质、金额、状态等要点。"""
    text = _CASE_NO_RE.sub("", summary)
    text = _SEPARATOR_RUN_RE.sub("、", text)
    text = _WHITESPACE_RE.sub(" ", text)
    return text.strip(" 、；，,:：。")


def _build_finding(
    key: str, spec: _DimensionSpec, entry: Mapping[object, object]
) -> DimensionFinding:
    status = DimensionStatus(_require_text(entry.get("status"), f"{key}.status"))
    raw = _coerce_text(entry.get("raw"), f"{key}.raw")
    message = _coerce_text(entry.get("message"), f"{key}.message")
    if status is DimensionStatus.SUCCESS and raw is not None:
        facts = _parse_facts(raw)
        change_items = _parse_change_sections(raw) if key == _CHANGE_TOOL else []
        if facts.abstract or facts.groups or change_items or not _EMPTY_MARKERS.search(raw):
            hit = spec.risk_relevant
            summary = _summarize(facts) or "有记录"
            if key == _SHELL_TOOL:
                counts = [int(match) for match in _SHELL_TAG_RE.findall(raw)]
                tag_count = max(counts) if counts else None
                hit = tag_count is not None and tag_count > 0
                if tag_count is not None:
                    summary = f"空壳识别标签 {tag_count} 项"
            elif key == _CHANGE_TOOL:
                # 「共有 N 条变更记录」只说条数，不说变了什么；分节式原文优先，
                # 旧版表格回退既有解析，二者都取不到时保留原始摘要。
                summary = _join_change_items(change_items) or _change_summary(facts) or summary
            evidence = (
                change_items
                if change_items
                else [item for _, items in facts.groups for item in items]
            )
            return DimensionFinding(
                key=key,
                name=spec.name,
                status=status,
                risk_level=_risk_level(raw),
                hit=hit,
                summary=summary,
                evidence_refs=evidence[:_MAX_EVIDENCE_ITEMS],
                raw_ref=f"dimensions.{key}.raw",
            )
        status = DimensionStatus.EMPTY
    elif status is DimensionStatus.SUCCESS:
        status = DimensionStatus.EMPTY
    if status is DimensionStatus.EMPTY:
        summary = f"未发现{spec.name}记录"
    elif status is DimensionStatus.ERROR:
        detail = _clip(mask_sensitive(message), 80) if message else ""
        summary = f"查询失败：{detail}" if detail else "查询失败"
    elif status is DimensionStatus.QUOTA_EXHAUSTED:
        summary = "额度耗尽，未执行查询"
    elif status is DimensionStatus.NOT_CONFIGURED:
        summary = "未启用，未执行查询"
    else:
        summary = "查询繁忙，未执行查询"
    return DimensionFinding(key=key, name=spec.name, status=status, hit=False, summary=summary)


class _Facts(NamedTuple):
    abstract: str | None
    groups: tuple[tuple[str, tuple[str, ...]], ...]


def _parse_facts(raw: str) -> _Facts:
    abstract: str | None = None
    groups: list[tuple[str, tuple[str, ...]]] = []
    title = ""
    items: list[str] = []
    header: list[str] = []
    in_table = False
    for line in raw[:20_000].splitlines():  # 扫描窗口有界，超长 raw 不放大报告
        stripped = line.strip()
        if stripped.startswith("|"):
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            if not in_table:
                in_table, header = True, cells
            elif header[0] != "字段" and not all(
                re.fullmatch(r":?-{2,}:?", cell) for cell in cells
            ):
                # 变更记录表先按列名组合「项目：前值 → 后值（日期）」；表头未命中变更列
                # 时返回 None，回退通用列选取，不影响其他维度的表格解析。
                change = _change_record_cell(header, cells)
                if change:
                    _append_fact(items, change)
                    continue
                value = _label_cell(header, cells)
                if value:
                    _append_unique(items, value)
            continue
        in_table = False
        if stripped.startswith("#"):
            if items:
                groups.append((title, tuple(items)))
            title, items = stripped.lstrip("#").strip(), []
        elif stripped.startswith("> 摘要："):
            abstract = abstract or stripped[len("> 摘要：") :].strip()
        elif stripped.startswith("- ") and not stripped.startswith("- tool:"):
            value = mask_sensitive(
                _BULLET_SPLIT_RE.split(stripped[2:].strip())[0].strip()
            )
            value = _clip(value, _MAX_ITEM_CHARS + 1)
            if value:
                _append_unique(items, value)
    if items:
        groups.append((title, tuple(items)))
    return _Facts(abstract, tuple(groups))


def _summarize(facts: _Facts) -> str:
    if facts.abstract:
        text = facts.abstract
    elif facts.groups:
        parts = [
            f"{title}：{'、'.join(items)}" if title else "、".join(items)
            for title, items in facts.groups
        ]
        text = "；".join(parts)
    else:
        return ""
    return _clip(mask_sensitive(text), _MAX_SUMMARY_CHARS)


def _change_summary(facts: _Facts) -> str:
    """变更记录摘要：用具体变更事实，不使用「共有 N 条变更记录」这类条数摘要。

    条数只说明查到了多少记录，不说明主体发生了什么变化；AI 与运营需要的是
    「项目：变更前 → 变更后（变更日期）」这样的可直接引用的事实。
    """
    items = [item for _, group in facts.groups for item in group]
    if not items:
        return ""
    return _clip(mask_sensitive("；".join(items)), _MAX_SUMMARY_CHARS)


def _join_change_items(items: list[str]) -> str:
    if not items:
        return ""
    return _clip(mask_sensitive("；".join(items)), _MAX_SUMMARY_CHARS)


def _parse_change_sections(raw: str) -> list[str]:
    """解析分节式变更记录（当前上游形态）。

    上游同一接口存在两种 Markdown 形态：旧版五行表格（``_parse_facts`` 处理）与
    分节形态——每条记录一个「#### 第 N 条 · 日期 · 项目」标题，变更前/后值放在
    ```text 代码块里。分节形态没有表格，旧解析只能退回「共有 N 条变更记录」，
    因而在此单独解析，返回按原文顺序（最新在前）的事实条目：

    - 单值变更（负责人、登记机关等）：「项目：变更前 → 变更后（日期）」；
    - 少量并列值（股东、董监高）：「项目：新增 X；退出 Y（日期）」；
    - 值过多或前后相同：只用「项目（日期）」，不臆造或截断成半句。
    """
    items: list[str] = []
    date = ""
    project = ""
    before: str | None = None
    after: str | None = None
    field: str | None = None
    fence: list[str] | None = None

    def flush() -> None:
        if not project:
            return
        text = project
        pair = _change_pair_text(before, after)
        if pair:
            text = f"{project}：{pair}"
        if date:
            text = f"{text}（{date}）"
        _append_fact(items, text)

    for line in raw.splitlines():
        stripped = line.strip()
        heading = _CHANGE_SECTION_RE.match(stripped)
        if heading:
            flush()
            date, project = _split_change_heading(heading.group("rest"))
            before = after = None
            field = None
            fence = None
            continue
        if not project:
            continue
        if stripped.startswith("#"):
            field = (
                "before"
                if "变更前原文" in stripped
                else "after"
                if "变更后原文" in stripped
                else None
            )
            continue
        if stripped.startswith("```"):
            if fence is None:
                fence = []
            else:
                value = _WHITESPACE_RE.sub(" ", " ".join(fence)).strip()
                if field == "before":
                    before = value
                elif field == "after":
                    after = value
                fence = None
                field = None
            continue
        if fence is not None:
            fence.append(stripped)
    flush()
    return items


def _split_change_heading(rest: str) -> tuple[str, str]:
    """标题载荷 → （日期，项目）；缺失日期时保持未知，不拿记录创建日期补齐。"""
    parts = [part.strip() for part in re.split(r"[·・]", rest, maxsplit=1)]
    if len(parts) == 2 and _CHANGE_DATE_RE.fullmatch(parts[0]):
        return parts[0], parts[1]
    return "", rest.strip()


def _change_pair_text(before: str | None, after: str | None) -> str:
    """变更前/后原文 → 可读对照；只做确定性的等值比较，不推断先后含义。"""
    if not before or not after or before == after:
        return ""
    if _single_change_value(before) and _single_change_value(after):
        return f"{before} → {after}"
    before_items = before.split()
    after_items = after.split()
    if len(before_items) > _MAX_CHANGE_DIFF_ITEMS or len(after_items) > _MAX_CHANGE_DIFF_ITEMS:
        return ""
    added = [item for item in after_items if item not in before_items]
    removed = [item for item in before_items if item not in after_items]
    parts = []
    if added:
        parts.append("新增 " + "、".join(added))
    if removed:
        parts.append("退出 " + "、".join(removed))
    return "；".join(parts)


def _single_change_value(text: str) -> bool:
    """单值变更：单个姓名/机构名，不含并列值，长度可控。"""
    return len(text) <= 16 and not any(char.isspace() for char in text)


def _risk_level(raw: str) -> RiskLevel | None:
    return next((level for level in RiskLevel if level.value in raw), None)


# 表格证据优先取这些列（按优先级），使摘要与证据突出案由/身份/金额/状态而非案号。
_PREFERRED_COLUMNS = ("案由", "案件身份", "金额", "标的", "状态")

# 变更记录表按**列名包含关系**识别，不依赖固定列序：真实返回既可能是
# 「序号|变更日期|变更项目|变更前|变更后」，也可能是「#|变更事项|变更日期」。
_CHANGE_ITEM_COLUMNS = ("变更项目", "变更事项", "变更内容", "变更类型")
_CHANGE_BEFORE_COLUMNS = ("变更前",)
_CHANGE_AFTER_COLUMNS = ("变更后",)
_CHANGE_DATE_COLUMNS = ("变更日期", "变更时间", "日期")


def _match_column(header: list[str], wanted: tuple[str, ...]) -> int | None:
    """按列名包含关系取首个命中列的位置；``wanted`` 按优先级排列。"""
    for name in wanted:
        position = next(
            (index for index, column in enumerate(header) if name in column), None
        )
        if position is not None:
            return position
    return None


def _change_record_cell(header: list[str], cells: list[str]) -> str | None:
    """变更记录行 → ``项目：变更前 → 变更后（变更日期）``。

    表头未命中变更项目列时返回 None，调用方回退通用列选取，因此只影响变更记录类
    表格；缺少前值/后值/日期列时自动省略对应片段，不臆造内容。``项目`` 里的括号
    说明文字（如「负责人变更（法定代表人、负责人等变更）」）原样保留。
    """
    item_index = _match_column(header, _CHANGE_ITEM_COLUMNS)
    if item_index is None:
        return None
    item = _cell_text(cells, item_index)
    before = _cell_text(cells, _match_column(header, _CHANGE_BEFORE_COLUMNS))
    after = _cell_text(cells, _match_column(header, _CHANGE_AFTER_COLUMNS))
    date = _cell_text(cells, _match_column(header, _CHANGE_DATE_COLUMNS))
    changed = f"{before} → {after}" if before and after else (before or after)
    text = f"{item}：{changed}" if item and changed else (changed or item or "")
    if not text:
        return None
    return f"{text}（{date}）" if date else text


def _cell_text(cells: list[str], index: int | None) -> str | None:
    """取单元格文本：保留日期与数值形态（``2026-07-14`` 不是纯数字序号），只剔除空值与 ``-``。"""
    if index is None or index >= len(cells):
        return None
    value = mask_sensitive(_WHITESPACE_RE.sub(" ", cells[index]).strip())
    if not value or value == "-":
        return None
    return _bounded_item(value)


def _label_cell(header: list[str], cells: list[str]) -> str | None:
    for wanted in _PREFERRED_COLUMNS:
        index = next(
            (position for position, name in enumerate(header) if wanted in name),
            None,
        )
        if index is None:
            continue
        # 命中优先级列：该列取值即证据；空值/-/纯数字表示本行没有可用要点。
        return _clean_cell(cells[index]) if index < len(cells) else None
    index = 1 if header and header[0] == "#" else 0
    return _clean_cell(cells[index]) if index < len(cells) else None


def _clean_cell(text: str) -> str | None:
    value = _bounded_item(mask_sensitive(_WHITESPACE_RE.sub(" ", text).strip()))
    if not value or value == "-" or _NUMERIC_RE.fullmatch(value):
        return None
    return value


def _append_unique(items: list[str], value: str) -> None:
    clean = _bounded_item(mask_sensitive(value))
    if clean and clean not in items and len(items) < _MAX_EVIDENCE_ITEMS:
        items.append(clean)


def _append_fact(items: list[str], value: str) -> None:
    """追加已组合的变更事实：沿用去重与条目数上限，但不再按单元格上限二次截断。

    各片段已由 ``_cell_text`` 按 ``_MAX_ITEM_CHARS`` 有界；组合串若再按 40 字截断，
    会把「变更后」与变更日期整段切掉，恰好丢掉最有价值的部分。
    """
    clean = mask_sensitive(value)
    if clean and clean not in items and len(items) < _MAX_EVIDENCE_ITEMS:
        items.append(clean)


def _bounded_item(text: str) -> str:
    """证据条目统一上限：超长截断，避免原始长文本放大报告体积。"""
    return text if len(text) <= _MAX_ITEM_CHARS else text[: _MAX_ITEM_CHARS] + "…"


def mask_sensitive(text: str) -> str:
    """电话/证件号脱敏；报告构造与分析上下文共用同一实现，避免两套屏蔽口径。"""
    for pattern in _SENSITIVE_PATTERNS:
        text = pattern.sub("[已脱敏]", text)
    return text


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _coerce_text(value: object, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field} 必须是字符串或 None")
    text = value.strip()
    return text or None


def _require_text(value: object, field: str) -> str:
    text = _coerce_text(value, field)
    if text is None:
        raise ValueError(f"{field} 不能为空")
    return text


def _period_key(supplier_code: str, generated_at: datetime) -> str:
    iso = generated_at.astimezone(_BEIJING).isocalendar()
    return f"tyc:{supplier_code}:{iso.year}-W{iso.week:02d}"
