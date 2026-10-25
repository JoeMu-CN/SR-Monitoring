import {useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent} from 'react';
import {createPortal} from 'react-dom';
import {ArrowDownToLine, Copy, Search, Terminal, X} from 'lucide-react';
import {api, ApiError, type SchedulerLogPage, type SchedulerLogUnavailableReason} from '../api';
import './SchedulerLogDrawer.css';

/**
 * 调度器原始日志抽屉。
 *
 * 数据来自 GET /api/v1/system/scheduler-logs 的 tail 接口，不读前端拼装的事件；
 * 每 2 秒按 cursor 增量拉取，只在抽屉打开时轮询。日志文件由 scheduler 容器写入共享卷，
 * 前端不拼接、不伪造异常文本；不可用时明确显示 not_configured / missing / unreadable。
 */

interface SchedulerLogDrawerProps {
  readonly onClose: () => void;
  readonly onRequestError: (error: ApiError) => void;
}

type LogFilter = 'all' | 'warn';

interface ParsedLogLine {
  readonly raw: string;
  readonly time: string | null;
  readonly level: string | null;
  readonly logger: string | null;
  readonly message: string;
  /** 多行堆栈的续行继承上一条日志的级别，便于“异常”筛选保留堆栈。 */
  readonly contextLevel: string | null;
}

const LOG_PAGE_SIZE = 200;
const LOG_POLL_MS = 2000;
const LOG_LINE_LIMIT = 1000;

const LOG_LINE_PATTERN = /^(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[,.]\d{1,6})?)\s+(DEBUG|INFO|WARNING|WARN|ERROR|CRITICAL|FATAL)\s+(\S+)\s?(.*)$/;

// scheduler 容器（compose 未设置 TZ）的 asctime 为 UTC；行内时间转换到浏览器本地时区，
// 完整原始时间仍保留在行 title 与复制文本中。
const formatLogTime = (value: string): string => {
  const normalized = value
    .replace(' ', 'T')
    .replace(',', '.')
    .replace(/(\.\d{3})\d+$/, '$1');
  const zoned = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(normalized) ? normalized : `${normalized}Z`;
  const date = new Date(zoned);
  if (Number.isNaN(date.getTime())) return value.replace('T', ' ');
  const parts = new Intl.DateTimeFormat('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(date);
  const part = (type: string) => parts.find((item) => item.type === type)?.value ?? '00';
  return `${part('hour')}:${part('minute')}:${part('second')},${String(date.getMilliseconds()).padStart(3, '0')}`;
};

const parseLine = (raw: string): ParsedLogLine => {
  const match = LOG_LINE_PATTERN.exec(raw);
  if (match === null) {
    return {raw, time: null, level: null, logger: null, message: raw, contextLevel: null};
  }
  return {
    raw,
    time: formatLogTime(match[1]),
    level: match[2] === 'WARN' ? 'WARNING' : match[2],
    logger: match[3],
    message: match[4],
    contextLevel: null,
  };
};

const parseLines = (raws: readonly string[]): ParsedLogLine[] => {
  let contextLevel: string | null = null;
  return raws.map((raw) => {
    const parsed = parseLine(raw);
    if (parsed.level !== null) contextLevel = parsed.level;
    return {...parsed, contextLevel};
  });
};

const levelClass = (level: string | null): string => {
  if (level === 'ERROR' || level === 'CRITICAL' || level === 'FATAL') return 'is-error';
  if (level === 'WARNING' || level === 'WARN') return 'is-warn';
  if (level === 'DEBUG') return 'is-debug';
  return 'is-info';
};

const unavailableText = (reason: SchedulerLogUnavailableReason | null): string => {
  if (reason === 'not_configured') {
    return '后端尚未配置 scheduler 日志文件。请设置 SCHEDULER_LOG_FILE，并让 app 与 scheduler 共享该目录。';
  }
  if (reason === 'unreadable') {
    return '日志文件存在但当前不可读。请检查 app 容器的只读挂载权限。';
  }
  return '日志文件尚未生成；scheduler 启动并写入后会自动出现。';
};

const formatUpdatedAt = (value: string | null): string => {
  if (value === null) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return date.toLocaleTimeString('zh-CN', {hour12: false});
};

const matchesFilter = (line: ParsedLogLine, filter: LogFilter, query: string): boolean => {
  const effectiveLevel = line.level ?? line.contextLevel;
  if (filter === 'warn' && !['ERROR', 'CRITICAL', 'FATAL', 'WARNING', 'WARN'].includes(effectiveLevel ?? '')) {
    return false;
  }
  if (query.length === 0) return true;
  return line.raw.toLowerCase().includes(query.toLowerCase());
};

export const SchedulerLogDrawer = ({onClose, onRequestError}: SchedulerLogDrawerProps) => {
  const [lines, setLines] = useState<readonly string[]>([]);
  const [available, setAvailable] = useState<boolean | null>(null);
  const [reason, setReason] = useState<SchedulerLogUnavailableReason | null>(null);
  const [truncated, setTruncated] = useState(false);
  const [updatedAt, setUpdatedAt] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [follow, setFollow] = useState(true);
  const [filter, setFilter] = useState<LogFilter>('all');
  const [query, setQuery] = useState('');
  const [copied, setCopied] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);

  const cursorRef = useRef<string | null>(null);
  const lineCountRef = useRef(0);
  const terminalRef = useRef<HTMLDivElement | null>(null);
  const drawerRef = useRef<HTMLDivElement | null>(null);
  const closeRef = useRef<HTMLButtonElement | null>(null);
  const requestErrorRef = useRef(onRequestError);
  const closeRefForPolling = useRef(onClose);

  useEffect(() => {
    requestErrorRef.current = onRequestError;
  }, [onRequestError]);

  useEffect(() => {
    closeRefForPolling.current = onClose;
  }, [onClose]);

  useEffect(() => {
    lineCountRef.current = lines.length;
  }, [lines]);

  useEffect(() => {
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    closeRef.current?.focus();
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, []);

  useEffect(() => {
    let disposed = false;
    let inFlight = false;
    let controller: AbortController | null = null;
    let timer = 0;

    setLines([]);
    setAvailable(null);
    setReason(null);
    setTruncated(false);
    setUpdatedAt(null);
    setLoading(true);
    setError(null);
    cursorRef.current = null;

    const load = async (initial: boolean) => {
      if (disposed || inFlight) return;
      inFlight = true;
      controller?.abort();
      controller = new AbortController();
      try {
        const page: SchedulerLogPage = await api.schedulerLogs(
          initial ? null : cursorRef.current,
          LOG_PAGE_SIZE,
          controller.signal,
        );
        if (disposed) return;
        cursorRef.current = page.next_cursor;
        setAvailable(page.available);
        setReason(page.reason);
        setUpdatedAt(page.updated_at);
        setLoading(false);
        setError(null);
        if (initial || page.reset) {
          setLines(page.lines.slice(-LOG_LINE_LIMIT));
          setTruncated(page.truncated || page.lines.length > LOG_LINE_LIMIT);
          return;
        }
        if (page.lines.length > 0) {
          if (lineCountRef.current + page.lines.length > LOG_LINE_LIMIT) setTruncated(true);
          setLines((current) => {
            const next = [...current, ...page.lines];
            return next.length > LOG_LINE_LIMIT ? next.slice(-LOG_LINE_LIMIT) : next;
          });
        }
        if (page.truncated) setTruncated(true);
      } catch (caught) {
        if (disposed) return;
        if (caught instanceof DOMException && caught.name === 'AbortError') return;
        if (caught instanceof ApiError) {
          if (caught.status === 401) {
            requestErrorRef.current(caught);
            closeRefForPolling.current();
            return;
          }
          if (caught.status === 403) {
            setError('当前账号没有查看原始调度日志的权限。');
          } else if (caught.status === 404) {
            setError('日志接口尚未部署，当前后端版本不支持原始日志。');
          } else {
            setError(`日志读取失败（HTTP ${caught.status}）。`);
          }
        } else {
          setError('无法连接日志接口，请稍后重试。');
        }
        setLoading(false);
      } finally {
        inFlight = false;
      }
    };

    void load(true);
    timer = window.setInterval(() => {
      if (document.visibilityState !== 'hidden') void load(false);
    }, LOG_POLL_MS);

    const handleVisibility = () => {
      if (document.visibilityState === 'visible') void load(false);
    };
    document.addEventListener('visibilitychange', handleVisibility);

    return () => {
      disposed = true;
      controller?.abort();
      window.clearInterval(timer);
      document.removeEventListener('visibilitychange', handleVisibility);
    };
  }, [reloadKey]);

  useEffect(() => {
    if (!follow) return;
    const terminal = terminalRef.current;
    if (terminal === null) return;
    terminal.scrollTop = terminal.scrollHeight;
  }, [follow, lines, filter, query]);

  useEffect(() => {
    if (!copied) return undefined;
    const timer = window.setTimeout(() => setCopied(false), 1600);
    return () => window.clearTimeout(timer);
  }, [copied]);

  const parsedLines = useMemo(() => parseLines(lines), [lines]);
  const visibleLines = useMemo(
    () => parsedLines.filter((line) => matchesFilter(line, filter, query)),
    [parsedLines, filter, query],
  );

  const handleScroll = useCallback(() => {
    const terminal = terminalRef.current;
    if (terminal === null) return;
    const distance = terminal.scrollHeight - terminal.scrollTop - terminal.clientHeight;
    setFollow(distance <= 28);
  }, []);

  const handleCopy = async () => {
    if (visibleLines.length === 0) return;
    try {
      await navigator.clipboard.writeText(visibleLines.map((line) => line.raw).join('\n'));
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };

  const handleKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      onClose();
      return;
    }
    if (event.key !== 'Tab' || drawerRef.current === null) return;
    const focusable = Array.from(
      drawerRef.current.querySelectorAll<HTMLElement>('button:not([disabled]), input, [href], [tabindex]:not([tabindex="-1"])'),
    ).filter((element) => element.offsetParent !== null);
    if (focusable.length === 0) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };

  const statusText = error !== null
    ? '读取异常'
    : available === false
      ? '无日志文件'
      : available === true
        ? 'LIVE'
        : '连接中';

  return createPortal(
    <div
      className="slog-overlay"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        className="slog-drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby="scheduler-log-title"
        ref={drawerRef}
        onKeyDown={handleKeyDown}
      >
        <header className="slog-header">
          <div className="slog-title">
            <Terminal aria-hidden="true" className="slog-title-icon" />
            <div>
              <h2 id="scheduler-log-title">运行日志</h2>
              <p>scheduler 原始日志 · 只读 · 每 2 秒 tail</p>
            </div>
          </div>
          <div className="slog-header-right">
            <span className={`slog-status ${error !== null ? 'is-error' : available === false ? 'is-offline' : 'is-live'}`}>
              <i />
              {statusText}
            </span>
            <span className="slog-updated">更新 {formatUpdatedAt(updatedAt)}</span>
            <button ref={closeRef} type="button" className="slog-close" onClick={onClose} aria-label="关闭运行日志">
              <X aria-hidden="true" />
            </button>
          </div>
        </header>

        <div className="slog-toolbar">
          <div className="slog-filters" role="group" aria-label="日志筛选">
            <button type="button" aria-pressed={filter === 'all'} onClick={() => setFilter('all')}>全部</button>
            <button type="button" aria-pressed={filter === 'warn'} onClick={() => setFilter('warn')}>异常</button>
          </div>
          <label className="slog-search">
            <Search aria-hidden="true" />
            <input
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="筛选 job_id / 关键词"
              aria-label="搜索日志"
            />
          </label>
          <div className="slog-toolbar-actions">
            <button
              type="button"
              className={`slog-follow ${follow ? 'is-on' : ''}`}
              aria-pressed={follow}
              onClick={() => {
                setFollow(true);
                const terminal = terminalRef.current;
                if (terminal !== null) terminal.scrollTop = terminal.scrollHeight;
              }}
            >
              <ArrowDownToLine aria-hidden="true" />
              {follow ? '跟随最新' : '已暂停跟随'}
            </button>
            <button type="button" className="slog-copy" onClick={() => void handleCopy()} disabled={visibleLines.length === 0}>
              <Copy aria-hidden="true" />
              {copied ? '已复制' : '复制'}
            </button>
          </div>
        </div>

        {error !== null ? (
          <div className="slog-state is-error" role="alert">
            <p>{error}</p>
            <button type="button" onClick={() => setReloadKey((value) => value + 1)}>重新读取</button>
          </div>
        ) : null}

        {available === false && error === null ? (
          <div className="slog-state">
            <Terminal aria-hidden="true" />
            <p>{unavailableText(reason)}</p>
            <button type="button" onClick={() => setReloadKey((value) => value + 1)}>重新检查</button>
          </div>
        ) : null}

        {available === true && lines.length === 0 && loading ? (
          <div className="slog-state">
            <p>正在读取原始日志…</p>
          </div>
        ) : null}

        {available === true && lines.length === 0 && !loading && error === null ? (
          <div className="slog-state">
            <p>日志文件为空，等待 scheduler 写入。</p>
          </div>
        ) : null}

        {lines.length > 0 ? (
          <div className="slog-terminal-wrap">
            <div
              ref={terminalRef}
              className="slog-terminal"
              role="log"
              aria-live="off"
              aria-label="调度器原始日志"
              tabIndex={0}
              onScroll={handleScroll}
            >
              {visibleLines.length === 0 ? (
                <p className="slog-no-match">没有匹配的日志行。</p>
              ) : visibleLines.map((line, index) => (
                <div key={`${line.raw}-${index}`} className={`slog-line ${levelClass(line.level)}`} title={line.raw}>
                  <span className="slog-time">{line.time ?? '—'}</span>
                  <span className="slog-level">{line.level ?? 'RAW'}</span>
                  <span className="slog-logger">{line.logger ?? ''}</span>
                  <span className="slog-message">{line.message}</span>
                </div>
              ))}
              {follow && <span className="slog-cursor" aria-hidden="true" />}
            </div>
            {!follow && (
              <button
                type="button"
                className="slog-return"
                onClick={() => {
                  setFollow(true);
                  const terminal = terminalRef.current;
                  if (terminal !== null) terminal.scrollTop = terminal.scrollHeight;
                }}
              >
                回到最新
              </button>
            )}
          </div>
        ) : null}

        <footer className="slog-footer">
          <span>显示 {visibleLines.length} / {lines.length} 行{truncated ? ' · 已截断较早日志' : ''}</span>
          <span>原始日志可能包含内部路径与来源地址，仅授权管理员可见</span>
        </footer>
      </div>
    </div>,
    document.body,
  );
};
