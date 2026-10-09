import {act, cleanup, renderHook} from '@testing-library/react';
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {api, ApiError, type MonitoringHealthRead} from './api';
import {routePaths} from './routes';
import {
  MONITORING_HEALTH_REFRESH_MS,
  MONITORING_HEALTH_SCHEDULER_REFRESH_MS,
  monitoringHealthRefreshMsForPath,
  useMonitoringHealth,
} from './useMonitoringHealth';

// 仅替换网络方法；保留真实 ApiError 与类型。
vi.mock('./api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./api')>();
  return {
    ...actual,
    api: {...actual.api, monitoringHealth: vi.fn()},
  };
});

const monitoringHealthOk: MonitoringHealthRead = {
  as_of: '2026-09-11T06:00:00Z',
  overall: 'ok',
  scheduler: {
    status: 'ok',
    last_heartbeat_at: '2026-09-11T05:59:30Z',
    age_seconds: 30,
    interval_seconds: 60,
    stale_after_seconds: 180,
    current_work: [],
    scheduled_jobs: [],
    recent_runs: [],
  },
  processing: {
    total: 0,
    classification_failed: 0,
    backlog_over_1h: 0,
    oldest_pending_age_seconds: null,
    last_run: {status: 'succeeded', started_at: '2026-09-11T05:58:00Z', finished_at: '2026-09-11T05:58:20Z', processed: 5, filtered: 1, failed: 0},
  },
  sources: [
    {
      source_id: 17,
      code: 'OFFICIAL-17',
      name: '官方风险源',
      state: 'ok',
      reason_code: 'success_observed',
      last_success_at: '2026-09-11T05:30:00Z',
      last_attempt_at: '2026-09-11T05:30:10Z',
      next_expected_at: '2026-09-11T06:00:00Z',
    },
  ],
};

const onRequestError = vi.fn();

interface HookOptions {
  readonly enabled?: boolean;
  readonly active?: boolean;
  readonly refreshVersion?: number;
  readonly refreshIntervalMs?: number;
}

interface HookProps {
  readonly enabled: boolean;
  readonly active: boolean;
  readonly refreshVersion: number;
  readonly refreshIntervalMs?: number;
}

const renderHealthHook = (options: HookOptions = {}) => renderHook(
  (props: HookProps) => useMonitoringHealth({...props, onRequestError}),
  {
    initialProps: {
      enabled: options.enabled ?? true,
      active: options.active ?? true,
      refreshVersion: options.refreshVersion ?? 0,
      ...(options.refreshIntervalMs === undefined ? {} : {refreshIntervalMs: options.refreshIntervalMs}),
    },
  },
);

const setVisibility = (state: 'visible' | 'hidden') => {
  vi.spyOn(document, 'visibilityState', 'get').mockReturnValue(state);
};

const flushEffects = async () => {
  // 在真实计时器下也可用：仅冲刷微任务队列，让在途 promise 链与 React 状态落地。
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
};

const dispatchVisibility = (state: 'visible' | 'hidden') => {
  setVisibility(state);
  act(() => {
    document.dispatchEvent(new Event('visibilitychange'));
  });
};

beforeEach(() => {
  vi.mocked(api.monitoringHealth).mockResolvedValue(monitoringHealthOk);
  onRequestError.mockClear();
  setVisibility('visible');
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.useRealTimers();
  vi.resetAllMocks();
});

describe('useMonitoringHealth 请求触发门控', () => {
  it('有权限、处于展示页且页面可见时立即请求一次并进入 ready', async () => {
    const {result} = renderHealthHook();
    await flushEffects();

    expect(api.monitoringHealth).toHaveBeenCalledTimes(1);
    expect(result.current).toEqual({status: 'ready', health: monitoringHealthOk});
  });

  it('无 source_status_view 权限（enabled=false）不发起诊断请求', async () => {
    const {result} = renderHealthHook({enabled: false});
    await flushEffects();

    expect(api.monitoringHealth).not.toHaveBeenCalled();
    expect(result.current).toEqual({status: 'loading'});
  });

  it('非展示路由（active=false）不发起诊断请求', async () => {
    const {result} = renderHealthHook({active: false});
    await flushEffects();

    expect(api.monitoringHealth).not.toHaveBeenCalled();
    expect(result.current).toEqual({status: 'loading'});
  });
});

describe('useMonitoringHealth 可见性与60秒周期', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  it('页面可见时每60秒刷新一次', async () => {
    renderHealthHook();
    await flushEffects();
    expect(api.monitoringHealth).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(MONITORING_HEALTH_REFRESH_MS);
    });
    expect(api.monitoringHealth).toHaveBeenCalledTimes(2);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(MONITORING_HEALTH_REFRESH_MS);
    });
    expect(api.monitoringHealth).toHaveBeenCalledTimes(3);
  });

  it('标签页隐藏时暂停轮询，重新可见立即刷新并恢复周期', async () => {
    renderHealthHook();
    await flushEffects();
    expect(api.monitoringHealth).toHaveBeenCalledTimes(1);

    dispatchVisibility('hidden');
    await act(async () => {
      await vi.advanceTimersByTimeAsync(MONITORING_HEALTH_REFRESH_MS * 5);
    });
    // 隐藏期间不得继续轮询
    expect(api.monitoringHealth).toHaveBeenCalledTimes(1);

    dispatchVisibility('visible');
    await flushEffects();
    // 重新可见立即刷新
    expect(api.monitoringHealth).toHaveBeenCalledTimes(2);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(MONITORING_HEALTH_REFRESH_MS);
    });
    // 恢复60秒周期
    expect(api.monitoringHealth).toHaveBeenCalledTimes(3);
  });

  it('挂载时页面已隐藏则不发起首次请求，可见后才开始', async () => {
    setVisibility('hidden');
    renderHealthHook();
    await flushEffects();
    expect(api.monitoringHealth).not.toHaveBeenCalled();

    dispatchVisibility('visible');
    await flushEffects();
    expect(api.monitoringHealth).toHaveBeenCalledTimes(1);
  });
});

describe('useMonitoringHealth 错误分流', () => {
  it('403 只隐藏诊断，不触发统一错误回调', async () => {
    vi.mocked(api.monitoringHealth).mockRejectedValue(new ApiError(403, '权限不足'));
    const {result} = renderHealthHook();
    await flushEffects();

    expect(result.current).toEqual({status: 'hidden'});
    expect(onRequestError).not.toHaveBeenCalled();
  });

  it('401 交给 App 统一会话失效处理，不伪装成 unknown', async () => {
    vi.mocked(api.monitoringHealth).mockRejectedValue(new ApiError(401, '登录已失效'));
    const {result} = renderHealthHook();
    await flushEffects();

    expect(onRequestError).toHaveBeenCalledTimes(1);
    const [caught] = onRequestError.mock.calls[0] as [ApiError];
    expect(caught).toBeInstanceOf(ApiError);
    expect(caught.status).toBe(401);
    expect(result.current).toEqual({status: 'loading'});
  });

  it('200 但载荷不符合 MonitoringHealthRead 契约时降级 unknown，不得进入 ready', async () => {
    // 故意用双断言模拟类型层无法表达的运行时非法载荷（如 FastAPI {detail:...} 携带 200）。
    const invalidPayload = {detail: 'Unhandled deterministic API fixture'} as unknown as MonitoringHealthRead;
    vi.mocked(api.monitoringHealth).mockResolvedValue(invalidPayload);
    const {result} = renderHealthHook();
    await flushEffects();

    expect(result.current).toEqual({status: 'unknown'});
    expect(onRequestError).not.toHaveBeenCalled();
  });

  it('503 等其他失败展示 unknown，不得把旧 ok 状态当作正常显示', async () => {
    vi.mocked(api.monitoringHealth)
      .mockResolvedValueOnce(monitoringHealthOk)
      .mockRejectedValueOnce(new ApiError(503, '诊断服务不可用'));
    const {result} = renderHealthHook();
    await flushEffects();
    expect(result.current).toEqual({status: 'ready', health: monitoringHealthOk});

    dispatchVisibility('hidden');
    dispatchVisibility('visible');
    await flushEffects();

    expect(result.current).toEqual({status: 'unknown'});
    expect(onRequestError).not.toHaveBeenCalled();
  });
});

describe('useMonitoringHealth 竞态与清理', () => {
  // 行为变化（相对旧的“允许重叠请求 + 序号淘汰”设计）：同一 effect 内最多一个在途请求，
  // 重新可见时的立即刷新若撞上在途请求会跳过，原响应正常采纳；跨 effect（refreshVersion/路由/周期
  // 重建）仍由 abort + 序号保证新结论不被旧响应覆盖。下一条测试锁定重建路径的丢弃语义。
  it('重新可见时在途请求未完成则跳过重叠请求，原响应正常采纳', async () => {
    let resolveFirst!: (value: MonitoringHealthRead) => void;
    const degradedHealth: MonitoringHealthRead = {...monitoringHealthOk, overall: 'degraded'};
    vi.mocked(api.monitoringHealth).mockImplementationOnce(() => new Promise<MonitoringHealthRead>((resolve) => { resolveFirst = resolve; }));

    const {result} = renderHealthHook();
    await flushEffects();
    expect(result.current).toEqual({status: 'loading'});
    const [signal] = vi.mocked(api.monitoringHealth).mock.calls[0] as [AbortSignal];

    // 隐藏再可见：仍有在途请求，立即刷新被跳过，不新增并发请求、不打断原请求。
    dispatchVisibility('hidden');
    dispatchVisibility('visible');
    await flushEffects();
    expect(api.monitoringHealth).toHaveBeenCalledTimes(1);
    expect(signal.aborted).toBe(false);

    // 原响应正常采纳，不因曾隐藏/重现被序号误杀。
    await act(async () => {
      resolveFirst(degradedHealth);
    });
    expect(result.current).toEqual({status: 'ready', health: degradedHealth});
  });

  it('卸载时取消在途请求并停止后续轮询', async () => {
    vi.useFakeTimers();
    let resolveFirst!: (value: MonitoringHealthRead) => void;
    vi.mocked(api.monitoringHealth).mockImplementation(() => new Promise<MonitoringHealthRead>((resolve) => { resolveFirst = resolve; }));
    const {unmount} = renderHealthHook();
    await flushEffects();

    const [signal] = vi.mocked(api.monitoringHealth).mock.calls[0] as [AbortSignal];
    unmount();

    expect(signal.aborted).toBe(true);
    const callsAtUnmount = vi.mocked(api.monitoringHealth).mock.calls.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(MONITORING_HEALTH_REFRESH_MS * 3);
    });
    expect(vi.mocked(api.monitoringHealth).mock.calls.length).toBe(callsAtUnmount);
  });
});

describe('useMonitoringHealth refreshVersion 即时刷新', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  it('refreshVersion 递增时立即刷新并重置 60 秒周期', async () => {
    const {result, rerender} = renderHealthHook();
    await flushEffects();
    expect(api.monitoringHealth).toHaveBeenCalledTimes(1);
    expect(result.current).toEqual({status: 'ready', health: monitoringHealthOk});

    // 距首次请求仅过半个周期
    await act(async () => {
      await vi.advanceTimersByTimeAsync(MONITORING_HEALTH_REFRESH_MS / 2);
    });
    expect(api.monitoringHealth).toHaveBeenCalledTimes(1);

    // refreshVersion 递增：不等周期到点，立即发起新请求
    rerender({enabled: true, active: true, refreshVersion: 1});
    await flushEffects();
    expect(api.monitoringHealth).toHaveBeenCalledTimes(2);

    // 周期从刷新时刻重新计时：从首次请求算满 60 秒也不得触发旧表
    await act(async () => {
      await vi.advanceTimersByTimeAsync(MONITORING_HEALTH_REFRESH_MS / 2);
    });
    expect(api.monitoringHealth).toHaveBeenCalledTimes(2);

    // 距刷新满 60 秒才触发下一轮
    await act(async () => {
      await vi.advanceTimersByTimeAsync(MONITORING_HEALTH_REFRESH_MS / 2);
    });
    expect(api.monitoringHealth).toHaveBeenCalledTimes(3);
  });

  it('refreshVersion 刷新后旧的在途响应晚到也不覆盖新结果', async () => {
    const staleHealth: MonitoringHealthRead = {...monitoringHealthOk, overall: 'degraded'};
    let resolveStale: ((value: MonitoringHealthRead) => void) | undefined;
    vi.mocked(api.monitoringHealth)
      .mockImplementationOnce(() => new Promise<MonitoringHealthRead>((resolve) => {
        resolveStale = resolve;
      }))
      .mockResolvedValueOnce(monitoringHealthOk);

    const {result, rerender} = renderHealthHook();
    await flushEffects();
    expect(api.monitoringHealth).toHaveBeenCalledTimes(1);
    expect(result.current).toEqual({status: 'loading'});

    // refreshVersion 递增：新请求立即发出并成功
    rerender({enabled: true, active: true, refreshVersion: 1});
    await flushEffects();
    expect(api.monitoringHealth).toHaveBeenCalledTimes(2);
    expect(result.current).toEqual({status: 'ready', health: monitoringHealthOk});

    // 旧响应此刻才返回：AbortController/序号保护必须丢弃它
    await act(async () => {
      if (resolveStale === undefined) throw new Error('旧请求未处于在途状态');
      resolveStale(staleHealth);
    });
    expect(result.current).toEqual({status: 'ready', health: monitoringHealthOk});
    expect(onRequestError).not.toHaveBeenCalled();
  });

  it('refreshVersion 重建后旧 effect 的 finally 不阻塞新 effect：下一周期照常发起请求', async () => {
    const staleHealth: MonitoringHealthRead = {...monitoringHealthOk, overall: 'degraded'};
    let resolveStale!: (value: MonitoringHealthRead) => void;
    vi.mocked(api.monitoringHealth)
      .mockImplementationOnce(() => new Promise<MonitoringHealthRead>((resolve) => {
        resolveStale = resolve;
      }))
      .mockResolvedValue(monitoringHealthOk);

    const {result, rerender} = renderHealthHook();
    await flushEffects();
    expect(api.monitoringHealth).toHaveBeenCalledTimes(1);
    const [staleSignal] = vi.mocked(api.monitoringHealth).mock.calls[0] as [AbortSignal];

    // refreshVersion 递增：旧 effect 清理 abort 旧 controller 并推进序号，新 effect 立即刷新。
    rerender({enabled: true, active: true, refreshVersion: 1});
    await flushEffects();
    expect(staleSignal.aborted).toBe(true);
    expect(api.monitoringHealth).toHaveBeenCalledTimes(2);
    expect(result.current).toEqual({status: 'ready', health: monitoringHealthOk});

    // 旧响应晚到：被 disposed/序号丢弃，不得覆盖新结论。
    await act(async () => {
      resolveStale(staleHealth);
    });
    expect(result.current).toEqual({status: 'ready', health: monitoringHealthOk});

    // 旧 effect 的 finally 此刻已执行，但 guard 属于旧 effect 闭包：新 effect 下一周期必须照常发请求。
    await act(async () => {
      await vi.advanceTimersByTimeAsync(MONITORING_HEALTH_REFRESH_MS);
    });
    expect(api.monitoringHealth).toHaveBeenCalledTimes(3);
  });

  it('refreshVersion 不变时不额外刷新', async () => {
    const {rerender} = renderHealthHook();
    await flushEffects();
    expect(api.monitoringHealth).toHaveBeenCalledTimes(1);

    rerender({enabled: true, active: true, refreshVersion: 0});
    await flushEffects();
    expect(api.monitoringHealth).toHaveBeenCalledTimes(1);
  });

  it('enabled=false 时 refreshVersion 递增不发请求', async () => {
    const {rerender} = renderHealthHook({enabled: false});
    await flushEffects();
    expect(api.monitoringHealth).not.toHaveBeenCalled();

    rerender({enabled: false, active: true, refreshVersion: 1});
    await flushEffects();
    expect(api.monitoringHealth).not.toHaveBeenCalled();
  });

  it('active=false 时 refreshVersion 递增不发请求', async () => {
    const {rerender} = renderHealthHook({active: false});
    await flushEffects();
    expect(api.monitoringHealth).not.toHaveBeenCalled();

    rerender({enabled: true, active: false, refreshVersion: 1});
    await flushEffects();
    expect(api.monitoringHealth).not.toHaveBeenCalled();
  });

  it('页面隐藏时 refreshVersion 递增不发请求，恢复可见后按可见性机制刷新', async () => {
    const {rerender} = renderHealthHook();
    await flushEffects();
    expect(api.monitoringHealth).toHaveBeenCalledTimes(1);

    dispatchVisibility('hidden');
    rerender({enabled: true, active: true, refreshVersion: 1});
    await flushEffects();
    expect(api.monitoringHealth).toHaveBeenCalledTimes(1);

    dispatchVisibility('visible');
    await flushEffects();
    expect(api.monitoringHealth).toHaveBeenCalledTimes(2);
  });
});

describe('useMonitoringHealth 可配置诊断周期（scheduler 5 秒）', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  const SCHEDULER_INTERVAL_MS = 5_000;

  it('导出常量与 scheduler 展示页的 5 秒约定一致', () => {
    expect(MONITORING_HEALTH_SCHEDULER_REFRESH_MS).toBe(SCHEDULER_INTERVAL_MS);
    expect(MONITORING_HEALTH_REFRESH_MS).toBe(60_000);
  });

  it('按路由唯一映射轮询周期：/scheduler=5 秒，其余展示页=默认 60 秒', () => {
    expect(monitoringHealthRefreshMsForPath(routePaths.scheduler)).toBe(SCHEDULER_INTERVAL_MS);
    expect(monitoringHealthRefreshMsForPath(routePaths.overview)).toBe(MONITORING_HEALTH_REFRESH_MS);
    expect(monitoringHealthRefreshMsForPath(routePaths.sources)).toBe(MONITORING_HEALTH_REFRESH_MS);
    expect(monitoringHealthRefreshMsForPath(routePaths.scheduler + '/unmatched')).toBe(MONITORING_HEALTH_REFRESH_MS);
    expect(monitoringHealthRefreshMsForPath(routePaths.rules)).toBe(MONITORING_HEALTH_REFRESH_MS);
  });

  it('refreshIntervalMs=5000 时每 5 秒刷新一次', async () => {
    renderHealthHook({refreshIntervalMs: SCHEDULER_INTERVAL_MS});
    await flushEffects();
    expect(api.monitoringHealth).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(SCHEDULER_INTERVAL_MS);
    });
    expect(api.monitoringHealth).toHaveBeenCalledTimes(2);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(SCHEDULER_INTERVAL_MS);
    });
    expect(api.monitoringHealth).toHaveBeenCalledTimes(3);
  });

  it('5000 切换到 60000 时重建唯一计时器：旧 5 秒表停止，新表按 60 秒从重建时刻计时', async () => {
    const {rerender} = renderHealthHook({refreshIntervalMs: SCHEDULER_INTERVAL_MS});
    await flushEffects();
    expect(api.monitoringHealth).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(SCHEDULER_INTERVAL_MS);
    });
    expect(api.monitoringHealth).toHaveBeenCalledTimes(2);

    // 周期切换：旧 effect 清理（含唯一 interval 计时器），立即按新周期发起一次并重建计时器。
    rerender({enabled: true, active: true, refreshVersion: 0, refreshIntervalMs: MONITORING_HEALTH_REFRESH_MS});
    await flushEffects();
    expect(api.monitoringHealth).toHaveBeenCalledTimes(3);

    // 旧 5 秒表必须已被清理：再走一个 5 秒不得触发请求。
    await act(async () => {
      await vi.advanceTimersByTimeAsync(SCHEDULER_INTERVAL_MS);
    });
    expect(api.monitoringHealth).toHaveBeenCalledTimes(3);

    // 新 60 秒表从重建时刻计时：再走剩余 55 秒才触发。
    await act(async () => {
      await vi.advanceTimersByTimeAsync(MONITORING_HEALTH_REFRESH_MS - SCHEDULER_INTERVAL_MS);
    });
    expect(api.monitoringHealth).toHaveBeenCalledTimes(4);
  });

  it('60000 切换到 5000 时立即按新周期刷新，不残留下一条 60 秒计时器', async () => {
    const {rerender} = renderHealthHook();
    await flushEffects();
    expect(api.monitoringHealth).toHaveBeenCalledTimes(1);

    rerender({enabled: true, active: true, refreshVersion: 0, refreshIntervalMs: SCHEDULER_INTERVAL_MS});
    await flushEffects();
    expect(api.monitoringHealth).toHaveBeenCalledTimes(2);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(SCHEDULER_INTERVAL_MS);
    });
    expect(api.monitoringHealth).toHaveBeenCalledTimes(3);

    // 旧 60 秒表若残留：走完 60 秒会出现额外请求（3 → 5），这里按 5 秒节奏增长。
    await act(async () => {
      await vi.advanceTimersByTimeAsync(SCHEDULER_INTERVAL_MS);
    });
    expect(api.monitoringHealth).toHaveBeenCalledTimes(4);
  });

  it('路由退出（active=false）停止轮询并取消在途请求；恢复展示后立即刷新且仍按 5 秒周期', async () => {
    const {rerender} = renderHealthHook({refreshIntervalMs: SCHEDULER_INTERVAL_MS});
    await flushEffects();
    expect(api.monitoringHealth).toHaveBeenCalledTimes(1);
    const [routeSignal] = vi.mocked(api.monitoringHealth).mock.calls[0] as [AbortSignal];

    // 离开展示路由：清理唯一计时器并 abort 在途请求，后续 15 秒不得再请求。
    rerender({enabled: true, active: false, refreshVersion: 0});
    await flushEffects();
    expect(routeSignal.aborted).toBe(true);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(SCHEDULER_INTERVAL_MS * 3);
    });
    expect(api.monitoringHealth).toHaveBeenCalledTimes(1);

    // 回到展示路由：立即刷新并按路由约定的 5 秒周期继续。
    rerender({enabled: true, active: true, refreshVersion: 0, refreshIntervalMs: SCHEDULER_INTERVAL_MS});
    await flushEffects();
    expect(api.monitoringHealth).toHaveBeenCalledTimes(2);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(SCHEDULER_INTERVAL_MS);
    });
    expect(api.monitoringHealth).toHaveBeenCalledTimes(3);
  });

  it('慢请求跨多个周期 tick 不增加并发；原响应正常采纳，完成后下一 tick 继续', async () => {
    const slowFirst: MonitoringHealthRead = {...monitoringHealthOk, overall: 'degraded'};
    let resolveSlow!: (value: MonitoringHealthRead) => void;
    vi.mocked(api.monitoringHealth)
      .mockImplementationOnce(() => new Promise<MonitoringHealthRead>((resolve) => { resolveSlow = resolve; }))
      .mockResolvedValueOnce(monitoringHealthOk);

    const {result} = renderHealthHook({refreshIntervalMs: SCHEDULER_INTERVAL_MS});
    await flushEffects();
    expect(api.monitoringHealth).toHaveBeenCalledTimes(1);
    const [slowSignal] = vi.mocked(api.monitoringHealth).mock.calls[0] as [AbortSignal];

    // 两个 5 秒 tick 都落在未完成的请求上：跳过而不是重叠发起，也不打断原请求。
    await act(async () => {
      await vi.advanceTimersByTimeAsync(SCHEDULER_INTERVAL_MS * 2);
    });
    expect(api.monitoringHealth).toHaveBeenCalledTimes(1);
    expect(slowSignal.aborted).toBe(false);

    // 不饥饿：原响应正常采纳，慢请求不会永远拿不到结论。
    await act(async () => {
      resolveSlow(slowFirst);
    });
    expect(result.current).toEqual({status: 'ready', health: slowFirst});

    // 完成后下一 tick 恢复轮询：发起新请求并采纳最新结论。
    await act(async () => {
      await vi.advanceTimersByTimeAsync(SCHEDULER_INTERVAL_MS);
    });
    expect(api.monitoringHealth).toHaveBeenCalledTimes(2);
    expect(result.current).toEqual({status: 'ready', health: monitoringHealthOk});
  });
});
