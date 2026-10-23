"""信源名称写入规范化：去掉括号内容，空结果回退原值。"""

from app.signals.schemas import DataSourceUpdate, DataSourceWrite


def _write(name: str) -> DataSourceWrite:
    return DataSourceWrite(
        code="test-source",
        name=name,
        source_type="macro",
        credibility=80,
    )


def test_write_strips_fullwidth_parentheses() -> None:
    payload = _write("测试源（E1 宏观经济）")

    assert payload.name == "测试源"


def test_write_strips_halfwidth_parentheses() -> None:
    payload = _write("Source (E1)")

    assert payload.name == "Source"


def test_update_strips_fullwidth_parentheses() -> None:
    payload = DataSourceUpdate(name="测试源（E1）")

    assert payload.name == "测试源"


def test_write_collapses_whitespace_after_stripping() -> None:
    payload = _write("  统计局  制造业 PMI （E1）  ")

    assert payload.name == "统计局 制造业 PMI"


def test_write_keeps_original_when_only_parentheses() -> None:
    # 去括号后为空不能落成空名称，回退原值以保留可读标识。
    payload = _write("（E1 宏观经济）")

    assert payload.name == "（E1 宏观经济）"


def test_update_keeps_none_name() -> None:
    payload = DataSourceUpdate()

    assert payload.name is None
