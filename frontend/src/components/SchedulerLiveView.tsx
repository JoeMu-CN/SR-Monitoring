import {useEffect, useState} from 'react';
import {CircleHelp, Loader2, ShieldQuestion, type LucideIcon} from 'lucide-react';
import {useReducedMotion} from 'motion/react';
import type {MonitoringScheduledJob} from '../api';
import type {MonitoringHealthSnapshot} from '../useMonitoringHealth';
import {ScheduleList, formatDateTime} from './SchedulerActivityPanels';
import {StaleNotice} from './SchedulerLivePanels';
import {StreamSection} from './SchedulerLiveStream';
import {ScheduleTransferLayer} from './SchedulerLiveTransfer';
import './SchedulerLiveView.css';

/**
 * 页面快照刷新口径：宿主页面（/scheduler 集成）按此周期提供新的 monitoringHealth 快照。
 * 组件自身不发起任何请求、不跑后台计时器；此值只作为对用户可见的刷新承诺展示。
 */
export const SCHEDULER_LIVE_REFRESH_SECONDS = 5;

interface SchedulerLiveViewProps {
  readonly snapshot: MonitoringHealthSnapshot;
}

// 心跳波形与扫描光点：只在可确认心跳（ok）时渲染。
// 减少动效时保留静态完整波形、不渲染光点；document 隐藏时由根节点暂停类冻结动画。
const HeartbeatTrace = ({reduced}: {readonly reduced: boolean}) => (
  <div data-testid="scheduler-live-heartbeat" aria-hidden="true" className="relative h-6 w-24 overflow-hidden">
    <svg viewBox="0 0 96 24" fill="none" className="h-full w-full text-[#34c759]">
      <path
        d="M0 12 H20 L24 6 L28 18 L32 9 L36 12 H48 L52 7 L56 16 L60 12 H96"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
        className={reduced ? undefined : 'scheduler-live-ecg'}
      />
    </svg>
    {!reduced && (
      <span
        data-testid="scheduler-live-heartbeat-dot"
        className="scheduler-live-heartbeat-dot absolute left-0 top-[calc(50%-3px)] h-1.5 w-1.5 rounded-full bg-[#34c759] shadow-[0_0_8px_rgba(52,199,89,0.9)]"
      />
    )}
  </div>
);

interface StatePanelProps {
  readonly testId: string;
  readonly icon: LucideIcon;
  readonly spin?: boolean;
  readonly title: string;
  readonly description: string;
}

// 无数据/不可确认状态的统一面板：给明确文案，不渲染任何在线心跳或虚构内容。
const StatePanel = ({testId, icon: Icon, spin = false, title, description}: StatePanelProps) => (
  <div data-testid={testId} role="status" className="flex flex-col items-center justify-center gap-1.5 rounded-xl border border-slate-800 bg-[#0e1726] px-4 py-8 text-center">
    <Icon aria-hidden="true" className={`h-5 w-5 text-slate-500${spin ? ' scheduler-live-spin animate-spin motion-reduce:animate-none' : ''}`} />
    <p className="text-sm font-bold text-white">{title}</p>
    <p className="max-w-md text-xs leading-relaxed text-slate-400">{description}</p>
  </div>
);

// 底部计划任务区：独立背景容器 + 充足上方间距，与执行流明显分区；完整列出已注册排期（含无后续排期项）。
const ScheduleSection = ({jobs, stale = false}: {readonly jobs: readonly MonitoringScheduledJob[]; readonly stale?: boolean}) => (
  <section data-testid="scheduler-live-schedule" className="mt-5 rounded-2xl border border-slate-800 bg-[#0e1726] p-3 sm:mt-8 sm:p-4">
    <ScheduleList jobs={jobs} stale={stale} />
  </section>
);

/**
 * 调度器实况页：只读消费 monitoringHealth 快照中 scheduler 的
 * scheduled_jobs、recent_runs 与 current_work（仅用于 stale 警示的运行中计数），不请求独立接口。
 * 状态互斥优先级 loading → hidden/unknown（诊断不可得）→ 心跳不可确认 → stale（心跳延迟）→ ok；
 * stale/unknown/hidden 不显示在线心跳，不把 running 历史当作确认当前活跃；
 * 执行流时间正序、最新在底部、按 id 去重，跟随滚动尊重用户阅读位置。
 */
export const SchedulerLiveView = ({snapshot}: SchedulerLiveViewProps) => {
  const reduceMotion = useReducedMotion() === true;
  const [pageVisible, setPageVisible] = useState(() => document.visibilityState !== 'hidden');
  // 执行流跟随状态（由 StreamSection 上报）：用户上滚暂停跟进期间不播放跨区投影。
  const [following, setFollowing] = useState(true);

  // document 隐藏时暂停循环动画与自动滚动，恢复可见后立即继续。
  useEffect(() => {
    const handleVisibility = () => setPageVisible(document.visibilityState !== 'hidden');
    document.addEventListener('visibilitychange', handleVisibility);
    return () => document.removeEventListener('visibilitychange', handleVisibility);
  }, []);

  const scheduler = snapshot.status === 'ready' ? snapshot.health.scheduler : null;
  const asOf = snapshot.status === 'ready' ? snapshot.health.as_of : null;
  // current_work 仅供 stale 警示的安全说明使用；ok 页面不再渲染当前任务区。
  const workCount = scheduler?.current_work.length ?? 0;
  const scheduledJobs = scheduler?.scheduled_jobs ?? [];
  const recentRuns = scheduler?.recent_runs ?? [];
  const heartbeatConfirmed = scheduler !== null && scheduler.status === 'ok';
  const lastHeartbeatAt = scheduler?.last_heartbeat_at ?? null;
  const intervalSeconds = scheduler?.interval_seconds ?? null;

  return (
    <section
      data-testid="scheduler-live-view"
      aria-label="调度器实况"
      data-reduced-motion={reduceMotion ? 'true' : 'false'}
      className={`relative flex flex-col gap-3 rounded-2xl border border-slate-800 bg-[#0b131e] p-3 text-slate-100 shadow-xl sm:p-4${pageVisible ? '' : ' scheduler-live-paused'}`}
    >
      <header className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2 rounded-xl border border-slate-800 bg-[#0e1726] p-3 sm:p-4">
        <div className="min-w-0">
          <p className="text-[10px] font-bold tracking-[0.18em] text-sky-300">SCHEDULER LIVE</p>
          <h2 className="mt-0.5 text-lg font-black text-white">调度器实况</h2>
          {/* 刷新与数据截至属于页面标题组的一部分：紧贴标题下方，不占据卡片外的独立行。 */}
          {asOf !== null && intervalSeconds !== null && (
            <p data-testid="scheduler-live-refresh-note" className="mt-1 text-pretty break-words text-[11px] text-slate-400">
              快照每 {SCHEDULER_LIVE_REFRESH_SECONDS} 秒刷新
              <span aria-hidden="true" className="mx-1.5 text-slate-600">·</span>
              后端心跳间隔 <span className="font-mono text-slate-300">{intervalSeconds}</span> 秒
              <span aria-hidden="true" className="mx-1.5 text-slate-600">·</span>
              数据截至 <span className="font-mono text-slate-300">{formatDateTime(asOf)}</span>
            </p>
          )}
        </div>
        {heartbeatConfirmed && (
          <div className="flex flex-col items-start gap-1 sm:items-end">
            <HeartbeatTrace reduced={reduceMotion} />
            <p data-testid="scheduler-live-heartbeat-meta" className="flex flex-wrap items-center gap-x-1.5 gap-y-0.5 text-[11px] font-bold text-[#34c759]">
              <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-[#34c759]" />
              心跳正常
              <span className="font-normal text-slate-400">
                最后心跳 <span className="font-mono text-slate-300">{lastHeartbeatAt === null ? '未知' : formatDateTime(lastHeartbeatAt)}</span>
              </span>
            </p>
          </div>
        )}
      </header>

      {snapshot.status === 'loading' && (
        <StatePanel
          testId="scheduler-live-loading"
          icon={Loader2}
          spin
          title="正在获取调度器快照"
          description="等待宿主页面提供最近一次监控健康聚合结果。"
        />
      )}
      {snapshot.status === 'hidden' && (
        <StatePanel
          testId="scheduler-live-hidden"
          icon={ShieldQuestion}
          title="无权查看调度器实况"
          description="当前账号没有只读监控诊断权限，页面不展示心跳、排期与执行历史。"
        />
      )}
      {snapshot.status === 'unknown' && (
        <StatePanel
          testId="scheduler-live-unknown"
          icon={CircleHelp}
          title="尚无法确认调度器实况"
          description="诊断请求失败，无法获取心跳、排期与执行历史，请稍后重试。"
        />
      )}
      {scheduler !== null && scheduler.status === 'unknown' && (
        <StatePanel
          testId="scheduler-live-unconfirmed"
          icon={CircleHelp}
          title="无法确认调度器心跳"
          description="最近一次快照没有可确认的心跳，排期与执行历史不可确认，不展示在线心跳。"
        />
      )}

      {scheduler !== null && scheduler.status === 'stale' && asOf !== null && (
        <div className="flex flex-col gap-3">
          <StaleNotice workCount={workCount} lastHeartbeatAt={lastHeartbeatAt} />
          <StreamSection runs={recentRuns} asOf={asOf} live={false} paused={!pageVisible} onFollowingChange={setFollowing} />
          <ScheduleSection jobs={scheduledJobs} stale />
        </div>
      )}

      {heartbeatConfirmed && asOf !== null && (
        <div className="flex flex-col gap-3">
          <StreamSection runs={recentRuns} asOf={asOf} live paused={!pageVisible} onFollowingChange={setFollowing} />
          <ScheduleSection jobs={scheduledJobs} />
        </div>
      )}

      {/* 跨区投影：只在 fresh ok 快照、页面可见、非减少动效且保持跟随时才可能播放。 */}
      <ScheduleTransferLayer
        active={heartbeatConfirmed && asOf !== null}
        canAnimate={heartbeatConfirmed && asOf !== null && pageVisible && !reduceMotion && following}
        following={following}
        jobs={scheduledJobs}
        runs={recentRuns}
        revision={snapshot}
      />
    </section>
  );
};
