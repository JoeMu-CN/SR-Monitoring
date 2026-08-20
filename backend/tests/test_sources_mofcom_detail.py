"""MofcomEntityDetailAdapter（商务部实体名单详情解析）单测。"""

from __future__ import annotations

import asyncio

import httpx

from app.signals.sources import (
    MofcomEntityDetailAdapter,
    _extract_entities_from_detail,
)

_DETAIL_MARKDOWN = """
商务部公告2026年第30号 公布将14家欧盟实体列入出口管制管控名单

根据《中华人民共和国出口管制法》等法律法规，决定将拉法特集团等14家欧盟实体
列入出口管制管控名单（见附件），并采取以下措施。

附件
出口管制管控名单
（2026年7月24日）

1.拉法特集团（Rafat Group）
地址：Smitweg 6, Kinderdijk, The Netherlands
邮编：2961

2.太脱拉卡车公司（TATRA TRUCKS a.s.）
地址：Areal Tatry 1450/1, Koprivnice, Czech Republic
邮编：74221

3.Opticoelectron集团（Opticoelectron Group）
地址：Industrial Park Opticoeletron, Panagyurishte, Bulgaria
邮编：4500

### 在线办事
  * [两用物项和技术进出口审批](http://www.mofcom.gov.cn/zwdt/lywxhjsjcksp/index.html)
"""


def test_extract_entities_from_detail() -> None:
    entities = _extract_entities_from_detail(_DETAIL_MARKDOWN)
    assert "拉法特集团" in entities
    assert "太脱拉卡车公司" in entities
    assert "Opticoelectron集团" in entities
    # 不应包含导航/正文干扰
    assert all("审批" not in e for e in entities)


def test_adapter_test_mode_parses_entities() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_DETAIL_MARKDOWN)

    adapter = MofcomEntityDetailAdapter(transport=httpx.MockTransport(handler))
    items = asyncio.run(adapter.fetch())
    assert len(items) == 3
    first = items[0]
    assert first.title == "出口管制名单新增：拉法特集团"
    assert "商务部公告2026年第30号" in first.content or "原文" in first.content
    assert first.external_id.startswith("mofcom-entity-")


def test_normalize_and_fingerprint_roundtrip() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_DETAIL_MARKDOWN)

    adapter = MofcomEntityDetailAdapter(transport=httpx.MockTransport(handler))
    item = asyncio.run(adapter.fetch())[0]
    signal = adapter.normalize(item)
    assert signal.title == item.title
    assert adapter.fingerprint(signal) == adapter.fingerprint(signal)


def test_healthcheck_ok() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_DETAIL_MARKDOWN)

    adapter = MofcomEntityDetailAdapter(transport=httpx.MockTransport(handler))
    health = asyncio.run(adapter.healthcheck())
    assert health.ok is True
    assert health.message == "返回 3 条实体信号"


def test_extract_skips_non_entity_lines() -> None:
    markdown = """
来源：安全与管制局 类型：原创
2026-07-24 16:00
商务部公告2026年第30号 公布将14家欧盟实体列入出口管制管控名单

1.拉法特集团（Rafat Group）
2.太脱拉卡车公司（TATRA TRUCKS a.s.）

（完）
"""
    entities = _extract_entities_from_detail(markdown)
    assert entities == ["拉法特集团", "太脱拉卡车公司"]
