"""Todo 8 隔离测试栈 stub E2E：专属 opt-in fixture 配置 tianyancha 提交态。

仅在隔离测试栈（compose.test.yaml 注入 ``TYC_TEST_STUB_URL``）中运行；缺少该
环境变量即 skip，避免在业务库/宿主机环境误跑。fixture 自行以提交态配置测试
密钥/端点/启停/额度，并在 try/finally 中 snapshot/restore 恢复所有被改字段
（用例体异常同样恢复）——通用 ``seed_e2e.seed()`` 不再触碰天眼查控制台字段
（回归见本文件首个用例）。核查走真实 ``McpTycGateway`` + 真实额度执行器，但
端点只可能是本地 stub（断言排除任何真实天眼查域名），全程零真实外网调用。
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator
from contextlib import suppress

import pytest
from fastapi.testclient import TestClient
from seed_e2e import seed
from sqlalchemy import select
from sqlalchemy.orm import Session
from tyc_batch_support import (
    TYC_SOURCE_CODE,
    committed_tyc_daily_used,
    committed_tyc_rows,
    restore_tyc_source,
    snapshot_tyc_source,
    truncate_committed_tyc_usage,
)

from app.database import engine
from app.signals.models import DataSource, RawSignal
from app.signals.secret_store import encrypt_secret
from app.suppliers.models import Supplier

STUB_URL_ENV = "TYC_TEST_STUB_URL"
STUB_TEST_KEY = "tyc-e2e-stub-key"
REAL_ENDPOINT_MARKERS = ("tianyancha.com", "mcp.tianyancha")
CONSOLE_BASELINE_ENDPOINT = "https://console-baseline.invalid/tyc"
STUB_LOGIN_CONFIG: dict[str, object] = {
    "mode": "on_demand",
    "secret_source": "console",
    "daily_limit": 1000,
    "monthly_limit": 10000,
}
_STATE_FIELDS = (
    "enabled",
    "api_key_encrypted",
    "api_key_hash",
    "api_key_last4",
    "login_config",
    "endpoint_url",
)

pytestmark = pytest.mark.skipif(
    not os.environ.get(STUB_URL_ENV),
    reason="仅在隔离测试栈（TYC_TEST_STUB_URL 注入）运行",
)


def _state_diff(
    before: tuple[object, ...], after: tuple[object, ...]
) -> list[str]:
    return [
        f"{label}: {old!r} != {new!r}"
        for label, old, new in zip(_STATE_FIELDS, before, after, strict=True)
        if old != new
    ]


def _write_console_baseline() -> None:
    """把 tianyancha 置为「未配置密钥的停用控制台基线」（仅回归断言用）。"""
    with Session(engine) as setup:
        source = setup.scalar(
            select(DataSource).where(DataSource.code == TYC_SOURCE_CODE)
        )
        assert source is not None
        source.enabled = False
        source.endpoint_url = CONSOLE_BASELINE_ENDPOINT
        source.api_key_encrypted = None
        source.api_key_hash = None
        source.api_key_last4 = None
        source.login_config = {}
        setup.commit()


def _configure_committed_stub_source() -> int:
    """提交态启用 tianyancha 并指向容器内确定性 stub；返回 source id。"""
    stub_url = os.environ[STUB_URL_ENV]
    assert not any(marker in stub_url for marker in REAL_ENDPOINT_MARKERS), (
        "TYC_TEST_STUB_URL 不得指向真实天眼查域名"
    )
    with Session(engine) as setup:
        source = setup.scalar(
            select(DataSource).where(DataSource.code == TYC_SOURCE_CODE)
        )
        assert source is not None
        source.enabled = True
        source.endpoint_url = stub_url
        source.api_key_encrypted = encrypt_secret(STUB_TEST_KEY)
        source.api_key_hash = hashlib.sha256(STUB_TEST_KEY.encode()).hexdigest()
        source.api_key_last4 = "stub"
        source.login_config = dict(STUB_LOGIN_CONFIG)
        setup.commit()
        return source.id


def _stub_source_override() -> Iterator[int]:
    """opt-in 覆盖：快照 → 提交态配置 → finally 恢复（异常路径同样恢复）。"""
    original = snapshot_tyc_source()
    truncate_committed_tyc_usage()
    try:
        yield _configure_committed_stub_source()
    finally:
        restore_tyc_source(original)
        truncate_committed_tyc_usage()


@pytest.fixture
def stub_configured_source(db_session: Session) -> Iterator[int]:
    """本用例独占的 tianyancha stub 提交态；结束后（含失败）逐字段恢复原状。"""
    db_session.expire_all()
    yield from _stub_source_override()


def test_global_seed_does_not_touch_tianyancha_console_state() -> None:
    """回归：通用 seed 不得改写 tianyancha 密钥/端点/启停/登录配置。

    Given 控制台为未配置密钥的停用基线，
    When 运行全局 ``seed_e2e.seed()``（通用测试栈启动路径），
    Then 六个控制台字段逐字段保持基线（曾因全局写 stub 配置污染
    ``test_sources_admin`` 的无密钥前置，见 wave3 归因 §8）。
    """
    original = snapshot_tyc_source()
    try:
        _write_console_baseline()
        baseline = snapshot_tyc_source()
        seed()
        assert _state_diff(baseline, snapshot_tyc_source()) == []
    finally:
        restore_tyc_source(original)


def test_stub_manual_check_runs_through_local_stub(
    client: TestClient, db_session: Session, stub_configured_source: int
) -> None:
    """Given fixture 将 tianyancha 指向本地 stub，When 手动单供应商核查，
    Then 逐工具计数全部成功、计费 13 条、报告信号落库；零真实外网。
    """
    supplier = Supplier(
        supplier_code="SUP-STUB-E2E-001",
        legal_name="Stub 端到端核查有限公司",
        country_code="CN",
        enabled=True,
    )
    db_session.add(supplier)
    db_session.flush()

    response = client.post(
        f"/api/v1/sources/{stub_configured_source}/run-tyc-batch"
        f"?supplier_id={supplier.id}"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["created_count"] == 1
    assert body["failed_count"] == 0
    assert body["quota_exhausted"] is False
    assert body["per_tool_counts"]["search_companies"]["success_with_records"] == 1
    dimension_counts = {
        key: value
        for key, value in body["per_tool_counts"].items()
        if key != "search_companies"
    }
    assert len(dimension_counts) == 12
    assert all(
        counts["success_with_records"] == 1 for counts in dimension_counts.values()
    )
    assert committed_tyc_daily_used() == 13
    assert len(committed_tyc_rows()) == 13

    signal = db_session.scalar(
        select(RawSignal).where(
            RawSignal.external_id.like(f"tyc-{supplier.supplier_code}-%")
        )
    )
    assert signal is not None
    assert signal.raw_data["report_kind"] == "supplier_profile"
    assert len(signal.raw_data["dimensions"]) == 12


def test_stub_fixture_restores_console_state_even_when_test_body_fails() -> None:
    """异常路径：用例体抛错后驱动 teardown，六个字段逐字段恢复 before/after 相同。"""
    original = snapshot_tyc_source()
    override = _stub_source_override()
    next(override)
    try:
        assert _state_diff(original, snapshot_tyc_source()) != [], "override 应先改写字段"
        raise RuntimeError("模拟用例体异常")
    except RuntimeError:
        pass
    finally:
        with suppress(StopIteration):
            next(override)
    assert _state_diff(original, snapshot_tyc_source()) == []
