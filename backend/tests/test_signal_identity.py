"""稳定去重身份（dedup_key）单元与回归测试：canonicalize_url 与按源字段子集。"""

from __future__ import annotations

import pytest

from app.signals.identity import (
    DEDUP_FIELDS,
    DEFAULT_DEDUP_FIELDS,
    canonicalize_url,
    compute_dedup_key,
)

MOFCOM = "mofcom-entity-detail"
MEE = "mee-announcement"
USGS = "usgs-earthquake-day"
SSE = "sse-shipping"
COMMODITY = "commodity-futures"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # scheme 与 host 小写（path 大小写保留：路径本身区分大小写）
        ("HTTPS://AQYGZJ.MOFCOM.GOV.CN/a/b", "https://aqygzj.mofcom.gov.cn/a/b"),
        ("HtTp://Example.COM/Path", "https://example.com/Path"),
        # HTTP 明文与 HTTPS 折叠为同一身份（默认端口 80/443 一并去掉）
        ("http://aqygzj.mofcom.gov.cn/a/b", "https://aqygzj.mofcom.gov.cn/a/b"),
        ("https://example.com:443/events/1", "https://example.com/events/1"),
        ("http://example.com:80/events/1", "https://example.com/events/1"),
        # 非默认端口保留
        ("https://example.com:8443/events/1", "https://example.com:8443/events/1"),
        # 结尾斜杠：非根路径去掉一个，根路径保留
        ("https://example.com/events/", "https://example.com/events"),
        ("https://example.com/", "https://example.com/"),
        # fragment 丢弃，path 与 query 保留
        ("https://example.com/events/1#section-2", "https://example.com/events/1"),
        ("https://example.com/events?page=2#top", "https://example.com/events?page=2"),
        # 首尾空白剥离
        ("  https://example.com/events/1  ", "https://example.com/events/1"),
    ],
)
def test_canonicalize_url_folds_equivalent_spellings(raw: str, expected: str) -> None:
    assert canonicalize_url(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "not-a-url",
        "/relative/path",
        "mailto:someone@example.com",
        # 非 http(s) 的绝对地址按原样返回，不猜测语义
        "FTP://Example.COM/Resource",
        "https://example.com:notaport/x",
    ],
)
def test_canonicalize_url_returns_non_http_input_unchanged(raw: str) -> None:
    assert canonicalize_url(raw) == raw.strip()


def test_canonicalize_url_handles_none_and_blank() -> None:
    assert canonicalize_url(None) is None
    assert canonicalize_url("   ") == ""


@pytest.mark.parametrize(
    "raw",
    [
        "https://aqygzj.mofcom.gov.cn/article/2026/30",
        "http://aqygzj.mofcom.gov.cn/article/2026/30/",
        "https://AQYGZJ.mofcom.gov.cn:443/article/2026/30#anchor",
        "https://example.com/events?page=2",
    ],
)
def test_canonicalize_url_is_idempotent(raw: str) -> None:
    once = canonicalize_url(raw)
    assert once is not None
    assert canonicalize_url(once) == once


def test_mofcom_scheme_flip_keeps_same_dedup_key() -> None:
    # Given: 同一实体详情页，官方在 HTTP 明文与 HTTPS 端点之间切换。
    http_key = compute_dedup_key(
        MOFCOM,
        external_id="mofcom-entity-laffitte",
        url="http://aqygzj.mofcom.gov.cn/article/2026/30",
        title="拉法特集团",
    )
    https_key = compute_dedup_key(
        MOFCOM,
        external_id="mofcom-entity-laffitte",
        url="https://aqygzj.mofcom.gov.cn/article/2026/30/",
        title="拉法特集团",
    )

    # Then: 只差 scheme/端口/结尾斜杠时视为同一条信号。
    assert http_key == https_key


def test_mofcom_same_announcement_distinct_entities_keep_distinct_keys() -> None:
    # Given: 一条公告列出多个实体，标题即实体名，详情页 URL 相同。
    url = "https://aqygzj.mofcom.gov.cn/article/2026/30"
    first = compute_dedup_key(MOFCOM, external_id=None, url=url, title="拉法特集团")
    second = compute_dedup_key(MOFCOM, external_id=None, url=url, title="太脱拉卡车公司")

    # Then: 不能互相吞掉。
    assert first != second


def test_price_churn_does_not_change_default_source_key() -> None:
    # Given: 商品期货同一合约的两个报价快照，价格与标题措辞都在漂移。
    before = compute_dedup_key(
        COMMODITY, external_id="cu-主力", url=None, title="沪铜 74000 元/吨"
    )
    after = compute_dedup_key(
        COMMODITY, external_id="cu-主力", url=None, title="沪铜最新 73850 元/吨"
    )

    assert before == after


def test_revised_title_keeps_usgs_key() -> None:
    # Given: USGS 官方会修订地震标题，历史已因此产生 190 条重复。
    before = compute_dedup_key(
        USGS, external_id="us7000thzz", url=None, title="10km SSE of Fiji"
    )
    after = compute_dedup_key(
        USGS, external_id="us7000thzz", url=None, title="M 5.2 - Fiji Region (revised)"
    )

    assert before == after


def test_revised_title_keeps_sse_shipping_key() -> None:
    before = compute_dedup_key(SSE, external_id="SSE-001", url=None, title="航线恢复")
    after = compute_dedup_key(SSE, external_id="SSE-001", url=None, title="航线恢复（更新）")

    assert before == after


def test_announcement_sources_key_on_canonical_url_only() -> None:
    # Given: 生态环境部公告没有 external_id，只能用 URL 身份。
    first = compute_dedup_key(
        MEE, external_id=None, url="https://mee.gov.cn/a.htm", title="甲"
    )
    equivalent = compute_dedup_key(
        MEE, external_id=None, url="http://mee.gov.cn/a.htm#top", title="乙"
    )
    other = compute_dedup_key(
        MEE, external_id=None, url="https://mee.gov.cn/b.htm", title="甲"
    )

    assert first == equivalent
    assert first != other


def test_declared_fields_override_source_map() -> None:
    # Given: 声明式适配器显式声明字段集合时以声明为准。
    declared = compute_dedup_key(
        MEE,
        external_id="MEE-1",
        url="https://mee.gov.cn/a.htm",
        title="标题",
        fields=("external_id",),
    )
    assert declared == compute_dedup_key(
        MEE,
        external_id="MEE-1",
        url="https://mee.gov.cn/b.htm",
        title="另一个",
        fields=("external_id",),
    )


def test_all_empty_fields_fall_back_to_triple_and_never_collapse() -> None:
    # Given: 既无 external_id 也无 url，只能靠标题兜底。
    first = compute_dedup_key("unknown-source", external_id=None, url=None, title="甲")
    same = compute_dedup_key(
        "unknown-source",
        external_id="",
        url="   ",
        title="甲",
        fields=("external_id", "url"),
    )
    other = compute_dedup_key("unknown-source", external_id=None, url=None, title="乙")

    assert first == same
    assert first != other
    assert len(first) == 64


def test_volatile_field_in_declaration_falls_back_to_triple() -> None:
    # Given: 历史遗留声明把 content 写进了身份；此时按缺失处理并回落三元组，
    # 保证迁移回填与运行时写入算出同一个键。
    with_content = compute_dedup_key(
        COMMODITY,
        external_id=None,
        url="https://example.com/x",
        title="标题",
        fields=("content",),
    )
    fallback = compute_dedup_key(
        COMMODITY, external_id=None, url="https://example.com/x", title="标题"
    )

    assert with_content == fallback


def test_source_map_defaults_are_explicit() -> None:
    assert DEDUP_FIELDS[MOFCOM] == ("external_id",)
    assert DEDUP_FIELDS[MEE] == ("url",)
    assert DEDUP_FIELDS["customs-announcement"] == ("url",)
    assert DEDUP_FIELDS["fmprc-press"] == ("url",)
    assert DEDUP_FIELDS[USGS] == ("external_id",)
    assert DEDUP_FIELDS[SSE] == ("external_id",)
    assert DEFAULT_DEDUP_FIELDS == ("external_id",)
    # 没有任何易变字段（正文 / 有效期 / 生命周期）进入身份。
    for fields in DEDUP_FIELDS.values():
        assert not {"content", "valid_until", "validity_key"} & set(fields)