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
from dataclasses import dataclass, field
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Literal, cast
from urllib.parse import urljoin, urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.signals.request_control import SourceRequestFailed, controlled_get
from app.signals.schemas import ManualSignalInput
from app.signals.sources import PullSourceAdapter, RawSourceItem, SourceFetchError, SourceHealth

MAX_PREVIEW_ITEMS = 10
MAX_FETCH_ITEMS = 1000
MAX_RESPONSE_BYTES = 10 * 1024 * 1024
MAX_INSPECTION_BYTES = 512 * 1024
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

    format: Literal["json", "csv", "html"]
    request: DeclarativeRequest
    items_path: str | None = None
    items_selector: str | None = None
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
        if self.format == "html" and not self.items_selector:
            raise ValueError("HTML 数据源必须配置 items_selector")
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
            response = await controlled_get(
                self.spec.request.url,
                params=self.spec.request.params,
                headers=headers,
                timeout=self.spec.request.timeout_seconds,
                maximum_bytes=self.spec.request.max_response_bytes,
                transport=self._transport,
                follow_redirects=False,
            )
            body = response.content
        except SourceFetchError:
            raise
        except SourceRequestFailed as exc:
            raise SourceFetchError(
                f"声明式数据源请求失败: {exc}",
                error_kind=exc.error_kind,
                http_status=exc.status_code,
            ) from exc
        rows = _parse_rows(self.spec, body)
        return [self._map_row(row) for row in rows[: self._item_limit]]

    def _map_row(self, row: dict[str, object]) -> RawSourceItem:
        mapping = self.spec.mapping
        html_node = row.get("__html_node__")
        if self.spec.format == "html":
            if not isinstance(html_node, _HtmlNode):
                raise SourceFetchError("HTML 列表项解析失败")

            def read(selector: str) -> object:
                return _read_html_value(html_node, selector)

        else:
            def read(selector: str) -> object:
                return _read_path(row, selector)
        title = _as_text(read(mapping.title))
        content = _as_text(read(mapping.content))
        external_id = _optional_text(read(mapping.external_id) if mapping.external_id else None)
        raw_url = _optional_text(read(mapping.url) if mapping.url else None)
        published = _optional_text(read(mapping.published_at) if mapping.published_at else None)
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


@dataclass
class _HtmlNode:
    tag: str
    attrs: dict[str, str]
    children: list[_HtmlNode | str] = field(default_factory=list)

    def text_content(self) -> str:
        if self.tag in {"script", "style", "noscript"}:
            return ""
        return " ".join(
            part
            for child in self.children
            for part in (
                [child.text_content()] if isinstance(child, _HtmlNode) else [child]
            )
            if part.strip()
        )


class _HtmlTreeParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _HtmlNode("__root__", {})
        self._stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = _HtmlNode(tag.lower(), {key.lower(): value or "" for key, value in attrs})
        self._stack[-1].children.append(node)
        self._stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self._stack.pop()

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        for index in range(len(self._stack) - 1, 0, -1):
            if self._stack[index].tag == tag:
                del self._stack[index:]
                break

    def handle_data(self, data: str) -> None:
        if data.strip() and not any(
            node.tag in {"script", "style", "noscript"} for node in self._stack
        ):
            self._stack[-1].children.append(data)


_SELECTOR_TOKEN = re.compile(
    r"^(?P<tag>\*|[A-Za-z][\w:-]*)?(?P<id>#[\w:-]+)?"
    r"(?P<classes>(?:\.[\w:-]+)*)(?P<attrs>(?:\[[^\]]+\])*)$"
)
_ATTRIBUTE_TOKEN = re.compile(r"\[\s*([\w:-]+)(?:\s*=\s*['\"]?([^'\"]*)['\"]?)?\s*\]")


def _descendants(node: _HtmlNode) -> list[_HtmlNode]:
    result = [node]
    for child in node.children:
        if isinstance(child, _HtmlNode):
            result.extend(_descendants(child))
    return result


def _matches_selector(node: _HtmlNode, token: str) -> bool:
    match = _SELECTOR_TOKEN.fullmatch(token)
    if match is None:
        raise SourceFetchError(f"不支持的 HTML 选择器：{token}")
    tag = match.group("tag")
    if tag and tag != "*" and node.tag != tag.lower():
        return False
    selector_id = match.group("id")
    if selector_id and node.attrs.get("id") != selector_id[1:]:
        return False
    classes = [part[1:] for part in re.findall(r"\.[\w:-]+", match.group("classes"))]
    node_classes = set(node.attrs.get("class", "").split())
    if any(class_name not in node_classes for class_name in classes):
        return False
    for attr_match in _ATTRIBUTE_TOKEN.finditer(match.group("attrs")):
        name, expected = attr_match.groups()
        if name not in node.attrs:
            return False
        if expected is not None and node.attrs[name] != expected:
            return False
    return True


def _select_nodes(root: _HtmlNode, selector: str) -> list[_HtmlNode]:
    if not selector.strip():
        raise SourceFetchError("HTML 选择器不能为空")
    current = [root]
    for token in selector.split():
        candidates = [item for node in current for item in _descendants(node)]
        current = [item for item in candidates if _matches_selector(item, token)]
        if not current:
            return []
    return current


def _read_html_value(node: _HtmlNode, selector: str) -> str | None:
    selector_text = selector
    attribute: str | None = None
    if "@" in selector:
        selector_text, attribute = selector.rsplit("@", 1)
    selected = _select_nodes(node, selector_text)
    if not selected:
        return None
    if attribute:
        return selected[0].attrs.get(attribute.lower())
    return " ".join(selected[0].text_content().split())


async def inspect_source_url(
    url: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, object]:
    await validate_public_https_url(url, resolve_dns=transport is None)
    try:
        response = await controlled_get(
            url,
            timeout=15,
            maximum_bytes=MAX_INSPECTION_BYTES,
            transport=transport,
            follow_redirects=False,
        )
        body = response.content
        content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
    except SourceFetchError:
        raise
    except SourceRequestFailed as exc:
        raise SourceFetchError(
            f"数据源探测请求失败: {exc}",
            error_kind=exc.error_kind,
            http_status=exc.status_code,
        ) from exc
    text = body.decode("utf-8-sig", errors="replace")
    stripped = text.lstrip()
    if "html" in content_type or stripped.startswith("<"):
        parser = _HtmlTreeParser()
        parser.feed(text)
        title_nodes = _select_nodes(parser.root, "title")
        candidates = _html_candidate_selectors(parser.root)
        return {
            "status": "success",
            "detected_format": "html",
            "content_type": content_type or "text/html",
            "title": " ".join(title_nodes[0].text_content().split()) if title_nodes else None,
            "text_excerpt": " ".join(parser.root.text_content().split())[:4000],
            "candidate_selectors": candidates,
            "message": "这是服务端渲染 HTML；可生成选择器适配器。",
        }
    detected = "json" if stripped.startswith(("{", "[")) else "csv"
    return {
        "status": "success",
        "detected_format": detected,
        "content_type": content_type or "application/octet-stream",
        "text_excerpt": text[:4000],
        "candidate_selectors": [],
        "message": "可继续使用 JSON/CSV 声明式适配器。",
    }


def _html_candidate_selectors(root: _HtmlNode) -> list[str]:
    nodes = _descendants(root)
    candidates = [tag for tag in ("article", "li", "tr") if any(node.tag == tag for node in nodes)]
    class_counts: dict[str, int] = {}
    for node in nodes:
        for class_name in node.attrs.get("class", "").split():
            if re.fullmatch(r"[\w:-]+", class_name):
                class_counts[class_name] = class_counts.get(class_name, 0) + 1
    candidates.extend(f".{name}" for name, count in class_counts.items() if count >= 2)
    return candidates[:20]


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


def _parse_rows(spec: AdapterSpec, body: bytes) -> list[dict[str, object]]:
    try:
        text = body.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise SourceFetchError("数据源响应不是 UTF-8 编码") from exc
    if spec.format == "html":
        parser = _HtmlTreeParser()
        parser.feed(text)
        html_items = _select_nodes(parser.root, spec.items_selector or "")
        if not html_items:
            raise SourceFetchError("items_selector 未匹配到 HTML 列表项")
        return [{"__html_node__": item} for item in html_items]
    if spec.format == "csv":
        try:
            return [dict(row) for row in csv.DictReader(io.StringIO(text))]
        except csv.Error as exc:
            raise SourceFetchError("数据源响应不是有效 CSV") from exc
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SourceFetchError("数据源响应不是有效 JSON") from exc
    json_items = _read_path(payload, spec.items_path)
    if not isinstance(json_items, list):
        raise SourceFetchError("items_path 未指向 JSON 数组")
    return [item for item in json_items if isinstance(item, dict)]


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
