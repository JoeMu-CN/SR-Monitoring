import {Ban, CalendarClock, CircleDot, ClockAlert, History, Loader2, XCircle, type LucideIcon} from 'lucide-react';
import type {MonitoringScheduledJob, MonitoringScheduledRun, MonitoringScheduledRunStatus} from '../api';

// SchedulerActivityModal 私有子组件：深色任务会话面板的展示块与只读时间格式化。
// 只消费 monitoring-health 契约中的时间戳与状态码，不包含信号内容、异常文本或日志。

export const formatDateTime = (value: string): string => (
  Number.isNaN(new Date(value).getTime()) ? '未知' : new Date(value).toLocaleString('zh-CN', {hour12: false})
);

const formatSeconds = (totalSeconds: number): string => {
  if (totalSeconds < 60) return `${totalSeconds} 秒`;
  const totalMinutes = Math.floor(totalSeconds / 60);
  if (totalMinutes < 60) return `${totalMinutes} 分钟`;
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  return minutes === 0 ? `${hours} 小时` : `${hours} 小时 ${minutes} 分钟`;
};

// 已运行时长以快照的 as_of 为基准，不引入本地计时器或第二套轮询；
// 开始时间缺失、非法或晚于快照时刻（倒序）时返回占位符，不用 0 冒充观测时长。
export const formatRunningDuration = (startedAt: string | null, asOf: string): string => {
  if (startedAt === null) return '—';
  const startedMs = new Date(startedAt).getTime();
  const asOfMs = new Date(asOf).getTime();
  if (!Number.isFinite(startedMs) || !Number.isFinite(asOfMs) || asOfMs < startedMs) return '—';
  return formatSeconds(Math.floor((asOfMs - startedMs) / 1000));
};

// 实际耗时只由两端真实时间戳计算；任一端缺失（如仍在运行）、非法或倒序（结束早于开始）时返回占位符，不编造。
// 导出供 SchedulerLiveView 复用同一耗时口径。
export const formatDuration = (startedAt: string | null, finishedAt: string | null): string => {
  if (startedAt === null || finishedAt === null) return '—';
  const startedMs = new Date(startedAt).getTime();
  const finishedMs = new Date(finishedAt).getTime();
  if (!Number.isFinite(startedMs) || !Number.isFinite(finishedMs) || finishedMs < startedMs) return '—';
  return formatSeconds(Math.floor((finishedMs - startedMs) / 1000));
};

// 最早的下一次执行：只取 next_run_at 非 null 的任务；时间并列时保留列表先出现者。
export type UpcomingScheduledJob = MonitoringScheduledJob & {readonly next_run_at: string};

export const earliestScheduledJob = (jobs: readonly MonitoringScheduledJob[]): UpcomingScheduledJob | null => {
  let earliest: UpcomingScheduledJob | null = null;
  let earliestMs = Number.POSITIVE_INFINITY;
  for (const job of jobs) {
    if (job.next_run_at === null) continue;
    const timeMs = new Date(job.next_run_at).getTime();
    if (!Number.isFinite(timeMs) || timeMs >= earliestMs) continue;
    earliest = {...job, next_run_at: job.next_run_at};
    earliestMs = timeMs;
  }
  return earliest;
};

export interface RunStatusMeta {
  readonly label: string;
  readonly icon: LucideIcon;
  readonly iconClass: string;
  readonly chipClass: string;
  readonly spinning: boolean;
}

// 五种运行状态穷举映射：后端新增状态时编译期强制补齐；文案保持诚实，completed 不写成「成功」，
// 也不使用成功绿与对勾图标（执行完成 ≠ 业务处理成功）。
// 导出供 SchedulerLiveView 复用，避免两套状态文案漂移。
export const RUN_STATUS_META: Record<MonitoringScheduledRunStatus, RunStatusMeta> = {
  running: {label: '执行中', icon: Loader2, iconClass: 'text-sky-300', chipClass: 'bg-sky-400/10 text-sky-300 ring-sky-400/30', spinning: true},
  completed: {label: '执行完成', icon: CircleDot, iconClass: 'text-slate-300', chipClass: 'bg-slate-400/10 text-slate-300 ring-slate-500/40', spinning: false},
  error: {label: '执行异常', icon: XCircle, iconClass: 'text-[#ff3b30]', chipClass: 'bg-[#ff3b30]/10 text-[#ff3b30] ring-[#ff3b30]/30', spinning: false},
  missed: {label: '错过执行', icon: ClockAlert, iconClass: 'text-amber-300', chipClass: 'bg-amber-400/10 text-amber-300 ring-amber-400/30', spinning: false},
  max_instances: {label: '并发上限跳过', icon: Ban, iconClass: 'text-slate-400', chipClass: 'bg-slate-400/10 text-slate-300 ring-slate-500/40', spinning: false},
};

export const AsOfLine = ({asOf}: {readonly asOf: string}) => (
  <p data-testid="scheduler-activity-as-of" className="text-[11px] text-slate-400">
    数据截至{' '}
    <span className="font-mono">{formatDateTime(asOf)}</span>
  </p>
);

export const SchedulerStatusStrip = ({asOf}: {readonly asOf: string}) => (
  <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 rounded-xl border border-slate-800 bg-[#0e1726] px-3 py-2">
    <span className="inline-flex items-center gap-2 text-[11px] font-bold text-slate-300">
      <span aria-hidden="true" className="h-2 w-2 rounded-full bg-[#34c759]" />
      调度器心跳正常
    </span>
    <AsOfLine asOf={asOf} />
  </div>
);

export const SectionHeader = ({icon: Icon, title, count}: {readonly icon: LucideIcon; readonly title: string; readonly count: number}) => (
  <div className="flex items-center justify-between gap-2">
    <h3 className="inline-flex items-center gap-1.5 text-[11px] font-bold tracking-wide text-slate-400">
      <Icon aria-hidden="true" className="h-3.5 w-3.5" />
      {title}
    </h3>
    <span className="font-mono text-[11px] font-semibold text-slate-400">{count}</span>
  </div>
);

const EmptyHint = ({testId, children}: {readonly testId: string; readonly children: string}) => (
  <p data-testid={testId} className="rounded-xl border border-dashed border-slate-700 px-3 py-4 text-center text-[11px] text-slate-400">{children}</p>
);

// 空闲时的高亮卡：只展示最早的真实 next_run_at，不引入倒计时或第二套时间推算。
export const NextRunCard = ({job}: {readonly job: UpcomingScheduledJob}) => (
  <div data-testid="scheduler-next-run" className="rounded-xl border border-[#007aff]/50 bg-[#007aff]/10 p-3.5">
    <p className="flex items-center gap-1.5 text-[11px] font-bold text-sky-300">
      <CalendarClock aria-hidden="true" className="h-3.5 w-3.5" />
      下一次执行
    </p>
    <p className="mt-1.5 break-words text-sm font-bold text-white">{job.name}</p>
    <p className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px] text-slate-400">
      <span className="font-mono text-slate-200">{formatDateTime(job.next_run_at)}</span>
      <span className="break-all font-mono text-slate-400">{job.job_id}</span>
    </p>
  </div>
);

const ScheduleRow = ({job, staleLabel}: {readonly job: MonitoringScheduledJob; readonly staleLabel: boolean}) => (
  <li
    data-testid={`scheduler-schedule-item-${job.job_id}`}
    className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 rounded-xl border border-slate-800 bg-[#0e1726] px-3 py-2.5"
  >
    <span className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5">
      <CalendarClock aria-hidden="true" className="h-3.5 w-3.5 shrink-0 text-slate-500" />
      <span className="break-words text-[13px] font-semibold text-slate-200">{job.name}</span>
      <span className="break-all font-mono text-[10px] text-slate-400">{job.job_id}</span>
    </span>
    {job.next_run_at === null ? (
      <span className="text-[11px] text-slate-400">暂无后续排期</span>
    ) : (
      <span className="whitespace-nowrap text-[11px] text-slate-400">
        {staleLabel ? '最近排期快照' : '下次执行'}{' '}
        <span className="font-mono text-slate-300">{formatDateTime(job.next_run_at)}</span>
      </span>
    )}
  </li>
);

export const ScheduleList = ({jobs, stale = false}: {readonly jobs: readonly MonitoringScheduledJob[]; readonly stale?: boolean}) => (
  <section className="space-y-3">
    {/* 标题层级与执行流统一：text-sm 白色标题 + 紧邻文本的计数，不再 justify-between 推远。 */}
    <h3 className="inline-flex flex-wrap items-center gap-1.5 text-sm font-bold text-white">
      <CalendarClock aria-hidden="true" className="h-4 w-4 text-slate-400" />
      计划任务流
      <span data-testid="scheduler-schedule-count" className="font-mono text-[11px] font-semibold text-slate-400">{jobs.length}</span>
    </h3>
    {jobs.length === 0 ? (
      <EmptyHint testId="scheduler-schedule-empty">调度器当前没有已注册的计划任务。</EmptyHint>
    ) : (
      // 单列排布：长任务名不被两列网格挤压错位。
      <ul aria-label="计划任务流" className="grid gap-2">
        {jobs.map((job) => <ScheduleRow key={job.job_id} job={job} staleLabel={stale} />)}
      </ul>
    )}
  </section>
);

const HistoryRow = ({run}: {readonly run: MonitoringScheduledRun}) => {
  const meta = RUN_STATUS_META[run.status];
  const StatusIcon = meta.icon;
  return (
    <li data-testid={`scheduler-history-item-${run.id}`} className="rounded-xl border border-slate-800 bg-[#0e1726] p-3">
      <div className="flex flex-wrap items-center gap-2">
        <StatusIcon
          aria-hidden="true"
          data-testid={meta.spinning ? 'scheduler-run-spinner' : undefined}
          className={`h-4 w-4 shrink-0 ${meta.iconClass}${meta.spinning ? ' animate-spin motion-reduce:animate-none' : ''}`}
        />
        <span className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] font-bold ring-1 ring-inset ${meta.chipClass}`}>{meta.label}</span>
        <span className="break-words text-[13px] font-bold text-slate-100">{run.name}</span>
        <span className="break-all font-mono text-[10px] text-slate-400">{run.job_id}</span>
      </div>
      <p className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px] text-slate-400">
        <span className="whitespace-nowrap">计划{' '}<span className="font-mono text-slate-300">{formatDateTime(run.scheduled_run_at)}</span></span>
        <span className="whitespace-nowrap">开始{' '}<span className="font-mono text-slate-300">{run.started_at === null ? '—' : formatDateTime(run.started_at)}</span></span>
        <span className="whitespace-nowrap">耗时{' '}<span className="font-mono text-slate-300">{formatDuration(run.started_at, run.finished_at)}</span></span>
      </p>
    </li>
  );
};

export const HistoryList = ({runs}: {readonly runs: readonly MonitoringScheduledRun[]}) => (
  <section className="space-y-2">
    <SectionHeader icon={History} title="最近执行记录" count={runs.length} />
    {runs.length === 0 ? (
      <EmptyHint testId="scheduler-history-empty">暂无调度执行历史记录。</EmptyHint>
    ) : (
      <ul aria-label="最近执行记录" className="space-y-2">
        {runs.map((run) => <HistoryRow key={run.id} run={run} />)}
      </ul>
    )}
  </section>
);
