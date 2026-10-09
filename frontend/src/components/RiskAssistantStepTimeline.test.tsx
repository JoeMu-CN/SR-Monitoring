import {act, cleanup, render, renderHook, screen} from '@testing-library/react';
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {api, type AgentStepKind, type AgentStepRead, type AgentRunStepsRead} from '../api';
import {RiskAssistantStepTimeline} from './RiskAssistantStepTimeline';
import {
  REVEAL_INTERVAL_MS,
  STEP_POLL_INTERVAL_MS,
  mergeStepRows,
  useRiskAssistantStepTimeline,
  type StepRow,
} from './useRiskAssistantStepTimeline';

// 仅替换轮询网络方法；保留真实类型与其余 api 成员。
vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>();
  return {
    ...actual,
    api: {...actual.api, chatSteps: vi.fn()},
  };
});

const step = (index: number, kind: AgentStepKind, tool: string | null = null): AgentStepRead => ({
  index,
  kind,
  tool,
  detail: null,
});

const snapshot = (steps: AgentStepRead[]) => ({
  run_token: 'test-run',
  status: 'running' as const,
  steps,
});

const rowKeys = (rows: readonly StepRow[]) => rows.map((row) => row.key);

beforeEach(() => {
  vi.useFakeTimers();
  vi.mocked(api.chatSteps).mockResolvedValue(snapshot([]));
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.resetAllMocks();
});

// 缺陷回归：旧实现把 tool_start 与 tool_done 渲染成两行，并在每行前放旋转 sync 图标，
// 导致「开始/完成各占一行」且历史步骤永久转动。合并规则必须以真实事件时序为准。
describe('mergeStepRows 真实事件时序合并', () => {
  it('同一工具的 tool_start 与 tool_done 原位合并成一行并标完成', () => {
    const rows = mergeStepRows([
      step(1, 'analyzing'),
      step(2, 'tool_start', 'query_suppliers'),
      step(3, 'tool_done', 'query_suppliers'),
      step(4, 'finalizing'),
    ]);

    // 4 条真实事件合并为 3 行：理解问题 / 一次工具调用 / 生成回答。
    expect(rows).toHaveLength(3);
    expect(rows.map((row) => row.label)).toEqual([
      '正在理解问题',
      '检索重点供应商、地点与产品',
      '正在生成回答',
    ]);
    // 已进入下一阶段的历史行按证据标完成；只有当前正在执行的「生成回答」保持运行态。
    expect(rows.map((row) => row.done)).toEqual([true, true, false]);
  });

  it('同一工具连续两次调用各自成唯一行，不按工具名全局去重', () => {
    const rows = mergeStepRows([
      step(1, 'tool_start', 'query_suppliers'),
      step(2, 'tool_done', 'query_suppliers'),
      step(3, 'tool_start', 'query_suppliers'),
      step(4, 'tool_done', 'query_suppliers'),
    ]);

    expect(rows).toHaveLength(2);
    expect(new Set(rowKeys(rows)).size).toBe(2);
    expect(rows.every((row) => row.done)).toBe(true);
  });

  it('analyzing 进入下一真实阶段后标完成，只有当前执行行保持运行态', () => {
    const rows = mergeStepRows([step(1, 'analyzing'), step(2, 'tool_start', 'query_suppliers')]);

    expect(rows).toHaveLength(2);
    expect(rows[0].done).toBe(true);
    expect(rows[1].done).toBe(false);
  });

  it('tool_done 缺少匹配的 tool_start 时仍单独成行并标完成，不丢事件', () => {
    const rows = mergeStepRows([step(1, 'tool_done', 'query_current_alerts')]);

    expect(rows).toHaveLength(1);
    expect(rows[0].done).toBe(true);
    expect(rows[0].label).toBe('检索当前有效 P1–P4 风险提醒');
  });

  it('同一快照重复合并结果完全一致，不产生重复行', () => {
    const steps = [
      step(1, 'analyzing'),
      step(2, 'tool_start', 'query_suppliers'),
      step(3, 'tool_done', 'query_suppliers'),
    ];

    expect(mergeStepRows(steps)).toEqual(mergeStepRows(steps));
  });
});

describe('RiskAssistantStepTimeline 步骤行渲染', () => {
  const renderTimeline = (rows: readonly StepRow[]) =>
    render(<RiskAssistantStepTimeline rows={rows} />);

  it('完成行末尾是文本对钩，且整条时间线没有任何行首图标', () => {
    const {container} = renderTimeline(mergeStepRows([
      step(1, 'analyzing'),
      step(2, 'tool_start', 'query_suppliers'),
      step(3, 'tool_done', 'query_suppliers'),
    ]));

    expect(screen.getAllByTestId('execution-step-row')).toHaveLength(2);
    expect(screen.getAllByTestId('execution-step-check')).toHaveLength(2);
    // 前缀图标是本次要根除的缺陷：整条时间线不得再出现任何 material 图标或旋转元素。
    expect(container.querySelector('.material-symbols-outlined')).toBeNull();
    expect(container.querySelector('.animate-spin')).toBeNull();
  });

  it('运行行文字后是三个真实句点，逐点错峰且尊重 prefers-reduced-motion', () => {
    renderTimeline(mergeStepRows([step(1, 'tool_start', 'query_suppliers')]));

    const dots = screen.getByTestId('execution-step-dots').querySelectorAll(
      '[data-testid="execution-step-dot"]',
    );
    expect(dots).toHaveLength(3);
    expect(Array.from(dots).map((dot) => dot.textContent)).toEqual(['.', '.', '.']);
    expect(Array.from(dots).map((dot) => (dot as HTMLElement).style.animationDelay)).toEqual([
      '0ms',
      '150ms',
      '300ms',
    ]);
    Array.from(dots).forEach((dot) => {
      expect(dot).toHaveClass('motion-reduce:animate-none');
    });
    // 正在执行的行不得出现完成对钩。
    expect(screen.queryByTestId('execution-step-check')).toBeNull();
  });

  it('还没有任何真实步骤时只显示运行态文案与跳动点，不伪造步骤行', () => {
    renderTimeline([]);

    expect(screen.queryByTestId('execution-step-row')).toBeNull();
    expect(screen.getByTestId('execution-step-dots')).toBeInTheDocument();
  });
});

// 轮询与展开时序：必须按真实事件逐个新增行，而不是一次性堆出整个快照。
describe('useRiskAssistantStepTimeline 轮询与渐进展开', () => {
  const threeStepSnapshot = snapshot([
    step(1, 'analyzing'),
    step(2, 'tool_start', 'query_suppliers'),
    step(3, 'tool_start', 'query_current_alerts'),
  ]);

  const startTimeline = () => {
    const view = renderHook(() => useRiskAssistantStepTimeline());
    act(() => view.result.current.start('run-1'));
    return view;
  };

  const advance = async (ms: number) => {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(ms);
    });
  };

  // 渐进展开的节拍由 effect 排程，必须让 React 在每一步之间提交一次；
  // 因此这里逐拍推进，而不是一次性跳完整个窗口。
  const revealAllAndPollAgain = async () => {
    await advance(STEP_POLL_INTERVAL_MS);
    await advance(REVEAL_INTERVAL_MS);
    await advance(REVEAL_INTERVAL_MS);
  };

  it('同一次快照的多条事件按 index 顺序逐个新增行，不一次堆出整份快照', async () => {
    vi.mocked(api.chatSteps).mockResolvedValue(threeStepSnapshot);
    const {result} = startTimeline();

    await advance(STEP_POLL_INTERVAL_MS);
    // 快照已到达，但只先展开第一行。
    expect(result.current.rows).toHaveLength(1);
    expect(result.current.rows[0].done).toBe(true);

    await advance(REVEAL_INTERVAL_MS);
    expect(result.current.rows).toHaveLength(2);

    await advance(REVEAL_INTERVAL_MS);
    expect(result.current.rows).toHaveLength(3);
    expect(result.current.rows[2].done).toBe(false);
  });

  it('重复轮询同一份快照不重复追加行', async () => {
    vi.mocked(api.chatSteps).mockResolvedValue(threeStepSnapshot);
    const {result} = startTimeline();

    await revealAllAndPollAgain();
    // 再走两轮轮询：同一份快照既不重复追加，也不让行数回退。
    await advance(STEP_POLL_INTERVAL_MS * 2);

    expect(vi.mocked(api.chatSteps).mock.calls.length).toBeGreaterThan(1);
    expect(result.current.rows).toHaveLength(3);
    expect(new Set(rowKeys(result.current.rows)).size).toBe(3);
  });

  it('较旧的乱序响应晚到时不让步骤回退', async () => {
    let call = 0;
    vi.mocked(api.chatSteps).mockImplementation(() => {
      call += 1;
      // 第一轮拿到 3 条事件，随后的旧响应只带回 1 条事件。
      return Promise.resolve(call === 1 ? threeStepSnapshot : snapshot([step(1, 'analyzing')]));
    });
    const {result} = startTimeline();

    await revealAllAndPollAgain();
    // 第二轮轮询落地的是更短的旧快照。
    await advance(STEP_POLL_INTERVAL_MS);

    expect(vi.mocked(api.chatSteps).mock.calls.length).toBe(2);
    expect(result.current.rows).toHaveLength(3);
  });

  it('运行令牌失效后，在途旧响应既不写入步骤也不再续排轮询', async () => {
    let release: ((value: AgentRunStepsRead) => void) | null = null;
    vi.mocked(api.chatSteps).mockImplementation(() => new Promise<AgentRunStepsRead>((resolve) => {
      release = resolve;
    }));
    const {result} = startTimeline();

    await advance(STEP_POLL_INTERVAL_MS);
    expect(vi.mocked(api.chatSteps).mock.calls.length).toBe(1);

    act(() => result.current.stop());
    expect(result.current.rows).toHaveLength(0);
    expect(vi.getTimerCount()).toBe(0);

    // 旧轮询的响应此刻才落地：必须被令牌校验丢弃，不污染新一轮运行。
    await act(async () => {
      release?.(threeStepSnapshot);
      await Promise.resolve();
    });
    expect(result.current.rows).toHaveLength(0);

    // 旧响应不得把轮询续起来。
    await advance(STEP_POLL_INTERVAL_MS * 2);
    expect(vi.mocked(api.chatSteps).mock.calls.length).toBe(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('组件卸载后停止轮询并清理计时器', async () => {
    vi.mocked(api.chatSteps).mockResolvedValue(threeStepSnapshot);
    const view = startTimeline();

    await advance(STEP_POLL_INTERVAL_MS);
    expect(vi.getTimerCount()).toBeGreaterThan(0);

    view.unmount();

    expect(vi.getTimerCount()).toBe(0);
  });
});