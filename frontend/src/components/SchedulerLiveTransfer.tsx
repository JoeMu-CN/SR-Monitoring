import {useLayoutEffect, useRef, useState} from 'react';
import {motion} from 'motion/react';
import type {MonitoringScheduledJob, MonitoringScheduledRun} from '../api';
import {RUN_STATUS_META, type UpcomingScheduledJob} from './SchedulerActivityPanels';

/**
 * 跨区投影动画（计划头 → 真实新 run）：
 * 当前后两份 fresh 快照严格对账——新落库 run 的 job_id 与上一快照最早计划头一致、
 * scheduled_run_at 与上一份 head.next_run_at 是同一时刻——就在页面卡片内画一层 bounded
 * ghost overlay，从计划行滑向真实运行行。它不移动任何真实 DOM、不按本地时钟推算、
 * 不伪造状态：只是既有事实的一次性视觉提示。
 */

export interface ScheduledTransferMatch {
  readonly run: MonitoringScheduledRun;
  readonly from: UpcomingScheduledJob;
}

export interface TransferBaseline {
  readonly jobs: readonly MonitoringScheduledJob[];
  readonly runIds: ReadonlySet<number>;
}

// 纯判定：对账上一份快照的全部合法计划条目（next_run_at 非 null 且可解析），
// 不假设第一条一定产 run（如 notify 每 60s 但被后端执行历史排除）；
// 多个对账成功时按上一快照排期最早的真实记录优先，只返回一个匹配。
export const detectScheduledTransfer = (
  baseline: TransferBaseline | null,
  runs: readonly MonitoringScheduledRun[],
  seenRunIds: ReadonlySet<number>,
): ScheduledTransferMatch | null => {
  if (baseline === null) return null;
  const planned = new Map<string, {readonly from: UpcomingScheduledJob; readonly ms: number}>();
  for (const job of baseline.jobs) {
    if (job.next_run_at === null) continue;
    const ms = new Date(job.next_run_at).getTime();
    if (!Number.isFinite(ms) || planned.has(job.job_id)) continue;
    planned.set(job.job_id, {from: {...job, next_run_at: job.next_run_at}, ms});
  }
  let best: ScheduledTransferMatch | null = null;
  let bestMs = Number.POSITIVE_INFINITY;
  for (const run of runs) {
    if (baseline.runIds.has(run.id) || seenRunIds.has(run.id)) continue;
    const entry = planned.get(run.job_id);
    if (entry === undefined) continue;
    if (new Date(run.scheduled_run_at).getTime() !== entry.ms) continue;
    if (entry.ms >= bestMs) continue;
    best = {run, from: entry.from};
    bestMs = entry.ms;
  }
  return best;
};

// 已处理运行 ID 的有界集合：超过上限按插入顺序淘汰最旧项。
const SEEN_RUN_LIMIT = 256;

interface SeenRuns {
  readonly ids: Set<number>;
  readonly order: number[];
}

const rememberRun = (seen: SeenRuns, runId: number): void => {
  seen.ids.add(runId);
  seen.order.push(runId);
  while (seen.order.length > SEEN_RUN_LIMIT) {
    const evicted = seen.order.shift();
    if (evicted !== undefined) seen.ids.delete(evicted);
  }
};

interface RectLike {
  readonly left: number;
  readonly top: number;
  readonly width: number;
  readonly height: number;
}

const rectOf = (element: Element): RectLike => {
  const rect = element.getBoundingClientRect();
  return {left: rect.left, top: rect.top, width: rect.width, height: rect.height};
};

// rect 可见性：完整落在（非零尺寸且被 outer 包含）才视为可见；任一 rect 离屏一律跳过。
const within = (inner: RectLike, outer: RectLike): boolean =>
  inner.width > 0 &&
  inner.height > 0 &&
  inner.left >= outer.left &&
  inner.top >= outer.top &&
  inner.left + inner.width <= outer.left + outer.width &&
  inner.top + inner.height <= outer.top + outer.height;

export interface TransferGhost {
  readonly run: MonitoringScheduledRun;
  readonly left: number;
  readonly top: number;
  readonly width: number;
  readonly height: number;
  readonly deltaX: number;
  readonly deltaY: number;
  readonly scale: number;
}

// 投影测量：起点用上一快照缓存的对应计划条目 rect（计划排序推进后仍是正确的来源位置），
// 目标在新增真实行挂载后测量；源与目标都必须完整在 window 视口内，目标还必须完整在
// 执行流剪裁视口内——目标离屏（即便仍在流滚动范围内）一律跳过，不滚动页面、不飞越全页。
const measureGhost = (
  layer: HTMLElement,
  container: HTMLElement,
  source: RectLike | null,
  match: ScheduledTransferMatch,
  following: boolean,
): TransferGhost | null => {
  if (source === null) return null;
  const stream = container.querySelector<HTMLElement>('[data-testid="scheduler-live-stream"]');
  const target = container.querySelector<HTMLElement>(`[data-testid="scheduler-live-run-${match.run.id}"]`);
  if (stream === null || target === null) return null;
  if (following) stream.scrollTop = stream.scrollHeight;
  const layerRect = rectOf(layer);
  const streamRect = rectOf(stream);
  const targetRect = rectOf(target);
  const viewport: RectLike = {left: 0, top: 0, width: window.innerWidth, height: window.innerHeight};
  if (!within(source, viewport) || !within(targetRect, viewport) || !within(targetRect, streamRect)) return null;
  const scale = Math.min(1.4, Math.max(0.6, targetRect.width / source.width));
  return {
    run: match.run,
    left: source.left - layerRect.left,
    top: source.top - layerRect.top,
    width: source.width,
    height: source.height,
    deltaX: targetRect.left + targetRect.width / 2 - (source.left + source.width / 2),
    deltaY: targetRect.top + targetRect.height / 2 - (source.top + source.height / 2),
    scale,
  };
};

const GhostBody = ({run}: {readonly run: MonitoringScheduledRun}) => {
  const meta = RUN_STATUS_META[run.status];
  const StatusIcon = meta.icon;
  // 只复述已落库的最终状态：不旋转、不写日志文本、不把 completed 伪造成 running。
  return (
    <div className="flex h-full w-full items-center gap-2 overflow-hidden rounded-xl border border-sky-400/40 bg-[#0e1726] px-3 ring-1 ring-sky-400/20">
      <StatusIcon aria-hidden="true" className={`h-4 w-4 shrink-0 ${meta.iconClass}`} />
      <span className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] font-bold ring-1 ring-inset ${meta.chipClass}`}>{meta.label}</span>
      <span className="truncate text-[13px] font-bold text-slate-100">{run.name}</span>
      <span className="truncate font-mono text-[10px] text-slate-400">{run.job_id}</span>
    </div>
  );
};

export const TransferGhostLayer = ({ghost, onDone}: {readonly ghost: TransferGhost; readonly onDone: () => void}) => (
  <motion.div
    data-testid="scheduler-live-transfer-ghost"
    aria-hidden="true"
    className="pointer-events-none absolute z-10"
    style={{left: ghost.left, top: ghost.top, width: ghost.width, height: ghost.height}}
    initial={{x: 0, y: 0, scale: 1, opacity: 0.95}}
    animate={{x: ghost.deltaX, y: ghost.deltaY, scale: ghost.scale, opacity: 0}}
    transition={{duration: 0.6, ease: [0.22, 0.61, 0.36, 1]}}
    onAnimationComplete={onDone}
  >
    <GhostBody run={ghost.run} />
  </motion.div>
);

export interface ScheduleTransferLayerProps {
  /** 快照为 fresh ok（心跳可确认且有 asOf）；false 时完全不更新对照基线、不触发。 */
  readonly active: boolean;
  /** 允许播放：页面可见、非减少动效、用户保持跟随。 */
  readonly canAnimate: boolean;
  readonly following: boolean;
  readonly jobs: readonly MonitoringScheduledJob[];
  readonly runs: readonly MonitoringScheduledRun[];
  /** 每次新快照提交的版本键（通常传 snapshot 对象）。 */
  readonly revision: unknown;
}

export const ScheduleTransferLayer = ({active, canAnimate, following, jobs, runs, revision}: ScheduleTransferLayerProps) => {
  const [ghost, setGhost] = useState<TransferGhost | null>(null);
  // 自持 overlay 层：layout effect 中自己的 DOM 一定已挂载（父级 host ref 在子组件 layout effect 时尚未 attach）。
  const layerRef = useRef<HTMLDivElement | null>(null);
  const baselineRef = useRef<TransferBaseline | null>(null);
  // 来源 rect 缓存：job_id → 上一快照测量到的该计划行位置；Map 每快照整体替换，有界。
  const sourceRectsRef = useRef<ReadonlyMap<string, RectLike>>(new Map());
  const seenRef = useRef<SeenRuns>({ids: new Set(), order: []});

  useLayoutEffect(() => {
    const layer = layerRef.current;
    const container = layer?.parentElement ?? null;
    if (!active || layer === null || container === null) {
      // 非 fresh 快照（stale/unknown/未就绪）：不推进基线，但进行中的投影不得跨快照残留。
      setGhost(null);
      return;
    }

    const match = detectScheduledTransfer(baselineRef.current, runs, seenRef.current.ids);
    if (match !== null && canAnimate) {
      // 起点取匹配到的计划条目（不是排序第一行）在上一快照时的缓存位置。
      const source = sourceRectsRef.current.get(match.from.job_id) ?? null;
      const next = measureGhost(layer, container, source, match, following);
      if (next !== null) {
        rememberRun(seenRef.current, match.run.id);
        setGhost(next);
      }
    } else if (!canAnimate) {
      // 播放 gate 关闭（document 隐藏 / 减少动效 / 暂停跟随）：进行中的投影立即消失。
      setGhost(null);
    }

    // 基线只由 fresh 快照推进；来源 rect 每快照全量重建（含暂不产 run 的条目，如 notify），
    // 留给下一次事件使用——即使计划排序推进也能命中对应条目的历史位置。
    const nextRects = new Map<string, RectLike>();
    for (const job of jobs) {
      const row = container.querySelector(`[data-testid="scheduler-schedule-item-${job.job_id}"]`);
      if (row !== null) nextRects.set(job.job_id, rectOf(row));
    }
    sourceRectsRef.current = nextRects;
    baselineRef.current = {jobs, runIds: new Set(runs.map((run) => run.id))};
  }, [active, canAnimate, following, jobs, runs, revision]);

  return (
    // bounded 父卡内覆盖层：absolute inset-0 跟随页面卡片，不跨出卡片、不遮挡底部导航。
    <div ref={layerRef} data-testid="scheduler-live-transfer-layer" className="pointer-events-none absolute inset-0 z-10">
      {ghost === null ? null : <TransferGhostLayer ghost={ghost} onDone={() => setGhost(null)} />}
    </div>
  );
};
