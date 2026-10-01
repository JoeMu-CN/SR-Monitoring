import {describe, expect, it} from 'vitest';
import type {MonitoringSourceHealth, MonitoringSourceState} from './api';
import {summarizeSourceHealth} from './sourceHealthSummary';

// 最小快照工厂：测试只消费 state，其余字段对齐 api.ts 的 MonitoringSourceHealth 契约。
const healthOf = (sourceId: number, state: MonitoringSourceState): MonitoringSourceHealth => ({
  source_id: sourceId,
  code: `src-${sourceId}`,
  name: `来源 ${sourceId}`,
  state,
  reason_code: 'test',
  last_success_at: null,
  last_attempt_at: null,
  next_expected_at: null,
});

const healthListOf = (...states: MonitoringSourceState[]): MonitoringSourceHealth[] =>
  states.map((state, index) => healthOf(index + 1, state));

describe('summarizeSourceHealth 可调度口径', () => {
  it('2 个 ok 与 8 个 disabled/on_demand 混合时只按 2 个可调度来源判 2/2 正常', () => {
    // Given 2 个 ok + 8 个 disabled/on_demand（旧口径会把 10 个全计入分母并报 2/10 告警）
    const sources = healthListOf(
      'ok',
      'ok',
      'disabled',
      'disabled',
      'disabled',
      'disabled',
      'disabled',
      'on_demand',
      'on_demand',
      'on_demand',
    );

    // When 汇总信息源健康
    const summary = summarizeSourceHealth(sources);

    // Then 分母只含 2 个可调度来源，全部正常
    expect(summary).toEqual({state: 'ok', detail: '2/2 定时信息源正常'});
  });

  it('可调度来源含 overdue 时按 1/2 判告警', () => {
    // Given 1 个 ok 与 1 个 overdue 的可调度来源
    const sources = healthListOf('ok', 'overdue');

    // When 汇总信息源健康
    const summary = summarizeSourceHealth(sources);

    // Then 未全绿，state 为 warn
    expect(summary).toEqual({state: 'warn', detail: '1/2 定时信息源正常'});
  });

  it('全部为 disabled/on_demand 时返回中性文案且不告警', () => {
    // Given 没有可调度来源
    const sources = healthListOf('disabled', 'on_demand', 'disabled', 'on_demand');

    // When 汇总信息源健康
    const summary = summarizeSourceHealth(sources);

    // Then 中性「未启用定时采集」文案
    expect(summary).toEqual({state: 'ok', detail: '未启用定时采集的信息源'});
  });

  it('空数组与无可调度来源等价', () => {
    // Given 空快照
    const sources: readonly MonitoringSourceHealth[] = [];

    // When 汇总信息源健康
    const summary = summarizeSourceHealth(sources);

    // Then 同无可调度来源的中性文案
    expect(summary).toEqual({state: 'ok', detail: '未启用定时采集的信息源'});
  });

  it('never_run 计入分母且视为非正常（1 ok + 1 never_run + 1 disabled → 1/2）', () => {
    // Given 1 个 ok、1 个 never_run 可调度来源与 1 个 disabled 不可调度来源
    const sources = healthListOf('ok', 'never_run', 'disabled');

    // When 汇总信息源健康
    const summary = summarizeSourceHealth(sources);

    // Then never_run 推高分母但不计 ok，state 为 warn
    expect(summary).toEqual({state: 'warn', detail: '1/2 定时信息源正常'});
  });
});
