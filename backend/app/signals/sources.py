"""拉取式数据源适配器。

技术方案 7.2：所有外部来源实现统一 SourceAdapter：
    fetch(cursor) -> RawSourceItem[]
    normalize(item) -> NormalizedSignal
    fingerprint(signal) -> string
    healthcheck() -> SourceHealth

manual-json 是文件上传式（见 adapter.py），本模块实现 HTTP 拉取式数据源。
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
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
        """生成 SPARQL：最近 lookback 天内发布的法规类条目（含标题/日期/CELEX）。"""
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
}} ORDER BY DESC(?date) LIMIT {self._MAX_ITEMS}
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


def _binding(row: dict[str, object], key: str) -> str | None:
    """从 SPARQL JSON 结果行提取绑定值（row = {"celex": {"type": "...", "value": "..."}}）。"""
    cell = row.get(key)
    if isinstance(cell, dict):
        value = cell.get("value")
        if isinstance(value, str) and value:
            return value
    return None
