"""SemiconductorIndexAdapter（国证半导体芯片指数 980017）单测。"""

from __future__ import annotations

import asyncio

import httpx

from app.signals.identity import DEDUP_FIELDS
from app.signals.router import build_pull_adapter
from app.signals.sources import SemiconductorIndexAdapter
from app.signals.validity import ValidityProfile

# 模拟新浪简版行情响应（GBK 编码）。
# 字段序：名称,点位,涨跌,涨跌幅%,量,额
_QUOTE = (
    'var hq_str_s_sz980017="国证芯片,3125.67,20.15,0.65,'
    '1234567,98765.43";'
)

# 非交易/无数据时新浪返回空串
_EMPTY_QUOTE = 'var hq_str_s_sz980017="";'

# 结构合法但点位为 0（尚未开盘或指数停牌），不得当作有效行情
_ZERO_QUOTE = 'var hq_str_s_sz980017="国证芯片,0.0000,0.00,0.00,0,0.00";'

# 字段数不足（页面结构漂移）
_SHORT_QUOTE = 'var hq_str_s_sz980017="国证芯片,3125.67";'

# 字段非数值
_MALFORMED_QUOTE = 'var hq_str_s_sz980017="国证芯片,abc,20.15,0.65,1,1";'


def _adapter(body: bytes) -> SemiconductorIndexAdapter:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "hq.sinajs.cn" in str(request.url)
        assert "s_sz980017" in str(request.url)
        return httpx.Response(200, content=body)

    return SemiconductorIndexAdapter(transport=httpx.MockTransport(handler))


def _raising_adapter() -> SemiconductorIndexAdapter:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network down")

    return SemiconductorIndexAdapter(transport=httpx.MockTransport(handler))


def test_fetch_parses_gbk_index_quote() -> None:
    adapter = _adapter(_QUOTE.encode("gbk"))
    items = asyncio.run(adapter.fetch())
    assert len(items) == 1
    item = items[0]
    assert item.external_id.startswith("semi-980017-")
    assert item.validity_profile is ValidityProfile.MARKET_PRICE_POINT
    assert item.validity_key == "semi-980017"
    assert item.extra == {
        "index_code": "980017",
        "last_price": 3125.67,
        "change_pct": 0.65,
        "name": "国证芯片",
    }
    # 标题/正文必须含可读的名称、点位与涨跌幅
    assert "国证芯片" in item.title
    assert "3125.67" in item.title
    assert "+0.65%" in item.title
    assert "3125.67" in item.content
    assert "+0.65%" in item.content


def test_fetch_parses_negative_change() -> None:
    body = 'var hq_str_s_sz980017="国证芯片,3000.00,-45.00,-1.48,10,1";'
    item = asyncio.run(_adapter(body.encode("gbk")).fetch())[0]
    assert item.extra["change_pct"] == -1.48
    assert "-1.48%" in item.title
    assert item.extra["last_price"] == 3000.0


def test_fetch_skips_empty_and_zero_and_malformed() -> None:
    # 空响应体
    assert asyncio.run(_adapter(b"").fetch()) == []
    # 空串条目（新浪在无数据时的标准返回）
    assert asyncio.run(_adapter(_EMPTY_QUOTE.encode("gbk")).fetch()) == []
    # 点位为 0
    assert asyncio.run(_adapter(_ZERO_QUOTE.encode("gbk")).fetch()) == []
    # 字段数不足
    assert asyncio.run(_adapter(_SHORT_QUOTE.encode("gbk")).fetch()) == []
    # 非数值
    assert asyncio.run(_adapter(_MALFORMED_QUOTE.encode("gbk")).fetch()) == []
    # 变量名不匹配（结构漂移）
    assert asyncio.run(_adapter(b'var hq_str_s_sz000001="x,1,1,1,1,1";').fetch()) == []


def test_external_id_and_fingerprint_stable_within_day() -> None:
    adapter = _adapter(_QUOTE.encode("gbk"))
    first = asyncio.run(adapter.fetch())
    second = asyncio.run(adapter.fetch())
    assert [it.external_id for it in first] == [it.external_id for it in second]

    signal1 = adapter.normalize(first[0])
    signal2 = adapter.normalize(second[0])
    assert adapter.fingerprint(signal1) == adapter.fingerprint(signal2)
    assert signal1.published_at is None  # 行情无发布时刻，normalize 不臆造
    assert signal1.url is not None


def test_healthcheck_ok_and_failure() -> None:
    ok = asyncio.run(_adapter(_QUOTE.encode("gbk")).healthcheck())
    assert ok.ok is True
    assert ok.message

    empty = asyncio.run(_adapter(_EMPTY_QUOTE.encode("gbk")).healthcheck())
    assert empty.ok is False
    assert empty.message

    down = asyncio.run(_raising_adapter().healthcheck())
    assert down.ok is False
    assert down.message


def test_registered_in_build_pull_adapter() -> None:
    adapter = build_pull_adapter("semiconductor-index")
    assert isinstance(adapter, SemiconductorIndexAdapter)


def test_dedup_fields_declared() -> None:
    # 点位会随行情漂移，去重身份只能落在 external_id（与 sse-shipping / usgs 同口径）
    assert DEDUP_FIELDS["semiconductor-index"] == ("external_id",)