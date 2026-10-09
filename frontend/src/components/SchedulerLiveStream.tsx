import {useEffect, useMemo, useRef, useState, type WheelEvent as ReactWheelEvent} from 'react';
import {ArrowDownToLine, History} from 'lucide-react';
import {motion, useReducedMotion} from 'motion/react';
import type {MonitoringScheduledRun} from '../api';
import {RUN_STATUS_META, formatDateTime, formatDuration, formatRunningDuration} from './SchedulerActivityPanels';

// SchedulerLiveView 私有执行流：recent_runs 的时间正序实时滚动区。
// 真实滚动语义：默认跟随最新（滚动到底部）；用户上滚或离开底部后停止跟随，
// 由「恢复跟随最新」按钮显式回到最新，数据更新不抢占用户阅读位置。

// 距底部阈值：仍在此范围内视为“处于最新”。
export const LIVE_FOLLOW_THRESHOLD_PX = 32;

// 稳定排序键：优先 started_at（实际观测到的开始时间），缺失时回退 scheduled_run_at；
// 两者都无法解析时按 0 排最早，同刻用 id 保证次序稳定。
const runTimeMs = (run: MonitoringScheduledRun): number => {
  for (const value of [run.started_at, run.scheduled_run_at]) {
    if (value === null) continue;
    const timeMs = new Date(value).getTime();
    if (Number.isFinite(timeMs)) return timeMs;
  }
  return 0;
};

// 去重 + 时间正序：同一 id 只保留最后一次观测；最新记录落在列表底部。
export const orderRecentRuns = (runs: readonly MonitoringScheduledRun[]): readonly MonitoringScheduledRun[] => {
  const byId = new Map<number, MonitoringScheduledRun>();
  for (const run of runs) byId.set(run.id, run);
  return [...byId.values()].sort((a, b) => runTimeMs(a) - runTimeMs(b) || a.id - b.id);
};

const LiveRunRow = ({run, asOf, live}: {readonly run: MonitoringScheduledRun; readonly asOf: string; readonly live: boolean}) => {
  const meta = RUN_STATUS_META[run.status];
  const StatusIcon = meta.icon;
  // 心跳不可确认（stale/unknown）时 running 历史不当作当前活跃：不给旋转动画，标签标注快照。
  const spinning = meta.spinning && live;
  const label = run.status === 'running' && !live ? '执行中（快照）' : meta.label;
  return (
    // 桌面 lg 单行：左列任务标识（minmax(0,1fr) 允许折行、不被右侧压出横向溢出），右列按内容宽度；
    // 移动为 grid 默认单列自然堆叠。
    <li
      data-testid={`scheduler-live-run-${run.id}`}
      className="scheduler-live-enter grid gap-y-1.5 rounded-xl border border-slate-800 bg-[#0e1726] p-3 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center lg:gap-x-4 lg:gap-y-0"
    >
      <div className="flex min-w-0 flex-wrap items-center gap-2">
        <StatusIcon
          aria-hidden="true"
          data-testid={spinning ? 'scheduler-live-spinner' : undefined}
          className={`h-4 w-4 shrink-0 ${meta.iconClass}${spinning ? ' scheduler-live-spin animate-spin motion-reduce:animate-none' : ''}`}
        />
        <span className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] font-bold ring-1 ring-inset ${meta.chipClass}`}>{label}</span>
        <span className="break-words text-[13px] font-bold text-slate-100">{run.name}</span>
        <span className="break-all font-mono text-[10px] text-slate-400">{run.job_id}</span>
      </div>
      {/* 计划执行时间不再展示（scheduled_run_at 仅参与内部排序）；右侧开始时间与时长同一行，桌面不换行、移动自然换行。 */}
      <p className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px] text-slate-400 lg:flex-nowrap lg:justify-end">
        <span className="whitespace-nowrap">开始 <span className="font-mono text-slate-300">{run.started_at === null ? '—' : formatDateTime(run.started_at)}</span></span>
        {run.status === 'running' ? (
          // 未结束的运行只给“按快照观测”的时长，不冒充精确执行耗时。
          <span className="whitespace-nowrap">观测时长 <span className="font-mono text-slate-300">{formatRunningDuration(run.started_at, asOf)}</span></span>
        ) : (
          <span className="whitespace-nowrap">记录时长 <span className="font-mono text-slate-300">{formatDuration(run.started_at, run.finished_at)}</span></span>
        )}
      </p>
    </li>
  );
};

interface StreamSectionProps {
  readonly runs: readonly MonitoringScheduledRun[];
  readonly asOf: string;
  /** 心跳是否可确认（ok）：false 时 running 历史不给旋转动画、标注快照。 */
  readonly live: boolean;
  /** document 是否隐藏：隐藏时暂停自动滚动，不打断后台状态。 */
  readonly paused: boolean;
  /** 跟随状态上报：父级用它门控跨区投影动画（用户上滚暂停期间不飞）。 */
  readonly onFollowingChange?: (following: boolean) => void;
}

export const StreamSection = ({runs, asOf, live, paused, onFollowingChange}: StreamSectionProps) => {
  const reduceMotion = useReducedMotion() === true;
  const orderedRuns = useMemo(() => orderRecentRuns(runs), [runs]);
  const streamRef = useRef<HTMLDivElement | null>(null);
  const [following, setFollowing] = useState(true);

  // 记录签名：仅在真实数据变化（新 id、状态或结束时间变化）时触发跟随滚动，
  // 避免父组件无关重渲染反复抢占滚动位置。
  const runsSignature = useMemo(
    () => orderedRuns.map((run) => `${run.id}:${run.status}:${run.finished_at ?? ''}`).join('|'),
    [orderedRuns],
  );

  useEffect(() => {
    if (!following || paused) return;
    const element = streamRef.current;
    if (element === null) return;
    element.scrollTop = element.scrollHeight;
  }, [following, paused, runsSignature]);

  // 上报给父级：跟随状态变化是跨区投影动画的门控条件之一。
  useEffect(() => {
    onFollowingChange?.(following);
  }, [following, onFollowingChange]);

  const handleScroll = () => {
    const element = streamRef.current;
    if (element === null) return;
    const distanceToBottom = element.scrollHeight - element.scrollTop - element.clientHeight;
    setFollowing(distanceToBottom <= LIVE_FOLLOW_THRESHOLD_PX);
  };

  // 用户向上滚动的即时意图：先退出跟随；向下滚动是否回到最新仍由滚动位置判定。
  const handleWheel = (event: ReactWheelEvent<HTMLDivElement>) => {
    if (event.deltaY < 0) setFollowing(false);
  };

  const handleResume = () => {
    setFollowing(true);
    const element = streamRef.current;
    if (element !== null) element.scrollTop = element.scrollHeight;
  };

  return (
    // 执行流独立外卡：与计划区同款 rounded-2xl / 深色卡背景 / 统一内边距。
    <section data-testid="scheduler-live-stream-card" className="min-w-0 rounded-2xl border border-slate-800 bg-[#0e1726] p-3 sm:p-4">
      <h3 className="inline-flex flex-wrap items-center gap-1.5 text-sm font-bold text-white">
        <History aria-hidden="true" className="h-4 w-4 text-slate-400" />
        实时执行流
        <span data-testid="scheduler-live-stream-count" className="font-mono text-[11px] font-semibold text-slate-400">{orderedRuns.length}</span>
      </h3>
      {/* relative 不滚动外壳：悬浮恢复胶囊相对它定位，absolute 不占布局高度。 */}
      <div className="relative mt-2">
        <div
          ref={streamRef}
          data-testid="scheduler-live-stream"
          className="scheduler-live-stream max-h-[clamp(15rem,42vh,30rem)] space-y-2 overflow-y-auto rounded-xl border border-slate-800/70 bg-[#0b131e] p-2 pb-16"
          onScroll={handleScroll}
          onWheel={handleWheel}
        >
          {orderedRuns.length === 0 ? (
            <p data-testid="scheduler-live-stream-empty" className="rounded-xl border border-dashed border-slate-700 px-3 py-6 text-center text-[11px] text-slate-400">
              暂无执行记录：调度器还没有落库的运行历史。
            </p>
          ) : (
            <ul aria-label="实时执行流" className="space-y-2">
              {orderedRuns.map((run) => <LiveRunRow key={run.id} run={run} asOf={asOf} live={live} />)}
            </ul>
          )}
        </div>
        {!following && (
          // 胶囊悬浮在滚动容器下部中央：wrapper 不吃指针事件、按钮恢复指针；
          // z-10 留在卡片内，不 fixed、不越出卡片遮盖底部导航。
          <div className="pointer-events-none absolute bottom-3 left-1/2 z-10 -translate-x-1/2">
            <motion.button
              type="button"
              data-testid="scheduler-live-resume"
              onClick={handleResume}
              initial={reduceMotion ? false : {opacity: 0, y: 6}}
              animate={{opacity: 1, y: 0}}
              transition={{duration: 0.18, ease: 'easeOut'}}
              className="pointer-events-auto inline-flex min-h-11 items-center gap-1.5 rounded-full border border-sky-400/40 bg-[#007aff] px-3.5 py-1.5 text-[11px] font-bold text-white shadow-lg hover:bg-[#0062cc] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#007aff]"
            >
              <ArrowDownToLine aria-hidden="true" className="h-3.5 w-3.5" />
              恢复跟随最新
            </motion.button>
          </div>
        )}
      </div>
    </section>
  );
};
