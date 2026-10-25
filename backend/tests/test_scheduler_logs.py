"""Scheduler 原始日志 tail、脱敏与 HTTP 契约测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.auth.permissions import (
    PERM_SYSTEM_LOG_VIEW,
    ROLE_PERMISSIONS,
)
from app.scheduler.log_tail import (
    MAX_LIMIT,
    MAX_READ_BYTES,
    read_log_tail,
    redact_log_line,
    scheduler_log_path,
)

LOG_ENV = "SCHEDULER_LOG_FILE"


def _log_line(index: int) -> str:
    return f"2026-10-25 14:32:{index % 60:02d},120 INFO scheduler.main job-{index}\n"


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


# --------------------------------------------------------------------------- #
# 1. 路径解析与不可用状态
# --------------------------------------------------------------------------- #
def test_scheduler_log_path_uses_env(monkeypatch, tmp_path: Path) -> None:
    target = tmp_path / "custom.log"
    monkeypatch.setenv(LOG_ENV, str(target))
    assert scheduler_log_path() == target


def test_scheduler_log_path_defaults_to_backend_data(monkeypatch) -> None:
    monkeypatch.delenv(LOG_ENV, raising=False)
    path = scheduler_log_path()
    assert path.name == "scheduler.log"
    assert path.parent.name == "scheduler-logs"
    assert path.parent.parent.name == "data"


def test_missing_file_is_unavailable(tmp_path: Path) -> None:
    page = read_log_tail(tmp_path / "absent.log", None, 200)
    assert page.available is False
    assert page.reason == "missing"
    assert page.lines == []
    assert page.next_cursor is None
    assert page.updated_at is None


def test_unconfigured_path_is_unavailable() -> None:
    page = read_log_tail(None, None, 200)
    assert page.available is False
    assert page.reason == "not_configured"
    assert page.lines == []


def test_directory_path_is_unreadable(tmp_path: Path) -> None:
    page = read_log_tail(tmp_path, None, 200)
    assert page.available is False
    assert page.reason == "unreadable"


# --------------------------------------------------------------------------- #
# 2. 首次读取：末尾窗口、半行、limit
# --------------------------------------------------------------------------- #
def test_empty_file_returns_zero_cursor(tmp_path: Path) -> None:
    path = tmp_path / "scheduler.log"
    path.touch()
    page = read_log_tail(path, None, 200)
    assert page.available is True
    assert page.reason is None
    assert page.lines == []
    assert page.next_cursor is not None
    assert page.next_cursor.endswith(":0")
    assert page.reset is False
    assert page.truncated is False
    assert page.updated_at is not None


def test_first_read_returns_last_lines_and_drops_partial_tail(tmp_path: Path) -> None:
    path = tmp_path / "scheduler.log"
    content = "".join(_log_line(index) for index in range(5)) + "half-written-line"
    _write(path, content)

    page = read_log_tail(path, None, 3)
    assert page.lines == [
        _log_line(2).rstrip("\n"),
        _log_line(3).rstrip("\n"),
        _log_line(4).rstrip("\n"),
    ]
    # 首次读取也如实标记：末尾窗口内还有更早的完整行未返回。
    assert page.truncated is True

    # 补上换行后，半行才作为完整行返回；cursor 之前停在换行之后。
    _write(path, content + "\n" + _log_line(5))
    incremental = read_log_tail(path, page.next_cursor, 200)
    assert incremental.lines == ["half-written-line", _log_line(5).rstrip("\n")]
    assert incremental.reset is False


def test_first_read_drops_partial_first_line_of_window(tmp_path: Path) -> None:
    path = tmp_path / "scheduler.log"
    # 首行远大于读取窗口，窗口起点必然落在该行中间。
    content = ("A" * 1000 + "\n") + ("B" * 10 + "\n") * (MAX_READ_BYTES // 11 + 200)
    _write(path, content)

    page = read_log_tail(path, None, 500)
    assert page.lines
    assert all(line == "B" * 10 for line in page.lines)
    assert len(page.lines) == 500


# --------------------------------------------------------------------------- #
# 3. 增量读取：cursor、半行、轮转、truncated
# --------------------------------------------------------------------------- #
def test_incremental_read_returns_only_new_complete_lines(tmp_path: Path) -> None:
    path = tmp_path / "scheduler.log"
    _write(path, _log_line(0) + _log_line(1))
    first = read_log_tail(path, None, 200)
    assert len(first.lines) == 2

    with path.open("a", encoding="utf-8") as handle:
        handle.write(_log_line(2) + _log_line(3))

    second = read_log_tail(path, first.next_cursor, 200)
    assert second.lines == [_log_line(2).rstrip("\n"), _log_line(3).rstrip("\n")]
    assert second.next_cursor != first.next_cursor
    assert second.reset is False
    assert second.truncated is False

    third = read_log_tail(path, second.next_cursor, 200)
    assert third.lines == []
    assert third.next_cursor == second.next_cursor


def test_incremental_read_waits_for_partial_line(tmp_path: Path) -> None:
    path = tmp_path / "scheduler.log"
    _write(path, _log_line(0))
    first = read_log_tail(path, None, 200)

    with path.open("a", encoding="utf-8") as handle:
        handle.write("still-typing")
    pending = read_log_tail(path, first.next_cursor, 200)
    assert pending.lines == []
    assert pending.next_cursor == first.next_cursor

    with path.open("a", encoding="utf-8") as handle:
        handle.write("-done\n")
    completed = read_log_tail(path, pending.next_cursor, 200)
    assert completed.lines == ["still-typing-done"]


def test_incremental_read_marks_truncated_but_advances_cursor(tmp_path: Path) -> None:
    path = tmp_path / "scheduler.log"
    _write(path, _log_line(0))
    first = read_log_tail(path, None, 200)

    with path.open("a", encoding="utf-8") as handle:
        handle.writelines(_log_line(index) for index in range(1, 11))

    page = read_log_tail(path, first.next_cursor, 3)
    assert page.truncated is True
    assert page.lines == [_log_line(index).rstrip("\n") for index in (8, 9, 10)]

    # cursor 已推进到已读末尾：再读无新行，不重复返回。
    follow_up = read_log_tail(path, page.next_cursor, 3)
    assert follow_up.lines == []
    assert follow_up.truncated is False


def test_rotation_resets_to_new_file_tail(tmp_path: Path) -> None:
    path = tmp_path / "scheduler.log"
    _write(path, _log_line(0) + _log_line(1))
    first = read_log_tail(path, None, 200)

    replacement = tmp_path / "scheduler.log.new"
    _write(replacement, _log_line(100) + _log_line(101))
    replacement.replace(path)

    page = read_log_tail(path, first.next_cursor, 200)
    assert page.reset is True
    assert page.lines == [_log_line(100).rstrip("\n"), _log_line(101).rstrip("\n")]


def test_shrunken_file_resets(tmp_path: Path) -> None:
    path = tmp_path / "scheduler.log"
    _write(path, "".join(_log_line(index) for index in range(10)))
    first = read_log_tail(path, None, 200)

    with path.open("w", encoding="utf-8") as handle:
        handle.write(_log_line(200))

    page = read_log_tail(path, first.next_cursor, 200)
    assert page.reset is True
    assert page.lines == [_log_line(200).rstrip("\n")]


def test_invalid_cursor_falls_back_to_tail(tmp_path: Path) -> None:
    path = tmp_path / "scheduler.log"
    _write(path, _log_line(0) + _log_line(1))
    page = read_log_tail(path, "not-a-cursor", 200)
    assert page.available is True
    assert page.reset is False
    assert page.lines == [_log_line(0).rstrip("\n"), _log_line(1).rstrip("\n")]


# --------------------------------------------------------------------------- #
# 4. 脱敏
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("raw", "must_hide", "must_keep"),
    [
        (
            "config api_key=abcd1234 loaded",
            "abcd1234",
            "api_key=***",
        ),
        (
            "db token: s3cr3t-value accepted",
            "s3cr3t-value",
            "token: ***",
        ),
        (
            "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig",
            "eyJhbGciOiJIUzI1NiJ9",
            "Authorization: ***",
        ),
        (
            "request header authorization=Bearer abc.def.ghi",
            "abc.def.ghi",
            "authorization=***",
        ),
        (
            "Cookie: session=abc123; theme=dark",
            "abc123",
            "theme=dark",
        ),
        (
            "GET https://api.example.com/v1/suppliers?token=abc&page=2",
            "token=abc",
            "https://api.example.com/v1/suppliers?token=***&page=2",
        ),
        (
            "callback https://x.example/hook?sign=deadbeef&supplier=SUP-1",
            "deadbeef",
            "https://x.example/hook?sign=***&supplier=SUP-1",
        ),
        (
            "provider key sk-ant-api03-abcdefghijklmnop rejected",
            "sk-ant-api03-abcdefghijklmnop",
            "provider key *** rejected",
        ),
        (
            "password='hunter2' rotated",
            "hunter2",
            "***",
        ),
    ],
)
def test_redact_log_line_hides_credentials(raw: str, must_hide: str, must_keep: str) -> None:
    redacted = redact_log_line(raw)
    assert must_hide not in redacted
    assert must_keep in redacted


def test_redact_log_line_keeps_normal_paths_and_supplier_names() -> None:
    raw = (
        "2026-10-25 14:32:05,120 INFO scheduler.jobs 采集供应商 上海电气集团 "
        "完成 https://example.com/suppliers/SUP-0001"
    )
    assert redact_log_line(raw) == raw


# --------------------------------------------------------------------------- #
# 5. 权限矩阵与 HTTP 契约
# --------------------------------------------------------------------------- #
def test_log_permission_granted_only_to_admins() -> None:
    assert PERM_SYSTEM_LOG_VIEW in ROLE_PERMISSIONS["risk_admin"]
    assert PERM_SYSTEM_LOG_VIEW in ROLE_PERMISSIONS["platform_admin"]
    assert PERM_SYSTEM_LOG_VIEW not in ROLE_PERMISSIONS["viewer"]
    assert PERM_SYSTEM_LOG_VIEW not in ROLE_PERMISSIONS["risk_analyst"]


def test_configure_file_logging_writes_nested_log_file(monkeypatch, tmp_path: Path) -> None:
    import logging

    from app.scheduler import main as scheduler_main

    path = tmp_path / "nested" / "scheduler.log"
    monkeypatch.setenv(LOG_ENV, str(path))
    scheduler_main._configure_file_logging()
    scheduler_main._configure_file_logging()  # 幂等：不重复挂 handler
    handlers = [
        handler
        for handler in logging.getLogger().handlers
        if handler.name == scheduler_main.FILE_LOG_HANDLER_NAME
    ]
    assert len(handlers) == 1
    handler = handlers[0]
    try:
        logging.getLogger("scheduler.test").warning("file-log-smoke")
        handler.flush()
        assert path.exists()
        assert "file-log-smoke" in path.read_text(encoding="utf-8")
    finally:
        logging.getLogger().removeHandler(handler)
        handler.close()


def test_scheduler_logs_requires_session(client) -> None:
    client.cookies.clear()
    client.headers.pop("X-CSRF-Token", None)
    response = client.get("/api/v1/system/scheduler-logs")
    assert response.status_code == 401


def test_scheduler_logs_forbidden_for_viewer(client, auth_as, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv(LOG_ENV, str(tmp_path / "scheduler.log"))
    auth_as("viewer", "log-viewer")
    response = client.get("/api/v1/system/scheduler-logs")
    assert response.status_code == 403


def test_scheduler_logs_returns_tail_for_platform_admin(
    client, monkeypatch, tmp_path: Path
) -> None:
    path = tmp_path / "scheduler.log"
    _write(path, "".join(_log_line(index) for index in range(3)))
    monkeypatch.setenv(LOG_ENV, str(path))

    response = client.get("/api/v1/system/scheduler-logs?limit=2")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert set(body) == {
        "available",
        "reason",
        "lines",
        "next_cursor",
        "reset",
        "truncated",
        "updated_at",
    }
    assert body["available"] is True
    assert body["reason"] is None
    assert body["lines"] == [_log_line(1).rstrip("\n"), _log_line(2).rstrip("\n")]
    assert body["reset"] is False
    # 首次窗口只返回最后 2 行，必须如实标记还有更早的完整行未返回。
    assert body["truncated"] is True
    assert body["next_cursor"]
    assert body["updated_at"].endswith("Z")

    # cursor 增量：追加一行后只返回新增行。
    with path.open("a", encoding="utf-8") as handle:
        handle.write(_log_line(3))
    incremental = client.get(
        "/api/v1/system/scheduler-logs",
        params={"cursor": body["next_cursor"], "limit": 200},
    )
    assert incremental.status_code == 200
    incremental_body = incremental.json()
    assert incremental_body["lines"] == [_log_line(3).rstrip("\n")]
    assert incremental_body["reset"] is False
    assert incremental_body["next_cursor"] != body["next_cursor"]


def test_scheduler_logs_unavailable_when_missing(client, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv(LOG_ENV, str(tmp_path / "absent.log"))
    response = client.get("/api/v1/system/scheduler-logs")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["available"] is False
    assert body["reason"] == "missing"
    assert body["lines"] == []
    assert body["next_cursor"] is None


def test_scheduler_logs_redacts_credentials(client, monkeypatch, tmp_path: Path) -> None:
    path = tmp_path / "scheduler.log"
    _write(path, "call https://api.example.com/x?token=abc123&page=1 token: s3cr3t\n")
    monkeypatch.setenv(LOG_ENV, str(path))

    response = client.get("/api/v1/system/scheduler-logs")
    assert response.status_code == 200
    assert "abc123" not in response.text
    assert "s3cr3t" not in response.text
    assert "https://api.example.com/x?token=***&page=1" in response.json()["lines"][0]


@pytest.mark.parametrize("limit", [0, MAX_LIMIT + 1])
def test_scheduler_logs_rejects_out_of_range_limit(
    client, monkeypatch, tmp_path: Path, limit: int
) -> None:
    monkeypatch.setenv(LOG_ENV, str(tmp_path / "scheduler.log"))
    response = client.get("/api/v1/system/scheduler-logs", params={"limit": limit})
    assert response.status_code == 422
