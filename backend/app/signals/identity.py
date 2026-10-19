"""信号稳定去重身份（dedup_key）。

去重身份只能依赖「同一条逻辑信号的稳定字段」，不能依赖 URL 的 scheme/path、
正文里的价格或会被官方修订的标题：这些字段每次采集都可能变，一旦进入身份，
同一实体就会被反复写成重复行（commodity-futures 因价格漂移产生过 152 条重复、
usgs-earthquake-day 因标题修订产生过 190 条重复）。

本模块是运行时写入与历史回填**唯一**共用的身份实现：迁移 0060 的回填必须调用
同一个 :func:`compute_dedup_key`，否则历史行与新写入行会算出不同的键，唯一索引
会把旧数据当成新数据重新插一遍。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import Final
from urllib.parse import urlsplit, urlunsplit

__all__ = [
    "DEDUP_FIELDS",
    "DEFAULT_DEDUP_FIELDS",
    "DEDUP_FIELD_WHITELIST",
    "IDENTITY_ANCHOR_FIELDS",
    "canonicalize_url",
    "compute_dedup_key",
    "dedup_fields_for",
]

# scheme -> 默认端口；出现在 netloc 里即视为可省略，显式带默认端口与省略等价。
_DEFAULT_PORTS: Final[dict[str, str]] = {"http": "80", "https": "443"}

# 允许参与去重身份的字段白名单（声明式适配器 fingerprint_fields 的收紧口径）。
# 明确不含 content / valid_until / validity_* / lifecycle_*：它们都会在同一条
# 逻辑信号的多次采集之间漂移，进入身份就等于制造重复。
DEDUP_FIELD_WHITELIST: Final[frozenset[str]] = frozenset(
    {"external_id", "url", "title", "published_at"}
)
# 身份锚点：字段集合至少要含其一，否则整条信号只能靠正文区分，必然产生重复。
IDENTITY_ANCHOR_FIELDS: Final[frozenset[str]] = frozenset({"external_id", "url"})

# 信源未声明专属口径时的默认稳定字段子集：外部标识号是唯一业务稳定锚点。
DEFAULT_DEDUP_FIELDS: Final[tuple[str, ...]] = ("external_id",)

# 按 source_code 声明的稳定字段子集。只列「必须偏离默认口径」的信号：
# - 公告类（mee/customs/fmprc）：无 external_id，url 是唯一稳定锚点；官站在
#   HTTP/HTTPS、结尾斜杠、默认端口之间的写法差异由 canonicalize_url 吸收。
# - mofcom 详情页：一条公告含多个实体，身份必须落到「实体」而非公告标题措辞；
#   适配器已把 external_id 定义为 sha256(规范化详情 URL | 实体名)，故直接用
#   external_id——既区分同页多实体，又不受标题措辞与 scheme 变化影响。
# - sse-shipping / usgs-earthquake-day：external_id 恒定稳定，标题/正文会漂移。
# - semiconductor-index：点位/涨跌幅每分钟都在变，标题与正文随之漂移；身份只能落在
#   每日一条的 external_id（与 sse-shipping 同口径）。
DEDUP_FIELDS: Final[dict[str, tuple[str, ...]]] = {
    "mofcom-entity-detail": ("external_id",),
    "mee-announcement": ("url",),
    "customs-announcement": ("url",),
    "fmprc-press": ("url",),
    "usgs-earthquake-day": ("external_id",),
    "sse-shipping": ("external_id",),
    "semiconductor-index": ("external_id",),
}


def dedup_fields_for(source_code: str, fields: Sequence[str] | None = None) -> tuple[str, ...]:
    """解析实际参与去重身份的字段集合：声明式显式声明优先，否则查信源映射。"""
    if fields is not None:
        return tuple(fields)
    return DEDUP_FIELDS.get(source_code, DEFAULT_DEDUP_FIELDS)


def canonicalize_url(url: str | None) -> str | None:
    """把等价写法的 HTTP(S) URL 折叠成同一形式。

    折叠：scheme 与 host 小写、HTTP 明文统一按 HTTPS 记（并折叠 80/443 默认端口）、
    丢弃 fragment、非根路径去掉一个结尾斜杠；path 与 query 原样保留（路径语义由
    信源决定，这里只吸收与身份无关的写法差异）。不是绝对 http(s) 地址时返回去除
    首尾空白的原值，不猜测语义。
    """
    if url is None:
        return None
    stripped = url.strip()
    if not stripped:
        return stripped
    try:
        parsed = urlsplit(stripped)
        scheme = parsed.scheme.lower()
        host = parsed.hostname
        if scheme not in _DEFAULT_PORTS or not host:
            return stripped
        port = parsed.port
    except ValueError:  # 非法端口等：按原值处理，不因规范化失败阻断写入
        return stripped
    # 身份只关心"同一份官方内容"，明文与 TLS 端点指向同一资源：统一按 https 记，
    # 官站在 HTTP/HTTPS 之间切换（商务部详情页历史上就切换过）不得改变身份。
    netloc = host.lower()
    if port is not None and str(port) not in _DEFAULT_PORTS.values():
        netloc = f"{netloc}:{port}"
    path = parsed.path
    if path.endswith("/") and path != "/":
        path = path[:-1]
    return urlunsplit(("https", netloc, path, parsed.query, ""))


def _has_value(value: str | None) -> bool:
    return value is not None and value.strip() != ""


def _normalize(field: str, value: str | None) -> str | None:
    """按字段语义归一：url 走 canonicalize_url，其余去首尾空白；空值统一为 None。"""
    if value is None:
        return None
    text = str(value).strip()
    if field == "url":
        return canonicalize_url(text) or None
    return text or None


def compute_dedup_key(
    source_code: str,
    *,
    external_id: str | None,
    url: str | None,
    title: str | None,
    fields: Sequence[str] | None = None,
) -> str:
    """计算信号在同一信源内的稳定去重键。

    ``fields`` 为声明式适配器显式声明的字段集合；缺省时按 ``source_code`` 查
    :data:`DEDUP_FIELDS`，其余信源回落到 ``external_id``。声明集合里出现未知
    字段（如历史遗留的 ``content``）时按缺失处理——保证迁移回填与运行时写入
    对同一份数据算出同一个键。

    声明字段全部为空时回落到 (title, url, external_id) 三元组，保证任何信号
    都能得到非空键，不会因「无锚点」退化成同源内唯一键。
    """
    raw: dict[str, str | None] = {
        "external_id": external_id,
        "url": url,
        "title": title,
    }
    selected = dedup_fields_for(source_code, fields)
    values = {field: _normalize(field, raw.get(field)) for field in selected}
    if not any(_has_value(value) for value in values.values()):
        values = {field: _normalize(field, raw[field]) for field in ("title", "url", "external_id")}
    canonical = json.dumps(
        values, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()