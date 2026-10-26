import {useCallback, useEffect, useState} from 'react';
import {ApiError, api} from '../api';
import type {MonitoringScheduledRun} from '../api';
import {formatDuration, formatRunningDuration} from './SchedulerActivityPanels';
import {formatShortDateTime, nodeStatusLabel} from './schedulerFlowModel';
import './SchedulerNodeRuns.css';

/**
 * 单任务最近执行（只读、按需）。
 *
 * 只在选中单任务节点时请求一次；切换节点即中止上一次请求，不轮询、不共享缓存。
 * result 缺失时明确说明没有结构化明细，不用 0 或空摘要冒充。
 */

const RUN_HISTORY_LIMIT = 5;

type RunState =
  | {readonly kind: 'loading'}
  | {readonly kind: 'error'; readonly message: string}
  | {readonly kind: 'ready'; readonly items: readonly MonitoringScheduledRun[]};

const errorMessage = (error: unknown): string => {
  if (error instanceof ApiError) {
    if (error.status === 403) return '当前账号没有查看调度执行历史的权限。';
    if (error.status === 404) return '历史接口尚未部署，当前后端版本不支持。';
    return `读取执行历史失败（HTTP ${error.status}）。`;
  }
  return '无法连接调度历史接口，请稍后重试。';
};

const formatCount = (value: number): string => value.toLocaleString('zh-CN');

const RunRow = ({run, asOf, live}: {
  readonly run: MonitoringScheduledRun;
  readonly asOf: string;
  readonly live: boolean;
}) => {
  const duration = run.status === 'running'
    ? formatRunningDuration(run.started_at, asOf)
    : formatDuration(run.started_at, run.finished_at);
  const time = run.finished_at ?? run.started_at ?? run.scheduled_run_at;
  const summary = run.result?.summary?.trim() ?? '';
  const metrics = run.result?.metrics ?? [];
  const outputs = run.result?.outputs ?? [];
  const hasDetails = summary !== '' || metrics.length > 0 || outputs.length > 0;
  return (
    <li className="sflow-run">
      <div className="sflow-run-head">
        <span className="sflow-run-time">{formatShortDateTime(time, asOf)}</span>
        <span className={`sflow-badge sflow-badge--${run.status}`}>{nodeStatusLabel(run.status, live)}</span>
        <span className="sflow-run-duration">{duration}</span>
      </div>
      {!hasDetails && (
        <p className="sflow-run-note">该次执行没有结构化明细（旧记录或失败）</p>
      )}
      {summary !== '' && <p className="sflow-run-summary">{summary}</p>}
      {metrics.length > 0 && (
        <div className="sflow-run-metrics">
          {metrics.map((metric, index) => (
            <span key={`${metric.label}-${index}`} className="sflow-run-metric">
              <em>{metric.label}</em>
              <b>{formatCount(metric.value)}</b>
            </span>
          ))}
        </div>
      )}
      {outputs.length > 0 && (
        <div className="sflow-run-outputs">
          <span className="sflow-run-outputs-label">产出</span>
          {outputs.map((output, index) => (
            <span key={`${output.label}-${index}`} className="sflow-run-metric">
              <em>{output.label}</em>
              <b>{formatCount(output.value)}</b>
            </span>
          ))}
        </div>
      )}
    </li>
  );
};

interface SchedulerNodeRunsProps {
  readonly jobId: string;
  readonly asOf: string;
  readonly live: boolean;
}

export const SchedulerNodeRuns = ({jobId, asOf, live}: SchedulerNodeRunsProps) => {
  const [reloadToken, setReloadToken] = useState(0);
  const [state, setState] = useState<RunState>({kind: 'loading'});

  useEffect(() => {
    const controller = new AbortController();
    let disposed = false;
    setState({kind: 'loading'});
    api.schedulerJobRuns(jobId, RUN_HISTORY_LIMIT, controller.signal)
      .then((page) => {
        if (disposed) return;
        setState({kind: 'ready', items: page.items});
      })
      .catch((error: unknown) => {
        if (disposed) return;
        if (error instanceof DOMException && error.name === 'AbortError') return;
        setState({kind: 'error', message: errorMessage(error)});
      });
    return () => {
      disposed = true;
      controller.abort();
    };
  }, [jobId, reloadToken]);

  const retry = useCallback(() => setReloadToken((value) => value + 1), []);

  return (
    <section
      className="sflow-runs"
      aria-label="最近执行"
      aria-busy={state.kind === 'loading'}
      data-testid="scheduler-node-runs"
    >
      <div className="sflow-runs-head">
        <h4 className="sflow-runs-title">最近执行</h4>
        {state.kind === 'ready' && state.items.length > 0 && (
          <span className="sflow-runs-count">共 {state.items.length} 次</span>
        )}
      </div>
      {state.kind === 'loading' && <p className="sflow-runs-state">正在读取最近执行…</p>}
      {state.kind === 'error' && (
        <p className="sflow-runs-state is-error">
          {state.message}
          <button type="button" className="sflow-runs-retry" onClick={retry}>重试</button>
        </p>
      )}
      {state.kind === 'ready' && state.items.length === 0 && (
        <p className="sflow-runs-state">最近没有执行记录</p>
      )}
      {state.kind === 'ready' && state.items.length > 0 && (
        <ul className="sflow-runs-list">
          {state.items.map((run) => <RunRow key={run.id} run={run} asOf={asOf} live={live}/>)}
        </ul>
      )}
    </section>
  );
};
