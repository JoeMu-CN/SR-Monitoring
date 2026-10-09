import {act, cleanup, fireEvent, render, screen, within} from '@testing-library/react';
import {createElement, type HTMLAttributes, type ReactNode} from 'react';
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import type {MonitoringHealthRead} from '../api';
import type {MonitoringHealthSnapshot} from '../useMonitoringHealth';
import {SchedulerLiveView} from './SchedulerLiveView';

// 只构造契约允许的字段：不注入信号标题/正文、异常文本或虚构日志。
type Scheduler = MonitoringHealthRead['scheduler'];
type SchedulerWork = Scheduler['current_work'][number];
type ScheduledJob = Scheduler['scheduled_jobs'][number];
type RecentRun = Scheduler['recent_runs'][number];

// 减动方向由 mock 控制：motion 的 hook 在 jsdom 中依赖 matchMedia，
// 直接 mock 才能确定性覆盖「减少动效」与「默认动效」两个方向（范式同 RuleEnginePipeline.test.tsx）。
// 投影动画的 motion.div 替换为轻量 DOM 适配器：保留布局与样式、捕获完成回调，
// 由测试显式触发 onAnimationComplete，不依赖 jsdom 未实现的动画引擎。
const motionControl = vi.hoisted(() => ({reduced: false}));
const transferMotion = vi.hoisted(() => ({completes: [] as Array<() => void>}));

vi.mock('motion/react', async (importOriginal) => {
  const actual = await importOriginal<typeof import('motion/react')>();
  return {
    ...actual,
    useReducedMotion: () => motionControl.reduced,
    motion: {
      button: actual.motion.button,
      div: ({initial, animate, transition, onAnimationComplete, children, ...domProps}: {
        readonly initial?: unknown;
        readonly animate?: unknown;
        readonly transition?: unknown;
        readonly onAnimationComplete?: () => void;
        readonly children?: ReactNode;
        readonly [key: string]: unknown;
      }) => {
        void initial;
        void animate;
        void transition;
        if (typeof onAnimationComplete === 'function') transferMotion.completes.push(onAnimationComplete);
        return createElement('div', domProps as HTMLAttributes<HTMLDivElement>, children);
      },
    },
  };
});

const workItem = (overrides: Partial<SchedulerWork> = {}): SchedulerWork => ({
  kind: 'source_collection',
  job_key: 'collect:17',
  source_id: 17,
  source_name: '官方风险源',
  stage: 'collecting',
  started_at: '2026-09-11T05:47:00Z',
  item_id: null,
  ...overrides,
});

const scheduledJob = (overrides: Partial<ScheduledJob> = {}): ScheduledJob => ({
  job_id: 'collect:17',
  name: '官方风险源 采集',
  next_run_at: '2026-09-11T06:30:00Z',
  ...overrides,
});

const recentRun = (overrides: Partial<RecentRun> = {}): RecentRun => ({
  id: 1,
  job_id: 'collect:17',
  name: '官方风险源 采集',
  status: 'completed',
  scheduled_run_at: '2026-09-11T05:30:00Z',
  started_at: '2026-09-11T05:30:02Z',
  finished_at: '2026-09-11T05:30:14Z',
  ...overrides,
});

const readySnapshot = (schedulerOverrides: Partial<Scheduler> = {}): MonitoringHealthSnapshot => ({
  status: 'ready',
  health: {
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
      ...schedulerOverrides,
    },
    processing: {
      total: 0,
      classification_failed: 0,
      backlog_over_1h: 0,
      oldest_pending_age_seconds: null,
      last_run: {status: 'succeeded', started_at: '2026-09-11T05:58:00Z', finished_at: '2026-09-11T05:58:20Z', processed: 5, filtered: 1, failed: 0},
    },
    sources: [],
  },
});

const renderLive = (snapshot: MonitoringHealthSnapshot) => render(<SchedulerLiveView snapshot={snapshot} />);

// 与组件同源的 Intl 格式化：断言时间数据流，而不是硬编码某台机器的时区字符串。
const expectedTime = (value: string) => new Date(value).toLocaleString('zh-CN', {hour12: false});

// 可滚动区域的确定性度量：jsdom 不做布局，按测试需要注入 scrollHeight/clientHeight/scrollTop。
interface ScrollMetrics {
  scrollHeight: number;
  clientHeight: number;
  scrollTop: number;
}

const mockScrollableStream = (element: HTMLElement): ScrollMetrics => {
  const metrics: ScrollMetrics = {scrollHeight: 2400, clientHeight: 320, scrollTop: 0};
  Object.defineProperty(element, 'scrollHeight', {configurable: true, get: () => metrics.scrollHeight});
  Object.defineProperty(element, 'clientHeight', {configurable: true, get: () => metrics.clientHeight});
  Object.defineProperty(element, 'scrollTop', {
    configurable: true,
    get: () => metrics.scrollTop,
    set: (value: number) => {
      metrics.scrollTop = Math.max(0, Math.min(value, metrics.scrollHeight - metrics.clientHeight));
    },
  });
  return metrics;
};

const setVisibility = (state: DocumentVisibilityState) => {
  Object.defineProperty(document, 'visibilityState', {configurable: true, get: () => state});
};

const dispatchVisibilityChange = () => {
  act(() => {
    document.dispatchEvent(new Event('visibilitychange'));
  });
};

// 浏览器布局的测试替身：按 data-testid 注入 getBoundingClientRect，
// 只模拟真实测量结果，不改变组件逻辑，也不假造任何后端执行。
interface RectSpec {
  readonly left: number;
  readonly top: number;
  readonly width: number;
  readonly height: number;
}

const rectRegistry = new Map<string, RectSpec>();

const zeroRect = (): DOMRect => ({
  left: 0, top: 0, width: 0, height: 0, right: 0, bottom: 0, x: 0, y: 0,
  toJSON: () => ({}),
}) as DOMRect;

const registerRects = (specs: Readonly<Record<string, RectSpec>>) => {
  rectRegistry.clear();
  for (const [testId, spec] of Object.entries(specs)) rectRegistry.set(testId, spec);
};

const installRectAdapter = () => {
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
    const spec = rectRegistry.get(this.getAttribute('data-testid') ?? '');
    if (spec === undefined) return zeroRect();
    return {
      ...spec,
      right: spec.left + spec.width,
      bottom: spec.top + spec.height,
      x: spec.left,
      y: spec.top,
      toJSON: () => ({}),
    } as DOMRect;
  });
};

const setViewport = (width: number, height: number) => {
  Object.defineProperty(window, 'innerWidth', {configurable: true, get: () => width});
  Object.defineProperty(window, 'innerHeight', {configurable: true, get: () => height});
};

afterEach(() => {
  cleanup();
  motionControl.reduced = false;
  transferMotion.completes.length = 0;
  setVisibility('visible');
  vi.restoreAllMocks();
});

describe('调度器实况页（SchedulerLiveView）', () => {
  it('ok：心跳波形与光点、最后心跳、刷新与数据截至保留；不再渲染当前任务区', () => {
    renderLive(readySnapshot({
      current_work: [workItem()],
      scheduled_jobs: [scheduledJob()],
      recent_runs: [recentRun()],
    }));

    expect(screen.getByTestId('scheduler-live-view')).toHaveAttribute('data-reduced-motion', 'false');
    expect(screen.getByTestId('scheduler-live-heartbeat')).toBeInTheDocument();
    expect(screen.getByTestId('scheduler-live-heartbeat-dot')).toBeInTheDocument();

    const heartbeatMeta = screen.getByTestId('scheduler-live-heartbeat-meta');
    expect(heartbeatMeta).toHaveTextContent('心跳正常');
    expect(heartbeatMeta).toHaveTextContent('最后心跳');
    expect(heartbeatMeta).toHaveTextContent(expectedTime('2026-09-11T05:59:30Z'));

    const refreshNote = screen.getByTestId('scheduler-live-refresh-note');
    expect(refreshNote).toHaveTextContent('每 5 秒刷新');
    expect(refreshNote).toHaveTextContent('后端心跳间隔');
    expect(refreshNote).toHaveTextContent('60 秒');
    expect(refreshNote).toHaveTextContent('数据截至');
    expect(refreshNote).toHaveTextContent(expectedTime('2026-09-11T06:00:00Z'));

    // 当前任务区整体移除：即使快照含 current_work，也不渲染 section、列表、计数或任务明细。
    expect(screen.queryByTestId('scheduler-live-current')).not.toBeInTheDocument();
    expect(screen.queryByTestId('scheduler-live-work-item-collect:17')).not.toBeInTheDocument();
    expect(screen.queryByRole('list', {name: '当前任务'})).not.toBeInTheDocument();
    expect(screen.queryByText('当前任务')).not.toBeInTheDocument();

    // 执行流与计划任务流保留可访问名称。
    expect(screen.getByRole('list', {name: '实时执行流'})).toBeInTheDocument();
    expect(screen.getByRole('list', {name: '计划任务流'})).toBeInTheDocument();
  });

  it('移除三处说明：页头快照说明、执行完成澄清与执行流 caption 均不再渲染', () => {
    renderLive(readySnapshot({recent_runs: [recentRun()]}));

    expect(screen.queryByText(/只读快照/)).not.toBeInTheDocument();
    expect(screen.queryByTestId('scheduler-live-completion-note')).not.toBeInTheDocument();
    expect(screen.queryByTestId('scheduler-live-stream-caption')).not.toBeInTheDocument();

    // 重要状态说明保留：心跳与数据截至。
    expect(screen.getByTestId('scheduler-live-heartbeat-meta')).toHaveTextContent('心跳正常');
    expect(screen.getByTestId('scheduler-live-refresh-note')).toHaveTextContent('数据截至');
  });

  it('loading/hidden/unknown 均不显示在线心跳与光点，只给出如实的获取状态', () => {
    const cases: ReadonlyArray<readonly [string, MonitoringHealthSnapshot, string, string]> = [
      ['loading', {status: 'loading'}, 'scheduler-live-loading', '正在获取调度器快照'],
      ['hidden', {status: 'hidden'}, 'scheduler-live-hidden', '无权'],
      ['unknown', {status: 'unknown'}, 'scheduler-live-unknown', '尚无法确认调度器实况'],
    ];
    for (const [label, snapshot, testId, text] of cases) {
      const {unmount} = renderLive(snapshot);
      expect(screen.getByTestId(testId), label).toHaveTextContent(text);
      expect(screen.queryByTestId('scheduler-live-heartbeat'), label).not.toBeInTheDocument();
      expect(screen.queryByTestId('scheduler-live-heartbeat-dot'), label).not.toBeInTheDocument();
      unmount();
    }
  });

  it('ready 但调度器心跳不可确认时，不渲染执行流与计划明细，也不显示在线心跳', () => {
    renderLive(readySnapshot({
      status: 'unknown',
      last_heartbeat_at: null,
      current_work: [workItem()],
      scheduled_jobs: [scheduledJob()],
      recent_runs: [recentRun()],
    }));

    expect(screen.getByTestId('scheduler-live-unconfirmed')).toHaveTextContent('无法确认调度器心跳');
    expect(screen.queryByTestId('scheduler-live-heartbeat')).not.toBeInTheDocument();
    expect(screen.queryByTestId('scheduler-live-heartbeat-dot')).not.toBeInTheDocument();
    expect(screen.queryByTestId('scheduler-live-stream')).not.toBeInTheDocument();
    expect(screen.queryByTestId('scheduler-live-schedule')).not.toBeInTheDocument();
  });

  it('stale：显示心跳延迟警示；running 历史不当作当前活跃；排期标注最近排期快照', () => {
    renderLive(readySnapshot({
      status: 'stale',
      last_heartbeat_at: '2026-09-11T05:40:00Z',
      age_seconds: 1200,
      current_work: [workItem()],
      scheduled_jobs: [scheduledJob()],
      recent_runs: [
        recentRun({id: 1, status: 'running', started_at: '2026-09-11T05:55:00Z', finished_at: null}),
        recentRun({id: 2, status: 'completed'}),
      ],
    }));

    // 不显示在线心跳与光点。
    expect(screen.queryByTestId('scheduler-live-heartbeat')).not.toBeInTheDocument();
    expect(screen.queryByTestId('scheduler-live-heartbeat-dot')).not.toBeInTheDocument();

    const notice = screen.getByTestId('scheduler-live-stale-notice');
    expect(notice).toHaveTextContent('心跳延迟');
    expect(notice).toHaveTextContent(expectedTime('2026-09-11T05:40:00Z'));
    expect(notice).toHaveTextContent('观测快照中有 1 项任务在进行中');
    expect(notice).toHaveTextContent('不代表当前仍在运行');

    // 当前任务区在 stale 下同样不渲染；可能失真的进行中任务明细不展示。
    expect(screen.queryByTestId('scheduler-live-current')).not.toBeInTheDocument();
    expect(screen.queryByTestId('scheduler-live-work-item-collect:17')).not.toBeInTheDocument();

    // running 历史保留状态事实，但不给旋转动画、不宣称当前活跃。
    const runningRow = screen.getByTestId('scheduler-live-run-1');
    expect(runningRow).toHaveTextContent('执行中（快照）');
    expect(within(runningRow).queryByTestId('scheduler-live-spinner')).not.toBeInTheDocument();

    // completed 仍是「执行完成」，已落库历史继续可见。
    expect(screen.getByTestId('scheduler-live-run-2')).toHaveTextContent('执行完成');

    // 排期明确标为最近排期快照；数据截至时间可读。
    expect(screen.getByTestId('scheduler-schedule-item-collect:17')).toHaveTextContent('最近排期快照');
    expect(screen.getByTestId('scheduler-live-refresh-note')).toHaveTextContent('数据截至');
  });

  it('执行流按时间正序排列，最新记录在底部', () => {
    renderLive(readySnapshot({
      recent_runs: [
        recentRun({id: 5, name: '任务 C', scheduled_run_at: '2026-09-11T05:50:00Z', started_at: '2026-09-11T05:50:05Z', finished_at: '2026-09-11T05:50:20Z'}),
        recentRun({id: 3, name: '任务 A', scheduled_run_at: '2026-09-11T05:30:00Z', started_at: '2026-09-11T05:30:02Z', finished_at: '2026-09-11T05:30:14Z'}),
        recentRun({id: 9, name: '任务 B', scheduled_run_at: '2026-09-11T05:40:00Z', started_at: '2026-09-11T05:40:01Z', finished_at: '2026-09-11T05:40:09Z'}),
      ],
    }));

    const rows = screen.getAllByTestId(/^scheduler-live-run-/);
    expect(rows.map((row) => row.getAttribute('data-testid'))).toEqual([
      'scheduler-live-run-3',
      'scheduler-live-run-9',
      'scheduler-live-run-5',
    ]);
    // 旧的「最新在底部」常驻 caption 已移除，排序契约只由行序承担。
    expect(screen.queryByTestId('scheduler-live-stream-caption')).not.toBeInTheDocument();
  });

  it('执行流按稳定 id 去重，同一 id 以最后一次观测为准', () => {
    renderLive(readySnapshot({
      recent_runs: [
        recentRun({id: 3, name: '任务 A', started_at: '2026-09-11T05:30:02Z', finished_at: '2026-09-11T05:30:14Z'}),
        recentRun({id: 7, name: '任务 B', started_at: '2026-09-11T05:40:01Z', finished_at: '2026-09-11T05:40:09Z'}),
        recentRun({id: 3, name: '任务 A（更新观测）', started_at: '2026-09-11T05:30:02Z', finished_at: '2026-09-11T05:30:14Z'}),
      ],
    }));

    const rows = screen.getAllByTestId(/^scheduler-live-run-/);
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent('任务 A（更新观测）');
    expect(rows[1]).toHaveTextContent('任务 B');
  });

  it('数据更新只让新记录入场，已有记录 DOM 不重新挂载（不整列表重复入场）', () => {
    const {rerender} = renderLive(readySnapshot({recent_runs: [recentRun({id: 1})]}));
    const firstRow = screen.getByTestId('scheduler-live-run-1');
    expect(firstRow.className).toContain('scheduler-live-enter');

    rerender(<SchedulerLiveView snapshot={readySnapshot({
      recent_runs: [recentRun({id: 1}), recentRun({id: 2, name: '新增任务', started_at: '2026-09-11T05:50:02Z', finished_at: '2026-09-11T05:50:11Z'})],
    })} />);

    expect(screen.getByTestId('scheduler-live-run-1')).toBe(firstRow);
    expect(screen.getByTestId('scheduler-live-run-2').className).toContain('scheduler-live-enter');

    // 空历史给出明确文案。
    rerender(<SchedulerLiveView snapshot={readySnapshot()} />);
    expect(screen.getByTestId('scheduler-live-stream-empty')).toHaveTextContent('暂无执行记录');
  });

  it('用户上滚立即退出跟随并显示恢复按钮，点击恢复跟随回到最新', () => {
    renderLive(readySnapshot({
      recent_runs: [recentRun({id: 1}), recentRun({id: 2, name: '第二任务', started_at: '2026-09-11T05:40:02Z', finished_at: '2026-09-11T05:40:14Z'})],
    }));

    const stream = screen.getByTestId('scheduler-live-stream');
    // 永久 caption 已移除；跟随状态只由可操作的恢复按钮体现。
    expect(screen.queryByTestId('scheduler-live-stream-caption')).not.toBeInTheDocument();
    expect(screen.queryByTestId('scheduler-live-resume')).not.toBeInTheDocument();

    fireEvent.wheel(stream, {deltaY: -240});
    const resume = screen.getByTestId('scheduler-live-resume');
    expect(resume).toHaveTextContent('恢复跟随');
    expect(resume.className).toContain('min-h-11');

    fireEvent.click(resume);
    expect(screen.queryByTestId('scheduler-live-resume')).not.toBeInTheDocument();
  });

  it('滚动远离底部保持退出跟随，回到阈值内自动恢复跟随', () => {
    renderLive(readySnapshot({recent_runs: [recentRun({id: 1})]}));
    const stream = screen.getByTestId('scheduler-live-stream');
    const metrics = mockScrollableStream(stream);

    metrics.scrollTop = 200;
    fireEvent.scroll(stream);
    expect(screen.getByTestId('scheduler-live-resume')).toBeInTheDocument();

    metrics.scrollTop = 2140;
    fireEvent.scroll(stream);
    expect(screen.queryByTestId('scheduler-live-resume')).not.toBeInTheDocument();
  });

  it('document 隐藏时暂停动画，恢复可见后移除暂停', () => {
    setVisibility('hidden');
    renderLive(readySnapshot());

    expect(screen.getByTestId('scheduler-live-view').className).toContain('scheduler-live-paused');
    // 暂停动画不等于隐藏信息：心跳数据仍可读。
    expect(screen.getByTestId('scheduler-live-heartbeat-meta')).toHaveTextContent('心跳正常');

    setVisibility('visible');
    dispatchVisibilityChange();
    expect(screen.getByTestId('scheduler-live-view').className).not.toContain('scheduler-live-paused');
  });

  it('减少动效：不渲染心跳光点、波形静态、根节点标注 reduced，循环动画保留降级类', () => {
    motionControl.reduced = true;
    renderLive(readySnapshot({
      recent_runs: [recentRun({id: 7, status: 'running', started_at: '2026-09-11T05:59:00Z', finished_at: null})],
    }));

    expect(screen.getByTestId('scheduler-live-view')).toHaveAttribute('data-reduced-motion', 'true');
    expect(screen.queryByTestId('scheduler-live-heartbeat-dot')).not.toBeInTheDocument();
    const path = screen.getByTestId('scheduler-live-heartbeat').querySelector('path');
    expect(path?.getAttribute('class') ?? '').not.toContain('scheduler-live-ecg');

    expect(screen.getByTestId('scheduler-live-spinner')).toHaveClass('motion-reduce:animate-none');
  });

  it('ready 有 current_work 时不渲染当前任务区与计数', () => {
    renderLive(readySnapshot({current_work: [
      workItem(),
      workItem({kind: 'pending_signal_processing', job_key: 'pending_signals', source_id: null, source_name: null, stage: 'processing_signal', item_id: 8421, started_at: null}),
    ]}));

    expect(screen.queryByTestId('scheduler-live-current')).not.toBeInTheDocument();
    expect(screen.queryByTestId('scheduler-live-work-item-collect:17')).not.toBeInTheDocument();
    expect(screen.queryByTestId('scheduler-live-work-item-pending_signals')).not.toBeInTheDocument();
    expect(screen.queryByTestId('scheduler-live-idle')).not.toBeInTheDocument();
    expect(screen.queryByRole('list', {name: '当前任务'})).not.toBeInTheDocument();
    expect(screen.queryByText('当前任务')).not.toBeInTheDocument();
  });

  it('空闲快照不再渲染「当前空闲 / 下一次执行」面板', () => {
    renderLive(readySnapshot({scheduled_jobs: [
      scheduledJob({job_id: 'collect:23', name: '第二官方源 采集', next_run_at: '2026-09-11T06:30:00Z'}),
      scheduledJob({job_id: 'retention_cleanup', name: '数据保留清理', next_run_at: null}),
      scheduledJob({job_id: 'collect:17', name: '官方风险源 采集', next_run_at: '2026-09-11T06:05:00Z'}),
    ]}));

    expect(screen.queryByTestId('scheduler-live-idle')).not.toBeInTheDocument();
    expect(screen.queryByText('当前空闲')).not.toBeInTheDocument();
    expect(screen.queryByText('下一次执行')).not.toBeInTheDocument();

    // 排期信息完整保留在计划任务流列表。
    expect(screen.getByRole('list', {name: '计划任务流'})).toBeInTheDocument();
    expect(screen.getByTestId('scheduler-schedule-item-collect:17')).toHaveTextContent('下次执行');
  });

  it('completed 仅写执行完成；running 的观测时长只来自真实时间戳', () => {
    renderLive(readySnapshot({recent_runs: [
      recentRun({id: 2, status: 'completed'}),
      recentRun({id: 3, status: 'running', started_at: '2026-09-11T05:59:00Z', finished_at: null}),
    ]}));

    const completed = screen.getByTestId('scheduler-live-run-2');
    expect(completed).toHaveTextContent('执行完成');
    expect(completed).not.toHaveTextContent('成功');
    expect(completed).toHaveTextContent('记录时长 12 秒');

    const running = screen.getByTestId('scheduler-live-run-3');
    expect(running).toHaveTextContent('执行中');
    expect(running).toHaveTextContent('观测时长');
    expect(running).toHaveTextContent('1 分钟');
  });

  it('执行行桌面单行：左侧标识与任务描述，右侧开始/时长同行；移动堆叠换行', () => {
    const longName = '华东地区重点供应商合规与制裁清单每日增量核对任务（含出口管制与保险制裁维度）';
    renderLive(readySnapshot({recent_runs: [
      recentRun({id: 2, name: longName, started_at: '2026-09-11T05:30:02Z', finished_at: '2026-09-11T05:30:14Z'}),
    ]}));

    const row = screen.getByTestId('scheduler-live-run-2');
    // 桌面 lg：左列 minmax(0,1fr) 可折行但不被右侧压出横向溢出，右列按内容宽度。
    expect(row.className).toContain('lg:grid-cols-[minmax(0,1fr)_auto]');
    expect(row.className).toContain('lg:items-center');
    expect(row.className).toContain('lg:gap-x-4');
    // 移动：grid 默认单列堆叠 + 纵向间距。
    expect(row.className).toContain('grid');
    expect(row.className).toContain('gap-y-1.5');

    // 左块可换行且不横向溢出；长任务名允许折行。
    const left = row.firstElementChild;
    expect(left?.className).toContain('min-w-0');
    expect(left?.className).toContain('flex-wrap');
    expect(within(row).getByText(longName).className).toContain('break-words');

    // 右侧语义块：开始与时长同处一个 p，桌面不换行并右对齐。
    const meta = row.querySelector('p');
    expect(meta?.className).toContain('lg:flex-nowrap');
    expect(meta?.className).toContain('lg:justify-end');
    expect(meta?.children).toHaveLength(2);
    expect(meta).toHaveTextContent('开始');
    expect(meta).toHaveTextContent('记录时长');
  });

  it('执行行不再渲染计划时间；开始与时长在缺失/倒序时保持占位符', () => {
    renderLive(readySnapshot({recent_runs: [
      recentRun({id: 2, started_at: null, finished_at: null}),
      recentRun({id: 3, status: 'running', started_at: null, finished_at: null}),
      recentRun({id: 4, started_at: '2026-09-11T06:00:00Z', finished_at: '2026-09-11T05:00:00Z'}),
    ]}));

    const missing = screen.getByTestId('scheduler-live-run-2');
    expect(missing).not.toHaveTextContent('计划');
    expect(missing).toHaveTextContent('开始 —');
    expect(missing).toHaveTextContent('记录时长 —');

    const running = screen.getByTestId('scheduler-live-run-3');
    expect(running).not.toHaveTextContent('计划');
    expect(running).toHaveTextContent('开始 —');
    expect(running).toHaveTextContent('观测时长 —');

    // 倒序时间戳不虚构 0 秒耗时。
    expect(screen.getByTestId('scheduler-live-run-4')).toHaveTextContent('记录时长 —');
  });

  it('执行完成采用中性色与非成功图标，不改成成功绿', () => {
    renderLive(readySnapshot({recent_runs: [recentRun({id: 2, status: 'completed'})]}));

    const completed = screen.getByTestId('scheduler-live-run-2');
    const statusIcon = completed.querySelector('svg');
    expect(statusIcon?.getAttribute('class') ?? '').toContain('text-slate-300');
    expect(statusIcon?.getAttribute('class') ?? '').not.toContain('34c759');
    // 完成行内不得残留成功绿（chip/图标），避免把执行完成渲染成业务成功。
    expect(completed.innerHTML).not.toContain('34c759');
  });

  it('执行流与计划区同款外卡；两个标题统一层级且计数紧邻标题文本，计划列表单列', () => {
    const longName = '华东地区重点供应商合规与制裁清单每日增量核对任务（含出口管制与保险制裁维度）';
    renderLive(readySnapshot({scheduled_jobs: [
      scheduledJob({job_id: 'collect:17', name: '官方风险源 采集', next_run_at: '2026-09-11T06:05:00Z'}),
      scheduledJob({job_id: 'retention_cleanup', name: longName, next_run_at: null}),
    ]}));

    // 执行流与计划区：同款独立外卡（rounded-2xl + 深色卡背景 + 统一内边距）。
    const streamCard = screen.getByTestId('scheduler-live-stream-card');
    const schedule = screen.getByTestId('scheduler-live-schedule');
    for (const card of [streamCard, schedule]) {
      for (const cls of ['rounded-2xl', 'border-slate-800', 'bg-[#0e1726]', 'p-3', 'sm:p-4']) {
        expect(card.className, cls).toContain(cls);
      }
    }

    // 两个 h3 统一 text-sm / font-bold / text-white，左侧 lucide 图标 h-4 w-4。
    const streamHeading = within(streamCard).getByRole('heading', {name: /实时执行流/});
    const scheduleHeading = within(schedule).getByRole('heading', {name: /计划任务流/});
    for (const heading of [streamHeading, scheduleHeading]) {
      expect(heading.className).toContain('text-sm');
      expect(heading.className).toContain('font-bold');
      expect(heading.className).toContain('text-white');
      const icon = heading.querySelector('svg');
      expect(icon?.getAttribute('class') ?? '').toContain('h-4');
      expect(icon?.getAttribute('class') ?? '').toContain('w-4');
    }

    // 数量紧邻标题文本：位于同一标题内且是最后一个子元素，不再被 justify-between 推远。
    const streamCount = screen.getByTestId('scheduler-live-stream-count');
    const scheduleCount = screen.getByTestId('scheduler-schedule-count');
    expect(streamCount.parentElement).toBe(streamHeading);
    expect(scheduleCount.parentElement).toBe(scheduleHeading);
    expect(streamHeading.lastElementChild).toBe(streamCount);
    expect(scheduleHeading.lastElementChild).toBe(scheduleCount);
    expect(streamHeading.parentElement?.className ?? '').not.toContain('justify-between');
    expect(scheduleHeading.parentElement?.className ?? '').not.toContain('justify-between');

    // 计划列表单列：不再存在桌面两列网格，行信息完整。
    const list = within(schedule).getByRole('list', {name: '计划任务流'});
    expect(list.className).not.toContain('lg:grid-cols-2');
    expect(list.className).toContain('grid');

    expect(within(schedule).getByTestId('scheduler-schedule-item-collect:17')).toHaveTextContent('下次执行');
    expect(within(schedule).getByTestId('scheduler-schedule-item-collect:17')).toHaveTextContent(expectedTime('2026-09-11T06:05:00Z'));
    const longRow = within(schedule).getByTestId('scheduler-schedule-item-retention_cleanup');
    expect(longRow).toHaveTextContent(longName);
    expect(longRow).toHaveTextContent('暂无后续排期');
    expect(within(longRow).getByText(longName).className).toContain('break-words');

    cleanup();
    renderLive(readySnapshot());
    expect(screen.getByTestId('scheduler-schedule-empty')).toHaveTextContent('调度器当前没有已注册的计划任务');
  });

  it('恢复跟随胶囊悬浮于执行流卡片下部中央：relative 不滚动外壳内 absolute，不改变布局高度、不 fixed', () => {
    renderLive(readySnapshot({recent_runs: [recentRun({id: 1})]}));
    const stream = screen.getByTestId('scheduler-live-stream');
    fireEvent.wheel(stream, {deltaY: -240});

    const resume = screen.getByTestId('scheduler-live-resume');
    const shell = stream.parentElement;
    const capsule = resume.closest('[class*="absolute"]');
    // 外壳相对滚动容器定位、自身不滚动；胶囊在其内部下部中央。
    expect(shell?.className).toContain('relative');
    expect(capsule).not.toBeNull();
    expect(capsule?.parentElement).toBe(shell);
    expect(capsule?.className).toContain('bottom-3');
    expect(capsule?.className).toContain('left-1/2');
    // wrapper 不吃指针事件、按钮恢复指针；保持 44px 触达高度。
    expect(capsule?.className).toContain('pointer-events-none');
    expect(resume.className).toContain('pointer-events-auto');
    expect(resume.className).toContain('min-h-11');
    // 不 fixed、不跨出卡片覆盖底部导航；胶囊仍在页面卡片内。
    expect(resume.closest('[class*="fixed"]')).toBeNull();
    expect(resume.closest('[class*="z-50"]')).toBeNull();
    expect(screen.queryByTestId('scheduler-live-view')?.contains(capsule)).toBe(true);
    // 滚动内容底部预留空间，最后一行不被悬浮胶囊遮挡。
    expect(stream.className).toContain('pb-16');
  });
});

describe('调度器实况标题组（刷新提示并入）', () => {
  it('刷新与数据截至提示并入「调度器实况」标题组，不再是页头卡片外的独立行', () => {
    renderLive(readySnapshot());

    const note = screen.getByTestId('scheduler-live-refresh-note');
    const heading = screen.getByRole('heading', {name: '调度器实况'});
    // 同一标题组：位于页头卡片内、紧接页面标题下方，与 h2 同父。
    expect(note.closest('header')).not.toBeNull();
    expect(note.parentElement).toBe(heading.parentElement);
    expect(heading.compareDocumentPosition(note) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    // 长中文允许自然换行、不孤字不溢出。
    expect(note.className).toContain('break-words');
    expect(note.className).toContain('text-pretty');
    // 事实数据保留。
    expect(note).toHaveTextContent('每 5 秒刷新');
    expect(note).toHaveTextContent('后端心跳间隔');
    expect(note).toHaveTextContent('60 秒');
    expect(note).toHaveTextContent('数据截至');
    expect(note).toHaveTextContent(expectedTime('2026-09-11T06:00:00Z'));
  });
});

describe('调度器实况跨区投影动画（计划头 → 真实新 run）', () => {
  const baseJobs = [scheduledJob({job_id: 'collect:17', next_run_at: '2026-09-11T06:30:00Z'})];
  const baseRuns = [recentRun({id: 1, scheduled_run_at: '2026-09-11T05:30:00Z'})];
  const rectSpecs: Readonly<Record<string, RectSpec>> = {
    'scheduler-live-transfer-layer': {left: 0, top: 0, width: 640, height: 900},
    'scheduler-schedule-item-collect:17': {left: 40, top: 700, width: 560, height: 48},
    'scheduler-live-stream': {left: 24, top: 200, width: 592, height: 320},
    'scheduler-live-run-2': {left: 32, top: 420, width: 576, height: 64},
  };
  // 与上一快照计划头对账成功的新 run：同 job、scheduled_run_at 等于 head.next_run_at，且状态已是最终态。
  const matchedSnapshot = () => readySnapshot({
    scheduled_jobs: [scheduledJob({job_id: 'collect:17', next_run_at: '2026-09-11T07:00:00Z'})],
    recent_runs: [
      ...baseRuns,
      recentRun({id: 2, scheduled_run_at: '2026-09-11T06:30:00Z', started_at: '2026-09-11T06:30:02Z', finished_at: '2026-09-11T06:30:09Z'}),
    ],
  });

  beforeEach(() => {
    setViewport(1024, 768);
    installRectAdapter();
    registerRects(rectSpecs);
  });

  it('两个 fresh 快照：对账成功触发投影，起点取上一快照缓存，计划保留且 next 推进', () => {
    const {rerender} = renderLive(readySnapshot({scheduled_jobs: baseJobs, recent_runs: baseRuns}));
    expect(screen.queryByTestId('scheduler-live-transfer-ghost')).not.toBeInTheDocument();

    // 模拟计划排序推进后的新布局：源行位置改变，但投影起点必须仍取上一快照缓存的位置。
    registerRects({...rectSpecs, 'scheduler-schedule-item-collect:17': {left: 40, top: 640, width: 560, height: 48}});
    rerender(<SchedulerLiveView snapshot={matchedSnapshot()} />);

    const ghost = screen.getByTestId('scheduler-live-transfer-ghost');
    expect(ghost).toHaveAttribute('aria-hidden', 'true');
    expect(ghost.className).toContain('pointer-events-none');
    expect(ghost.style.left).toBe('40px');
    expect(ghost.style.top).toBe('700px');
    // 投影复述实际最终状态：completed 显示「执行完成」，不伪冒 running，也不是 shell 日志文本。
    expect(ghost).toHaveTextContent('执行完成');
    expect(ghost).not.toHaveTextContent('执行中');
    expect(ghost).not.toHaveTextContent('开始');

    // 真实目标行存在；投影不进入可访问列表，不重复记录。
    expect(screen.getByTestId('scheduler-live-run-2')).toBeInTheDocument();
    const streamList = screen.getByRole('list', {name: '实时执行流'});
    expect(within(streamList).getAllByRole('listitem')).toHaveLength(2);

    // 计划没有被移除：同一行仍在且 next 推进到新值。
    expect(screen.getByTestId('scheduler-schedule-item-collect:17')).toHaveTextContent(expectedTime('2026-09-11T07:00:00Z'));

    // 完成回调清除投影。
    act(() => transferMotion.completes.at(-1)?.());
    expect(screen.queryByTestId('scheduler-live-transfer-ghost')).not.toBeInTheDocument();
  });

  it('初次加载只有一份快照：即使存在匹配记录也不触发', () => {
    renderLive(readySnapshot({
      scheduled_jobs: [scheduledJob({job_id: 'collect:17', next_run_at: '2026-09-11T07:00:00Z'})],
      recent_runs: [...baseRuns, recentRun({id: 2, scheduled_run_at: '2026-09-11T06:30:00Z'})],
    }));
    expect(screen.queryByTestId('scheduler-live-transfer-ghost')).not.toBeInTheDocument();
  });

  it('已存在 run 仅状态变更（running → completed）不触发', () => {
    const {rerender} = renderLive(readySnapshot({
      scheduled_jobs: baseJobs,
      recent_runs: [recentRun({id: 2, status: 'running', scheduled_run_at: '2026-09-11T06:30:00Z', started_at: '2026-09-11T06:30:02Z', finished_at: null})],
    }));
    rerender(<SchedulerLiveView snapshot={readySnapshot({
      scheduled_jobs: baseJobs,
      recent_runs: [recentRun({id: 2, status: 'completed', scheduled_run_at: '2026-09-11T06:30:00Z', started_at: '2026-09-11T06:30:02Z', finished_at: '2026-09-11T06:30:09Z'})],
    })} />);
    expect(screen.queryByTestId('scheduler-live-transfer-ghost')).not.toBeInTheDocument();
  });

  it('同一运行 id 已处理过：重复快照不再触发', () => {
    const {rerender} = renderLive(readySnapshot({scheduled_jobs: baseJobs, recent_runs: baseRuns}));
    rerender(<SchedulerLiveView snapshot={matchedSnapshot()} />);
    act(() => transferMotion.completes.at(-1)?.());
    expect(screen.queryByTestId('scheduler-live-transfer-ghost')).not.toBeInTheDocument();

    rerender(<SchedulerLiveView snapshot={matchedSnapshot()} />);
    expect(screen.queryByTestId('scheduler-live-transfer-ghost')).not.toBeInTheDocument();
  });

  it('新 run 的 job_id 与计划头不一致时不触发', () => {
    const {rerender} = renderLive(readySnapshot({scheduled_jobs: baseJobs, recent_runs: baseRuns}));
    rerender(<SchedulerLiveView snapshot={readySnapshot({
      scheduled_jobs: [scheduledJob({job_id: 'collect:17', next_run_at: '2026-09-11T07:00:00Z'})],
      recent_runs: [...baseRuns, recentRun({id: 2, job_id: 'collect:23', scheduled_run_at: '2026-09-11T06:30:00Z'})],
    })} />);
    expect(screen.queryByTestId('scheduler-live-transfer-ghost')).not.toBeInTheDocument();
  });

  it('新 run 的 scheduled_run_at 与上一快照计划头时间不等价（过期历史）不触发', () => {
    const {rerender} = renderLive(readySnapshot({scheduled_jobs: baseJobs, recent_runs: baseRuns}));
    rerender(<SchedulerLiveView snapshot={readySnapshot({
      scheduled_jobs: [scheduledJob({job_id: 'collect:17', next_run_at: '2026-09-11T07:00:00Z'})],
      recent_runs: [...baseRuns, recentRun({id: 2, scheduled_run_at: '2026-09-11T06:29:59Z'})],
    })} />);
    expect(screen.queryByTestId('scheduler-live-transfer-ghost')).not.toBeInTheDocument();
  });

  it('stale 快照不触发也不推进对照基线', () => {
    const {rerender} = renderLive(readySnapshot({scheduled_jobs: baseJobs, recent_runs: baseRuns}));
    rerender(<SchedulerLiveView snapshot={readySnapshot({
      status: 'stale',
      last_heartbeat_at: '2026-09-11T05:40:00Z',
      scheduled_jobs: [scheduledJob({job_id: 'collect:17', next_run_at: '2026-09-11T07:00:00Z'})],
      recent_runs: [...baseRuns, recentRun({id: 2, scheduled_run_at: '2026-09-11T06:30:00Z'})],
    })} />);
    expect(screen.queryByTestId('scheduler-live-transfer-ghost')).not.toBeInTheDocument();
    // 执行流仍如实展示记录，只是不做跨区动画。
    expect(screen.getByTestId('scheduler-live-run-2')).toBeInTheDocument();
  });

  it('document 隐藏期间不触发', () => {
    const {rerender} = renderLive(readySnapshot({scheduled_jobs: baseJobs, recent_runs: baseRuns}));
    setVisibility('hidden');
    dispatchVisibilityChange();
    rerender(<SchedulerLiveView snapshot={matchedSnapshot()} />);
    expect(screen.queryByTestId('scheduler-live-transfer-ghost')).not.toBeInTheDocument();
  });

  it('减少动效时不触发', () => {
    const {rerender} = renderLive(readySnapshot({scheduled_jobs: baseJobs, recent_runs: baseRuns}));
    motionControl.reduced = true;
    rerender(<SchedulerLiveView snapshot={matchedSnapshot()} />);
    expect(screen.queryByTestId('scheduler-live-transfer-ghost')).not.toBeInTheDocument();
  });

  it('用户上滚暂停跟随期间不触发（不抢阅读位置）', () => {
    const {rerender} = renderLive(readySnapshot({scheduled_jobs: baseJobs, recent_runs: baseRuns}));
    fireEvent.wheel(screen.getByTestId('scheduler-live-stream'), {deltaY: -240});
    rerender(<SchedulerLiveView snapshot={matchedSnapshot()} />);
    expect(screen.queryByTestId('scheduler-live-transfer-ghost')).not.toBeInTheDocument();
  });

  it('目标 run 离屏（不在执行流可视区）时跳过，且不滚动页面', () => {
    const scrollTo = vi.spyOn(window, 'scrollTo');
    const {rerender} = renderLive(readySnapshot({scheduled_jobs: baseJobs, recent_runs: baseRuns}));
    registerRects({
      ...rectSpecs,
      'scheduler-live-run-2': {left: 32, top: 700, width: 576, height: 64},
    });
    rerender(<SchedulerLiveView snapshot={matchedSnapshot()} />);

    expect(screen.queryByTestId('scheduler-live-transfer-ghost')).not.toBeInTheDocument();
    expect(scrollTo).not.toHaveBeenCalled();
    // 离屏跳过不等于隐藏记录。
    expect(screen.getByTestId('scheduler-live-run-2')).toBeInTheDocument();
  });

  it('第一条计划（notify）无 run 也不挡路：source 的新 run 从 source 行上一快照位置起飞', () => {
    registerRects({
      ...rectSpecs,
      'scheduler-schedule-item-notify': {left: 40, top: 620, width: 560, height: 44},
    });
    const {rerender} = renderLive(readySnapshot({
      scheduled_jobs: [
        scheduledJob({job_id: 'notify', name: '通知扫描', next_run_at: '2026-09-11T06:29:00Z'}),
        scheduledJob({job_id: 'collect:17', name: '官方风险源 采集', next_run_at: '2026-09-11T06:30:00Z'}),
      ],
      recent_runs: baseRuns,
    }));
    expect(screen.queryByTestId('scheduler-live-transfer-ghost')).not.toBeInTheDocument();

    rerender(<SchedulerLiveView snapshot={readySnapshot({
      scheduled_jobs: [
        scheduledJob({job_id: 'notify', name: '通知扫描', next_run_at: '2026-09-11T06:30:00Z'}),
        scheduledJob({job_id: 'collect:17', name: '官方风险源 采集', next_run_at: '2026-09-11T07:00:00Z'}),
      ],
      recent_runs: [...baseRuns, recentRun({id: 2, scheduled_run_at: '2026-09-11T06:30:00Z', started_at: '2026-09-11T06:30:02Z', finished_at: '2026-09-11T06:30:09Z'})],
    })} />);

    const ghost = screen.getByTestId('scheduler-live-transfer-ghost');
    // 起点 = 上一快照缓存里 source 行的位置（top 700），而不是第一行 notify 的位置（top 620）。
    expect(ghost.style.left).toBe('40px');
    expect(ghost.style.top).toBe('700px');
    expect(ghost).toHaveTextContent('执行完成');
    // 两条计划都保留，source 的 next 已推进；notify 无 run 不产生投影。
    expect(screen.getByTestId('scheduler-schedule-item-notify')).toHaveTextContent(expectedTime('2026-09-11T06:30:00Z'));
    expect(screen.getByTestId('scheduler-schedule-item-collect:17')).toHaveTextContent(expectedTime('2026-09-11T07:00:00Z'));
  });

  it('动画进行中 document 隐藏：ghost 立即消失', () => {
    const {rerender} = renderLive(readySnapshot({scheduled_jobs: baseJobs, recent_runs: baseRuns}));
    rerender(<SchedulerLiveView snapshot={matchedSnapshot()} />);
    expect(screen.getByTestId('scheduler-live-transfer-ghost')).toBeInTheDocument();

    setVisibility('hidden');
    dispatchVisibilityChange();
    expect(screen.queryByTestId('scheduler-live-transfer-ghost')).not.toBeInTheDocument();
  });

  it('动画进行中快照转 stale：ghost 立即清除', () => {
    const {rerender} = renderLive(readySnapshot({scheduled_jobs: baseJobs, recent_runs: baseRuns}));
    rerender(<SchedulerLiveView snapshot={matchedSnapshot()} />);
    expect(screen.getByTestId('scheduler-live-transfer-ghost')).toBeInTheDocument();

    rerender(<SchedulerLiveView snapshot={readySnapshot({
      status: 'stale',
      last_heartbeat_at: '2026-09-11T05:40:00Z',
      scheduled_jobs: [scheduledJob({job_id: 'collect:17', next_run_at: '2026-09-11T07:00:00Z'})],
      recent_runs: [...baseRuns, recentRun({id: 2, scheduled_run_at: '2026-09-11T06:30:00Z'})],
    })} />);
    expect(screen.queryByTestId('scheduler-live-transfer-ghost')).not.toBeInTheDocument();
  });

  it('动画进行中开启减少动效：ghost 立即清除', () => {
    const {rerender} = renderLive(readySnapshot({scheduled_jobs: baseJobs, recent_runs: baseRuns}));
    rerender(<SchedulerLiveView snapshot={matchedSnapshot()} />);
    expect(screen.getByTestId('scheduler-live-transfer-ghost')).toBeInTheDocument();

    motionControl.reduced = true;
    rerender(<SchedulerLiveView snapshot={matchedSnapshot()} />);
    expect(screen.queryByTestId('scheduler-live-transfer-ghost')).not.toBeInTheDocument();
  });

  it('动画进行中用户上滚暂停跟随：ghost 立即清除', () => {
    const {rerender} = renderLive(readySnapshot({scheduled_jobs: baseJobs, recent_runs: baseRuns}));
    rerender(<SchedulerLiveView snapshot={matchedSnapshot()} />);
    expect(screen.getByTestId('scheduler-live-transfer-ghost')).toBeInTheDocument();

    fireEvent.wheel(screen.getByTestId('scheduler-live-stream'), {deltaY: -240});
    expect(screen.queryByTestId('scheduler-live-transfer-ghost')).not.toBeInTheDocument();
  });

  it('源 rect 滚出 window 视口时跳过（不飞）', () => {
    registerRects({
      ...rectSpecs,
      'scheduler-schedule-item-collect:17': {left: 40, top: 820, width: 560, height: 48},
    });
    const {rerender} = renderLive(readySnapshot({scheduled_jobs: baseJobs, recent_runs: baseRuns}));
    rerender(<SchedulerLiveView snapshot={matchedSnapshot()} />);
    expect(screen.queryByTestId('scheduler-live-transfer-ghost')).not.toBeInTheDocument();
  });

  it('目标 rect 在 window 视口外（即使仍落在流滚动范围内）时跳过', () => {
    registerRects({
      ...rectSpecs,
      'scheduler-live-stream': {left: 24, top: 100, width: 592, height: 2000},
      'scheduler-live-run-2': {left: 32, top: 800, width: 576, height: 64},
    });
    const {rerender} = renderLive(readySnapshot({scheduled_jobs: baseJobs, recent_runs: baseRuns}));
    rerender(<SchedulerLiveView snapshot={matchedSnapshot()} />);
    expect(screen.queryByTestId('scheduler-live-transfer-ghost')).not.toBeInTheDocument();
  });
});
