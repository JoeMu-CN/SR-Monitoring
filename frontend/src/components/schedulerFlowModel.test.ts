import {describe, expect, it} from 'vitest';
import type {MonitoringHealthRead} from '../api';
import {
  buildSchedulerFlowModel,
  formatShortDateTime,
  memberCountLabel,
  nodeStatusLabel,
} from './schedulerFlowModel';

type Scheduler = MonitoringHealthRead['scheduler'];
type ScheduledJob = Scheduler['scheduled_jobs'][number];
type RecentRun = Scheduler['recent_runs'][number];
type Work = Scheduler['current_work'][number];
type Processing = MonitoringHealthRead['processing'];

const job = (overrides: Partial<ScheduledJob>): ScheduledJob => ({
  job_id: 'collect',
  name: '定时采集与处理',
  next_run_at: '2026-10-25T13:00:00Z',
  ...overrides,
});

const run = (overrides: Partial<RecentRun>): RecentRun => ({
  id: 1,
  job_id: 'collect',
  name: '定时采集与处理',
  status: 'completed',
  scheduled_run_at: '2026-10-25T12:00:00Z',
  started_at: '2026-10-25T12:00:01Z',
  finished_at: '2026-10-25T12:00:13Z',
  ...overrides,
});

const work = (overrides: Partial<Work> = {}): Work => ({
  kind: 'source_collection',
  job_key: 'collect:12',
  source_id: 12,
  source_name: '工信部',
  stage: 'collecting',
  started_at: '2026-10-25T12:10:00Z',
  item_id: null,
  ...overrides,
});

const processing: Processing = {
  total: 42,
  classification_failed: 0,
  backlog_over_1h: 3,
  oldest_pending_age_seconds: 3600,
  processing_state_counts: {},
  last_run: {
    status: 'succeeded',
    started_at: '2026-10-25T12:00:00Z',
    finished_at: '2026-10-25T12:00:20Z',
    processed: 5,
    filtered: 1,
    failed: 0,
  },
};

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

const richScheduler = (): Scheduler => baseScheduler({
  current_work: [
    work(),
    work({kind: 'pending_signal_processing', job_key: 'pending_signals', source_id: null, source_name: null, stage: 'processing_signal', item_id: 9001}),
  ],
  scheduled_jobs: [
    job({job_id: 'collect', next_run_at: '2026-10-25T12:30:00Z'}),
    job({job_id: 'source-12', name: '工信部 采集', next_run_at: '2026-10-25T12:20:00Z'}),
    job({job_id: 'source-13', name: '市场监管总局 采集', next_run_at: '2026-10-25T12:25:00Z'}),
    job({job_id: 'tyc-shard-0', name: '天眼查核查 · 分片 0', next_run_at: '2026-10-26T02:00:00Z'}),
    job({job_id: 'signal-consume', name: '待分析信号消费', next_run_at: '2026-10-25T12:12:00Z'}),
    job({job_id: 'signal-priority-aging', name: '待分析信号优先级老化', next_run_at: '2026-10-25T13:00:00Z'}),
    job({job_id: 'notify', name: '风险提醒推送', next_run_at: '2026-10-25T12:15:00Z'}),
    job({job_id: 'expire', name: '风险有效期物化', next_run_at: '2026-10-26T03:10:00Z'}),
    job({job_id: 'intelligence', name: '政策与行业情报', next_run_at: '2026-10-26T08:30:00Z'}),
    job({job_id: 'research-daily', name: '创建每日研究任务', next_run_at: '2026-10-26T07:00:00Z'}),
    job({job_id: 'research-weekly', name: '创建周度研究批次', next_run_at: '2026-10-27T08:30:00Z'}),
    job({job_id: 'cleanup', name: '保留清理', next_run_at: '2026-10-26T03:00:00Z'}),
    job({job_id: 'notify-monthly', name: '风险月报推送', next_run_at: '2026-11-01T09:00:00Z'}),
    job({job_id: 'runtime-heartbeat', name: '持久化调度器心跳', next_run_at: '2026-10-25T12:12:00Z'}),
  ],
  recent_runs: [
    run({id: 1, job_id: 'collect', status: 'completed', started_at: '2026-10-25T12:00:01Z', finished_at: '2026-10-25T12:00:13Z'}),
    run({id: 2, job_id: 'source-12', name: '工信部 采集', status: 'running', started_at: '2026-10-25T12:10:00Z', finished_at: null}),
    run({id: 3, job_id: 'source-13', name: '市场监管总局 采集', status: 'error', started_at: '2026-10-25T12:05:00Z', finished_at: '2026-10-25T12:05:08Z'}),
    run({id: 4, job_id: 'tyc-shard-0', name: '天眼查核查 · 分片 0', status: 'missed', scheduled_run_at: '2026-10-25T03:00:00Z', started_at: null, finished_at: null}),
    run({id: 5, job_id: 'signal-consume', name: '待分析信号消费', status: 'running', started_at: '2026-10-25T12:11:20Z', finished_at: null}),
    run({id: 6, job_id: 'signal-priority-aging', name: '待分析信号优先级老化', status: 'completed', started_at: '2026-10-25T12:00:00Z', finished_at: '2026-10-25T12:00:01Z'}),
    run({id: 7, job_id: 'expire', name: '风险有效期物化', status: 'completed', started_at: '2026-10-25T03:10:00Z', finished_at: '2026-10-25T03:10:18Z'}),
    run({id: 8, job_id: 'intelligence', name: '政策与行业情报', status: 'completed', started_at: '2026-10-25T08:30:00Z', finished_at: '2026-10-25T08:34:12Z'}),
    run({id: 9, job_id: 'research-daily', name: '创建每日研究任务', status: 'completed', started_at: '2026-10-25T07:00:00Z', finished_at: '2026-10-25T07:02:08Z'}),
    run({id: 10, job_id: 'cleanup', name: '保留清理', status: 'missed', scheduled_run_at: '2026-10-25T04:00:00Z', started_at: null, finished_at: null}),
    run({id: 11, job_id: 'runtime-heartbeat', name: '持久化调度器心跳', status: 'completed', started_at: '2026-10-25T12:00:00Z', finished_at: '2026-10-25T12:00:00Z'}),
  ],
});

describe('buildSchedulerFlowModel 分组与状态', () => {
  it('四列主图按采集 / 信号池 / 信号处理 / 产出归组', () => {
    const model = buildSchedulerFlowModel(richScheduler(), processing);
    expect(model.nodes.map((node) => node.key)).toEqual([
      'collect', 'source', 'tyc', 'hub', 'consume', 'notify', 'expire', 'intel',
    ]);
    expect(model.nodes.find((node) => node.key === 'hub')?.processing?.total).toBe(42);
  });

  it('聚合节点保留成员，并在运行中之外单独统计异常', () => {
    const model = buildSchedulerFlowModel(richScheduler(), processing);
    const source = model.nodes.find((node) => node.key === 'source');
    expect(source?.members.map((member) => member.jobId)).toEqual(['source-12', 'source-13']);
    expect(source?.status).toBe('running');
    expect(source?.runningCount).toBe(1);
    expect(source?.errorCount).toBe(1);
    expect(memberCountLabel(source!)).toBe('×2');
  });

  it('没有近期记录的 notify 显示无记录，而不是 completed/空闲', () => {
    const model = buildSchedulerFlowModel(richScheduler(), processing);
    const notify = model.nodes.find((node) => node.key === 'notify');
    expect(notify?.status).toBe('no_record');
    expect(notify?.latestRun).toBeNull();
    expect(notify?.nextRunAt).toBe('2026-10-25T12:15:00Z');
  });

  it('内部维护任务不进入业务图，未知 job_id 归入其他任务', () => {
    const scheduler = richScheduler();
    const model = buildSchedulerFlowModel({
      ...scheduler,
      scheduled_jobs: [
        ...scheduler.scheduled_jobs,
        job({job_id: 'mystery-job', name: '未来新增任务', next_run_at: '2026-10-25T14:00:00Z'}),
      ],
    }, processing);
    const allKeys = [...model.nodes, ...model.aux].map((node) => node.key);
    expect(allKeys).not.toContain('runtime-heartbeat');
    expect(model.aux.find((node) => node.key === 'other')?.members[0]?.jobId).toBe('mystery-job');
    expect(model.jobCount).toBe(14);
  });

  it('current_work 让 source 与 signal-consume 在 run 记录之外也标记运行中', () => {
    const scheduler = richScheduler();
    const model = buildSchedulerFlowModel({
      ...scheduler,
      recent_runs: scheduler.recent_runs.filter((item) => item.job_id !== 'source-12' && item.job_id !== 'signal-consume'),
    }, processing);
    expect(model.nodes.find((node) => node.key === 'source')?.status).toBe('running');
    expect(model.nodes.find((node) => node.key === 'consume')?.status).toBe('running');
  });

  it('nextRunAt 取成员最早排期，latestRun 取最新一次执行', () => {
    const model = buildSchedulerFlowModel(richScheduler(), processing);
    const source = model.nodes.find((node) => node.key === 'source');
    expect(source?.nextRunAt).toBe('2026-10-25T12:20:00Z');
    expect(source?.latestRun?.id).toBe(2);
  });

  it('关注数与运行数按 job 去重，异常/错过都计入关注', () => {
    const model = buildSchedulerFlowModel(richScheduler(), processing);
    expect(model.runningCount).toBe(2);
    expect(model.attentionCount).toBe(3);
    expect(model.recentRunCount).toBe(11);
  });

  it('研究轨与低频维护进入独立排期，未知任务不丢失', () => {
    const model = buildSchedulerFlowModel(richScheduler(), processing);
    expect(model.aux.map((node) => node.key)).toEqual(['research', 'aging', 'monthly', 'cleanup']);
    expect(model.aux.find((node) => node.key === 'research')?.members).toHaveLength(2);
  });
});

describe('展示口径', () => {
  it('stale 时运行中标注快照，completed 不写成成功', () => {
    expect(nodeStatusLabel('running', true)).toBe('执行中');
    expect(nodeStatusLabel('running', false)).toBe('执行中（快照）');
    expect(nodeStatusLabel('completed', true)).toBe('执行完成');
    expect(nodeStatusLabel('no_record', true)).toBe('无近期记录');
  });

  it('短时间格式按快照日期判断今天', () => {
    const asOf = new Date(2026, 9, 25, 12, 0).toISOString();
    const next = new Date(2026, 9, 25, 13, 0).toISOString();
    expect(formatShortDateTime(next, asOf)).toBe('今天 13:00');
    expect(formatShortDateTime(null, asOf)).toBe('暂无后续排期');
  });
});
