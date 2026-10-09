import {describe, expect, it} from 'vitest';
import type {MonitoringScheduledJob, MonitoringScheduledRun} from '../api';
import {detectScheduledTransfer} from './SchedulerLiveTransfer';

// 跨区投影事件判定是纯函数：只用两份快照的真实数据对账，
// 不读取本地时钟、不生成日志、不修改计划数据。

const scheduledJob = (overrides: Partial<MonitoringScheduledJob> = {}): MonitoringScheduledJob => ({
  job_id: 'collect:17',
  name: '官方风险源 采集',
  next_run_at: '2026-09-11T06:30:00Z',
  ...overrides,
});

const scheduledRun = (overrides: Partial<MonitoringScheduledRun> = {}): MonitoringScheduledRun => ({
  id: 42,
  job_id: 'collect:17',
  name: '官方风险源 采集',
  status: 'completed',
  scheduled_run_at: '2026-09-11T06:30:00Z',
  started_at: '2026-09-11T06:30:02Z',
  finished_at: '2026-09-11T06:30:14Z',
  ...overrides,
});

const baselineOf = (jobs: readonly MonitoringScheduledJob[], runIds: readonly number[]) => ({
  jobs,
  runIds: new Set(runIds),
});

describe('detectScheduledTransfer（跨区投影事件判定）', () => {
  it('初次加载（没有上一份快照）不产生事件', () => {
    expect(detectScheduledTransfer(null, [scheduledRun()], new Set())).toBeNull();
  });

  it('新 run 与上一快照最早非 null 计划头同 job、计划时刻同一时刻：命中', () => {
    const match = detectScheduledTransfer(baselineOf([scheduledJob()], [1]), [scheduledRun({id: 2})], new Set());
    expect(match?.run.id).toBe(2);
    expect(match?.from.job_id).toBe('collect:17');
    expect(match?.from.next_run_at).toBe('2026-09-11T06:30:00Z');
  });

  it('同一时刻的不同 UTC 表示（+08:00 与 Z）视为等价', () => {
    const match = detectScheduledTransfer(
      baselineOf([scheduledJob({next_run_at: '2026-09-11T14:30:00+08:00'})], [1]),
      [scheduledRun({id: 2, scheduled_run_at: '2026-09-11T06:30:00Z'})],
      new Set(),
    );
    expect(match?.run.id).toBe(2);
  });

  it('id 已存在于上一快照（如仅状态变更）不触发', () => {
    expect(detectScheduledTransfer(baselineOf([scheduledJob()], [2]), [scheduledRun({id: 2, status: 'running'})], new Set())).toBeNull();
  });

  it('id 已在已处理集合（重复事件）不触发', () => {
    expect(detectScheduledTransfer(baselineOf([scheduledJob()], [1]), [scheduledRun({id: 2})], new Set([2]))).toBeNull();
  });

  it('job_id 与计划头不一致不触发', () => {
    expect(detectScheduledTransfer(
      baselineOf([scheduledJob({job_id: 'collect:17'})], [1]),
      [scheduledRun({id: 2, job_id: 'collect:23'})],
      new Set(),
    )).toBeNull();
  });

  it('scheduled_run_at 与计划头 next_run_at 不等价（过期历史）不触发', () => {
    expect(detectScheduledTransfer(
      baselineOf([scheduledJob()], [1]),
      [scheduledRun({id: 2, scheduled_run_at: '2026-09-11T06:29:59Z'})],
      new Set(),
    )).toBeNull();
  });

  it('notify 排在第一位但不产 run：第二位的 source 真实 run 仍能对账（不被首条计划挡住）', () => {
    const jobs = [
      scheduledJob({job_id: 'notify', name: '通知扫描', next_run_at: '2026-09-11T06:29:00Z'}),
      scheduledJob({job_id: 'source:17', name: '官方风险源 采集', next_run_at: '2026-09-11T06:30:00Z'}),
    ];
    const match = detectScheduledTransfer(baselineOf(jobs, [1]), [
      scheduledRun({id: 2, job_id: 'source:17', scheduled_run_at: '2026-09-11T06:30:00Z'}),
    ], new Set());
    expect(match?.from.job_id).toBe('source:17');
    expect(match?.run.id).toBe(2);
  });

  it('多个 job 同时出现新 run：取上一快照排期最早的真实记录，只返回一个匹配', () => {
    const jobs = [
      scheduledJob({job_id: 'source:23', name: '第二官方源 采集', next_run_at: '2026-09-11T06:31:00Z'}),
      scheduledJob({job_id: 'source:17', name: '官方风险源 采集', next_run_at: '2026-09-11T06:30:00Z'}),
    ];
    const match = detectScheduledTransfer(baselineOf(jobs, [1]), [
      scheduledRun({id: 2, job_id: 'source:23', scheduled_run_at: '2026-09-11T06:31:00Z'}),
      scheduledRun({id: 3, job_id: 'source:17', scheduled_run_at: '2026-09-11T06:30:00Z'}),
    ], new Set());
    expect(match?.from.job_id).toBe('source:17');
    expect(match?.run.id).toBe(3);
  });

  it('多个新 run 中只有与某条计划对账成功的那条命中', () => {
    const match = detectScheduledTransfer(baselineOf([scheduledJob()], [1]), [
      scheduledRun({id: 2, scheduled_run_at: '2026-09-11T06:29:00Z'}),
      scheduledRun({id: 3}),
    ], new Set());
    expect(match?.run.id).toBe(3);
  });

  it('计划头时间非法时不触发', () => {
    expect(detectScheduledTransfer(baselineOf([scheduledJob({next_run_at: 'not-a-date'})], [1]), [scheduledRun({id: 2})], new Set())).toBeNull();
  });

  it('计划头全部无后续排期（next_run_at 全 null）时不触发', () => {
    expect(detectScheduledTransfer(baselineOf([scheduledJob({next_run_at: null})], [1]), [scheduledRun({id: 2})], new Set())).toBeNull();
  });
});
