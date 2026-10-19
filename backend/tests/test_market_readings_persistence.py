"""拉取式采集的行情读数持久化（``raw_data["source_extra"]``）写路径锁定。

行情类适配器把结构化观察点放在 ``RawSourceItem.extra``（commodity-futures 的
``symbol`` / ``last_price`` / ``change_pct``，pbc-lpr 的 ``lpr_1y`` 等）。采集服务
必须把它原样写进 ``raw_signals.raw_data["source_extra"]``，形成按指标可查询的
观察点序列；``extra`` 为空时保持现状（``raw_data`` 仍等于信号
``model_dump(mode="json")``，不写空 ``source_extra`` 键）。

读取契约：行情评估器与行情 API 只认 ``raw_data["source_extra"]``；
``ManualSignalInput`` 保持 ``extra="forbid"``，结构化读数不得挂在信号模型上。
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.signals.models import DataSource, RawSignal
from app.signals.schemas import ManualSignalInput
from app.signals.service import collect_source
from app.signals.sources import (
    CommodityFuturesAdapter,
    RawSourceItem,
    SourceHealth,
)

# 测试统一有效期策略：固定 3650 天，避免 profile 解析干扰 raw_data 断言。
_TEST_VALIDITY_POLICY: dict[str, object] = {"mode": "fixed_days", "fixed_days": 3650}


def _source(session: Session, code: str) -> DataSource:
    """复用迁移已播种的信源，缺失时才新建并启用。"""
    source = session.scalar(select(DataSource).where(DataSource.code == code))
    if source is None:
        source = DataSource(
            code=code,
            name=code,
            source_type="api",
            credibility=90,
            enabled=True,
            adapter_status="builtin",
            adapter_version=0,
            auth_type="none",
            login_config={},
            adapter_config={},
        )
        session.add(source)
    source.enabled = True
    source.validity_policy = _TEST_VALIDITY_POLICY
    session.flush()
    return source


def _signal_row(session: Session, source: DataSource) -> RawSignal:
    signal = session.scalar(
        select(RawSignal).where(RawSignal.source_id == source.id).limit(1)
    )
    assert signal is not None, "采集应写入一条 raw_signals 行"
    return signal


class _StubAdapter:
    """最小拉取式适配器：直接回放给定条目，不访问网络。"""

    def __init__(self, source_code: str, items: list[RawSourceItem]) -> None:
        self.source_code = source_code
        self._items = items

    async def fetch(self, cursor: str | None = None) -> list[RawSourceItem]:
        del cursor
        return list(self._items)

    def normalize(self, item: RawSourceItem) -> ManualSignalInput:
        return ManualSignalInput(
            external_id=item.external_id,
            title=item.title,
            content=item.content,
            url=item.url,
            published_at=item.published_at,
        )

    def fingerprint(self, signal: ManualSignalInput) -> str:
        return f"stub-fp-{signal.external_id}"

    async def healthcheck(self) -> SourceHealth:
        return SourceHealth(ok=True, message="stub")


def _commodity_quote_body() -> str:
    """沪铜连续：昨结 1000 → 最新 1050（+5.00%）。"""
    return (
        'var hq_str_nf_CU0="沪铜连续,150000,1000.000,1020.000,'
        "1000.000,2000.000,1050.000,2000.000,2000.000,1000.000,"
        '900.000,1,2,100.000,50,,2026-08-21";\n'
    )


def test_market_reading_extra_is_persisted_into_raw_data(db_session: Session) -> None:
    """行情采集后 raw_data["source_extra"] 与 item.extra 完全一致。"""
    source = _source(db_session, "commodity-futures")
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200, content=_commodity_quote_body().encode("gbk")
        )
    )
    adapter = CommodityFuturesAdapter(transport=transport)
    expected_extra = asyncio.run(adapter.fetch())[0].extra

    run = collect_source(db_session, source, adapter)

    assert run.status == "succeeded"
    assert run.created_count == 1
    signal = _signal_row(db_session, source)
    assert signal.raw_data is not None
    assert signal.raw_data["source_extra"] == expected_extra
    # 结构化读数完整：symbol / last_price / change_pct 三个观察点都在
    assert signal.raw_data["source_extra"] == {
        "symbol": "nf_CU0",
        "last_price": 1050.0,
        "change_pct": 5.0,
    }
    # 既有 raw_data 形状不变：信号自身字段仍原样落库
    assert signal.raw_data["external_id"] == signal.external_id
    assert signal.raw_data["title"] == signal.title


def test_source_extra_persisted_for_any_adapter_with_extra(
    db_session: Session,
) -> None:
    """非行情适配器只要给出 extra，也走同一条 source_extra 通道。"""
    source = _source(db_session, "stub-with-extra")
    item = RawSourceItem(
        external_id="stub-extra-1",
        title="测试读数",
        content="正文",
        extra={"lpr_1y": "3.0", "lpr_5y": "3.5"},
    )
    run = collect_source(db_session, source, _StubAdapter("stub-with-extra", [item]))

    assert run.created_count == 1
    signal = _signal_row(db_session, source)
    assert signal.raw_data is not None
    assert signal.raw_data["source_extra"] == {
        "lpr_1y": "3.0",
        "lpr_5y": "3.5",
    }


def test_empty_extra_keeps_raw_data_unchanged(db_session: Session) -> None:
    """item.extra == {} 时保持现状：不写 source_extra，raw_data 仍是信号 dump。"""
    source = _source(db_session, "stub-empty-extra")
    item = RawSourceItem(
        external_id="stub-empty-1",
        title="无结构化读数",
        content="正文",
        extra={},
    )
    adapter = _StubAdapter("stub-empty-extra", [item])

    run = collect_source(db_session, source, adapter)

    assert run.created_count == 1
    signal = _signal_row(db_session, source)
    assert signal.raw_data is not None
    assert "source_extra" not in signal.raw_data
    assert signal.raw_data == adapter.normalize(item).model_dump(mode="json")


def test_missing_extra_field_keeps_raw_data_unchanged(db_session: Session) -> None:
    """适配器完全不传 extra（默认空 dict）同样不产生 source_extra 键。"""
    source = _source(db_session, "stub-default-extra")
    item = RawSourceItem(
        external_id="stub-default-1", title="默认读数", content="正文"
    )
    assert item.extra == {}

    collect_source(db_session, source, _StubAdapter("stub-default-extra", [item]))

    signal = _signal_row(db_session, source)
    assert signal.raw_data is not None
    assert "source_extra" not in signal.raw_data
    assert signal.raw_data["title"] == "默认读数"


def test_none_extra_is_tolerated_and_not_persisted(db_session: Session) -> None:
    """对抗性：适配器把 extra 置为 None 也不得写出 source_extra 键。"""
    source = _source(db_session, "stub-none-extra")
    item = RawSourceItem(
        external_id="stub-none-1", title="None 读数", content="正文"
    )
    object.__setattr__(item, "extra", None)

    collect_source(db_session, source, _StubAdapter("stub-none-extra", [item]))

    signal = _signal_row(db_session, source)
    assert signal.raw_data is not None
    assert "source_extra" not in signal.raw_data
    assert signal.raw_data["title"] == "None 读数"


@pytest.mark.parametrize("read_key", ["symbol", "last_price", "change_pct"])
def test_each_market_observation_point_is_queryable(
    db_session: Session, read_key: str
) -> None:
    """按指标逐个观察点可读：source_extra 是唯一读取通道。"""
    source = _source(db_session, "commodity-futures")
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200, content=_commodity_quote_body().encode("gbk")
        )
    )

    collect_source(db_session, source, CommodityFuturesAdapter(transport=transport))

    signal = _signal_row(db_session, source)
    assert signal.raw_data is not None
    extra = signal.raw_data["source_extra"]
    assert isinstance(extra, dict)
    assert read_key in extra
    # 信号模型上不得出现结构化读数（ManualSignalInput 仍 extra="forbid"）
    assert not hasattr(signal, "source_extra")