"""``app.agent.source_skill`` 已内置信源标注与推荐规则单测。

覆盖：
- USGS 目录条目带结构化 ``builtin`` 标记与「已内置采集」说明；
- ``_catalog_text()`` 渲染内置标记，且标记确实驱动「已内置」文案（非重言：
  去掉标记的目录副本不得再输出该文案）；
- 接入提示词包含「标注已内置的信源不得再推荐为新接入」规则。
"""

from __future__ import annotations

from app.agent.source_skill import (
    VERIFIED_SOURCE_CATALOG,
    _catalog_text,
    build_source_onboarding_skill,
)

USGS_CODE = "usgs-earthquake-day"
BUILTIN_NOTE = "已内置采集（编码 usgs-earthquake-day），切勿重复接入"


def _usgs_entry() -> dict[str, str]:
    for item in VERIFIED_SOURCE_CATALOG:
        if USGS_CODE in item.get("url", "") or "USGS" in item.get("name", ""):
            return item
    raise AssertionError("已验证信源目录缺少 USGS 条目")


def test_usgs_catalog_entry_marks_builtin() -> None:
    entry = _usgs_entry()
    assert entry["builtin"] == "true"
    assert entry["builtin_note"] == BUILTIN_NOTE


def test_catalog_text_includes_builtin_marker_and_note() -> None:
    text = _catalog_text()
    assert "USGS" in text
    assert "builtin" in text
    assert BUILTIN_NOTE in text


def test_catalog_text_accepts_custom_catalog() -> None:
    assert _catalog_text(()) == "（暂无已验证目录）"


def test_onboarding_prompt_forbids_recommending_builtin_sources() -> None:
    prompt = build_source_onboarding_skill(current_step="source_url", answers={})
    assert "标注已内置的信源不得再推荐为新接入" in prompt
    assert "builtin" in prompt


def test_catalog_text_without_builtin_marker_omits_builtin_wording() -> None:
    # 非重言：去掉 builtin 标记的目录副本不得再输出「已内置」/builtin 文案。
    stripped_catalog = tuple(
        {key: value for key, value in item.items() if key != "builtin"}
        for item in VERIFIED_SOURCE_CATALOG
    )
    text = _catalog_text(stripped_catalog)
    assert "USGS" in text  # 目录条目仍在渲染，排除因目录为空而假通过的场景
    assert "已内置" not in text
    assert "builtin" not in text
