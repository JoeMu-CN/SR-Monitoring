import {useEffect, useId, useMemo, useRef, type MouseEvent, type ReactNode} from 'react';
import {createPortal} from 'react-dom';
import {X} from 'lucide-react';
import type {MonitoringSchedulerHealth, MonitoringSchedulerWork} from '../api';
import {formatDuration, formatRunningDuration} from './SchedulerActivityPanels';
import {
  formatShortDateTime,
  nodeStatusLabel,
  selectRegisteredJobs,
  type FlowMember,
  type FlowNodeStatus,
  type SchedulerFlowModel,
} from './schedulerFlowModel';
import './SchedulerKpiDetail.css';

/**
 * KPI 卡只读详情。
 *
 * 数据全部来自当前 snapshot 与 flowModel：不额外取数、不新增指标。
 * 每个列表与对应 KPI 同源：运行中/需要关注去重 members，已注册任务用同一过滤口径，
 * 近期执行直接用 recent_runs，因此列表条数与卡片数值一致。
 */

export type SchedulerKpiKey = 'running' | 'jobs' | 'runs' | 'attention';

const DETAIL_META: Record<SchedulerKpiKey, {readonly title: string; readonly unit: string}> = {
  running: {title: '运行中', unit: '个任务'},
  jobs: {title: '已注册任务', unit: '个'},
  runs: {title: '近期执行', unit: '条'},
  attention: {title: '需要关注', unit: '个'},
};

/** 同一 jobId 可能出现在多个节点：合并时优先保留带 latestRun 的成员。 */
const dedupeMembers = (model: SchedulerFlowModel): FlowMember[] => {
  const byJob = new Map<string, FlowMember>();
  for (const members of model.membersByKey.values()) {
    for (const member of members) {
      const existing = byJob.get(member.jobId);
      if (existing === undefined || (existing.latestRun === null && member.latestRun !== null)) {
        byJob.set(member.jobId, member);
      }
    }
  }
  return [...byJob.values()];
};

const nextRunMs = (value: string | null): number => {
  if (value === null) return Number.POSITIVE_INFINITY;
  const ms = new Date(value).getTime();
  return Number.isFinite(ms) ? ms : Number.POSITIVE_INFINITY;
};

const workName = (work: MonitoringSchedulerWork): string => {
  if (work.kind === 'source_collection') return work.source_name ?? '信源采集';
  return work.item_id === null ? '待处理信号批次' : `信号 #${work.item_id}`;
};

const workStageLabel = (work: MonitoringSchedulerWork): string => (
  work.kind === 'source_collection' ? '来源采集' : '信号处理'
);

interface RowProps {
  readonly name: string;
  readonly meta?: string;
  readonly status?: FlowNodeStatus;
  readonly statusText?: string;
  readonly value?: ReactNode;
}

const Row = ({name, meta, status, statusText, value}: RowProps) => (
  <li className="scheduler-kpi-row">
    <span className="scheduler-kpi-row-main">
      <span className="scheduler-kpi-row-name">{name}</span>
      {meta !== undefined && <span className="scheduler-kpi-row-meta">{meta}</span>}
    </span>
    {status !== undefined && statusText !== undefined && (
      <span className={`sflow-badge sflow-badge--${status}`}>{statusText}</span>
    )}
    {value !== undefined && <span className="scheduler-kpi-row-value">{value}</span>}
  </li>
);

const Section = ({title, count, children}: {
  readonly title: string;
  readonly count: number;
  readonly children: ReactNode;
}) => (
  <section className="scheduler-kpi-section">
    <h3 className="scheduler-kpi-section-title">{title}<span>{count}</span></h3>
    {children}
  </section>
);

const Empty = ({children}: {readonly children: ReactNode}) => (
  <p className="scheduler-kpi-empty">{children}</p>
);

interface SchedulerKpiDetailProps {
  readonly detail: SchedulerKpiKey;
  readonly scheduler: MonitoringSchedulerHealth;
  readonly model: SchedulerFlowModel;
  readonly asOf: string;
  readonly live: boolean;
  readonly onClose: () => void;
}

export const SchedulerKpiDetail = ({detail, scheduler, model, asOf, live, onClose}: SchedulerKpiDetailProps) => {
  const titleId = useId();
  const closeRef = useRef<HTMLButtonElement | null>(null);
  const members = useMemo(() => dedupeMembers(model), [model]);
  const runningMembers = useMemo(
    () => members.filter((member) => member.status === 'running'),
    [members],
  );
  const attentionMembers = useMemo(
    () => members.filter((member) => member.status === 'error' || member.status === 'missed'),
    [members],
  );
  const jobs = useMemo(
    () => selectRegisteredJobs(scheduler)
      .slice()
      .sort((left, right) => (
        nextRunMs(left.next_run_at) - nextRunMs(right.next_run_at) || left.name.localeCompare(right.name, 'zh-CN')
      )),
    [scheduler],
  );

  useEffect(() => {
    closeRef.current?.focus();
  }, [detail]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      onClose();
    };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [onClose]);

  const detailMeta = DETAIL_META[detail];
  const count = detail === 'running'
    ? runningMembers.length
    : detail === 'jobs'
      ? jobs.length
      : detail === 'runs'
        ? scheduler.recent_runs.length
        : attentionMembers.length;
  const handleOverlayClick = (event: MouseEvent<HTMLDivElement>) => {
    if (event.target === event.currentTarget) onClose();
  };

  const dialog = (
    <div
      className="scheduler-kpi-overlay"
      onClick={handleOverlayClick}
      data-testid="scheduler-kpi-overlay"
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="scheduler-kpi-dialog"
        data-testid={`scheduler-kpi-dialog-${detail}`}
      >
        <header className="scheduler-kpi-head">
          <div className="scheduler-kpi-head-main">
            <h2 id={titleId} className="scheduler-kpi-title">{detailMeta.title}</h2>
            <p className="scheduler-kpi-count">
              <b>{count}</b>
              <em>{detailMeta.unit}</em>
            </p>
          </div>
          <button
            ref={closeRef}
            type="button"
            className="scheduler-kpi-close"
            aria-label="关闭"
            onClick={onClose}
          >
            <X aria-hidden="true" />
          </button>
        </header>

        <div className="scheduler-kpi-body">
          {detail === 'running' && (
            <>
              <Section title="当前工作项" count={scheduler.current_work.length}>
                {scheduler.current_work.length === 0 ? (
                  <Empty>当前没有进行中的工作项</Empty>
                ) : (
                  <ul className="scheduler-kpi-list">
                    {scheduler.current_work.map((work, index) => (
                      <Row
                        key={`${work.kind}-${work.job_key}-${index}`}
                        name={workName(work)}
                        meta={work.started_at === null
                          ? workStageLabel(work)
                          : `${workStageLabel(work)} · 开始 ${formatShortDateTime(work.started_at, asOf)}`}
                        value={formatRunningDuration(work.started_at, asOf)}
                      />
                    ))}
                  </ul>
                )}
              </Section>
              <Section title="运行中的任务" count={runningMembers.length}>
                {runningMembers.length === 0 ? (
                  <Empty>当前没有运行中的任务</Empty>
                ) : (
                  <ul className="scheduler-kpi-list">
                    {runningMembers.map((member) => {
                      const startedAt = member.latestRun?.started_at ?? member.runningWork[0]?.started_at ?? null;
                      return (
                        <Row
                          key={member.jobId}
                          name={member.name}
                          meta={startedAt === null ? '开始时间未知' : `开始 ${formatShortDateTime(startedAt, asOf)}`}
                          value={formatRunningDuration(startedAt, asOf)}
                        />
                      );
                    })}
                  </ul>
                )}
              </Section>
            </>
          )}

          {detail === 'jobs' && (
            jobs.length === 0 ? (
              <Empty>暂无已注册任务</Empty>
            ) : (
              <ul className="scheduler-kpi-list">
                {jobs.map((job) => (
                  <Row
                    key={job.job_id}
                    name={job.name}
                    value={(
                      <>
                        <span className="scheduler-kpi-row-label">下次</span>
                        {formatShortDateTime(job.next_run_at, asOf)}
                      </>
                    )}
                  />
                ))}
              </ul>
            )
          )}

          {detail === 'runs' && (
            scheduler.recent_runs.length === 0 ? (
              <Empty>暂无近期执行</Empty>
            ) : (
              <ul className="scheduler-kpi-list">
                {scheduler.recent_runs.map((run) => {
                  const duration = run.status === 'running'
                    ? formatRunningDuration(run.started_at, asOf)
                    : formatDuration(run.started_at, run.finished_at);
                  const time = run.finished_at !== null
                    ? `完成 ${formatShortDateTime(run.finished_at, asOf)}`
                    : run.started_at !== null
                      ? `开始 ${formatShortDateTime(run.started_at, asOf)}`
                      : `计划 ${formatShortDateTime(run.scheduled_run_at, asOf)}`;
                  return (
                    <Row
                      key={run.id}
                      name={run.name}
                      meta={time}
                      status={run.status}
                      statusText={nodeStatusLabel(run.status, live)}
                      value={duration}
                    />
                  );
                })}
              </ul>
            )
          )}

          {detail === 'attention' && (
            attentionMembers.length === 0 ? (
              <Empty>当前没有需要关注的执行</Empty>
            ) : (
              <ul className="scheduler-kpi-list">
                {attentionMembers.map((member) => {
                  const run = member.latestRun;
                  const lastAt = run === null ? null : run.finished_at ?? run.started_at ?? run.scheduled_run_at;
                  return (
                    <Row
                      key={member.jobId}
                      name={member.name}
                      meta={lastAt === null ? '最近执行时间未知' : `最近 ${formatShortDateTime(lastAt, asOf)}`}
                      status={member.status}
                      statusText={nodeStatusLabel(member.status, live)}
                    />
                  );
                })}
              </ul>
            )
          )}
        </div>
      </div>
    </div>
  );

  return createPortal(dialog, document.body);
};
