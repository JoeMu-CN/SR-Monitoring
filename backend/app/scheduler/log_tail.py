"""Scheduler 原始日志的只读 tail 与脱敏。

Scheduler 是独立容器，日志由 ``app/scheduler/main.py`` 通过
``RotatingFileHandler`` 写入共享卷；Web API 只按游标读取该文件末尾，
不暴露日志目录、不接受路径参数，也不调用 docker/shell。

契约要点：
- 首次请求（无 cursor）返回文件末尾最多 ``limit`` 行，只读最后约 256KB，
  丢弃截断处的不完整首行，不返回正在写入的半个末行；
- 带 cursor 时按 ``${st_ino}:${offset}`` 从 offset 读新增内容，最多 256KB，
  只返回完整行；新增超过 ``limit`` 行时返回最后 ``limit`` 行并置
  ``truncated=True``，cursor 仍推进到已读末尾；
- 文件轮转（st_ino 变化）或 size < offset 视为重置，读取新文件末尾并
  置 ``reset=True``；
- 所有返回行统一脱敏，避免日志中的凭据经只读接口外泄。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO, Literal

# 单次读取上限：日志行较短，256KB 足以覆盖默认 limit 的末尾窗口。
MAX_READ_BYTES = 256 * 1024
# 与接口的 limit 默认/上限保持一致；纯函数内部再兜底一次。
DEFAULT_LIMIT = 200
MAX_LIMIT = 500

LogUnavailableReason = Literal["not_configured", "missing", "unreadable"]

_REDACTED = "***"
_BEARER_RE = re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=\-]+")
_SK_RE = re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}\b")
_SENSITIVE_KEY = (
    r"(?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|secret|"
    r"client[_-]?secret|password|passwd|authorization|cookie)"
)
# key=value / key: value：值可能带引号，Authorization 允许 "Bearer xxx" 形式。
_KV_RE = re.compile(
    rf"(?i)(?P<key>\b{_SENSITIVE_KEY}\b)(?P<sep>\s*[:=]\s*)"
    r"(?P<value>(?:Bearer\s+[A-Za-z0-9._~+/=\-]+)|\"[^\"]*\"|'[^']*'|[^\s,;&]+)"
)
# URL query 中的敏感参数（key/sign 不在上面的通用键名中，单独处理）。
_QUERY_RE = re.compile(
    r"(?i)(?P<prefix>[?&](?:api[_-]?key|key|token|secret|password|sign|signature)=)"
    r"(?P<value>[^&\s#]+)"
)


@dataclass(frozen=True)
class SchedulerLogPage:
    """一次读取的结果；``available=False`` 时 ``lines`` 为空、cursor 为 None。"""

    available: bool
    reason: LogUnavailableReason | None
    lines: list[str]
    next_cursor: str | None
    reset: bool
    truncated: bool
    updated_at: datetime | None


def scheduler_log_path() -> Path:
    """Scheduler 日志文件路径。

    优先 ``SCHEDULER_LOG_FILE``；未设置时用仓库后端 ``data/scheduler-logs``
    （容器内即 /app/data/scheduler-logs/scheduler.log，由 compose 共享卷挂载）。
    """
    configured = os.getenv("SCHEDULER_LOG_FILE", "").strip()
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[2] / "data" / "scheduler-logs" / "scheduler.log"


def redact_log_line(line: str) -> str:
    """脱敏单行日志中的凭据，保留普通 URL 路径、供应商名与其余文本。"""
    redacted = _KV_RE.sub(
        lambda match: f"{match.group('key')}{match.group('sep')}{_REDACTED}", line
    )
    redacted = _BEARER_RE.sub(lambda match: f"{match.group(1)} {_REDACTED}", redacted)
    redacted = _SK_RE.sub(_REDACTED, redacted)
    redacted = _QUERY_RE.sub(lambda match: f"{match.group('prefix')}{_REDACTED}", redacted)
    return redacted


def read_log_tail(path: Path | None, cursor: str | None, limit: int) -> SchedulerLogPage:
    """读取日志末尾或游标之后的增量（纯函数，不做 IO 以外的副作用）。"""
    if path is None:
        return _unavailable("not_configured")
    safe_limit = max(1, min(limit, MAX_LIMIT))
    parsed_cursor = _parse_cursor(cursor)
    try:
        with path.open("rb") as handle:
            stat = os.fstat(handle.fileno())
            inode = stat.st_ino
            size = stat.st_size
            updated_at = datetime.fromtimestamp(stat.st_mtime, tz=UTC).replace(microsecond=0)
            if parsed_cursor is None:
                lines, next_offset = _read_tail_lines(handle, size)
                truncated = len(lines) > safe_limit
                tail_lines = lines[-safe_limit:]
                return SchedulerLogPage(
                    available=True,
                    reason=None,
                    lines=_redact_lines(tail_lines),
                    next_cursor=f"{inode}:{next_offset}",
                    reset=False,
                    truncated=truncated,
                    updated_at=updated_at,
                )
            cursor_inode, offset = parsed_cursor
            if cursor_inode != inode or size < offset:
                # 轮转（新 inode）或被截断：从头文件末尾重新开始，提示前端重置。
                lines, next_offset = _read_tail_lines(handle, size)
                return SchedulerLogPage(
                    available=True,
                    reason=None,
                    lines=_redact_lines(lines[-safe_limit:]),
                    next_cursor=f"{inode}:{next_offset}",
                    reset=True,
                    truncated=False,
                    updated_at=updated_at,
                )
            handle.seek(offset)
            chunk = handle.read(MAX_READ_BYTES)
            complete_len = chunk.rfind(b"\n") + 1
            complete = chunk[:complete_len]
            raw_lines = _split_complete_lines(complete)
            truncated = len(raw_lines) > safe_limit
            if truncated:
                raw_lines = raw_lines[-safe_limit:]
            return SchedulerLogPage(
                available=True,
                reason=None,
                lines=_redact_lines(raw_lines),
                # 即使结果被 limit 截断，cursor 仍推进到已读末尾，避免重复读取。
                next_cursor=f"{inode}:{offset + complete_len}",
                reset=False,
                truncated=truncated,
                updated_at=updated_at,
            )
    except (FileNotFoundError, NotADirectoryError):
        return _unavailable("missing")
    except OSError:
        # 权限不足、路径是目录、设备错误等统一视为不可读。
        return _unavailable("unreadable")


def _unavailable(reason: LogUnavailableReason) -> SchedulerLogPage:
    return SchedulerLogPage(
        available=False,
        reason=reason,
        lines=[],
        next_cursor=None,
        reset=False,
        truncated=False,
        updated_at=None,
    )


def _parse_cursor(cursor: str | None) -> tuple[int, int] | None:
    """解析 ``${st_ino}:${offset}``；非法游标按首次请求处理。"""
    if not cursor:
        return None
    inode_text, separator, offset_text = cursor.partition(":")
    if not separator or not inode_text.isdigit() or not offset_text.isdigit():
        return None
    return int(inode_text), int(offset_text)


def _read_tail_lines(handle: BinaryIO, size: int) -> tuple[list[bytes], int]:
    """读取末尾窗口内的完整行，返回（原始行字节, 最后完整换行之后的位置）。"""
    start = max(0, size - MAX_READ_BYTES)
    handle.seek(start)
    chunk = handle.read(size - start)
    complete_len = chunk.rfind(b"\n") + 1
    complete = chunk[:complete_len]
    if start > 0 and complete:
        # 窗口起点落在行中间时，首个换行之前的部分是半行，直接丢弃。
        first_newline = complete.find(b"\n")
        complete = complete[first_newline + 1 :] if first_newline >= 0 else b""
    return _split_complete_lines(complete), start + complete_len


def _split_complete_lines(complete: bytes) -> list[bytes]:
    if not complete:
        return []
    raw_lines = complete.split(b"\n")
    if raw_lines and raw_lines[-1] == b"":
        raw_lines.pop()
    return raw_lines


def _redact_lines(raw_lines: list[bytes]) -> list[str]:
    return [redact_log_line(line.decode("utf-8", errors="replace")) for line in raw_lines]
