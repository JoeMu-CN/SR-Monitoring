import {act, cleanup, renderHook} from '@testing-library/react';
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {api, ApiError, type MonitoringHealthRead} from './api';
import {MONITORING_HEALTH_REFRESH_MS, useMonitoringHealth} from './useMonitoringHealth';

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
}

const renderHealthHook = (options: HookOptions = {}) => renderHook(() => useMonitoringHealth({
  enabled: options.enabled ?? true,
  active: options.active ?? true,
  onRequestError,
}));

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
  it('慢的旧响应晚到不覆盖新一轮状态', async () => {
    let resolveFirst!: (value: MonitoringHealthRead) => void;
    const staleHealth: MonitoringHealthRead = {...monitoringHealthOk, overall: 'degraded'};
    vi.mocked(api.monitoringHealth)
      .mockImplementationOnce(() => new Promise<MonitoringHealthRead>((resolve) => { resolveFirst = resolve; }))
      .mockResolvedValueOnce(monitoringHealthOk);

    const {result} = renderHealthHook();
    await flushEffects();
    expect(result.current).toEqual({status: 'loading'});

    // 触发第二次（重新可见立即刷新）
    dispatchVisibility('hidden');
    dispatchVisibility('visible');
    await flushEffects();
    expect(result.current).toEqual({status: 'ready', health: monitoringHealthOk});

    // 旧响应此时才返回：必须被请求序号丢弃
    await act(async () => {
      resolveFirst(staleHealth);
    });
    expect(result.current).toEqual({status: 'ready', health: monitoringHealthOk});
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
