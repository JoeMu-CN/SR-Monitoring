"""CommodityFuturesAdapter（新浪期货行情，E3 大宗商品）单测。"""

from __future__ import annotations

import asyncio

import httpx

from app.signals.sources import CommodityFuturesAdapter

# 模拟新浪期货行情响应（GBK 编码，两个品种）
_QUOTE = (
    'var hq_str_nf_CU0="沪铜连续,150000,106190.000,107670.000,'
    "106150.000,107520.000,107520.000,107530.000,107520.000,"
    "107010.000,106860.000,1,6,176877.000,66412,,2026-08-21\";\n"
    'var hq_str_nf_AL0="沪铝连续,150000,23500.000,23760.000,'
    "23465.000,23710.000,23705.000,23710.000,23710.000,"
    "23620.000,23630.000,72,138,267438.000,175287,,2026-08-21\";\n"
)


def _adapter(handler) -> CommodityFuturesAdapter:
    return CommodityFuturesAdapter(transport=httpx.MockTransport(handler))


def _handler_for(symbols: dict[str, str]):
    lines = []
    for code, fields in symbols.items():
        lines.append(f'var hq_str_{code}="{fields}";')
    body = "\n".join(lines)

    def handler(request: httpx.Request) -> httpx.Response:
        assert "hq.sinajs.cn" in str(request.url)
        return httpx.Response(200, content=body.encode("gbk"))

    return handler


def _fields(name: str, prev: str, open_: str, last: str) -> str:
    """构造 9+ 字段行情行。"""
    return (
        f"{name},150000,{prev},{open_},1000.000,2000.000,"
        f"{last},2000.000,2000.000,1000.000,900.000,1,2,100.000,50,"
        ",2026-08-21"
    )


def test_fetch_parses_quotes_with_change_pct() -> None:
    # 铜：昨结 1000 → 最新 1050（+5%）；铝：昨结 2000 → 最新 2000（0%）
    symbols = {
        "nf_CU0": _fields("沪铜连续", "1000.000", "1020.000", "1050.000"),
        "nf_AL0": _fields("沪铝连续", "2000.000", "2010.000", "2000.000"),
    }
    adapter = _adapter(_handler_for(symbols))
    items = asyncio.run(adapter.fetch())
    assert len(items) == 2
    cu = [it for it in items if "沪铜" in it.title][0]
    assert "1050" in cu.title
    assert "+5.00%" in cu.title
    assert "较昨结算：+5.00%" in cu.content
    assert cu.external_id.startswith("fut-nf_CU0-")
    assert "finance.sina.com.cn" in cu.url  # type: ignore[operator]
    al = [it for it in items if "沪铝" in it.title][0]
    assert "+0.00%" in al.title


def test_fetch_external_id_stable_per_day() -> None:
    symbols = {"nf_CU0": _fields("沪铜连续", "1000.000", "1020.000", "1050.000")}
    adapter = _adapter(_handler_for(symbols))
    ids1 = [it.external_id for it in asyncio.run(adapter.fetch())]
    ids2 = [it.external_id for it in asyncio.run(adapter.fetch())]
    assert ids1 == ids2  # 同一日幂等


def test_healthcheck_ok_and_failure() -> None:
    def ok_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_QUOTE.encode("gbk"))

    health = asyncio.run(_adapter(ok_handler).healthcheck())
    assert health.ok is True

    def error_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network down")

    health2 = asyncio.run(_adapter(error_handler).healthcheck())
    assert health2.ok is False
