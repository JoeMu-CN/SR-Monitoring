import type {
  MonitoringProcessingHealth,
  MonitoringScheduledJob,
  MonitoringScheduledRun,
  MonitoringSchedulerHealth,
  MonitoringSchedulerWork,
} from '../api';

/**
 * 调度器实况四列网络的数据模型（纯函数，无 React / DOM 依赖）。
 *
 * 口径：
 * - 只使用 monitoring-health 契约中的 scheduled_jobs / recent_runs / current_work /
 *   processing；不推断不存在的任务，不把 completed 写成成功。
 * - recent_runs 最多 30 条；没有近期记录的节点显示「无近期记录」，不显示「空闲」。
 * - runtime-heartbeat / registry-refresh / research-registry-refresh /
 *   research-capacity-recovery 是内部维护任务，不在业务图中出现。
 * - 聚合节点保留成员明细；主状态之外单独统计异常/错过数，避免一个成员异常被整体状态吞掉。
 */

export type FlowNodeStatus =
  | 'running'
  | 'error'
  | 'missed'
  | 'max_instances'
  | 'completed'
  | 'no_record'
  | 'unregistered'
  | 'data';

export type FlowStage = 'collect' | 'signal' | 'output' | 'aux';

export interface FlowMember {
  readonly jobId: string;
  readonly name: string;
  readonly nextRunAt: string | null;
  readonly latestRun: MonitoringScheduledRun | null;
  readonly runningWork: readonly MonitoringSchedulerWork[];
  readonly status: FlowNodeStatus;
}

export interface FlowNode {
  readonly key: string;
  readonly kind: 'job' | 'aggregate' | 'data';
  readonly name: string;
  readonly jobId: string;
  readonly stage: FlowStage;
  readonly status: FlowNodeStatus;
  readonly present: boolean;
  readonly members: readonly FlowMember[];
  readonly nextRunAt: string | null;
  readonly latestRun: MonitoringScheduledRun | null;
  readonly runningWork: readonly MonitoringSchedulerWork[];
  readonly runningCount: number;
  readonly errorCount: number;
  readonly missedCount: number;
  /** 仅信号池数据节点使用。 */
  readonly processing?: MonitoringProcessingHealth;
}

export interface NextScheduledJob {
  readonly jobId: string;
  readonly name: string;
  readonly nextRunAt: string;
}

export interface SchedulerFlowModel {
  readonly nodes: readonly FlowNode[];
  readonly aux: readonly FlowNode[];
  readonly membersByKey: ReadonlyMap<string, readonly FlowMember[]>;
  readonly jobCount: number;
  readonly runningCount: number;
  readonly attentionCount: number;
  readonly recentRunCount: number;
  readonly nextJob: NextScheduledJob | null;
}

const INTERNAL_JOB_IDS = new Set([
  'runtime-heartbeat',
  'registry-refresh',
  'research-registry-refresh',
  'research-capacity-recovery',
]);

const RESEARCH_JOB_IDS = new Set([
  'research-daily',
  'research-weekly',
  'research-monthly',
]);

const AUX_SINGLE_KEYS = [
  {key: 'aging', jobId: 'signal-priority-aging', name: '优先级老化'},
  {key: 'monthly', jobId: 'notify-monthly', name: '风险月报推送'},
  {key: 'cleanup', jobId: 'cleanup', name: '保留清理'},
] as const;

const runTimeMs = (run: MonitoringScheduledRun): number => {
  for (const value of [run.started_at, run.scheduled_run_at]) {
    if (value === null) continue;
    const parsed = new Date(value).getTime();
    if (Number.isFinite(parsed)) return parsed;
  }
  return 0;
};

const isLaterRun = (candidate: MonitoringScheduledRun, current: MonitoringScheduledRun | null): boolean => {
  if (current === null) return true;
  const delta = runTimeMs(candidate) - runTimeMs(current);
  return delta > 0 || (delta === 0 && candidate.id > current.id);
};

const latestRunByJob = (runs: readonly MonitoringScheduledRun[]): Map<string, MonitoringScheduledRun> => {
  const latest = new Map<string, MonitoringScheduledRun>();
  for (const run of runs) {
    if (INTERNAL_JOB_IDS.has(run.job_id)) continue;
    if (isLaterRun(run, latest.get(run.job_id) ?? null)) latest.set(run.job_id, run);
  }
  return latest;
};

const jobIdForWork = (work: MonitoringSchedulerWork): string | null => {
  if (work.kind === 'source_collection' && work.source_id !== null) return `source-${work.source_id}`;
  if (work.kind === 'pending_signal_processing') return 'signal-consume';
  return null;
};

const statusOfMember = (
  latestRun: MonitoringScheduledRun | null,
  isRunning: boolean,
): FlowNodeStatus => {
  if (isRunning) return 'running';
  if (latestRun === null) return 'no_record';
  return latestRun.status;
};

const buildMember = (
  jobId: string,
  jobs: ReadonlyMap<string, MonitoringScheduledJob>,
  latestRuns: ReadonlyMap<string, MonitoringScheduledRun>,
  workByJob: ReadonlyMap<string, readonly MonitoringSchedulerWork[]>,
  fallbackName?: string,
): FlowMember => {
  const job = jobs.get(jobId);
  const latestRun = latestRuns.get(jobId) ?? null;
  const runningWork = workByJob.get(jobId) ?? [];
  const isRunning = latestRun?.status === 'running' || runningWork.length > 0;
  return {
    jobId,
    name: job?.name ?? latestRun?.name ?? fallbackName ?? jobId,
    nextRunAt: job?.next_run_at ?? null,
    latestRun,
    runningWork,
    status: statusOfMember(latestRun, isRunning),
  };
};

const earliestNext = (members: readonly FlowMember[]): string | null => {
  let best: string | null = null;
  let bestMs = Number.POSITIVE_INFINITY;
  for (const member of members) {
    if (member.nextRunAt === null) continue;
    const ms = new Date(member.nextRunAt).getTime();
    if (!Number.isFinite(ms) || ms >= bestMs) continue;
    best = member.nextRunAt;
    bestMs = ms;
  }
  return best;
};

const latestMemberRun = (members: readonly FlowMember[]): MonitoringScheduledRun | null => {
  let latest: MonitoringScheduledRun | null = null;
  for (const member of members) {
    if (member.latestRun !== null && isLaterRun(member.latestRun, latest)) latest = member.latestRun;
  }
  return latest;
};

const groupStatus = (members: readonly FlowMember[]): FlowNodeStatus => {
  if (members.length === 0) return 'unregistered';
  if (members.some((member) => member.status === 'running')) return 'running';
  if (members.some((member) => member.status === 'error')) return 'error';
  if (members.some((member) => member.status === 'missed')) return 'missed';
  if (members.some((member) => member.status === 'max_instances')) return 'max_instances';
  if (members.some((member) => member.status === 'completed')) return 'completed';
  return 'no_record';
};

const membersFor = (
  jobIds: Iterable<string>,
  nameOf: (jobId: string) => string,
  jobs: ReadonlyMap<string, MonitoringScheduledJob>,
  latestRuns: ReadonlyMap<string, MonitoringScheduledRun>,
  workByJob: ReadonlyMap<string, readonly MonitoringSchedulerWork[]>,
): FlowMember[] => Array.from(jobIds)
  .sort((left, right) => left.localeCompare(right, 'en'))
  .map((jobId) => buildMember(jobId, jobs, latestRuns, workByJob, nameOf(jobId)));

interface NodeSpec {
  readonly key: string;
  readonly name: string;
  readonly jobId: string;
  readonly stage: FlowStage;
  readonly members: readonly FlowMember[];
  readonly kind?: 'job' | 'aggregate';
}

const buildNode = (spec: NodeSpec): FlowNode => {
  const {members} = spec;
  const present = members.length > 0;
  return {
    key: spec.key,
    kind: spec.kind ?? (members.length > 1 ? 'aggregate' : 'job'),
    name: spec.name,
    jobId: spec.jobId,
    stage: spec.stage,
    status: present ? groupStatus(members) : 'unregistered',
    present,
    members,
    nextRunAt: earliestNext(members),
    latestRun: latestMemberRun(members),
    runningWork: members.flatMap((member) => member.runningWork),
    runningCount: members.filter((member) => member.status === 'running').length,
    errorCount: members.filter((member) => member.status === 'error').length,
    missedCount: members.filter((member) => member.status === 'missed').length,
  };
};

const sourceJobName = (jobId: string, jobs: ReadonlyMap<string, MonitoringScheduledJob>): string => {
  const job = jobs.get(jobId);
  if (job !== undefined) return job.name;
  const id = jobId.startsWith('source-') ? jobId.slice('source-'.length) : '';
  return id.length > 0 ? `信源 ${id}` : jobId;
};

/**
 * 从一份 ready 快照构建四列网络。
 * 未知的新 job_id 不会被丢弃：归入「其他任务」辅助节点，点击可看成员。
 */
export const buildSchedulerFlowModel = (
  scheduler: MonitoringSchedulerHealth,
  processing: MonitoringProcessingHealth,
): SchedulerFlowModel => {
  const jobs = new Map<string, MonitoringScheduledJob>();
  for (const job of scheduler.scheduled_jobs) {
    if (!INTERNAL_JOB_IDS.has(job.job_id)) jobs.set(job.job_id, job);
  }
  const latestRuns = latestRunByJob(scheduler.recent_runs);

  const workByJob = new Map<string, MonitoringSchedulerWork[]>();
  for (const work of scheduler.current_work) {
    const jobId = jobIdForWork(work);
    if (jobId === null || INTERNAL_JOB_IDS.has(jobId)) continue;
    const list = workByJob.get(jobId) ?? [];
    list.push(work);
    workByJob.set(jobId, list);
  }

  const allJobIds = new Set<string>([
    ...jobs.keys(),
    ...latestRuns.keys(),
    ...workByJob.keys(),
  ]);

  const sourceIds = new Set<string>();
  const tycIds = new Set<string>();
  const researchIds = new Set<string>();
  const otherIds = new Set<string>();

  for (const jobId of allJobIds) {
    if (jobId === 'collect') {
    } else if (jobId.startsWith('source-')) {
      sourceIds.add(jobId);
    } else if (jobId.startsWith('tyc-shard-')) {
      tycIds.add(jobId);
    } else if (jobId === 'signal-consume') {
    } else if (jobId === 'notify') {
    } else if (jobId === 'expire') {
    } else if (jobId === 'intelligence') {
    } else if (RESEARCH_JOB_IDS.has(jobId) || (jobId.startsWith('research-') && !INTERNAL_JOB_IDS.has(jobId))) {
      researchIds.add(jobId);
    } else if (AUX_SINGLE_KEYS.some((item) => item.jobId === jobId)) {
    } else {
      otherIds.add(jobId);
    }
  }

  const collectMembers = membersFor(
    [...allJobIds].filter((jobId) => jobId === 'collect'),
    () => '定时采集与处理',
    jobs,
    latestRuns,
    workByJob,
  );
  const sourceMembers = membersFor(sourceIds, (jobId) => sourceJobName(jobId, jobs), jobs, latestRuns, workByJob);
  const tycMembers = membersFor(tycIds, (jobId) => jobs.get(jobId)?.name ?? jobId, jobs, latestRuns, workByJob);
  const consumeMembers = membersFor(
    [...allJobIds].filter((jobId) => jobId === 'signal-consume'),
    () => '待分析信号消费',
    jobs,
    latestRuns,
    workByJob,
  );
  const notifyMembers = membersFor(
    [...allJobIds].filter((jobId) => jobId === 'notify'),
    () => '风险提醒推送',
    jobs,
    latestRuns,
    workByJob,
  );
  const expireMembers = membersFor(
    [...allJobIds].filter((jobId) => jobId === 'expire'),
    () => '风险有效期物化',
    jobs,
    latestRuns,
    workByJob,
  );
  const intelligenceMembers = membersFor(
    [...allJobIds].filter((jobId) => jobId === 'intelligence'),
    () => '政策与行业情报',
    jobs,
    latestRuns,
    workByJob,
  );

  const nodes: FlowNode[] = [
    buildNode({key: 'collect', name: '定时采集与处理', jobId: 'collect', stage: 'collect', members: collectMembers}),
    buildNode({key: 'source', name: '信源采集', jobId: 'source-*', stage: 'collect', members: sourceMembers, kind: 'aggregate'}),
    buildNode({key: 'tyc', name: '天眼查核查', jobId: 'tyc-shard-*', stage: 'collect', members: tycMembers, kind: 'aggregate'}),
    {
      key: 'hub',
      kind: 'data',
      name: '待分析信号池',
      jobId: 'processing',
      stage: 'signal',
      status: 'data',
      present: true,
      members: [],
      nextRunAt: null,
      latestRun: null,
      runningWork: [],
      runningCount: 0,
      errorCount: 0,
      missedCount: 0,
      processing,
    },
    buildNode({key: 'consume', name: '待分析信号消费', jobId: 'signal-consume', stage: 'signal', members: consumeMembers}),
    buildNode({key: 'notify', name: '风险提醒推送', jobId: 'notify', stage: 'output', members: notifyMembers}),
    buildNode({key: 'expire', name: '风险有效期物化', jobId: 'expire', stage: 'output', members: expireMembers}),
    buildNode({key: 'intel', name: '政策与行业情报', jobId: 'intelligence', stage: 'output', members: intelligenceMembers}),
  ];

  const researchMembers = membersFor(
    researchIds,
    (jobId) => jobs.get(jobId)?.name ?? jobId,
    jobs,
    latestRuns,
    workByJob,
  );
  const aux: FlowNode[] = [];
  if (researchMembers.length > 0) {
    aux.push(buildNode({
      key: 'research',
      name: `研究任务 ×${researchMembers.length}`,
      jobId: 'research-*',
      stage: 'aux',
      members: researchMembers,
      kind: 'aggregate',
    }));
  }
  for (const item of AUX_SINGLE_KEYS) {
    const members = membersFor(
      [...allJobIds].filter((jobId) => jobId === item.jobId),
      () => item.name,
      jobs,
      latestRuns,
      workByJob,
    );
    if (members.length > 0) {
      aux.push(buildNode({key: item.key, name: item.name, jobId: item.jobId, stage: 'aux', members}));
    }
  }
  if (otherIds.size > 0) {
    const otherMembers = membersFor(
      otherIds,
      (jobId) => jobs.get(jobId)?.name ?? latestRuns.get(jobId)?.name ?? jobId,
      jobs,
      latestRuns,
      workByJob,
    );
    aux.push(buildNode({
      key: 'other',
      name: `其他任务 ×${otherMembers.length}`,
      jobId: 'other',
      stage: 'aux',
      members: otherMembers,
      kind: 'aggregate',
    }));
  }

  const membersByKey = new Map<string, readonly FlowMember[]>();
  for (const node of [...nodes, ...aux]) {
    if (node.kind !== 'data') membersByKey.set(node.key, node.members);
  }

  const allMembers = [...membersByKey.values()].flat();
  const runningJobIds = new Set(
    allMembers.filter((member) => member.status === 'running').map((member) => member.jobId),
  );
  const attentionJobIds = new Set(
    allMembers
      .filter((member) => member.status === 'error' || member.status === 'missed')
      .map((member) => member.jobId),
  );

  let nextJob: NextScheduledJob | null = null;
  let nextMs = Number.POSITIVE_INFINITY;
  for (const job of jobs.values()) {
    if (job.next_run_at === null) continue;
    const ms = new Date(job.next_run_at).getTime();
    if (!Number.isFinite(ms) || ms >= nextMs) continue;
    nextJob = {jobId: job.job_id, name: job.name, nextRunAt: job.next_run_at};
    nextMs = ms;
  }

  return {
    nodes,
    aux,
    membersByKey,
    jobCount: jobs.size,
    runningCount: runningJobIds.size,
    attentionCount: attentionJobIds.size,
    recentRunCount: scheduler.recent_runs.length,
    nextJob,
  };
};

/** 聚合节点卡片上的成员数后缀；单任务不显示 ×1。 */
export const memberCountLabel = (node: FlowNode): string | null => (
  node.members.length > 1 ? `×${node.members.length}` : null
);

/** 节点卡片状态文案；stale 时运行中要明确标注为快照。 */
export const nodeStatusLabel = (status: FlowNodeStatus, live: boolean): string => {
  if (status === 'running') return live ? '执行中' : '执行中（快照）';
  if (status === 'error') return '执行异常';
  if (status === 'missed') return '错过执行';
  if (status === 'max_instances') return '并发上限跳过';
  if (status === 'completed') return '执行完成';
  if (status === 'no_record') return '无近期记录';
  if (status === 'unregistered') return '未注册';
  return '数据节点';
};

/** 把 ISO 时间压成卡片可用的短格式；参考快照时间判断“今天”。 */
export const formatShortDateTime = (value: string | null, asOf: string | null): string => {
  if (value === null) return '暂无后续排期';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '时间未知';
  const reference = asOf === null ? new Date() : new Date(asOf);
  const sameDay = Number.isFinite(reference.getTime())
    && date.getFullYear() === reference.getFullYear()
    && date.getMonth() === reference.getMonth()
    && date.getDate() === reference.getDate();
  const time = date.toLocaleTimeString('zh-CN', {hour12: false, hour: '2-digit', minute: '2-digit'});
  if (sameDay) return `今天 ${time}`;
  const day = `${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
  return `${day} ${time}`;
};
