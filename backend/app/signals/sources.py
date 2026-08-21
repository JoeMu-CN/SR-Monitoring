"""拉取式数据源适配器。

技术方案 7.2：所有外部来源实现统一 SourceAdapter：
    fetch(cursor) -> RawSourceItem[]
    normalize(item) -> NormalizedSignal
    fingerprint(signal) -> string
    healthcheck() -> SourceHealth

manual-json 是文件上传式（见 adapter.py），本模块实现 HTTP 拉取式数据源。
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import html
import io
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from typing import Protocol

import httpx
from pydantic import HttpUrl, ValidationError

from app.signals.request_control import SourceRequestFailed, controlled_get
from app.signals.schemas import ManualSignalInput

DEFAULT_TIMEOUT_SECONDS = 15.0
MAX_ITEMS_PER_FETCH = 100
MAX_NMC_BYTES = 2 * 1024 * 1024
MAX_OFAC_BYTES = 20 * 1024 * 1024


class SourceFetchError(RuntimeError):
    """拉取数据源在请求或解析阶段失败。"""

    def __init__(
        self,
        message: str,
        *,
        error_kind: str | None = None,
        http_status: int | None = None,
    ) -> None:
        self.error_kind = error_kind
        self.http_status = http_status
        super().__init__(message)


class SourceHealth:
    def __init__(self, ok: bool, message: str | None = None) -> None:
        self.ok = ok
        self.message = message


@dataclass(frozen=True)
class RawSourceItem:
    """数据源返回的原始条目，未经过校验和标准化。"""

    external_id: str
    title: str
    content: str
    url: str | None = None
    published_at: datetime | None = None
    extra: dict[str, object] = field(default_factory=dict)


class PullSourceAdapter(Protocol):
    """拉取式数据源接口约定（协议类，可被任何实现替换）。"""

    source_code: str

    async def fetch(self, cursor: str | None = None) -> list[RawSourceItem]: ...

    def normalize(self, item: RawSourceItem) -> ManualSignalInput: ...

    def fingerprint(self, signal: ManualSignalInput) -> str: ...

    async def healthcheck(self) -> SourceHealth: ...


def _fingerprint_sha256(canonical_text: str) -> str:
    return hashlib.sha256(canonical_text.encode("utf-8")).hexdigest()


def _to_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class NmcWeatherAdapter(PullSourceAdapter):
    """中央气象台天气预警数据源（自然灾害/天气类）。

    接口：http://www.nmc.cn/rest/findAlarm
    返回全国各级气象台发布的灾害预警信号（暴雨、台风、山洪、大雾等）。
    属于官方公开数据源，无需鉴权，符合技术方案 7.2 首批数据源选择原则。
    """

    source_code = "nmc-weather"
    endpoint = "http://www.nmc.cn/rest/findAlarm"

    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._transport = transport
        self._timeout_seconds = timeout_seconds

    async def fetch(self, cursor: str | None = None) -> list[RawSourceItem]:
        params = {
            "pageNo": cursor or "1",
            "pageSize": str(MAX_ITEMS_PER_FETCH),
            "signaltype": "",
            "signallevel": "",
            "province": "",
        }
        try:
            response = await controlled_get(
                self.endpoint,
                params=params,
                timeout=self._timeout_seconds,
                maximum_bytes=MAX_NMC_BYTES,
                transport=self._transport,
            )
        except SourceRequestFailed as exc:
            raise SourceFetchError(
                f"中央气象台接口请求失败: {exc}",
                error_kind=exc.error_kind,
                http_status=exc.status_code,
            ) from exc
        try:
            payload = json.loads(response.content)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise SourceFetchError("中央气象台接口返回不是有效 JSON") from exc

        if payload.get("code") != 0 or not isinstance(payload.get("data"), dict):
            raise SourceFetchError(f"中央气象台接口业务错误: {payload.get('msg')}")
        page = payload["data"].get("page") or {}
        rows = page.get("list") or []
        items: list[RawSourceItem] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            alert_id = str(row.get("alertid") or "").strip()
            title = str(row.get("title") or "").strip()
            if not alert_id or not title:
                continue
            items.append(
                RawSourceItem(
                    external_id=alert_id,
                    title=title,
                    content=title,
                    url=self._absolute_url(str(row.get("url") or "")),
                    published_at=_parse_nmc_time(row.get("issuetime")),
                    extra={"pic": str(row.get("pic") or "")},
                )
            )
        return items

    @staticmethod
    def _absolute_url(path: str) -> str | None:
        if not path:
            return None
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return f"https://www.nmc.cn{path}"

    def normalize(self, item: RawSourceItem) -> ManualSignalInput:
        try:
            return ManualSignalInput(
                external_id=item.external_id,
                title=item.title,
                content=item.content,
                url=HttpUrl(item.url) if item.url else None,
                published_at=_to_utc(item.published_at),
            )
        except ValidationError as exc:
            raise SourceFetchError(f"信号校验失败: {exc}") from exc

    def fingerprint(self, signal: ManualSignalInput) -> str:
        canonical = json.dumps(
            signal.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return _fingerprint_sha256(canonical)

    async def healthcheck(self) -> SourceHealth:
        try:
            items = await self.fetch()
        except SourceFetchError as exc:
            return SourceHealth(ok=False, message=str(exc))
        if not items:
            return SourceHealth(ok=False, message="中央气象台接口无返回数据")
        return SourceHealth(ok=True, message=f"返回 {len(items)} 条预警")


class OfacSdnAdapter(PullSourceAdapter):
    """美国财政部 OFAC SDN 官方公开 CSV 制裁名单。"""

    source_code = "ofac-sdn"
    endpoint = "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN.CSV"

    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._transport = transport
        self._timeout_seconds = timeout_seconds

    async def fetch(self, cursor: str | None = None) -> list[RawSourceItem]:
        del cursor
        try:
            response = await controlled_get(
                self.endpoint,
                timeout=self._timeout_seconds,
                maximum_bytes=MAX_OFAC_BYTES,
                follow_redirects=True,
                transport=self._transport,
            )
        except SourceRequestFailed as exc:
            raise SourceFetchError(
                f"OFAC SDN 接口请求失败: {exc}",
                error_kind=exc.error_kind,
                http_status=exc.status_code,
            ) from exc
        try:
            text = response.content.decode("utf-8-sig")
            rows = csv.reader(io.StringIO(text))
            items: list[RawSourceItem] = []
            for line_number, row in enumerate(rows, start=1):
                if not row or not any(cell.strip() for cell in row):
                    continue
                if len(row) < 4:
                    # OFAC 当前 CSV 末尾带 DOS EOF 标记（Ctrl-Z）尾行，不是实体记录。
                    if len(row) == 1 and row[0] == "\x1a":
                        continue
                    raise SourceFetchError(f"OFAC SDN 第 {line_number} 行字段不足")
                entity_number = row[0].strip()
                name = row[1].strip()
                country = row[3].strip()
                remarks = row[11].strip() if len(row) > 11 else ""
                if not entity_number or not name:
                    continue
                details = [f"名称：{name}"]
                if country and country != "-0-":
                    details.append(f"国家/地区：{country}")
                if remarks and remarks != "-0-":
                    details.append(f"备注：{remarks}")
                items.append(
                    RawSourceItem(
                        external_id=f"ofac-sdn-{entity_number}",
                        title=f"OFAC SDN 制裁名单：{name}",
                        content="；".join(details),
                        url=self.endpoint,
                        extra={"entity_number": entity_number, "country": country},
                    )
                )
        except UnicodeDecodeError as exc:
            raise SourceFetchError("OFAC SDN 文件不是 UTF-8 编码") from exc
        except csv.Error as exc:
            raise SourceFetchError("OFAC SDN 文件不是有效 CSV") from exc
        return items

    def normalize(self, item: RawSourceItem) -> ManualSignalInput:
        try:
            return ManualSignalInput(
                external_id=item.external_id,
                title=item.title,
                content=item.content,
                url=HttpUrl(item.url) if item.url else None,
                published_at=_to_utc(item.published_at),
            )
        except ValidationError as exc:
            raise SourceFetchError(f"信号校验失败: {exc}") from exc

    def fingerprint(self, signal: ManualSignalInput) -> str:
        canonical = json.dumps(
            signal.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return _fingerprint_sha256(canonical)

    async def healthcheck(self) -> SourceHealth:
        try:
            items = await self.fetch()
        except SourceFetchError as exc:
            return SourceHealth(ok=False, message=str(exc))
        if not items:
            return SourceHealth(ok=False, message="OFAC SDN 接口无有效条目")
        return SourceHealth(ok=True, message=f"返回 {len(items)} 条制裁条目")


class EuOfficialJournalAdapter(PullSourceAdapter):
    """欧盟官方公报（EUR-Lex CELLAR SPARQL）法规目录适配器。

    直连 publications.europa.eu/webapi/rdf/sparql（SPARQL GET，JSON 结果），
    查询最近发布的法规类（CELEX 以 3 开头）条目，含标题/日期/CELEX。
    不依赖浏览器渲染（EUR-Lex 网页被 CloudFront 202 JS 挑战拦截，SPARQL 可直连）。
    """

    source_code = "eu-official-journal"
    endpoint = "https://publications.europa.eu/webapi/rdf/sparql"
    _DEFAULT_LOOKBACK_DAYS = 7
    _MAX_ITEMS = 50
    # 关键词过滤（空 = 抓全部法规；子类覆盖为供应链合规关键词）
    _KEYWORDS: tuple[str, ...] = ()
    _SPARQL_PREFIX = (
        "PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>"
        "PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>"
    )

    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        lookback_days: int = _DEFAULT_LOOKBACK_DAYS,
    ) -> None:
        self._transport = transport
        self._timeout_seconds = timeout_seconds
        self._lookback_days = lookback_days

    def _build_query(self, since_date: str) -> str:
        """生成 SPARQL：最近 lookback 天内发布的法规类条目（含标题/日期/CELEX）。

        关键词过滤在 SPARQL 层完成（CONTAINS + LCASE），避免 LIMIT 截断后
        Python 侧过滤的漏检（欧盟法规日发布量大，50 条截断覆盖不到合规类）。
        """
        keyword_filter = ""
        if self._KEYWORDS:
            conds = " || ".join(
                f'CONTAINS(LCASE(STR(?title)), "{kw}")' for kw in self._KEYWORDS
            )
            keyword_filter = f"  FILTER({conds})\n"
        return f"""
{self._SPARQL_PREFIX}
select distinct ?celex ?date ?title where{{
  ?work cdm:work_has_resource-type ?type.
  ?work cdm:resource_legal_id_celex ?celex.
  ?work cdm:work_date_document ?date.
  ?work cdm:work_title ?title.
  FILTER(lang(?title) = 'en')
  FILTER not exists{{?work cdm:do_not_index "true"^^xsd:boolean}}.
  FILTER(STRSTARTS(STR(?celex), "3"))
  FILTER(?date >= "{since_date}"^^xsd:date)
{keyword_filter}}} ORDER BY DESC(?date) LIMIT {self._MAX_ITEMS}
"""

    async def fetch(self, cursor: str | None = None) -> list[RawSourceItem]:
        del cursor
        since = (datetime.now(UTC) - timedelta(days=self._lookback_days)).date().isoformat()
        query = self._build_query(since)
        params = {"query": query, "format": "json"}
        url = self.endpoint
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en;q=0.9",
            "Accept": "application/sparql-results+json,application/json,*/*;q=0.8",
        }
        if self._transport is not None:
            # 测试/自定义 transport：直接调用（受控模式与声明式信源不同，
            # EUR-Lex 是官方只读 SPARQL，请求体为 GET 参数，无写操作）。
            try:
                async with httpx.AsyncClient(
                    timeout=self._timeout_seconds, transport=self._transport
                ) as client:
                    raw_response = await client.get(url, params=params, headers=headers)
            except httpx.HTTPError as exc:
                raise SourceFetchError("EUR-Lex SPARQL 网络请求失败") from exc
            body_bytes = raw_response.content
        else:
            from urllib.parse import urlencode

            request_url = f"{url}?{urlencode(params)}"
            try:
                controlled = await controlled_get(
                    request_url,
                    headers=headers,
                    timeout=self._timeout_seconds,
                    maximum_bytes=5 * 1024 * 1024,
                )
            except SourceRequestFailed as exc:
                raise SourceFetchError(
                    f"EUR-Lex SPARQL 请求失败: {exc}",
                    error_kind=exc.error_kind,
                    http_status=exc.status_code,
                ) from exc
            body_bytes = controlled.content
        try:
            payload = json.loads(body_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SourceFetchError("EUR-Lex SPARQL 返回不是有效 JSON") from exc
        bindings = (
            payload.get("results", {}).get("bindings", [])
            if isinstance(payload, dict)
            else []
        )
        items: list[RawSourceItem] = []
        seen: set[str] = set()
        for row in bindings:
            if not isinstance(row, dict):
                continue
            celex = _binding(row, "celex")
            title = _binding(row, "title")
            date = _binding(row, "date")
            if not celex or not title or celex in seen:
                continue
            seen.add(celex)
            items.append(
                RawSourceItem(
                    external_id=f"euoj-{celex}",
                    title=title,
                    content=(
                        f"CELEX：{celex}；发布日期：{date or '未知'}"
                    ),
                    url=f"https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:{celex}",
                    extra={"celex": celex, "published_date": date},
                )
            )
        return items

    def normalize(self, item: RawSourceItem) -> ManualSignalInput:
        try:
            return ManualSignalInput(
                external_id=item.external_id,
                title=item.title,
                content=item.content,
                url=HttpUrl(item.url) if item.url else None,
                published_at=_to_utc(item.published_at),
            )
        except ValidationError as exc:
            raise SourceFetchError(f"信号校验失败: {exc}") from exc

    def fingerprint(self, signal: ManualSignalInput) -> str:
        canonical = json.dumps(
            signal.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return _fingerprint_sha256(canonical)

    async def healthcheck(self) -> SourceHealth:
        try:
            items = await self.fetch()
        except SourceFetchError as exc:
            return SourceHealth(ok=False, message=str(exc))
        if not items:
            return SourceHealth(ok=False, message="EUR-Lex 最近无法规条目")
        return SourceHealth(ok=True, message=f"返回 {len(items)} 条法规")


class EuComplianceAdapter(EuOfficialJournalAdapter):
    """供应链合规法规（P3：CBAM / CSDDD / 强迫劳动等）关键词过滤版。

    复用 EuOfficialJournalAdapter 的 SPARQL 直连链路，仅在标题层按
    合规关键词过滤（英文标题）。用于监控 EU 碳边境调节机制（CBAM）、
    企业可持续发展尽职调查指令（CSDDD）、强迫劳动/供应链人权等法规变更。
    """

    source_code = "eu-compliance"
    # 标题命中任一关键词即保留（小写匹配）。UFLPA 属美国法律，由 DHS 信源覆盖。
    _KEYWORDS: tuple[str, ...] = (
        "carbon border",
        "cbam",
        "forced labour",
        "forced labor",
        "due diligence",
        "supply chain",
        "human rights",
        "conflict minerals",
        "sustainable corporate governance",
    )


class UflpaEntityAdapter(PullSourceAdapter):
    """美国 UFLPA 实体清单（P3 供应链合规：涉疆强迫劳动执法名单）。

    直连 DHS 官方实体清单页（dhs.gov/uflpa-entity-list，HTML 表格，137KB
    可直连），解析 "<tr><td>实体名</td><td>生效日期</td></tr>" 结构。
    每条实体生成一条信号（external_id=uflpa-{sha256(实体名)[:16]}，指纹稳定
    可去重），可匹配清单内供应商及产业链上下游（原材料/产地涉疆）。
    全量清单每次抓取返回全部实体，增量靠指纹去重。
    """

    source_code = "uflpa-entity-list"
    endpoint = "https://www.dhs.gov/uflpa-entity-list"

    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._transport = transport
        self._timeout_seconds = timeout_seconds

    async def fetch(self, cursor: str | None = None) -> list[RawSourceItem]:
        del cursor
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": "text/html,*/*;q=0.8",
        }
        if self._transport is not None:
            try:
                async with httpx.AsyncClient(
                    timeout=self._timeout_seconds, transport=self._transport
                ) as client:
                    raw_response = await client.get(self.endpoint, headers=headers)
            except httpx.HTTPError as exc:
                raise SourceFetchError("UFLPA 实体清单网络请求失败") from exc
            body_bytes = raw_response.content
        else:
            try:
                controlled = await controlled_get(
                    self.endpoint,
                    headers=headers,
                    timeout=self._timeout_seconds,
                    maximum_bytes=5 * 1024 * 1024,
                )
            except SourceRequestFailed as exc:
                raise SourceFetchError(
                    f"UFLPA 实体清单请求失败: {exc}",
                    error_kind=exc.error_kind,
                    http_status=exc.status_code,
                ) from exc
            body_bytes = controlled.content
        try:
            page_html = body_bytes.decode("utf-8", "ignore")
        except Exception as exc:  # noqa: BLE001
            raise SourceFetchError("UFLPA 实体清单响应解码失败") from exc
        # 实体行: <tr><td>Name</td><td>June 21, 2022</td></tr>
        rows = re.findall(r"<tr><td>([^<]+)</td><td>([^<]+)</td></tr>", page_html)
        items: list[RawSourceItem] = []
        for name, date_text in rows:
            name = html.unescape(name.strip())
            if not name or name.lower() == "name of entity":
                continue
            published_at = _parse_uflpa_date(date_text.strip())
            items.append(
                RawSourceItem(
                    external_id="uflpa-"
                    + hashlib.sha256(name.encode("utf-8")).hexdigest()[:16],
                    title=name,
                    content=(
                        f"生效日期：{date_text.strip()}；依据：UFLPA"
                        "（Uyghur Forced Labor Prevention Act）"
                    ),
                    url=self.endpoint,
                    published_at=published_at,
                    extra={"effective_date": date_text.strip()},
                )
            )
        return items

    def normalize(self, item: RawSourceItem) -> ManualSignalInput:
        try:
            return ManualSignalInput(
                external_id=item.external_id,
                title=item.title,
                content=item.content,
                url=HttpUrl(item.url) if item.url else None,
                published_at=_to_utc(item.published_at),
            )
        except ValidationError as exc:
            raise SourceFetchError(f"信号校验失败: {exc}") from exc

    def fingerprint(self, signal: ManualSignalInput) -> str:
        canonical = json.dumps(
            signal.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return _fingerprint_sha256(canonical)

    async def healthcheck(self) -> SourceHealth:
        try:
            items = await self.fetch()
        except SourceFetchError as exc:
            return SourceHealth(ok=False, message=str(exc))
        if not items:
            return SourceHealth(ok=False, message="UFLPA 实体清单为空")
        return SourceHealth(ok=True, message=f"返回 {len(items)} 个实体")


class BisEntityListAdapter(PullSourceAdapter):
    """美国商务部 BIS 实体清单（I2 技术断供：芯片管制/出口管制实体）。

    直连 bis.gov/regulations/ear/744（Entity List 所在法规页，16.5MB HTML
    表格），解析 "Country | Entity | License requirement | License review
    policy | Federal Register citation" 列结构。每条实体生成一条信号
    （external_id=bis-{sha256(实体名)[:16]}，指纹稳定可去重）。
    全量清单每次抓取返回全部实体，增量靠指纹去重。
    """

    source_code = "bis-entity-list"
    # /entity-list 307 重定向到 EAR 744 法规页（含 Entity List 表格），
    # 直接配置最终官方地址（受控链路将重定向视为错误，遵循 SSRF 防护设计）。
    endpoint = "https://www.bis.gov/regulations/ear/744"

    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._transport = transport
        self._timeout_seconds = timeout_seconds

    @staticmethod
    def _clean_cell(raw: str) -> str:
        """去 HTML 标签 + 解码实体 + 压缩水平空白（保留换行）。"""
        text = re.sub(r"<br\s*/?>", "\n", raw)  # 保留换行以切分名称/地址
        text = re.sub(r"<[^>]+>", " ", text)
        text = html.unescape(text)
        text = re.sub(r"[ \t]+", " ", text)
        return text.strip()

    async def fetch(self, cursor: str | None = None) -> list[RawSourceItem]:
        del cursor
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": "text/html,*/*;q=0.8",
        }
        if self._transport is not None:
            try:
                async with httpx.AsyncClient(
                    timeout=self._timeout_seconds, transport=self._transport
                ) as client:
                    raw_response = await client.get(self.endpoint, headers=headers)
            except httpx.HTTPError as exc:
                raise SourceFetchError("BIS 实体清单网络请求失败") from exc
            body_bytes = raw_response.content
        else:
            try:
                controlled = await controlled_get(
                    self.endpoint,
                    headers=headers,
                    timeout=self._timeout_seconds,
                    maximum_bytes=25 * 1024 * 1024,  # 页面约 16.5MB
                )
            except SourceRequestFailed as exc:
                raise SourceFetchError(
                    f"BIS 实体清单请求失败: {exc}",
                    error_kind=exc.error_kind,
                    http_status=exc.status_code,
                ) from exc
            body_bytes = controlled.content
        page_html = body_bytes.decode("utf-8", "ignore")
        # 表格行: <tr...><td>Country</td><td>Entity 名+别名+地址</td><td>License...</td>...
        items: list[RawSourceItem] = []
        seen: set[str] = set()
        for row in re.findall(r"<tr[^>]*>([\s\S]{0,4000}?)</tr>", page_html):
            tds = re.findall(r"<td[^>]*>([\s\S]{0,2500}?)</td>", row)
            if len(tds) < 2:
                continue
            country = self._clean_cell(tds[0])
            entity_raw = self._clean_cell(tds[1])
            license_req = self._clean_cell(tds[2]) if len(tds) > 2 else ""
            review_policy = self._clean_cell(tds[3]) if len(tds) > 3 else ""
            if not entity_raw or len(entity_raw) < 4:
                continue
            if entity_raw.lower().startswith(("entity", "name of entity", "country")):
                continue  # 表头
            # 实体名 = 第一行（含 a.k.a. 别名信息）；地址在换行后
            name = entity_raw.split("\n")[0].strip()
            if not name or name in seen:
                continue
            seen.add(name)
            content_parts = []
            if country and country.lower() != "country":
                content_parts.append(f"国家：{country}")
            content_parts.append(f"实体：{entity_raw}")
            if license_req and license_req.lower() != "license requirement":
                content_parts.append(f"许可要求：{license_req}")
            if review_policy and review_policy.lower() != "license review policy":
                content_parts.append(f"许可审查政策：{review_policy}")
            items.append(
                RawSourceItem(
                    external_id="bis-"
                    + hashlib.sha256(name.encode("utf-8")).hexdigest()[:16],
                    title=name[:200],
                    content="；".join(content_parts),
                    url=self.endpoint,
                    extra={"country": country, "license_requirement": license_req},
                )
            )
        return items

    def normalize(self, item: RawSourceItem) -> ManualSignalInput:
        try:
            return ManualSignalInput(
                external_id=item.external_id,
                title=item.title,
                content=item.content,
                url=HttpUrl(item.url) if item.url else None,
                published_at=_to_utc(item.published_at),
            )
        except ValidationError as exc:
            raise SourceFetchError(f"信号校验失败: {exc}") from exc

    def fingerprint(self, signal: ManualSignalInput) -> str:
        canonical = json.dumps(
            signal.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return _fingerprint_sha256(canonical)

    async def healthcheck(self) -> SourceHealth:
        try:
            items = await self.fetch()
        except SourceFetchError as exc:
            return SourceHealth(ok=False, message=str(exc))
        if not items:
            return SourceHealth(ok=False, message="BIS 实体清单为空")
        return SourceHealth(ok=True, message=f"返回 {len(items)} 个实体")


class CommodityFuturesAdapter(PullSourceAdapter):
    """大宗商品期货行情（E3 关键原材料价格/短缺）。

    免费 JSON 直连（新浪期货行情接口 hq.sinajs.cn，需 Referer，GBK 编码），
    取核心 15 个品种主力连续合约（铜/铝/锌/镍/锡/铅/金/银/原油/燃料油/
    螺纹钢/不锈钢/PTA/天然橡胶/棉花）。每条品种生成一条信号（含最新价与
    较昨结算涨跌幅），可匹配供应商关键原材料成本波动。external_id=
    fut-{code}-{YYYYMMDD}（每日一条，指纹稳定可去重）。
    """

    source_code = "commodity-futures"
    endpoint = "https://hq.sinajs.cn/list="
    # 品种代码 → (中文名, 单位)：主力连续合约
    _SYMBOLS: tuple[tuple[str, str, str], ...] = (
        ("nf_CU0", "沪铜", "元/吨"),
        ("nf_AL0", "沪铝", "元/吨"),
        ("nf_ZN0", "沪锌", "元/吨"),
        ("nf_NI0", "沪镍", "元/吨"),
        ("nf_SN0", "沪锡", "元/吨"),
        ("nf_PB0", "沪铅", "元/吨"),
        ("nf_AU0", "沪金", "元/克"),
        ("nf_AG0", "沪银", "元/千克"),
        ("nf_SC0", "原油", "元/桶"),
        ("nf_FU0", "燃料油", "元/吨"),
        ("nf_RB0", "螺纹钢", "元/吨"),
        ("nf_SS0", "不锈钢", "元/吨"),
        ("nf_TA0", "PTA", "元/吨"),
        ("nf_RU0", "天然橡胶", "元/吨"),
        ("nf_CF0", "棉花", "元/吨"),
    )

    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._transport = transport
        self._timeout_seconds = timeout_seconds

    async def fetch(self, cursor: str | None = None) -> list[RawSourceItem]:
        del cursor
        url = self.endpoint + ",".join(code for code, _name, _unit in self._SYMBOLS)
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": "https://finance.sina.com.cn/",
        }
        if self._transport is not None:
            try:
                async with httpx.AsyncClient(
                    timeout=self._timeout_seconds, transport=self._transport
                ) as client:
                    raw_response = await client.get(url, headers=headers)
            except httpx.HTTPError as exc:
                raise SourceFetchError("大宗商品行情网络请求失败") from exc
            body_bytes = raw_response.content
        else:
            try:
                controlled = await controlled_get(
                    url,
                    headers=headers,
                    timeout=self._timeout_seconds,
                    maximum_bytes=2 * 1024 * 1024,
                )
            except SourceRequestFailed as exc:
                raise SourceFetchError(
                    f"大宗商品行情请求失败: {exc}",
                    error_kind=exc.error_kind,
                    http_status=exc.status_code,
                ) from exc
            body_bytes = controlled.content
        try:
            body = body_bytes.decode("gbk", "ignore")
        except Exception as exc:  # noqa: BLE001
            raise SourceFetchError("大宗商品行情响应解码失败") from exc
        today = datetime.now(UTC).date().isoformat()
        items: list[RawSourceItem] = []
        for code, cname, unit in self._SYMBOLS:
            m = re.search(
                rf'hq_str_{re.escape(code)}="([^"]*)"', body
            )
            if not m:
                continue
            fields = m.group(1).split(",")
            if len(fields) < 9:
                continue
            try:
                last_price = float(fields[6])
                prev_settle = float(fields[2])
                open_price = float(fields[3])
            except ValueError:
                continue
            change_pct = (
                (last_price - prev_settle) / prev_settle * 100
                if prev_settle
                else 0.0
            )
            items.append(
                RawSourceItem(
                    external_id=f"fut-{code}-{today}",
                    title=f"{cname}主力 {last_price:.0f} {unit}"
                    f"（{change_pct:+.2f}%）",
                    content=(
                        f"最新价：{last_price:.1f} {unit}；较昨结算："
                        f"{change_pct:+.2f}%；昨结算：{prev_settle:.1f}；"
                        f"今开：{open_price:.1f}"
                    ),
                    url=(
                        "https://finance.sina.com.cn/futures/quotes/"
                        + code[3:]
                        + ".shtml"
                    ),
                    extra={
                        "symbol": code,
                        "last_price": last_price,
                        "change_pct": round(change_pct, 2),
                    },
                )
            )
        return items

    def normalize(self, item: RawSourceItem) -> ManualSignalInput:
        try:
            return ManualSignalInput(
                external_id=item.external_id,
                title=item.title,
                content=item.content,
                url=HttpUrl(item.url) if item.url else None,
                published_at=_to_utc(item.published_at),
            )
        except ValidationError as exc:
            raise SourceFetchError(f"信号校验失败: {exc}") from exc

    def fingerprint(self, signal: ManualSignalInput) -> str:
        canonical = json.dumps(
            signal.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return _fingerprint_sha256(canonical)

    async def healthcheck(self) -> SourceHealth:
        try:
            items = await self.fetch()
        except SourceFetchError as exc:
            return SourceHealth(ok=False, message=str(exc))
        if not items:
            return SourceHealth(ok=False, message="大宗商品行情为空")
        return SourceHealth(ok=True, message=f"返回 {len(items)} 个品种")


class PbcLprAdapter(PullSourceAdapter):
    """中国人民银行 LPR 报价（E4 货币政策：融资成本基准）。

    直连央行 LPR 栏目页（zhengcehuobisi/125207/125213/125440，月度公告列表），
    取最新一条"贷款市场报价利率（LPR）公告"，解析 1 年期 / 5 年期以上 LPR。
    external_id=pbc-lpr-{YYYY-MM-DD}（每月一条，指纹稳定可去重）。
    LPR 直接反映企业融资成本，可匹配供应商资金链压力（E4 传导路径）。
    """

    source_code = "pbc-lpr"
    endpoint = (
        "https://www.pbc.gov.cn/zhengcehuobisi/125207/125213/125440/index.html"
    )
    _DETAIL_RE = re.compile(
        r'href="(/zhengcehuobisi/125207/125213/125440/'
        r"\d+/20\d{12,}/index\.html)\"[^>]*>([^<]*贷款市场报价利率[^<]*)"
    )

    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._transport = transport
        self._timeout_seconds = timeout_seconds

    @staticmethod
    def _clean_text(raw: str) -> str:
        text = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", "", raw)
        text = re.sub(r"<[^>]+>", " ", text)
        return re.sub(r"\s+", " ", text).strip()

    async def _get(
        self, url: str, headers: dict[str, str]
    ) -> bytes:
        if self._transport is not None:
            try:
                async with httpx.AsyncClient(
                    timeout=self._timeout_seconds, transport=self._transport
                ) as client:
                    resp = await client.get(url, headers=headers)
            except httpx.HTTPError as exc:
                raise SourceFetchError("央行 LPR 网络请求失败") from exc
            return resp.content
        try:
            controlled = await controlled_get(
                url,
                headers=headers,
                timeout=self._timeout_seconds,
                maximum_bytes=5 * 1024 * 1024,
            )
        except SourceRequestFailed as exc:
            raise SourceFetchError(
                f"央行 LPR 请求失败: {exc}",
                error_kind=exc.error_kind,
                http_status=exc.status_code,
            ) from exc
        return controlled.content

    async def fetch(self, cursor: str | None = None) -> list[RawSourceItem]:
        del cursor
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Accept": "text/html,*/*;q=0.8",
        }
        try:
            list_html = (await self._get(self.endpoint, headers)).decode(
                "utf-8", "ignore"
            )
        except UnicodeDecodeError as exc:
            raise SourceFetchError("央行 LPR 列表解码失败") from exc
        # 最新一条 LPR 公告
        m = self._DETAIL_RE.search(list_html)
        if not m:
            raise SourceFetchError("央行 LPR 栏目未找到公告")
        detail_url = "https://www.pbc.gov.cn" + m.group(1)
        # 受控链路同域名请求最小间隔 10 秒（MIN_REQUEST_INTERVAL），
        # 列表页与详情页之间需等待，否则触发"请求过于频繁"。
        await asyncio.sleep(11)
        try:
            detail_html = (await self._get(detail_url, headers)).decode(
                "utf-8", "ignore"
            )
        except UnicodeDecodeError as exc:
            raise SourceFetchError("央行 LPR 详情解码失败") from exc
        text = self._clean_text(detail_html)
        # 公告日期：标题/正文中的 20XX年X月X日
        date_m = re.search(r"(20\d{2})年(\d{1,2})月(\d{1,2})日", text)
        lpr1_m = re.search(r"1年期LPR为([0-9.]+)%", text)
        lpr5_m = re.search(r"5年期(?:以上)?LPR为([0-9.]+)%", text)
        if not lpr1_m or not lpr5_m:
            raise SourceFetchError("央行 LPR 公告未解析到数值")
        if date_m:
            pub_date = (
                f"{date_m.group(1)}-{int(date_m.group(2)):02d}-"
                f"{int(date_m.group(3)):02d}"
            )
        else:
            pub_date = datetime.now(UTC).date().isoformat()
        lpr1, lpr5 = lpr1_m.group(1), lpr5_m.group(1)
        return [
            RawSourceItem(
                external_id=f"pbc-lpr-{pub_date}",
                title=f"LPR 报价：1年期 {lpr1}%、5年期以上 {lpr5}%（{pub_date}）",
                content=(
                    f"1年期 LPR：{lpr1}%；5年期以上 LPR：{lpr5}%；"
                    f"发布于 {pub_date}，下一次发布前有效"
                ),
                url=detail_url,
                extra={"lpr_1y": lpr1, "lpr_5y": lpr5, "date": pub_date},
            )
        ]

    def normalize(self, item: RawSourceItem) -> ManualSignalInput:
        try:
            return ManualSignalInput(
                external_id=item.external_id,
                title=item.title,
                content=item.content,
                url=HttpUrl(item.url) if item.url else None,
                published_at=_to_utc(item.published_at),
            )
        except ValidationError as exc:
            raise SourceFetchError(f"信号校验失败: {exc}") from exc

    def fingerprint(self, signal: ManualSignalInput) -> str:
        canonical = json.dumps(
            signal.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return _fingerprint_sha256(canonical)

    async def healthcheck(self) -> SourceHealth:
        try:
            items = await self.fetch()
        except SourceFetchError as exc:
            return SourceHealth(ok=False, message=str(exc))
        if not items:
            return SourceHealth(ok=False, message="央行 LPR 公告为空")
        return SourceHealth(ok=True, message=f"返回 {len(items)} 条 LPR 报价")


class StatsPmiAdapter(PullSourceAdapter):
    """国家统计局制造业 PMI（E1 宏观经济需求端）。

    直连统计局数据发布栏目（sj/zxfb，月度发布列表），取最新"中国采购经理
    指数运行情况"，解析制造业 PMI 数值与环比变化（>50 扩张 / <50 收缩）。
    external_id=stats-pmi-{YYYYMM}（每月一条，指纹稳定可去重）。
    PMI 直接反映订单/需求萎缩 → 供应商订单减少的传导路径（E1）。
    """

    source_code = "stats-pmi"
    endpoint = "https://www.stats.gov.cn/sj/zxfb/"
    _LIST_RE = re.compile(
        r'href="(\./\d{6}/t\d+_\d+\.html)"[^>]*>([^<]*中国采购经理指数运行情况)'
    )

    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._transport = transport
        self._timeout_seconds = timeout_seconds

    @staticmethod
    def _clean_text(raw: str) -> str:
        text = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", "", raw)
        text = re.sub(r"<[^>]+>", " ", text)
        return re.sub(r"\s+", " ", text).strip()

    async def _get(
        self, url: str, headers: dict[str, str]
    ) -> bytes:
        if self._transport is not None:
            try:
                async with httpx.AsyncClient(
                    timeout=self._timeout_seconds, transport=self._transport
                ) as client:
                    resp = await client.get(url, headers=headers)
            except httpx.HTTPError as exc:
                raise SourceFetchError("统计局 PMI 网络请求失败") from exc
            return resp.content
        try:
            controlled = await controlled_get(
                url,
                headers=headers,
                timeout=self._timeout_seconds,
                maximum_bytes=5 * 1024 * 1024,
            )
        except SourceRequestFailed as exc:
            raise SourceFetchError(
                f"统计局 PMI 请求失败: {exc}",
                error_kind=exc.error_kind,
                http_status=exc.status_code,
            ) from exc
        return controlled.content

    async def fetch(self, cursor: str | None = None) -> list[RawSourceItem]:
        del cursor
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Accept": "text/html,*/*;q=0.8",
        }
        list_html = (await self._get(self.endpoint, headers)).decode(
            "utf-8", "ignore"
        )
        m = self._LIST_RE.search(list_html)
        if not m:
            raise SourceFetchError("统计局数据发布栏目未找到 PMI 公告")
        detail_url = "https://www.stats.gov.cn/sj/zxfb/" + m.group(1).lstrip("./")
        # 受控链路同域名请求最小间隔 10 秒（MIN_REQUEST_INTERVAL）
        await asyncio.sleep(11)
        detail_html = (await self._get(detail_url, headers)).decode(
            "utf-8", "ignore"
        )
        text = self._clean_text(detail_html)
        # 7 月份，制造业采购经理指数（ PMI ）为 49.2%，比上月下降 1.1 个百分点
        pmi_m = re.search(
            r"制造业采购经理指数[（(]?\s*PMI\s*[）)]?为\s*([0-9.]+)%", text
        )
        change_m = re.search(r"比上月(下降|上升|持平)\s*([0-9.]+)?\s*个百分点", text)
        if not pmi_m:
            raise SourceFetchError("统计局 PMI 公告未解析到数值")
        # 月度标识：详情页标题/URL 中的 2026年7月 / 202607
        month_m = re.search(r"(20\d{2})年(\d{1,2})月", text) or re.search(
            r"(20\d{2})年(\d{1,2})月", m.group(2)
        )
        if month_m:
            month_key = f"{month_m.group(1)}-{int(month_m.group(2)):02d}"
        else:
            month_key = datetime.now(UTC).strftime("%Y-%m")
        pmi_val = pmi_m.group(1)
        if change_m:
            direction = change_m.group(1)
            amount = change_m.group(2) or "0"
            change_desc = f"环比{direction}{amount}个百分点"
        else:
            change_desc = "环比持平"
        return [
            RawSourceItem(
                external_id=f"stats-pmi-{month_key}",
                title=f"制造业 PMI {pmi_val}%（{month_key}，{change_desc}）",
                content=(
                    f"制造业采购经理指数（PMI）：{pmi_val}%（{month_key}）；"
                    f"{change_desc}；50 为荣枯线（>50 扩张 / <50 收缩）"
                ),
                url=detail_url,
                extra={
                    "pmi": pmi_val,
                    "month": month_key,
                    "change_desc": change_desc,
                },
            )
        ]

    def normalize(self, item: RawSourceItem) -> ManualSignalInput:
        try:
            return ManualSignalInput(
                external_id=item.external_id,
                title=item.title,
                content=item.content,
                url=HttpUrl(item.url) if item.url else None,
                published_at=_to_utc(item.published_at),
            )
        except ValidationError as exc:
            raise SourceFetchError(f"信号校验失败: {exc}") from exc

    def fingerprint(self, signal: ManualSignalInput) -> str:
        canonical = json.dumps(
            signal.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return _fingerprint_sha256(canonical)

    async def healthcheck(self) -> SourceHealth:
        try:
            items = await self.fetch()
        except SourceFetchError as exc:
            return SourceHealth(ok=False, message=str(exc))
        if not items:
            return SourceHealth(ok=False, message="统计局 PMI 公告为空")
        return SourceHealth(ok=True, message=f"返回 {len(items)} 条 PMI")


class MeeAnnouncementAdapter(PullSourceAdapter):
    """生态环境部公告（P1 行业监管：环评/标准/督察/名录）。

    直连生态环境部首页（mee.gov.cn，176KB 可直连），提取全部公告链接
    （约 150 条/次），按监管关键词过滤（环评/审批/督察/处罚/标准/名录/
    污染/停产/辐射）。每条公告生成一条信号，external_id=mee-{sha256(URL)}
    指纹稳定可去重。环保监管是供应商整改/停产/准入风险的直接触发源。
    """

    source_code = "mee-announcement"
    endpoint = "https://www.mee.gov.cn/"
    # 监管类关键词（标题命中任一即保留）
    _KEYWORDS: tuple[str, ...] = (
        "环评",
        "环境影响评价",
        "审批",
        "督察",
        "处罚",
        "标准",
        "名录",
        "污染",
        "停产",
        "辐射",
        "排污",
        "预警",
        "整治",
    )
    _LINK_RE = re.compile(
        r'href=[\'"]([^\'"]*20\d{4}/t\d+_\d+\.(?:shtml|html))[\'"]'
        r"[^>]*>([^<]{10,80})</a>"
    )

    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._transport = transport
        self._timeout_seconds = timeout_seconds

    async def fetch(self, cursor: str | None = None) -> list[RawSourceItem]:
        del cursor
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Accept": "text/html,*/*;q=0.8",
        }
        if self._transport is not None:
            try:
                async with httpx.AsyncClient(
                    timeout=self._timeout_seconds, transport=self._transport
                ) as client:
                    raw_response = await client.get(self.endpoint, headers=headers)
            except httpx.HTTPError as exc:
                raise SourceFetchError("生态环境部网络请求失败") from exc
            body_bytes = raw_response.content
        else:
            try:
                controlled = await controlled_get(
                    self.endpoint,
                    headers=headers,
                    timeout=self._timeout_seconds,
                    maximum_bytes=5 * 1024 * 1024,
                )
            except SourceRequestFailed as exc:
                raise SourceFetchError(
                    f"生态环境部请求失败: {exc}",
                    error_kind=exc.error_kind,
                    http_status=exc.status_code,
                ) from exc
            body_bytes = controlled.content
        page_html = body_bytes.decode("utf-8", "ignore")
        items: list[RawSourceItem] = []
        seen: set[str] = set()
        for href, title in self._LINK_RE.findall(page_html):
            title = html.unescape(title.strip())
            if not title or title in seen:
                continue
            seen.add(title)
            if not any(k in title for k in self._KEYWORDS):
                continue
            url = "https://www.mee.gov.cn" + href.lstrip(".")
            # 日期从 URL 提取（t20260821 → 2026-08-21）
            published_at = None
            m_date = re.search(r"/t(20\d{6})_", href)
            if m_date:
                try:
                    published_at = datetime.strptime(
                        m_date.group(1), "%Y%m%d"
                    ).replace(tzinfo=UTC)
                except ValueError:
                    published_at = None
            items.append(
                RawSourceItem(
                    external_id="mee-"
                    + hashlib.sha256(url.encode("utf-8")).hexdigest()[:16],
                    title=title,
                    content=(
                        "来源：生态环境部；发布于 "
                        f"{published_at.date() if published_at else '未知'}"
                    ),
                    url=url,
                    published_at=published_at,
                    extra={"published_date": m_date.group(1) if m_date else None},
                )
            )
        return items

    def normalize(self, item: RawSourceItem) -> ManualSignalInput:
        try:
            return ManualSignalInput(
                external_id=item.external_id,
                title=item.title,
                content=item.content,
                url=HttpUrl(item.url) if item.url else None,
                published_at=_to_utc(item.published_at),
            )
        except ValidationError as exc:
            raise SourceFetchError(f"信号校验失败: {exc}") from exc

    def fingerprint(self, signal: ManualSignalInput) -> str:
        canonical = json.dumps(
            signal.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return _fingerprint_sha256(canonical)

    async def healthcheck(self) -> SourceHealth:
        try:
            items = await self.fetch()
        except SourceFetchError as exc:
            return SourceHealth(ok=False, message=str(exc))
        if not items:
            return SourceHealth(ok=False, message="生态环境部无监管公告")
        return SourceHealth(ok=True, message=f"返回 {len(items)} 条公告")


class CustomsAnnouncementAdapter(PullSourceAdapter):
    """海关总署公告（P2 进出口政策 / E5 关税与禁限）。

    官网 www.customs.gov.cn 仅 HTTP 明文可达（HTTPS 504）；且直连连续请求
    触发 WAF 412（限频）。故走 Crawl4AI 浏览器渲染（对 WAF 容忍度高），
    解析首页"海关总署公告"条目（标题 + 日期 + 原文链接）。
    """

    source_code = "customs-announcement"
    endpoint = "http://www.customs.gov.cn/"
    _MAX_ITEMS = 30
    _HTTP_ALLOW_HOSTS = frozenset({"www.customs.gov.cn"})

    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._transport = transport
        self._timeout_seconds = timeout_seconds

    async def fetch(self, cursor: str | None = None) -> list[RawSourceItem]:
        del cursor
        from app.signals.fallback import read_public_page_with_crawl4ai_for_monitor

        if self._transport is None:
            markdown = await read_public_page_with_crawl4ai_for_monitor(
                self.endpoint, allow_http_hosts=self._HTTP_ALLOW_HOSTS
            )
        else:
            # 测试：用 transport 直连（测试桩返回 markdown 结构）
            import httpx as _httpx

            try:
                async with _httpx.AsyncClient(
                    timeout=self._timeout_seconds, transport=self._transport
                ) as client:
                    raw_response = await client.get(self.endpoint)
            except _httpx.HTTPError as exc:
                raise SourceFetchError("海关总署网络请求失败") from exc
            markdown = raw_response.text
        return _parse_customs_markdown(markdown, limit=self._MAX_ITEMS)

    def normalize(self, item: RawSourceItem) -> ManualSignalInput:
        try:
            return ManualSignalInput(
                external_id=item.external_id,
                title=item.title,
                content=item.content,
                url=HttpUrl(item.url) if item.url else None,
                published_at=_to_utc(item.published_at),
            )
        except ValidationError as exc:
            raise SourceFetchError(f"信号校验失败: {exc}") from exc

    def fingerprint(self, signal: ManualSignalInput) -> str:
        canonical = json.dumps(
            signal.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return _fingerprint_sha256(canonical)

    async def healthcheck(self) -> SourceHealth:
        try:
            items = await self.fetch()
        except SourceFetchError as exc:
            return SourceHealth(ok=False, message=str(exc))
        if not items:
            return SourceHealth(ok=False, message="海关总署公告无有效条目")
        return SourceHealth(ok=True, message=f"返回 {len(items)} 条公告")


class FxRatesAdapter(PullSourceAdapter):
    """国际汇率（E1 汇率 / E3 成本维度）。

    数据源：open.er-api.com（免费、无 key、JSON 直达，166 币种实时汇率）。
    以 USD 为基准取关键币种（CNY/EUR/JPY/GBP），生成汇率信号，
    供供应商成本/结算货币风险匹配。
    """

    source_code = "fx-rates"
    endpoint = "https://open.er-api.com/v6/latest/USD"
    _KEY_CURRENCIES = ("CNY", "EUR", "JPY", "GBP")
    _MAX_ITEMS = 10

    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._transport = transport
        self._timeout_seconds = timeout_seconds

    async def fetch(self, cursor: str | None = None) -> list[RawSourceItem]:
        del cursor
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json",
        }
        if self._transport is not None:
            async with httpx.AsyncClient(
                timeout=self._timeout_seconds, transport=self._transport
            ) as client:
                raw_response = await client.get(self.endpoint, headers=headers)
            body_bytes = raw_response.content
        else:
            try:
                controlled = await controlled_get(
                    self.endpoint,
                    headers=headers,
                    timeout=self._timeout_seconds,
                    maximum_bytes=1024 * 1024,
                )
            except SourceRequestFailed as exc:
                raise SourceFetchError(
                    f"汇率接口请求失败: {exc}",
                    error_kind=exc.error_kind,
                    http_status=exc.status_code,
                ) from exc
            body_bytes = controlled.content
        try:
            payload = json.loads(body_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SourceFetchError("汇率接口返回不是有效 JSON") from exc
        if payload.get("result") != "success":
            raise SourceFetchError("汇率接口返回异常状态")
        rates = payload.get("rates") or {}
        updated = payload.get("time_last_update_utc") or ""
        items: list[RawSourceItem] = []
        for code in self._KEY_CURRENCIES:
            value = rates.get(code)
            if not isinstance(value, (int, float)):
                continue
            items.append(
                RawSourceItem(
                    external_id=f"fx-{code}-{updated}",
                    title=f"汇率：USD/{code} = {value:.4f}",
                    content=(
                        f"1 美元兑 {code}：{value:.4f}；"
                        f"更新时间：{updated}"
                    ),
                    url=self.endpoint,
                    extra={"currency": code, "rate": value, "updated": updated},
                )
            )
        return items

    def normalize(self, item: RawSourceItem) -> ManualSignalInput:
        try:
            return ManualSignalInput(
                external_id=item.external_id,
                title=item.title,
                content=item.content,
                url=HttpUrl(item.url) if item.url else None,
                published_at=_to_utc(item.published_at),
            )
        except ValidationError as exc:
            raise SourceFetchError(f"信号校验失败: {exc}") from exc

    def fingerprint(self, signal: ManualSignalInput) -> str:
        canonical = json.dumps(
            signal.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return _fingerprint_sha256(canonical)

    async def healthcheck(self) -> SourceHealth:
        try:
            items = await self.fetch()
        except SourceFetchError as exc:
            return SourceHealth(ok=False, message=str(exc))
        if not items:
            return SourceHealth(ok=False, message="汇率接口无有效条目")
        return SourceHealth(ok=True, message=f"返回 {len(items)} 条汇率")


class SseShippingAdapter(PullSourceAdapter):
    """上海航运交易所集装箱运价指数（I4 航运 / E3 物流成本维度）。

    数据源：CCFI 单期查询页（www.sse.net.cn），数据经 JS 渲染，
    走 Crawl4AI 渲染后解析 markdown 表格（综合指数 + 分航线上期/本期/涨跌）。
    """

    source_code = "sse-shipping"
    endpoint = "https://www.sse.net.cn/index/singleIndex?indexType=ccfi"
    _MAX_ITEMS = 30

    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._transport = transport
        self._timeout_seconds = timeout_seconds

    async def fetch(self, cursor: str | None = None) -> list[RawSourceItem]:
        del cursor
        from app.signals.fallback import read_public_page_with_crawl4ai_for_monitor

        if self._transport is None:
            markdown = await read_public_page_with_crawl4ai_for_monitor(self.endpoint)
        else:
            import httpx as _httpx

            try:
                async with _httpx.AsyncClient(
                    timeout=self._timeout_seconds, transport=self._transport
                ) as client:
                    raw_response = await client.get(self.endpoint)
            except _httpx.HTTPError as exc:
                raise SourceFetchError("上海航交所网络请求失败") from exc
            markdown = raw_response.text
        return _parse_sse_shipping(markdown, limit=self._MAX_ITEMS)

    def normalize(self, item: RawSourceItem) -> ManualSignalInput:
        try:
            return ManualSignalInput(
                external_id=item.external_id,
                title=item.title,
                content=item.content,
                url=HttpUrl(item.url) if item.url else None,
                published_at=_to_utc(item.published_at),
            )
        except ValidationError as exc:
            raise SourceFetchError(f"信号校验失败: {exc}") from exc

    def fingerprint(self, signal: ManualSignalInput) -> str:
        canonical = json.dumps(
            signal.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return _fingerprint_sha256(canonical)

    async def healthcheck(self) -> SourceHealth:
        try:
            items = await self.fetch()
        except SourceFetchError as exc:
            return SourceHealth(ok=False, message=str(exc))
        if not items:
            return SourceHealth(ok=False, message="上海航交所无有效指数")
        return SourceHealth(ok=True, message=f"返回 {len(items)} 条指数信号")


def _parse_sse_shipping(markdown: str, *, limit: int = 30) -> list[RawSourceItem]:
    """解析 CCFI markdown 表格（综合指数 + 分航线）。

    实测结构（2026-08-20）：
        中国出口集装箱运价综合指数 | 1839.61 | 1846.96 | 0.4
        日本航线 (JAPAN SERVICE) | 941.44 | 896.71 | -4.8
    """
    lines = [ln.strip() for ln in markdown.splitlines() if "|" in ln]
    items: list[RawSourceItem] = []
    for line in lines:
        cells = [c.strip() for c in line.split("|")]
        # markdown 表格行: ['', '航线', '上期', '本期', '涨跌(%)', '']
        cells = [c for c in cells if c]
        if len(cells) < 4:
            continue
        name = cells[0]
        if name in ("航线", "上期", "---") or not re.search(r"[A-Z\u4e00-\u9fa5]", name):
            continue
        prev, curr = cells[1], cells[2]
        change = cells[3] if len(cells) > 3 else ""
        if not re.match(r"^\d+(\.\d+)?$", prev) or not re.match(r"^\d+(\.\d+)?$", curr):
            continue
        external_id = "sse-" + hashlib.sha256(
            f"{name}|{curr}".encode()
        ).hexdigest()[:16]
        items.append(
            RawSourceItem(
                external_id=external_id,
                title=f"CCFI 运价指数：{name}",
                content=(
                    f"航线：{name}；上期 {prev} → 本期 {curr}"
                    f"（涨跌 {change}%）；来源：上海航运交易所 CCFI"
                ),
                url="https://www.sse.net.cn/index/singleIndex?indexType=ccfi",
                extra={"route": name, "previous": prev, "current": curr, "change_pct": change},
            )
        )
        if len(items) >= limit:
            break
    return items


class FmprcPressAdapter(PullSourceAdapter):
    """外交部例行记者会（G4 双边关系与外交事件）。

    数据源：外交部发言人栏目（直连 200），列表页含最近记者会标题 + 日期 + 详情 URL。
    信号标题含"发言人主持例行记者会"；AI 相关性过滤负责精筛制裁/贸易/出口管制话题。
    """

    source_code = "fmprc-press"
    endpoint = "https://www.mfa.gov.cn/web/wjdt_674879/fyrbt_674889/"
    _MAX_ITEMS = 10

    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._transport = transport
        self._timeout_seconds = timeout_seconds

    async def fetch(self, cursor: str | None = None) -> list[RawSourceItem]:
        del cursor
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Accept": "text/html,*/*;q=0.8",
        }
        if self._transport is not None:
            try:
                async with httpx.AsyncClient(
                    timeout=self._timeout_seconds, transport=self._transport
                ) as client:
                    raw_response = await client.get(self.endpoint, headers=headers)
            except httpx.HTTPError as exc:
                raise SourceFetchError("外交部记者会网络请求失败") from exc
            body_bytes = raw_response.content
        else:
            try:
                controlled = await controlled_get(
                    self.endpoint,
                    headers=headers,
                    timeout=self._timeout_seconds,
                    maximum_bytes=5 * 1024 * 1024,
                )
            except SourceRequestFailed as exc:
                raise SourceFetchError(
                    f"外交部记者会请求失败: {exc}",
                    error_kind=exc.error_kind,
                    http_status=exc.status_code,
                ) from exc
            body_bytes = controlled.content
        html = body_bytes.decode("utf-8", "ignore")
        items: list[RawSourceItem] = []
        # 列表项: ./202608/t20260820_12007399.shtml + 记者会标题（含日期）
        _list_pat = r'<a[^>]*?href="(\./202\d{3}/t\d+_\d+\.shtml)"[^>]*>([^<]{10,80})</a>'
        for href, title in re.findall(_list_pat, html):
            title = title.strip()
            if "例行记者会" not in title or "发言" not in title:
                continue
            url = self.endpoint.rstrip("/") + "/" + href.lstrip("./")
            # 标题含日期（2026-08-20）
            m_date = re.search(r"(20\d{2}-\d{2}-\d{2})", title)
            published_at = None
            if m_date:
                try:
                    published_at = datetime.strptime(
                        m_date.group(1), "%Y-%m-%d"
                    ).replace(tzinfo=UTC)
                except ValueError:
                    published_at = None
            items.append(
                RawSourceItem(
                    external_id="fmprc-" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:16],
                    title=title,
                    content=(
                        f"日期：{published_at.date().isoformat() if published_at else '未知'}；"
                        f"来源：外交部例行记者会"
                    ),
                    url=url,
                    published_at=published_at,
                )
            )
            if len(items) >= self._MAX_ITEMS:
                break
        return items

    def normalize(self, item: RawSourceItem) -> ManualSignalInput:
        try:
            return ManualSignalInput(
                external_id=item.external_id,
                title=item.title,
                content=item.content,
                url=HttpUrl(item.url) if item.url else None,
                published_at=_to_utc(item.published_at),
            )
        except ValidationError as exc:
            raise SourceFetchError(f"信号校验失败: {exc}") from exc

    def fingerprint(self, signal: ManualSignalInput) -> str:
        canonical = json.dumps(
            signal.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return _fingerprint_sha256(canonical)

    async def healthcheck(self) -> SourceHealth:
        try:
            items = await self.fetch()
        except SourceFetchError as exc:
            return SourceHealth(ok=False, message=str(exc))
        if not items:
            return SourceHealth(ok=False, message="外交部记者会无有效条目")
        return SourceHealth(ok=True, message=f"返回 {len(items)} 条记者会")


class MofcomEntityDetailAdapter(PullSourceAdapter):
    """商务部实体名单详情解析（G2 制裁：出口管制管控名单 / 不可靠实体清单）。

    列表页（mofcom-entity-control，声明式）只产出公告标题+URL；本适配器对
    每条"实体/名单"公告详情页做 Crawl4AI 渲染，提取被列入的具体实体
    （如"拉法特集团等14家欧盟实体"），每个实体生成一条信号，
    供供应商主体匹配（F2 司法 / G2 制裁命中）。
    """

    source_code = "mofcom-entity-detail"
    _LIST_URL = "http://aqygzj.mofcom.gov.cn/"
    _HTTP_ALLOW_HOSTS = frozenset({"aqygzj.mofcom.gov.cn"})
    _MAX_ENTITIES = 200  # 单次采集实体信号上限（防爆炸）

    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._transport = transport
        self._timeout_seconds = timeout_seconds

    async def fetch(self, cursor: str | None = None) -> list[RawSourceItem]:
        del cursor
        from app.signals.fallback import read_public_page_with_crawl4ai_for_monitor

        # 1. 抓列表页拿公告标题+URL（复用声明式解析）
        if self._transport is not None:
            # 测试：transport 直连列表页 + 详情页（桩响应）
            return await self._fetch_test_mode()
        list_items = await _fetch_mofcom_list(self._transport)
        # 2. 只挑实体/名单类公告（标题含"列入""管控名单""不可靠实体清单"）
        detail_urls = [
            (it.title, it.url)
            for it in list_items
            if it.url
            and any(k in it.title for k in ("列入", "管控名单", "不可靠实体", "反制措施"))
        ]
        items: list[RawSourceItem] = []
        for title, url in detail_urls[:8]:  # 每轮最多 8 条详情页
            try:
                md = await read_public_page_with_crawl4ai_for_monitor(
                    url, allow_http_hosts=self._HTTP_ALLOW_HOSTS
                )
            except SourceFetchError:
                continue
            entities = _extract_entities_from_detail(md)
            for entity_name in entities:
                if len(items) >= self._MAX_ENTITIES:
                    break
                items.append(
                    RawSourceItem(
                        external_id="mofcom-entity-"
                        + hashlib.sha256(
                            f"{url}|{entity_name}".encode()
                        ).hexdigest()[:16],
                        title=f"出口管制名单新增：{entity_name}",
                        content=(
                            f"来源公告：{title}；"
                            f"原文：{url}"
                        ),
                        url=url,
                    )
                )
        return items

    async def _fetch_test_mode(self) -> list[RawSourceItem]:
        # 测试桩：直接解析详情页 markdown（transport 场景只测详情解析）
        import httpx as _httpx

        async with _httpx.AsyncClient(
            timeout=self._timeout_seconds, transport=self._transport
        ) as client:
            raw_response = await client.get(self._LIST_URL)
        md = raw_response.text
        entities = _extract_entities_from_detail(md)
        return [
            RawSourceItem(
                external_id="mofcom-entity-"
                + hashlib.sha256(f"test|{name}".encode()).hexdigest()[:16],
                title=f"出口管制名单新增：{name}",
                content=f"来源公告：测试公告；原文：{self._LIST_URL}",
                url=self._LIST_URL,
            )
            for name in entities
        ]

    def normalize(self, item: RawSourceItem) -> ManualSignalInput:
        try:
            return ManualSignalInput(
                external_id=item.external_id,
                title=item.title,
                content=item.content,
                url=HttpUrl(item.url) if item.url else None,
                published_at=_to_utc(item.published_at),
            )
        except ValidationError as exc:
            raise SourceFetchError(f"信号校验失败: {exc}") from exc

    def fingerprint(self, signal: ManualSignalInput) -> str:
        canonical = json.dumps(
            signal.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return _fingerprint_sha256(canonical)

    async def healthcheck(self) -> SourceHealth:
        try:
            items = await self.fetch()
        except SourceFetchError as exc:
            return SourceHealth(ok=False, message=str(exc))
        if not items:
            return SourceHealth(ok=False, message="商务部实体名单无有效条目")
        return SourceHealth(ok=True, message=f"返回 {len(items)} 条实体信号")


async def _fetch_mofcom_list(
    transport: httpx.AsyncBaseTransport | None = None,
) -> list[RawSourceItem]:
    """复用 mofcom-entity-control 的声明式配置抓列表页。"""
    from sqlalchemy import select

    from app.database import SessionLocal
    from app.signals.declarative import AdapterSpec, DeclarativeSourceAdapter
    from app.signals.models import DataSource

    with SessionLocal() as session:
        source = session.scalar(
            select(DataSource).where(DataSource.code == "mofcom-entity-control")
        )
        if source is None or not source.adapter_config:
            return []
        spec = AdapterSpec.model_validate(source.adapter_config)
        adapter = DeclarativeSourceAdapter(
            "mofcom-entity-control",
            spec,
            auth_type=source.auth_type,
            credential_ref=source.credential_ref,
            login_config=source.login_config,
            transport=transport,
        )
        return await adapter.fetch()


def _extract_entities_from_detail(markdown: str) -> list[str]:
    """从公告详情页 markdown 提取被列入实体名称。

    附件名单实测结构（2026-08-20）：
        1.斯凯迪奥公司（Skydio Inc.）
        2.BRINC无人机公司（BRINC Drones,Inc.）
        ...
    也兼容纯文本行 "N.名称（English Name）"。
    """
    entities: list[str] = []
    for line in markdown.splitlines():
        line = line.strip()
        # 编号.实体名（英文名） 或 编号.实体名
        _ent_pat = (
            r"^\d{1,3}[\.、]\s*([\u4e00-\u9fa5A-Za-z0-9]"
            r"[^（）()\n]{1,80}?)(?:（[^）]*）)?$"
        )
        m = re.match(_ent_pat, line)
        if m:
            name = m.group(1).strip()
            if len(name) >= 3 and name not in entities:
                entities.append(name)
    return entities


def _parse_customs_markdown(markdown: str, *, limit: int = 30) -> list[RawSourceItem]:
    """解析 Crawl4AI 渲染后的海关总署首页公告条目。

    实测结构（2026-08-20）：
        [海关总署公告2026年第121号（关于修改海关总署公告2019年第170号...）](http://www.customs.gov.cn/customs/2026-08/19/article_xxx.html)
        2026-08-12
    """
    items: list[RawSourceItem] = []
    # markdown 链接 [标题](url) 或 [标题](url "title")，允许列表项 * 前缀
    _link_pat = r"\[\s*([^\]]*(?:海关总署公告)[^\]]*)\]\s*\((https?://[^\s)]+)(?:\s+\"[^\"]*\")?\)"
    links = re.findall(_link_pat, markdown)
    seen: set[str] = set()
    for title, url in links:
        title = title.strip()
        if not title.startswith("海关总署公告") or title in seen:
            continue
        seen.add(title)
        # 找标题后第一个 YYYY-MM-DD（公告行后紧跟日期；URL 中也可能含年份数字，
        # 因此用"日期模式 + 前缀校验"而非排除数字）
        published_at = None
        m_date = re.search(
            rf"{re.escape(title)}[\s\S]{{0,300}}?(\d{{4}}-\d{{2}}-\d{{2}})",
            markdown,
        )
        if m_date:
            try:
                published_at = datetime.strptime(
                    m_date.group(1), "%Y-%m-%d"
                ).replace(tzinfo=UTC)
            except ValueError:
                published_at = None
        items.append(
            RawSourceItem(
                external_id="customs-" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:16],
                title=title,
                content=f"公告日期：{published_at.date().isoformat() if published_at else '未知'}",
                url=url,
                published_at=published_at,
            )
        )
        if len(items) >= limit:
            break
    return items


class WtoNewsAdapter(PullSourceAdapter):
    """WTO 新闻（E5 贸易摩擦：关税/反倾销/保障措施/争端）。

    官网 news_e.htm 文章列表为 JS 渲染（直连只有导航壳），
    走 Crawl4AI 渲染获取 markdown；解析 ``### 标题 + 日期 + 摘要 + [News item](url)``
    结构生成信号。
    """

    source_code = "wto-news"
    endpoint = "https://www.wto.org/english/news_e/news_e.htm"
    _MAX_ITEMS = 20

    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._transport = transport
        self._timeout_seconds = timeout_seconds

    async def fetch(self, cursor: str | None = None) -> list[RawSourceItem]:
        del cursor
        from app.signals.fallback import read_public_page_with_crawl4ai_for_monitor

        if self._transport is None:
            markdown = await read_public_page_with_crawl4ai_for_monitor(self.endpoint)
        else:
            # 测试：用 transport 直连（测试桩返回 markdown 结构，不走真实 Crawl4AI）
            import httpx as _httpx

            try:
                async with _httpx.AsyncClient(
                    timeout=self._timeout_seconds, transport=self._transport
                ) as client:
                    raw_response = await client.get(self.endpoint)
            except _httpx.HTTPError as exc:
                raise SourceFetchError("WTO 网络请求失败") from exc
            markdown = raw_response.text
        return _parse_wto_markdown(markdown, limit=self._MAX_ITEMS)

    def normalize(self, item: RawSourceItem) -> ManualSignalInput:
        try:
            return ManualSignalInput(
                external_id=item.external_id,
                title=item.title,
                content=item.content,
                url=HttpUrl(item.url) if item.url else None,
                published_at=_to_utc(item.published_at),
            )
        except ValidationError as exc:
            raise SourceFetchError(f"信号校验失败: {exc}") from exc

    def fingerprint(self, signal: ManualSignalInput) -> str:
        canonical = json.dumps(
            signal.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return _fingerprint_sha256(canonical)

    async def healthcheck(self) -> SourceHealth:
        try:
            items = await self.fetch()
        except SourceFetchError as exc:
            return SourceHealth(ok=False, message=str(exc))
        if not items:
            return SourceHealth(ok=False, message="WTO 新闻无有效条目")
        return SourceHealth(ok=True, message=f"返回 {len(items)} 条新闻")


def _parse_wto_markdown(markdown: str, *, limit: int = 20) -> list[RawSourceItem]:
    """解析 Crawl4AI 渲染后的 WTO news markdown。

    条目结构（实测 2026-08-20）：
        ###  {标题}
        {日期，如 5 August 2026}
        {摘要文本}
          * [News item](https://www.wto.org/english/news_e/news26_e/xxx_e.htm)
    """
    import datetime as _dt

    items: list[RawSourceItem] = []
    # 按 H3 标题切块
    blocks = re.split(r"\n###\s+", markdown)
    for block in blocks[1:]:  # 第一块是 H3 之前的导航
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        title = lines[0]
        _skip_titles = {
            "archives", "share", "latest video", "latest photo", "media newsroom",
        }
        if len(title) < 10 or title.lower() in _skip_titles:
            continue
        # 日期行：尝试解析常见英文日期格式
        published_at: datetime | None = None
        for line in lines[1:4]:
            try:
                parsed = _dt.datetime.strptime(line, "%d %B %Y")
                published_at = parsed.replace(tzinfo=UTC)
                break
            except ValueError:
                continue
        # 提取 News item 链接
        url = None
        m = re.search(r"\[[^\]]*\]\((https?://www\.wto\.org[^)]+\.htm)\)", block)
        if m:
            url = m.group(1)
        # 摘要：标题与 [News item] 之间的非空文本（去图片/日期）
        date_str = published_at.strftime("%d %B %Y") if published_at else None
        summary_lines = []
        for line in lines:
            if line == title or (date_str and line == date_str):
                continue
            if line.startswith("* [") or line.startswith("!["):
                continue
            summary_lines.append(line)
        summary = " ".join(summary_lines)[:400]
        date_text = published_at.date().isoformat() if published_at else "未知"
        content = f"日期：{date_text}；{summary}" if summary else f"日期：{date_text}"
        external_id = "wto-" + hashlib.sha256(title.encode("utf-8")).hexdigest()[:16]
        items.append(
            RawSourceItem(
                external_id=external_id,
                title=title,
                content=content,
                url=url or "https://www.wto.org/english/news_e/news_e.htm",
                published_at=published_at,
            )
        )
        if len(items) >= limit:
            break
    return items


def _parse_nmc_time(value: object) -> datetime | None:
    """解析中央气象台发布时间，如 '2026/08/07 22:30'（东八区，无时区）。"""
    if not isinstance(value, str):
        return None
    text = value.strip()
    for fmt in ("%Y/%m/%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            naive = datetime.strptime(text, fmt)
        except ValueError:
            continue
        return naive.replace(tzinfo=timezone(timedelta(hours=8)))
    return None


def _parse_uflpa_date(value: object) -> datetime | None:
    """解析 UFLPA 实体清单生效日期，如 'June 21, 2022'（UTC）。"""
    if not isinstance(value, str):
        return None
    text = value.strip()
    try:
        naive = datetime.strptime(text, "%B %d, %Y")
    except ValueError:
        return None
    return naive.replace(tzinfo=UTC)


def _binding(row: dict[str, object], key: str) -> str | None:
    """从 SPARQL JSON 结果行提取绑定值（row = {"celex": {"type": "...", "value": "..."}}）。"""
    cell = row.get(key)
    if isinstance(cell, dict):
        value = cell.get("value")
        if isinstance(value, str) and value:
            return value
    return None
