import {act, cleanup, fireEvent, render, screen, within} from '@testing-library/react';
import {createElement, type HTMLAttributes, type ReactNode} from 'react';
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {api, ApiError, type MonitoringHealthRead, type SchedulerRunHistory} from '../api';
import type {MonitoringHealthSnapshot} from '../useMonitoringHealth';
import {SchedulerLiveView} from './SchedulerLiveView';

// 只构造契约允许的字段：不注入信号标题/正文、异常文本或虚构日志。
type Scheduler = MonitoringHealthRead['scheduler'];
type SchedulerWork = Scheduler['current_work'][number];
type ScheduledJob = Scheduler['scheduled_jobs'][number];
type RecentRun = Scheduler['recent_runs'][number];

const motionControl = vi.hoisted(() => ({reduced: false}));

vi.mock('motion/react', async (importOriginal) => {
  const actual = await importOriginal<typeof import('motion/react')>();
  return {
    ...actual,
    useReducedMotion: () => motionControl.reduced,
    motion: {
      div: ({children, ...domProps}: {
        readonly children?: ReactNode;
        readonly [key: string]: unknown;
      }) => createElement('div', domProps as HTMLAttributes<HTMLDivElement>, children),
    },
  };
});

const workItem = (overrides: Partial<SchedulerWork> = {}): SchedulerWork => ({
  kind: 'source_collection',
  job_key: 'collect:12',
  source_id: 12,
  source_name: '工信部',
  stage: 'collecting',
  started_at: '2026-10-25T12:10:00Z',
  item_id: null,
  ...overrides,
});

const scheduledJob = (overrides: Partial<ScheduledJob> = {}): ScheduledJob => ({
  job_id: 'collect',
  name: '定时采集与处理',
  next_run_at: '2026-10-25T12:30:00Z',
  ...overrides,
});

const recentRun = (overrides: Partial<RecentRun> = {}): RecentRun => ({
  id: 1,
  job_id: 'collect',
  name: '定时采集与处理',
  status: 'completed',
  scheduled_run_at: '2026-10-25T12:00:00Z',
  started_at: '2026-10-25T12:00:01Z',
  finished_at: '2026-10-25T12:00:13Z',
  ...overrides,
});

// 结构化结果是可选字段：旧记录没有 result，测试里显式区分“有明细/无明细”。
const runWithResult = (overrides: Partial<RecentRun> = {}): RecentRun => recentRun({
  id: 11,
  job_id: 'collect',
  name: '定时采集与处理',
  status: 'completed',
  started_at: '2026-10-26T04:00:01Z',
  finished_at: '2026-10-26T04:00:08Z',
  result: {
    summary: '采集完成',
    metrics: [{label: '抓取', value: 20}, {label: '新增', value: 5}, {label: '重复', value: 15}],
    outputs: [{label: '新增信号', value: 5}],
  },
  ...overrides,
});

const baseScheduler = (overrides: Partial<Scheduler> = {}): Scheduler => ({
  status: 'ok',
  last_heartbeat_at: '2026-10-25T12:11:50Z',
  age_seconds: 10,
  interval_seconds: 60,
  stale_after_seconds: 180,
  current_work: [],
  scheduled_jobs: [],
  recent_runs: [],
  ...overrides,
});

const readySnapshot = (
  schedulerOverrides: Partial<Scheduler> = {},
  processingOverrides: Partial<MonitoringHealthRead['processing']> = {},
): MonitoringHealthSnapshot => ({
  status: 'ready',
  health: {
    as_of: '2026-10-25T12:12:00Z',
    overall: 'ok',
    scheduler: baseScheduler({
      current_work: [workItem()],
      scheduled_jobs: [
        scheduledJob({job_id: 'collect', next_run_at: '2026-10-25T12:30:00Z'}),
        scheduledJob({job_id: 'source-12', name: '工信部 采集', next_run_at: '2026-10-25T12:20:00Z'}),
        scheduledJob({job_id: 'source-13', name: '市场监管总局 采集', next_run_at: '2026-10-25T12:25:00Z'}),
        scheduledJob({job_id: 'tyc-shard-0', name: '天眼查核查 · 分片 0', next_run_at: '2026-10-26T02:00:00Z'}),
        scheduledJob({job_id: 'signal-consume', name: '待分析信号消费', next_run_at: '2026-10-25T12:12:00Z'}),
        scheduledJob({job_id: 'notify', name: '风险提醒推送', next_run_at: '2026-10-25T12:15:00Z'}),
        scheduledJob({job_id: 'expire', name: '风险有效期物化', next_run_at: '2026-10-26T03:10:00Z'}),
        scheduledJob({job_id: 'intelligence', name: '政策与行业情报', next_run_at: '2026-10-26T08:30:00Z'}),
        scheduledJob({job_id: 'research-daily', name: '创建每日研究任务', next_run_at: '2026-10-26T07:00:00Z'}),
        scheduledJob({job_id: 'cleanup', name: '保留清理', next_run_at: '2026-10-26T03:00:00Z'}),
        scheduledJob({job_id: 'runtime-heartbeat', name: '持久化调度器心跳', next_run_at: '2026-10-25T12:12:00Z'}),
      ],
      recent_runs: [
        recentRun({id: 1, job_id: 'collect', status: 'completed'}),
        recentRun({id: 2, job_id: 'source-12', name: '工信部 采集', status: 'running', started_at: '2026-10-25T12:10:00Z', finished_at: null}),
        recentRun({id: 7, job_id: 'source-13', name: '市场监管总局 采集', status: 'completed', started_at: '2026-10-25T12:05:00Z', finished_at: '2026-10-25T12:05:18Z'}),
        recentRun({id: 3, job_id: 'tyc-shard-0', name: '天眼查核查 · 分片 0', status: 'missed', scheduled_run_at: '2026-10-25T03:00:00Z', started_at: null, finished_at: null}),
        recentRun({id: 4, job_id: 'signal-consume', name: '待分析信号消费', status: 'running', started_at: '2026-10-25T12:11:20Z', finished_at: null}),
        recentRun({id: 5, job_id: 'expire', name: '风险有效期物化', status: 'completed', started_at: '2026-10-25T03:10:00Z', finished_at: '2026-10-25T03:10:18Z'}),
        recentRun({id: 6, job_id: 'cleanup', name: '保留清理', status: 'missed', scheduled_run_at: '2026-10-25T04:00:00Z', started_at: null, finished_at: null}),
      ],
      ...schedulerOverrides,
    }),
    processing: {
      total: 42,
      classification_failed: 0,
      backlog_over_1h: 3,
      oldest_pending_age_seconds: 3600,
      processing_state_counts: {},
      last_run: {status: 'succeeded', started_at: '2026-10-25T12:00:00Z', finished_at: '2026-10-25T12:00:20Z', processed: 5, filtered: 1, failed: 0},
      ...processingOverrides,
    },
    sources: [],
  },
});

const logPage = {
  available: true,
  reason: null,
  lines: [
    '2026-10-25 12:10:01,123 INFO scheduler.jobs 采集完成 source-12',
    '2026-10-25 12:11:40,900 ERROR scheduler.jobs 天眼查分片执行异常 tyc-shard-0',
  ],
  next_cursor: '1:300',
  reset: false,
  truncated: false,
  updated_at: '2026-10-25T12:12:00Z',
} as const;

const renderLive = (
  snapshot: MonitoringHealthSnapshot,
  options: {canViewLogs?: boolean; onError?: (error: ApiError) => void} = {},
) => render(
  <SchedulerLiveView
    snapshot={snapshot}
    canViewLogs={options.canViewLogs ?? false}
    onRequestError={options.onError}
  />,
);

const openLog = async () => {
  fireEvent.click(screen.getByTestId('scheduler-log-open'));
  await screen.findByRole('dialog', {name: '运行日志'});
};

beforeEach(() => {
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null);
  // 每个用例默认不返回执行历史：避免未命中用例真的发请求，也避免 act 之外的异步更新。
  vi.spyOn(api, 'schedulerJobRuns').mockImplementation(() => new Promise<SchedulerRunHistory>(() => {}));
});

afterEach(() => {
  cleanup();
  motionControl.reduced = false;
  vi.restoreAllMocks();
});

describe('调度器实况页外壳', () => {
  it('ready + 正常心跳：展示刷新说明、心跳与四列网络', () => {
    renderLive(readySnapshot());
    expect(screen.getByTestId('scheduler-live-view')).toHaveAttribute('data-reduced-motion', 'false');
    expect(screen.getByTestId('scheduler-live-heartbeat')).toBeInTheDocument();
    expect(screen.getByTestId('scheduler-live-heartbeat-dot')).toBeInTheDocument();
    expect(screen.getByTestId('scheduler-live-refresh-note')).toHaveTextContent('快照每 5 秒刷新');
    expect(screen.getByTestId('scheduler-flow-map')).toBeInTheDocument();
    expect(screen.getByTestId('scheduler-flow-node-hub')).toHaveTextContent('42');
  });

  it('网络节点状态来自快照：source 执行中、tyc 错过、notify 无近期记录', () => {
    renderLive(readySnapshot());
    expect(screen.getByTestId('scheduler-flow-node-source')).toHaveTextContent('执行中');
    expect(screen.getByTestId('scheduler-flow-node-tyc')).toHaveTextContent('错过执行');
    expect(screen.getByTestId('scheduler-flow-node-notify')).toHaveTextContent('无近期记录');
    expect(screen.getByTestId('scheduler-flow-node-consume')).toHaveTextContent('执行中');
  });

  it('点击聚合节点显示成员明细，异常成员不被整体运行状态吞掉', () => {
    renderLive(readySnapshot());
    fireEvent.click(screen.getByTestId('scheduler-flow-node-source'));
    const detail = screen.getByTestId('scheduler-flow-detail');
    expect(detail).toHaveTextContent('信源采集');
    expect(detail).not.toHaveTextContent('source-12');
    expect(detail).toHaveTextContent('工信部 采集');
  });

  it('心跳 stale：不展示在线心跳，显示延迟提示，运行中标注快照', () => {
    renderLive(readySnapshot({status: 'stale'}));
    expect(screen.queryByTestId('scheduler-live-heartbeat')).not.toBeInTheDocument();
    expect(screen.queryByTestId('scheduler-live-heartbeat-dot')).not.toBeInTheDocument();
    expect(screen.getByTestId('scheduler-live-stale-notice')).toBeInTheDocument();
    expect(screen.getByTestId('scheduler-flow-node-source')).toHaveTextContent('执行中（快照）');
  });

  it('scheduler heartbeat unknown：只给不可确认状态，不渲染网络', () => {
    renderLive(readySnapshot({status: 'unknown'}));
    expect(screen.getByTestId('scheduler-live-unconfirmed')).toBeInTheDocument();
    expect(screen.queryByTestId('scheduler-flow-map')).not.toBeInTheDocument();
  });

  it('snapshot loading / hidden / unknown 使用明确状态面板', () => {
    const {rerender} = renderLive({status: 'loading'});
    expect(screen.getByTestId('scheduler-live-loading')).toBeInTheDocument();
    rerender(<SchedulerLiveView snapshot={{status: 'hidden'}} />);
    expect(screen.getByTestId('scheduler-live-hidden')).toBeInTheDocument();
    rerender(<SchedulerLiveView snapshot={{status: 'unknown'}} />);
    expect(screen.getByTestId('scheduler-live-unknown')).toBeInTheDocument();
  });

  it('减少动效时保留静态心跳但移除扫描光点', () => {
    motionControl.reduced = true;
    renderLive(readySnapshot());
    expect(screen.getByTestId('scheduler-live-view')).toHaveAttribute('data-reduced-motion', 'true');
    expect(screen.getByTestId('scheduler-live-heartbeat')).toBeInTheDocument();
    expect(screen.queryByTestId('scheduler-live-heartbeat-dot')).not.toBeInTheDocument();
  });
});

describe('节点最近执行', () => {
  it('选中单任务节点时按需请求最近 5 次执行，并替换重复的下次/上次字段', async () => {
    const spy = vi.spyOn(api, 'schedulerJobRuns').mockResolvedValue({
      job_id: 'collect',
      items: [runWithResult()],
    });
    renderLive(readySnapshot());

    fireEvent.click(screen.getByTestId('scheduler-flow-node-collect'));
    expect(spy).toHaveBeenCalledWith('collect', 5, expect.any(AbortSignal));

    const detail = screen.getByTestId('scheduler-flow-detail');
    expect(await within(detail).findByText('采集完成')).toBeInTheDocument();
    expect(within(detail).getByText('抓取')).toBeInTheDocument();
    expect(within(detail).getByText('20')).toBeInTheDocument();
    expect(within(detail).getByText('重复')).toBeInTheDocument();
    expect(within(detail).getByText('新增信号')).toBeInTheDocument();
    expect(within(detail).queryByText('下次执行')).not.toBeInTheDocument();
    expect(within(detail).queryByText('最近一次')).not.toBeInTheDocument();
    expect(within(detail).queryByText('运行中')).not.toBeInTheDocument();
  });

  it('历史记录没有结构化结果时明确占位，不编造摘要或计数', async () => {
    vi.spyOn(api, 'schedulerJobRuns').mockResolvedValue({
      job_id: 'collect',
      items: [runWithResult({id: 12, status: 'error', finished_at: null, result: null})],
    });
    renderLive(readySnapshot());
    fireEvent.click(screen.getByTestId('scheduler-flow-node-collect'));

    const detail = screen.getByTestId('scheduler-flow-detail');
    expect(await within(detail).findByText('该次执行没有结构化明细（旧记录或失败）')).toBeInTheDocument();
    expect(within(detail).queryByText('采集完成')).not.toBeInTheDocument();
    expect(within(detail).queryByText('抓取')).not.toBeInTheDocument();
  });

  it('没有执行记录时显示空态', async () => {
    vi.spyOn(api, 'schedulerJobRuns').mockResolvedValue({job_id: 'collect', items: []});
    renderLive(readySnapshot());
    fireEvent.click(screen.getByTestId('scheduler-flow-node-collect'));

    const detail = screen.getByTestId('scheduler-flow-detail');
    expect(await within(detail).findByText('最近没有执行记录')).toBeInTheDocument();
  });

  it('加载失败给出原因并可重试', async () => {
    let attempt = 0;
    vi.spyOn(api, 'schedulerJobRuns').mockImplementation((jobId) => {
      if (jobId !== 'collect') return new Promise<SchedulerRunHistory>(() => {});
      attempt += 1;
      return attempt === 1
        ? Promise.reject(new ApiError(500, 'boom'))
        : Promise.resolve({job_id: 'collect', items: [runWithResult()]});
    });
    renderLive(readySnapshot());
    fireEvent.click(screen.getByTestId('scheduler-flow-node-collect'));

    const detail = screen.getByTestId('scheduler-flow-detail');
    expect(await within(detail).findByText('读取执行历史失败（HTTP 500）。')).toBeInTheDocument();
    fireEvent.click(within(detail).getByRole('button', {name: '重试'}));
    expect(await within(detail).findByText('采集完成')).toBeInTheDocument();
  });

  it('聚合节点保留成员列表与结果摘要，不为每个成员发请求', () => {
    const snapshot = readySnapshot({
      recent_runs: [
        recentRun({
          id: 21,
          job_id: 'source-12',
          name: '工信部 采集',
          status: 'completed',
          started_at: '2026-10-25T12:00:00Z',
          finished_at: '2026-10-25T12:00:06Z',
          result: {summary: '抓取 20 条，新增 5 条', metrics: [{label: '抓取', value: 20}]},
        }),
        recentRun({id: 22, job_id: 'source-13', name: '市场监管总局 采集', status: 'completed'}),
      ],
    });
    const spy = vi.spyOn(api, 'schedulerJobRuns')
      .mockImplementation(() => new Promise<SchedulerRunHistory>(() => {}));
    renderLive(snapshot);

    fireEvent.click(screen.getByTestId('scheduler-flow-node-source'));
    const detail = screen.getByTestId('scheduler-flow-detail');
    expect(within(detail).getByText('工信部 采集')).toBeInTheDocument();
    expect(within(detail).getByText('抓取 20 条，新增 5 条')).toBeInTheDocument();
    // 默认选中的单任务会请求一次；聚合节点不为 19 个成员逐个请求。
    expect(spy.mock.calls.map((call) => call[0])).toEqual(['signal-consume']);
  });

  it('切换节点中止上一请求，且不把上一节点的结果显示在新节点下', async () => {
    const signals: AbortSignal[] = [];
    vi.spyOn(api, 'schedulerJobRuns').mockImplementation((jobId, _limit, signal) => {
      if (signal !== undefined) signals.push(signal);
      if (jobId === 'collect') {
        return Promise.resolve({job_id: 'collect', items: [runWithResult()]});
      }
      return new Promise<SchedulerRunHistory>(() => {});
    });
    renderLive(readySnapshot());

    fireEvent.click(screen.getByTestId('scheduler-flow-node-collect'));
    const detail = screen.getByTestId('scheduler-flow-detail');
    expect(await within(detail).findByText('采集完成')).toBeInTheDocument();
    expect(signals[0].aborted).toBe(true);

    fireEvent.click(screen.getByTestId('scheduler-flow-node-notify'));
    expect(signals[1].aborted).toBe(true);
    expect(signals[2].aborted).toBe(false);
    expect(within(detail).queryByText('采集完成')).not.toBeInTheDocument();
    expect(within(detail).getByText('正在读取最近执行…')).toBeInTheDocument();
  });
});

describe('KPI 卡详情', () => {
  it('四张 KPI 卡都能打开对应标题与内容，同一时间只有一个弹窗', () => {
    renderLive(readySnapshot());

    const cases = [
      ['running', '运行中', ['当前工作项', '运行中的任务', '待分析信号消费']],
      ['jobs', '已注册任务', ['定时采集与处理', '待分析信号消费']],
      ['runs', '近期执行', ['保留清理']],
      ['attention', '需要关注', ['天眼查核查 · 分片 0']],
    ] as const;

    for (const [key, title, texts] of cases) {
      const trigger = screen.getByTestId(`scheduler-kpi-${key}`);
      expect(trigger.tagName).toBe('BUTTON');
      expect(trigger).toHaveAttribute('aria-haspopup', 'dialog');

      fireEvent.click(trigger);
      expect(trigger).toHaveAttribute('aria-expanded', 'true');
      const dialog = screen.getByRole('dialog', {name: title});
      expect(dialog).toHaveAttribute('aria-modal', 'true');
      for (const text of texts) expect(within(dialog).getByText(text)).toBeInTheDocument();
      expect(screen.getAllByRole('dialog')).toHaveLength(1);

      fireEvent.keyDown(document, {key: 'Escape'});
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
      expect(trigger).toHaveAttribute('aria-expanded', 'false');
    }
  });

  it('关闭后打开另一张 KPI：内容切换，不叠加弹窗', () => {
    renderLive(readySnapshot());

    fireEvent.click(screen.getByTestId('scheduler-kpi-running'));
    expect(screen.getByRole('dialog', {name: '运行中'})).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('scheduler-kpi-overlay'));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId('scheduler-kpi-jobs'));
    expect(screen.getByRole('dialog', {name: '已注册任务'})).toBeInTheDocument();
    expect(screen.queryByRole('dialog', {name: '运行中'})).not.toBeInTheDocument();
    expect(screen.getAllByRole('dialog')).toHaveLength(1);
  });

  it('四张卡的数量与对应列表条数一致', () => {
    renderLive(readySnapshot());
    const countOf = (dialog: HTMLElement) => dialog.querySelector('.scheduler-kpi-count')?.textContent ?? '';

    fireEvent.click(screen.getByTestId('scheduler-kpi-running'));
    const runningDialog = screen.getByRole('dialog', {name: '运行中'});
    expect(countOf(runningDialog)).toBe('2个任务');
    expect(within(runningDialog).getAllByRole('listitem')).toHaveLength(3); // 1 个当前工作 + 2 个运行中任务
    fireEvent.keyDown(document, {key: 'Escape'});

    fireEvent.click(screen.getByTestId('scheduler-kpi-jobs'));
    const jobsDialog = screen.getByRole('dialog', {name: '已注册任务'});
    expect(countOf(jobsDialog)).toBe('10个');
    expect(within(jobsDialog).getAllByRole('listitem')).toHaveLength(10);
    fireEvent.keyDown(document, {key: 'Escape'});

    fireEvent.click(screen.getByTestId('scheduler-kpi-runs'));
    const runsDialog = screen.getByRole('dialog', {name: '近期执行'});
    expect(countOf(runsDialog)).toBe('7条');
    expect(within(runsDialog).getAllByRole('listitem')).toHaveLength(7);
    fireEvent.keyDown(document, {key: 'Escape'});

    fireEvent.click(screen.getByTestId('scheduler-kpi-attention'));
    const attentionDialog = screen.getByRole('dialog', {name: '需要关注'});
    expect(countOf(attentionDialog)).toBe('2个');
    expect(within(attentionDialog).getAllByRole('listitem')).toHaveLength(2);
  });

  it('已注册任务与近期执行列表不展示英文 jobId，已注册任务按下次执行升序', () => {
    renderLive(readySnapshot());

    fireEvent.click(screen.getByTestId('scheduler-kpi-jobs'));
    const jobsDialog = screen.getByRole('dialog', {name: '已注册任务'});
    const jobs = within(jobsDialog).getAllByRole('listitem');
    expect(jobs[0]).toHaveTextContent('待分析信号消费');
    expect(jobs[1]).toHaveTextContent('风险提醒推送');
    expect(jobsDialog).not.toHaveTextContent('source-12');
    expect(jobsDialog).not.toHaveTextContent('tyc-shard-0');
    // 内部维护任务既不在列表里，也不参与计数。
    expect(jobsDialog).not.toHaveTextContent('runtime-heartbeat');
    expect(jobsDialog).not.toHaveTextContent('持久化调度器心跳');

    fireEvent.keyDown(document, {key: 'Escape'});
    fireEvent.click(screen.getByTestId('scheduler-kpi-runs'));
    const runsDialog = screen.getByRole('dialog', {name: '近期执行'});
    expect(runsDialog).not.toHaveTextContent('signal-consume');
    expect(runsDialog).not.toHaveTextContent('cleanup');
    expect(within(runsDialog).getAllByRole('listitem')).toHaveLength(7);
  });

  it('需要关注为空时显示空态，不虚构条目', () => {
    renderLive(readySnapshot({
      recent_runs: [recentRun({id: 1, job_id: 'collect', status: 'completed'})],
    }));

    const trigger = screen.getByTestId('scheduler-kpi-attention');
    expect(trigger).toHaveTextContent('0');
    fireEvent.click(trigger);
    const dialog = screen.getByRole('dialog', {name: '需要关注'});
    expect(within(dialog).getByText('当前没有需要关注的执行')).toBeInTheDocument();
    expect(within(dialog).queryAllByRole('listitem')).toHaveLength(0);
  });

  it('Esc 关闭详情并把焦点还给触发的 KPI 卡', () => {
    renderLive(readySnapshot());
    const trigger = screen.getByTestId('scheduler-kpi-runs');
    fireEvent.click(trigger);
    expect(screen.getByRole('dialog', {name: '近期执行'})).toBeInTheDocument();

    fireEvent.keyDown(document, {key: 'Escape'});
    expect(screen.queryByRole('dialog', {name: '近期执行'})).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it('关闭按钮与遮罩点击都能关闭详情', () => {
    renderLive(readySnapshot());

    const trigger = screen.getByTestId('scheduler-kpi-running');
    fireEvent.click(trigger);
    fireEvent.click(screen.getByRole('button', {name: '关闭'}));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();

    fireEvent.click(screen.getByTestId('scheduler-kpi-jobs'));
    fireEvent.click(screen.getByTestId('scheduler-kpi-overlay'));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});

describe('原始日志入口', () => {
  it('没有 system_log_view 权限时不渲染入口', () => {
    renderLive(readySnapshot(), {canViewLogs: false});
    expect(screen.queryByTestId('scheduler-log-open')).not.toBeInTheDocument();
  });

  it('有权限时可打开终端抽屉并展示原始行', async () => {
    vi.spyOn(api, 'schedulerLogs').mockResolvedValue({...logPage});
    renderLive(readySnapshot(), {canViewLogs: true});
    await openLog();
    expect(screen.getByRole('dialog', {name: '运行日志'})).toBeInTheDocument();
    expect(await screen.findByText(/采集完成 source-12/)).toBeInTheDocument();
    expect(screen.getByText(/天眼查分片执行异常/)).toBeInTheDocument();
  });

  it('异常筛选保留 ERROR 行并隐藏 INFO 行', async () => {
    vi.spyOn(api, 'schedulerLogs').mockResolvedValue({...logPage});
    renderLive(readySnapshot(), {canViewLogs: true});
    await openLog();
    fireEvent.click(screen.getByRole('button', {name: '异常'}));
    expect(screen.queryByText(/采集完成 source-12/)).not.toBeInTheDocument();
    expect(screen.getByText(/天眼查分片执行异常/)).toBeInTheDocument();
  });

  it('文件缺失时显示明确原因，不伪造日志行', async () => {
    vi.spyOn(api, 'schedulerLogs').mockResolvedValue({
      available: false,
      reason: 'missing',
      lines: [],
      next_cursor: null,
      reset: false,
      truncated: false,
      updated_at: null,
    });
    renderLive(readySnapshot(), {canViewLogs: true});
    await openLog();
    expect(await screen.findByText(/日志文件尚未生成/)).toBeInTheDocument();
    expect(screen.queryByText(/采集完成 source-12/)).not.toBeInTheDocument();
  });

  it('403 时就地提示权限不足；401 交给统一错误出口', async () => {
    const onError = vi.fn();
    vi.spyOn(api, 'schedulerLogs').mockRejectedValueOnce(new ApiError(403, 'forbidden'));
    renderLive(readySnapshot(), {canViewLogs: true, onError});
    await openLog();
    expect(await screen.findByText(/没有查看原始调度日志的权限/)).toBeInTheDocument();
    expect(onError).not.toHaveBeenCalled();

    cleanup();
    vi.spyOn(api, 'schedulerLogs').mockRejectedValueOnce(new ApiError(401, 'unauthorized'));
    renderLive(readySnapshot(), {canViewLogs: true, onError});
    await openLog();
    expect(onError).toHaveBeenCalledWith(expect.objectContaining({status: 401}));
  });

  it('Esc 关闭抽屉并把焦点还给入口按钮', async () => {
    vi.spyOn(api, 'schedulerLogs').mockResolvedValue({...logPage});
    renderLive(readySnapshot(), {canViewLogs: true});
    const trigger = screen.getByTestId('scheduler-log-open');
    await openLog();
    act(() => {
      fireEvent.keyDown(screen.getByRole('dialog', {name: '运行日志'}), {key: 'Escape'});
    });
    expect(screen.queryByRole('dialog', {name: '运行日志'})).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });
});
