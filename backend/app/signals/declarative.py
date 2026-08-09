"""受控的声明式拉取适配器。"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import ipaddress
import json
import os
import re
import socket
from datetime import UTC, datetime
from typing import Literal, cast
from urllib.parse import urljoin, urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.signals.schemas import ManualSignalInput
from app.signals.sources import PullSourceAdapter, RawSourceItem, SourceFetchError, SourceHealth

MAX_PREVIEW_ITEMS = 10
MAX_FETCH_ITEMS = 1000
MAX_RESPONSE_BYTES = 10 * 1024 * 1024
_SENSITIVE_HEADER = re.compile(r"authorization|cookie|token|secret|api[-_]?key", re.I)
_ENV_REF = re.compile(r"env:([A-Z][A-Z0-9_]{0,127})\Z")
FingerprintField = Literal["external_id", "title", "content", "url", "published_at"]


def _default_fingerprint_fields() -> list[FingerprintField]:
    return ["external_id", "title", "published_at"]


class DeclarativeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method: Literal["GET"] = "GET"
    url: str = Field(min_length=1, max_length=2048)
    params: dict[str, str] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: float = Field(default=15, ge=1, le=30)
    max_response_bytes: int = Field(default=MAX_RESPONSE_BYTES, ge=1024, le=MAX_RESPONSE_BYTES)

    @field_validator("url")
    @classmethod
    def require_https(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("数据源 URL 必须是无内嵌凭据的 HTTPS 地址")
        return value

    @field_validator("headers")
    @classmethod
    def reject_secret_headers(cls, value: dict[str, str]) -> dict[str, str]:
        for name, content in value.items():
            if _SENSITIVE_HEADER.search(name):
                raise ValueError(f"静态请求头不得包含凭据字段：{name}")
            if "\r" in name + content or "\n" in name + content:
                raise ValueError("请求头不得包含换行符")
        return value


class SignalMapping(BaseModel):
    model_config = ConfigDict(extra="forbid")

    external_id: str | None = None
    title: str = Field(min_length=1)
    content: str = Field(min_length=1)
    url: str | None = None
    published_at: str | None = None


class AdapterSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: Literal["json", "csv"]
    request: DeclarativeRequest
    items_path: str | None = None
    mapping: SignalMapping
    fingerprint_fields: list[FingerprintField] = Field(
        default_factory=_default_fingerprint_fields,
        min_length=1,
    )
    max_items: int = Field(default=MAX_FETCH_ITEMS, ge=1, le=MAX_FETCH_ITEMS)

    @model_validator(mode="after")
    def validate_format_options(self) -> AdapterSpec:
        if self.format == "json" and not self.items_path:
            raise ValueError("JSON 数据源必须配置 items_path")
        if len(set(self.fingerprint_fields)) != len(self.fingerprint_fields):
            raise ValueError("fingerprint_fields 不得重复")
        return self


class AdapterPreview(BaseModel):
    fetched_count: int
    items: list[ManualSignalInput]


class DeclarativeSourceAdapter(PullSourceAdapter):
    def __init__(
        self,
        source_code: str,
        spec: AdapterSpec,
        *,
        auth_type: str = "none",
        credential_ref: str | None = None,
        login_config: dict[str, object] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        item_limit: int | None = None,
    ) -> None:
        self.source_code = source_code
        self.spec = spec
        self.auth_type = auth_type
        self.credential_ref = credential_ref
        self.login_config = login_config or {}
        self._transport = transport
        self._item_limit = min(item_limit or spec.max_items, spec.max_items)

    async def fetch(self, cursor: str | None = None) -> list[RawSourceItem]:
        del cursor
        await validate_public_https_url(
            self.spec.request.url,
            resolve_dns=self._transport is None,
        )
        headers = dict(self.spec.request.headers)
        headers.update(_credential_headers(self.auth_type, self.credential_ref, self.login_config))
        try:
            async with httpx.AsyncClient(
                timeout=self.spec.request.timeout_seconds,
                transport=self._transport,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                async with client.stream(
                    "GET",
                    self.spec.request.url,
                    params=self.spec.request.params,
                    headers=headers,
                ) as response:
                    if 300 <= response.status_code < 400:
                        raise SourceFetchError("数据源返回重定向；请配置最终官方 HTTPS 地址")
                    response.raise_for_status()
                    body = await _read_limited(response, self.spec.request.max_response_bytes)
        except SourceFetchError:
            raise
        except httpx.HTTPError as exc:
            raise SourceFetchError(f"声明式数据源请求失败: {exc}") from exc
        rows = _parse_rows(self.spec, body)
        return [self._map_row(row) for row in rows[: self._item_limit]]

    def _map_row(self, row: dict[str, object]) -> RawSourceItem:
        mapping = self.spec.mapping
        title = _as_text(_read_path(row, mapping.title))
        content = _as_text(_read_path(row, mapping.content))
        external_id = _optional_text(
            _read_path(row, mapping.external_id) if mapping.external_id else None
        )
        raw_url = _optional_text(_read_path(row, mapping.url) if mapping.url else None)
        published = _optional_text(
            _read_path(row, mapping.published_at) if mapping.published_at else None
        )
        if not title or not content:
            raise SourceFetchError("字段映射后 title 或 content 为空")
        return RawSourceItem(
            external_id=external_id or "",
            title=title,
            content=content,
            url=urljoin(self.spec.request.url, raw_url) if raw_url else None,
            published_at=_parse_datetime(published),
            extra={"raw": row},
        )

    def normalize(self, item: RawSourceItem) -> ManualSignalInput:
        try:
            return ManualSignalInput.model_validate(
                {
                    "external_id": item.external_id or None,
                    "title": item.title,
                    "content": item.content,
                    "url": item.url,
                    "published_at": item.published_at,
                }
            )
        except ValueError as exc:
            raise SourceFetchError(f"声明式数据源字段校验失败: {exc}") from exc

    def fingerprint(self, signal: ManualSignalInput) -> str:
        values = signal.model_dump(mode="json")
        canonical = json.dumps(
            {field: values.get(field) for field in self.spec.fingerprint_fields},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    async def healthcheck(self) -> SourceHealth:
        try:
            items = await self.fetch()
        except SourceFetchError as exc:
            return SourceHealth(False, str(exc))
        message = f"返回 {len(items)} 条有效记录" if items else "未返回有效记录"
        return SourceHealth(bool(items), message)


async def preview_adapter(
    source_code: str,
    spec: AdapterSpec,
    *,
    auth_type: str = "none",
    credential_ref: str | None = None,
    login_config: dict[str, object] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> AdapterPreview:
    adapter = DeclarativeSourceAdapter(
        source_code,
        spec,
        auth_type=auth_type,
        credential_ref=credential_ref,
        login_config=login_config,
        transport=transport,
        item_limit=MAX_PREVIEW_ITEMS,
    )
    raw_items = await adapter.fetch()
    items = [adapter.normalize(item) for item in raw_items]
    return AdapterPreview(fetched_count=len(items), items=items)


async def validate_public_https_url(url: str, *, resolve_dns: bool = True) -> None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").rstrip(".").lower()
    if parsed.scheme != "https" or not host:
        raise SourceFetchError("仅允许访问 HTTPS 数据源")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise SourceFetchError("禁止访问本机或内部网络地址")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None and not literal.is_global:
        raise SourceFetchError("禁止访问非公网 IP 地址")
    if not resolve_dns or literal is not None:
        return
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, host, parsed.port or 443)
    except OSError as exc:
        raise SourceFetchError(f"数据源域名解析失败: {host}") from exc
    addresses = {cast(str, item[4][0]).split("%")[0] for item in infos}
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise SourceFetchError("数据源域名解析到非公网地址")


async def _read_limited(response: httpx.Response, maximum: int) -> bytes:
    length = response.headers.get("content-length")
    if length and length.isdigit() and int(length) > maximum:
        raise SourceFetchError(f"数据源响应超过 {maximum} 字节限制")
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > maximum:
            raise SourceFetchError(f"数据源响应超过 {maximum} 字节限制")
        chunks.append(chunk)
    return b"".join(chunks)


def _parse_rows(spec: AdapterSpec, body: bytes) -> list[dict[str, object]]:
    try:
        text = body.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise SourceFetchError("数据源响应不是 UTF-8 编码") from exc
    if spec.format == "csv":
        try:
            return [dict(row) for row in csv.DictReader(io.StringIO(text))]
        except csv.Error as exc:
            raise SourceFetchError("数据源响应不是有效 CSV") from exc
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SourceFetchError("数据源响应不是有效 JSON") from exc
    items = _read_path(payload, spec.items_path)
    if not isinstance(items, list):
        raise SourceFetchError("items_path 未指向 JSON 数组")
    return [item for item in items if isinstance(item, dict)]


def _read_path(value: object, path: str | None) -> object:
    if path is None or path in {"", "$"}:
        return value
    current = value
    for part in path.removeprefix("$.").split("."):
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            return None
    return current


def _credential_headers(
    auth_type: str,
    credential_ref: str | None,
    login_config: dict[str, object],
) -> dict[str, str]:
    if auth_type == "none":
        return {}
    if auth_type not in {"api_key", "bearer"}:
        raise SourceFetchError("声明式适配器当前仅支持无认证、API Key Header 和 Bearer Token")
    match = _ENV_REF.fullmatch(credential_ref or "")
    if not match:
        raise SourceFetchError("认证数据源必须使用 env:VARIABLE_NAME 形式的凭据引用")
    secret = os.getenv(match.group(1))
    if not secret:
        raise SourceFetchError("凭据引用未配置")
    if auth_type == "bearer":
        return {"Authorization": f"Bearer {secret}"}
    header_name = str(login_config.get("header_name") or "X-API-Key").strip()
    if not re.fullmatch(r"[A-Za-z0-9-]{1,64}", header_name):
        raise SourceFetchError("API Key 请求头名称无效")
    return {header_name: secret}


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _as_text(value: object) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return _optional_text(value) or ""


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SourceFetchError("published_at 必须是 ISO 8601 时间") from exc
    if parsed.tzinfo is None:
        raise SourceFetchError("published_at 必须包含时区")
    return parsed.astimezone(UTC)
