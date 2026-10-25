import {useCallback, useEffect, useMemo, useRef, useState} from 'react';
import {CircleHelp, Loader2, ShieldQuestion, Terminal} from 'lucide-react';
import {useReducedMotion} from 'motion/react';
import type {ApiError} from '../api';
import type {MonitoringHealthSnapshot} from '../useMonitoringHealth';
import {ProcessingHealthDetailBanner} from './MonitoringHealthBanner';
import {SchedulerFlowMap} from './SchedulerFlowMap';
import {SchedulerLogDrawer} from './SchedulerLogDrawer';
import {StaleNotice} from './SchedulerLivePanels';
import {formatDateTime} from './SchedulerActivityPanels';
import {buildSchedulerFlowModel, formatShortDateTime} from './schedulerFlowModel';
import './SchedulerLiveView.css';

/**
 * 页面快照刷新口径：宿主页面（/scheduler 集成）按此周期提供新的 monitoringHealth 快照。
 * 组件自身不发起 health 请求；原始日志抽屉在打开时单独 tail 后端日志文件。
 */
export const SCHEDULER_LIVE_REFRESH_SECONDS = 5;

/** 原始日志查看权限；后端仅授予 risk_admin / platform_admin。 */
export const SCHEDULER_LOG_PERMISSION = 'system_log_view';

interface SchedulerLiveViewProps {
  readonly snapshot: MonitoringHealthSnapshot;
  /** 是否具备 system_log_view；false 时不渲染日志入口，也不发日志请求。 */
  readonly canViewLogs?: boolean;
  /** 401 统一出口；403 由日志抽屉就地展示。 */
  readonly onRequestError?: (error: ApiError) => void;
}

// 心跳波形与扫描光点：只在可确认心跳（ok）时渲染。
// 减少动效时保留静态完整波形、不渲染光点；document 隐藏时由根节点暂停类冻结动画。
const HeartbeatTrace = ({reduced}: {readonly reduced: boolean}) => (
  <div data-testid="scheduler-live-heartbeat" aria-hidden="true" className="scheduler-live-heartbeat-trace">
    <svg viewBox="0 0 96 24" fill="none" className="scheduler-live-ecg">
      <path
        d="M0 12 H20 L24 6 L28 18 L32 9 L36 12 H48 L52 7 L56 16 L60 12 H96"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
        className={reduced ? undefined : 'scheduler-live-ecg-sweep'}
      />
    </svg>
    {!reduced && <span data-testid="scheduler-live-heartbeat-dot" className="scheduler-live-heartbeat-dot" />}
  </div>
);

interface StatePanelProps {
  readonly testId: string;
  readonly icon: typeof Loader2;
  readonly spin?: boolean;
  readonly title: string;
  readonly description: string;
}

// 无数据/不可确认状态的统一面板：给明确文案，不渲染任何在线心跳或虚构内容。
const StatePanel = ({testId, icon: Icon, spin = false, title, description}: StatePanelProps) => (
  <div data-testid={testId} role="status" className="scheduler-live-state">
    <Icon aria-hidden="true" className={`scheduler-live-state-icon${spin ? ' scheduler-live-spin' : ''}`} />
    <p className="scheduler-live-state-title">{title}</p>
    <p className="scheduler-live-state-desc">{description}</p>
  </div>
);

const Header = ({
  asOf,
  intervalSeconds,
  heartbeatConfirmed,
  lastHeartbeatAt,
  reduced,
}: {
  readonly asOf: string | null;
  readonly intervalSeconds: number | null;
  readonly heartbeatConfirmed: boolean;
  readonly lastHeartbeatAt: string | null;
  readonly reduced: boolean;
}) => (
  <header className="scheduler-live-head">
    <div className="scheduler-live-head-main">
      <h2 className="scheduler-live-title">调度器实况</h2>
      {asOf !== null && intervalSeconds !== null && (
        <p data-testid="scheduler-live-refresh-note" className="scheduler-live-refresh">
          快照每 {SCHEDULER_LIVE_REFRESH_SECONDS} 秒刷新
          <span aria-hidden="true">·</span>
          后端心跳间隔 <b>{intervalSeconds}</b> 秒
          <span aria-hidden="true">·</span>
          数据截至 <b>{formatDateTime(asOf)}</b>
        </p>
      )}
    </div>
    {heartbeatConfirmed && (
      <div className="scheduler-live-heartbeat-wrap">
        <HeartbeatTrace reduced={reduced} />
        <p data-testid="scheduler-live-heartbeat-meta" className="scheduler-live-heartbeat-meta">
          <span aria-hidden="true" className="scheduler-live-heartbeat-dot-static" />
          心跳正常
          <span>
            最后心跳 <b>{lastHeartbeatAt === null ? '未知' : formatDateTime(lastHeartbeatAt)}</b>
          </span>
        </p>
      </div>
    )}
  </header>
);

const Stats = ({
  runningCount,
  workCount,
  jobCount,
  recentRunCount,
  attentionCount,
  stale,
}: {
  readonly runningCount: number;
  readonly workCount: number;
  readonly jobCount: number;
  readonly recentRunCount: number;
  readonly attentionCount: number;
  readonly stale: boolean;
}) => (
  <div className="scheduler-live-stats" aria-label="调度器概览">
    <div className={`scheduler-live-stat ${runningCount > 0 ? 'is-running' : ''}`}>
      <span className="scheduler-live-stat-label">运行中</span>
      <strong className="scheduler-live-stat-value">{runningCount}<em>个任务</em></strong>
      <span className="scheduler-live-stat-sub">{workCount > 0 ? `${workCount} 项当前工作` : '无当前工作项'}</span>
    </div>
    <div className="scheduler-live-stat">
      <span className="scheduler-live-stat-label">已注册任务</span>
      <strong className="scheduler-live-stat-value">{jobCount}<em>个</em></strong>
      <span className="scheduler-live-stat-sub">不含内部维护任务</span>
    </div>
    <div className="scheduler-live-stat">
      <span className="scheduler-live-stat-label">近期执行</span>
      <strong className="scheduler-live-stat-value">{recentRunCount}<em>条</em></strong>
      <span className="scheduler-live-stat-sub">最近 30 条窗口</span>
    </div>
    <div className={`scheduler-live-stat ${attentionCount > 0 ? 'is-attention' : ''}`}>
      <span className="scheduler-live-stat-label">需要关注</span>
      <strong className="scheduler-live-stat-value">{attentionCount}<em>个</em></strong>
      <span className="scheduler-live-stat-sub">{stale ? '快照内最新异常 / 错过' : '最新一次异常 / 错过'}</span>
    </div>
  </div>
);

export const SchedulerLiveView = ({snapshot, canViewLogs = false, onRequestError}: SchedulerLiveViewProps) => {
  const reduceMotion = useReducedMotion() === true;
  const [pageVisible, setPageVisible] = useState(() => document.visibilityState !== 'hidden');
  const [logOpen, setLogOpen] = useState(false);
  const logButtonRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    const handleVisibility = () => setPageVisible(document.visibilityState !== 'hidden');
    document.addEventListener('visibilitychange', handleVisibility);
    return () => document.removeEventListener('visibilitychange', handleVisibility);
  }, []);

  const scheduler = snapshot.status === 'ready' ? snapshot.health.scheduler : null;
  const processing = snapshot.status === 'ready' ? snapshot.health.processing : null;
  const asOf = snapshot.status === 'ready' ? snapshot.health.as_of : null;
  const flowModel = useMemo(
    () => (scheduler !== null && processing !== null ? buildSchedulerFlowModel(scheduler, processing) : null),
    [scheduler, processing],
  );
  const heartbeatConfirmed = scheduler !== null && scheduler.status === 'ok';
  const stale = scheduler !== null && scheduler.status === 'stale';
  const lastHeartbeatAt = scheduler?.last_heartbeat_at ?? null;
  const intervalSeconds = scheduler?.interval_seconds ?? null;
  const workCount = scheduler?.current_work.length ?? 0;
  const showFlow = flowModel !== null && asOf !== null && (heartbeatConfirmed || stale);

  const requestError = onRequestError ?? (() => {});
  const closeLog = useCallback(() => {
    setLogOpen(false);
    logButtonRef.current?.focus();
    window.requestAnimationFrame(() => logButtonRef.current?.focus());
  }, []);

  return (
    <section
      data-testid="scheduler-live-view"
      aria-label="调度器实况"
      data-reduced-motion={reduceMotion ? 'true' : 'false'}
      className={`scheduler-live${pageVisible ? '' : ' scheduler-live-paused'}`}
    >
      <Header
        asOf={asOf}
        intervalSeconds={intervalSeconds}
        heartbeatConfirmed={heartbeatConfirmed}
        lastHeartbeatAt={lastHeartbeatAt}
        reduced={reduceMotion}
      />

      {/* 信号处理细节：心跳细节已由本页心跳区与 StaleNotice 展示，不重复。 */}
      <ProcessingHealthDetailBanner snapshot={snapshot}/>

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
          description="当前账号没有只读监控诊断权限，页面不展示心跳、任务网络与执行历史。"
        />
      )}
      {snapshot.status === 'unknown' && (
        <StatePanel
          testId="scheduler-live-unknown"
          icon={CircleHelp}
          title="尚无法确认调度器实况"
          description="诊断请求失败，无法获取心跳、任务网络与执行历史，请稍后重试。"
        />
      )}
      {scheduler !== null && scheduler.status === 'unknown' && (
        <StatePanel
          testId="scheduler-live-unconfirmed"
          icon={CircleHelp}
          title="无法确认调度器心跳"
          description="最近一次快照没有可确认的心跳，任务网络与执行历史不可确认，不展示在线心跳。"
        />
      )}

      {showFlow && flowModel !== null && asOf !== null && (
        <div className="scheduler-live-body">
          <Stats
            runningCount={flowModel.runningCount}
            workCount={workCount}
            jobCount={flowModel.jobCount}
            recentRunCount={flowModel.recentRunCount}
            attentionCount={flowModel.attentionCount}
            stale={stale}
          />

          {stale && <StaleNotice workCount={workCount} lastHeartbeatAt={lastHeartbeatAt} />}

          <div className="scheduler-live-toolbar">
            <div className="scheduler-live-toolbar-left">
              <span className={`scheduler-live-live-dot ${stale ? 'is-stale' : ''}`} aria-hidden="true" />
              <span className="scheduler-live-toolbar-title">任务网络</span>
              {flowModel.nextJob !== null && (
                <span className="scheduler-live-next">
                  下次 {formatShortDateTime(flowModel.nextJob.nextRunAt, asOf)} · {flowModel.nextJob.name}
                </span>
              )}
            </div>
            {canViewLogs && (
              <button
                ref={logButtonRef}
                type="button"
                data-testid="scheduler-log-open"
                className="scheduler-live-log-button"
                aria-haspopup="dialog"
                onClick={() => setLogOpen(true)}
              >
                <Terminal aria-hidden="true" />
                运行日志
                <span className="scheduler-live-log-live" aria-hidden="true" />
              </button>
            )}
          </div>

          <SchedulerFlowMap model={flowModel} asOf={asOf} live={heartbeatConfirmed} />
        </div>
      )}

      {logOpen && canViewLogs && (
        <SchedulerLogDrawer onClose={closeLog} onRequestError={requestError} />
      )}
    </section>
  );
};
