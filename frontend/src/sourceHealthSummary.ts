import type {MonitoringSourceHealth, MonitoringSourceState} from './api';

// 与后端 backend/app/scheduler/health_rules.py 的可调度定义对齐：
// disabled / on_demand 不参与定时采集统计，不得计入“信息源正常”的分母。
const NON_SCHEDULABLE_STATES: ReadonlySet<MonitoringSourceState> = new Set(['disabled', 'on_demand']);

export interface SourceHealthSummary {
  readonly state: 'ok' | 'warn';
  readonly detail: string;
}

export function summarizeSourceHealth(sources: readonly MonitoringSourceHealth[]): SourceHealthSummary {
  const schedulable = sources.filter((source) => !NON_SCHEDULABLE_STATES.has(source.state));
  if (schedulable.length === 0) {
    return {state: 'ok', detail: '未启用定时采集的信息源'};
  }
  const okCount = schedulable.filter((source) => source.state === 'ok').length;
  const detail = `${okCount}/${schedulable.length} 定时信息源正常`;
  return {state: okCount === schedulable.length ? 'ok' : 'warn', detail};
}
