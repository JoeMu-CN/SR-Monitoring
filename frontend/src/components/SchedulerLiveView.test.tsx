import {act, cleanup, fireEvent, render, screen} from '@testing-library/react';
import {createElement, type HTMLAttributes, type ReactNode} from 'react';
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {api, ApiError, type MonitoringHealthRead} from '../api';
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
    expect(detail).toHaveTextContent('source-12');
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
