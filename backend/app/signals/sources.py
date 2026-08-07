"""拉取式数据源适配器。

技术方案 7.2：所有外部来源实现统一 SourceAdapter：
    fetch(cursor) -> RawSourceItem[]
    normalize(item) -> NormalizedSignal
    fingerprint(signal) -> string
    healthcheck() -> SourceHealth

manual-json 是文件上传式（见 adapter.py），本模块实现 HTTP 拉取式数据源。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from typing import Protocol

import httpx
from pydantic import HttpUrl, ValidationError

from app.signals.schemas import ManualSignalInput

DEFAULT_TIMEOUT_SECONDS = 15.0
MAX_ITEMS_PER_FETCH = 100


class SourceFetchError(RuntimeError):
    """拉取数据源在请求或解析阶段失败。"""


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
    _endpoint = "http://www.nmc.cn/rest/findAlarm"

    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._transport = transport
        self._timeout_seconds = timeout_seconds

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self._timeout_seconds, transport=self._transport)

    async def fetch(self, cursor: str | None = None) -> list[RawSourceItem]:
        params = {
            "pageNo": cursor or "1",
            "pageSize": str(MAX_ITEMS_PER_FETCH),
            "signaltype": "",
            "signallevel": "",
            "province": "",
        }
        async with self._client() as client:
            try:
                response = await client.get(self._endpoint, params=params)
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise SourceFetchError(f"中央气象台接口请求失败: {exc}") from exc
            try:
                payload = response.json()
            except json.JSONDecodeError as exc:
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
