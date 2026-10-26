import {useCallback, useEffect, useLayoutEffect, useRef, useState, type MutableRefObject} from 'react';
import {useReducedMotion} from 'motion/react';
import {PROCESSING_STATE_LABELS, type MonitoringSchedulerWork} from '../api';
import {formatDateTime, formatDuration, formatRunningDuration} from './SchedulerActivityPanels';
import {
  formatShortDateTime,
  memberCountLabel,
  nodeStatusLabel,
  type FlowMember,
  type FlowNode,
  type FlowNodeStatus,
  type SchedulerFlowModel,
} from './schedulerFlowModel';
import './SchedulerFlowMap.css';

/**
 * 调度器实况四列网络图。
 *
 * 布局：01 采集 → 02 信号池 → 03 信号处理 → 04 产出。
 * 采集三节点汇入一条纵向总线，再单线进入信号池；消费节点从信号池中心直出，
 * 再通过第二条总线分叉到三个产出节点。所有连线为水平/垂直折线，拐角 8px 圆角，
 * 不画斜线、不穿卡片。
 *
 * 动效：只有运行中的边持续产生光点；完成/异常/错过只在首次观测到该 run 时放一次，
 * 不做与真实状态无关的循环装饰。stale / 减少动效 / 页面隐藏时停止光点。
 */

interface SchedulerFlowMapProps {
  readonly model: SchedulerFlowModel;
  readonly asOf: string;
  readonly live: boolean;
}

interface Point {
  readonly x: number;
  readonly y: number;
}

interface BasePath {
  readonly id: string;
  readonly d: string;
  readonly state: FlowNodeStatus;
}

interface ParticleRoute {
  readonly id: string;
  readonly points: readonly Point[];
  readonly sourceKey: string;
  readonly targetKey: string;
}

interface TriggerRequest {
  readonly routeId: string;
}

interface FlowLayout {
  readonly width: number;
  readonly height: number;
  readonly basePaths: readonly BasePath[];
  readonly ports: readonly (Point & {readonly state: FlowNodeStatus})[];
  readonly routes: readonly ParticleRoute[];
}

type ParticleKind = 'ambient' | 'trigger';
type FlashColor = 'white' | 'yellow';

interface Particle {
  readonly route: ParticleRoute;
  readonly kind: ParticleKind;
  t: number;
  readonly speed: number;
  arrived: boolean;
}

const EMPTY_LAYOUT: FlowLayout = {width: 0, height: 0, basePaths: [], ports: [], routes: []};
const STAGE_LABEL: Record<FlowNode['stage'], string> = {
  collect: '采集',
  signal: '信号',
  output: '产出',
  aux: '独立排期',
};
const SOURCE_KEYS = ['collect', 'source', 'tyc'] as const;
const OUTPUT_KEYS = ['notify', 'expire', 'intel'] as const;

const pathState = (status: FlowNodeStatus): string => {
  if (status === 'running') return 'active';
  if (status === 'error' || status === 'missed' || status === 'max_instances') return 'warn';
  if (status === 'completed') return 'done';
  return 'idle';
};

const dedupePoints = (points: readonly Point[]): Point[] => {
  const result: Point[] = [];
  for (const point of points) {
    const last = result[result.length - 1];
    if (last !== undefined && Math.abs(last.x - point.x) < 0.5 && Math.abs(last.y - point.y) < 0.5) continue;
    result.push(point);
  }
  return result;
};

/** 轴对齐折线 + 8px 圆角；只处理水平/垂直段。 */
const roundedPath = (points: readonly Point[], radius = 8): string => {
  const pts = dedupePoints(points);
  if (pts.length === 0) return '';
  if (pts.length === 1) return `M ${pts[0].x} ${pts[0].y}`;
  let d = `M ${pts[0].x} ${pts[0].y}`;
  for (let i = 1; i < pts.length - 1; i += 1) {
    const prev = pts[i - 1];
    const current = pts[i];
    const next = pts[i + 1];
    const inLength = Math.hypot(current.x - prev.x, current.y - prev.y);
    const outLength = Math.hypot(next.x - current.x, next.y - current.y);
    const r = Math.min(radius, inLength / 2, outLength / 2);
    if (r < 0.5) {
      d += ` L ${current.x} ${current.y}`;
      continue;
    }
    const inX = current.x - ((current.x - prev.x) / inLength) * r;
    const inY = current.y - ((current.y - prev.y) / inLength) * r;
    const outX = current.x + ((next.x - current.x) / outLength) * r;
    const outY = current.y + ((next.y - current.y) / outLength) * r;
    d += ` L ${inX} ${inY} Q ${current.x} ${current.y} ${outX} ${outY}`;
  }
  const last = pts[pts.length - 1];
  d += ` L ${last.x} ${last.y}`;
  return d;
};

const pointAtProgress = (points: readonly Point[], t: number): Point => {
  const pts = dedupePoints(points);
  if (pts.length === 0) return {x: 0, y: 0};
  if (pts.length === 1) return pts[0];
  let total = 0;
  const lengths: number[] = [];
  for (let i = 1; i < pts.length; i += 1) {
    const length = Math.hypot(pts[i].x - pts[i - 1].x, pts[i].y - pts[i - 1].y);
    lengths.push(length);
    total += length;
  }
  if (total <= 0) return pts[0];
  const target = Math.max(0, Math.min(1, t)) * total;
  let walked = 0;
  for (let i = 0; i < lengths.length; i += 1) {
    const length = lengths[i];
    if (walked + length >= target) {
      const ratio = length === 0 ? 0 : (target - walked) / length;
      return {
        x: pts[i].x + (pts[i + 1].x - pts[i].x) * ratio,
        y: pts[i].y + (pts[i + 1].y - pts[i].y) * ratio,
      };
    }
    walked += length;
  }
  return pts[pts.length - 1];
};

const rectRelative = (element: HTMLElement, base: DOMRect): {left: number; top: number; width: number; height: number} | null => {
  const rect = element.getBoundingClientRect();
  if (rect.width < 1 || rect.height < 1) return null;
  return {
    left: rect.left - base.left,
    top: rect.top - base.top,
    width: rect.width,
    height: rect.height,
  };
};

const buildLayout = (
  grid: HTMLElement,
  nodeEls: ReadonlyMap<string, HTMLElement>,
  modelNodes: readonly FlowNode[],
): FlowLayout => {
  const base = grid.getBoundingClientRect();
  const width = grid.clientWidth;
  const height = grid.clientHeight;
  if (width < 10 || height < 10) return EMPTY_LAYOUT;
  const rectOf = (key: string) => {
    const element = nodeEls.get(key);
    return element === undefined ? null : rectRelative(element, base);
  };
  const hub = rectOf('hub');
  const consume = rectOf('consume');
  if (hub === null || consume === null) return EMPTY_LAYOUT;

  const nodeByKey = new Map(modelNodes.map((node) => [node.key, node]));
  interface RectItem {
    readonly key: string;
    readonly rect: {left: number; top: number; width: number; height: number};
    readonly node: FlowNode;
  }
  const sourceRects: RectItem[] = [];
  for (const key of SOURCE_KEYS) {
    const rect = rectOf(key);
    const node = nodeByKey.get(key);
    if (rect !== null && node !== undefined) sourceRects.push({key, rect, node});
  }
  const outputRects: RectItem[] = [];
  for (const key of OUTPUT_KEYS) {
    const rect = rectOf(key);
    const node = nodeByKey.get(key);
    if (rect !== null && node !== undefined) outputRects.push({key, rect, node});
  }
  if (sourceRects.length === 0 || outputRects.length === 0) return EMPTY_LAYOUT;

  const hubCenterY = hub.top + hub.height / 2;
  const consumeCenterY = consume.top + consume.height / 2;
  const mergeGap = Math.max(20, hub.left - (sourceRects[0].rect.left + sourceRects[0].rect.width));
  const fanGap = Math.max(20, outputRects[0].rect.left - (consume.left + consume.width));
  const mergeBusX = sourceRects[0].rect.left + sourceRects[0].rect.width + mergeGap / 2;
  const fanBusX = consume.left + consume.width + fanGap / 2;
  const mergeState: FlowNodeStatus = sourceRects.some((item) => item.node.status === 'running')
    ? 'running'
    : sourceRects.some((item) => item.node.status === 'error')
      ? 'error'
      : sourceRects.some((item) => item.node.status === 'missed')
        ? 'missed'
        : sourceRects.some((item) => item.node.status === 'completed')
          ? 'completed'
          : 'no_record';
  const consumeState = nodeByKey.get('consume')?.status ?? 'no_record';

  const basePaths: BasePath[] = [];
  const ports: (Point & {state: FlowNodeStatus})[] = [];
  const routes: ParticleRoute[] = [];

  // 汇合：每个来源画一条完整折线（经过总线再进入池），圆角由 roundedPath 统一处理。
  for (const item of sourceRects) {
    const right = item.rect.left + item.rect.width;
    const y = item.rect.top + item.rect.height / 2;
    const points = dedupePoints([
      {x: right, y},
      {x: mergeBusX, y},
      {x: mergeBusX, y: hubCenterY},
      {x: hub.left, y: hubCenterY},
    ]);
    basePaths.push({id: `merge-${item.key}`, d: roundedPath(points), state: mergeState});
    ports.push({x: right, y, state: item.node.status});
    routes.push({id: `source-${item.key}`, points, sourceKey: item.key, targetKey: 'hub'});
  }
  ports.push({x: hub.left, y: hubCenterY, state: mergeState});
  ports.push({x: hub.left + hub.width, y: hubCenterY, state: consumeState});

  const hubRight = hub.left + hub.width;
  const consumeLeft = consume.left;
  const hubConsumePoints = dedupePoints([
    {x: hubRight, y: hubCenterY},
    {x: consumeLeft, y: consumeCenterY},
  ]);
  basePaths.push({id: 'hub-consume', d: roundedPath(hubConsumePoints), state: consumeState});
  ports.push({x: consumeLeft, y: consumeCenterY, state: consumeState});
  routes.push({id: 'hub-consume', points: hubConsumePoints, sourceKey: 'hub', targetKey: 'consume'});

  // 分叉：每个产出画一条完整折线（从消费节点经过总线），分支拐角为圆角。
  const consumeRight = consume.left + consume.width;
  for (const item of outputRects) {
    const y = item.rect.top + item.rect.height / 2;
    const points = dedupePoints([
      {x: consumeRight, y: consumeCenterY},
      {x: fanBusX, y: consumeCenterY},
      {x: fanBusX, y},
      {x: item.rect.left, y},
    ]);
    basePaths.push({id: `fan-${item.key}`, d: roundedPath(points), state: consumeState});
    ports.push({x: item.rect.left, y, state: item.node.status});
    routes.push({id: `output-${item.key}`, points, sourceKey: 'consume', targetKey: item.key});
  }
  ports.push({x: consumeRight, y: consumeCenterY, state: consumeState});

  return {width, height, basePaths, ports, routes};
};

interface FlowParticlesProps {
  readonly layout: FlowLayout;
  readonly live: boolean;
  readonly reduced: boolean;
  readonly paused: boolean;
  readonly triggerQueueRef: MutableRefObject<TriggerRequest[]>;
  readonly triggerVersion: number;
  readonly onArrive: (key: string, kind: ParticleKind) => void;
}

const AMBIENT_INTERVAL_SECONDS = 3.2;
const AMBIENT_SPEED = 1 / 3.6;
const TRIGGER_SPEED = 1 / 2.4;

const FlowParticles = ({
  layout,
  live,
  reduced,
  paused,
  triggerQueueRef,
  triggerVersion,
  onArrive,
}: FlowParticlesProps) => {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const rafRef = useRef(0);
  const particlesRef = useRef<Particle[]>([]);
  const routesRef = useRef<readonly ParticleRoute[]>(layout.routes);
  const onArriveRef = useRef(onArrive);
  routesRef.current = layout.routes;

  useEffect(() => {
    onArriveRef.current = onArrive;
  }, [onArrive]);

  const spawnParticle = useCallback((route: ParticleRoute, kind: ParticleKind) => {
    particlesRef.current.push({
      route,
      kind,
      t: 0,
      speed: kind === 'trigger' ? TRIGGER_SPEED : AMBIENT_SPEED,
      arrived: false,
    });
    if (particlesRef.current.length > 28) particlesRef.current.shift();
  }, []);

  // 主循环只随画布尺寸/可见性重建；快照状态变化不会重置正在飞行的光点。
  useEffect(() => {
    const canvas = canvasRef.current;
    if (canvas === null || layout.width < 10 || layout.height < 10) return;
    const dpr = Math.max(1, Math.min(2, window.devicePixelRatio || 1));
    canvas.width = Math.max(1, Math.round(layout.width * dpr));
    canvas.height = Math.max(1, Math.round(layout.height * dpr));
    const context = canvas.getContext('2d');
    if (context === null) return;
    context.setTransform(dpr, 0, 0, dpr, 0, 0);
    context.clearRect(0, 0, layout.width, layout.height);

    particlesRef.current = [];
    if (!live || reduced || paused) return undefined;

    let nextAmbientAt = performance.now() / 1000 + 1.1;
    let ambientIndex = 0;

    const draw = (frameNow: number) => {
      const seconds = frameNow / 1000;
      context.clearRect(0, 0, layout.width, layout.height);

      const routes = routesRef.current;
      if (routes.length > 0 && seconds >= nextAmbientAt) {
        spawnParticle(routes[ambientIndex % routes.length], 'ambient');
        ambientIndex += 1;
        nextAmbientAt = seconds + AMBIENT_INTERVAL_SECONDS;
      }

      particlesRef.current = particlesRef.current.filter((particle) => particle.t < 1);
      for (const particle of particlesRef.current) {
        particle.t = Math.min(1.01, particle.t + (1 / 60) * particle.speed);
        if (!particle.arrived && particle.t >= 0.985) {
          particle.arrived = true;
          onArriveRef.current(particle.route.targetKey, particle.kind);
        }
        const point = pointAtProgress(particle.route.points, particle.t);
        const isTrigger = particle.kind === 'trigger';
        const color = isTrigger ? '#ffd60a' : '#e8f0fb';
        if (isTrigger) {
          for (const offset of [0.02, 0.042]) {
            const trailPoint = pointAtProgress(particle.route.points, Math.max(0, particle.t - offset));
            context.beginPath();
            context.globalAlpha = 0.2;
            context.fillStyle = color;
            context.arc(trailPoint.x, trailPoint.y, 2.4, 0, Math.PI * 2);
            context.fill();
          }
        }
        context.globalAlpha = isTrigger ? 0.98 : 0.7;
        context.beginPath();
        context.fillStyle = color;
        context.shadowColor = color;
        context.shadowBlur = isTrigger ? 10 : 5;
        context.arc(point.x, point.y, isTrigger ? 4 : 2.2, 0, Math.PI * 2);
        context.fill();
        context.shadowBlur = 0;
        context.globalAlpha = 1;
      }
      rafRef.current = window.requestAnimationFrame(draw);
    };

    rafRef.current = window.requestAnimationFrame(draw);
    return () => {
      window.cancelAnimationFrame(rafRef.current);
      rafRef.current = 0;
      context.clearRect(0, 0, layout.width, layout.height);
    };
  }, [layout.width, layout.height, live, reduced, paused, spawnParticle]);

  // 真实任务触发：由父组件放入请求队列，这里只负责放一颗黄色光点。
  useEffect(() => {
    if (!live || reduced || paused) {
      triggerQueueRef.current.length = 0;
      return;
    }
    const requests = triggerQueueRef.current.splice(0);
    for (const request of requests) {
      const route = routesRef.current.find((item) => item.id === request.routeId);
      if (route !== undefined) spawnParticle(route, 'trigger');
    }
  }, [triggerVersion, live, reduced, paused, triggerQueueRef, spawnParticle]);

  return (
    <canvas
      ref={canvasRef}
      className="sflow-particles"
      aria-hidden="true"
      style={{width: layout.width > 0 ? `${layout.width}px` : '100%', height: layout.height > 0 ? `${layout.height}px` : '100%'}}
    />
  );
};

const StatusBadge = ({status, live}: {readonly status: FlowNodeStatus; readonly live: boolean}) => (
  <span className={`sflow-badge sflow-badge--${status}`}>{nodeStatusLabel(status, live)}</span>
);

const formatCount = (value: number): string => value.toLocaleString('zh-CN');

const workLabel = (work: MonitoringSchedulerWork): string => {
  if (work.kind === 'source_collection') return `${work.source_name ?? '信源'} · 采集中`;
  return `信号 #${work.item_id ?? '—'} · 处理中`;
};

const NodeFoot = ({node, asOf, live}: {readonly node: FlowNode; readonly asOf: string; readonly live: boolean}) => {
  // 未注册节点没有可对照的执行记录：保留注册说明，下一次照样给占位。
  if (!node.present) {
    return (
      <span className="sflow-node-foot">
        <span className="sflow-node-foot-line">未在调度器注册</span>
        <span className="sflow-node-foot-line">
          <span className="sflow-node-foot-label">下次</span>
          <span className="sflow-node-foot-time">—</span>
        </span>
      </span>
    );
  }
  const latest = node.latestRun;
  const running = node.status === 'running';
  const startedAt = latest?.started_at ?? node.runningWork[0]?.started_at ?? null;
  // 上次时间与“最近一次”详情同一口径：结束时间优先，其次开始，最后计划时间。
  const lastRunAt = running
    ? startedAt
    : latest === null
      ? null
      : latest.finished_at ?? latest.started_at ?? latest.scheduled_run_at;
  const duration = node.errorCount > 0
    ? `${node.errorCount} 异常`
    : running
      ? formatRunningDuration(startedAt, asOf)
      : latest === null
        ? null
        : latest.started_at === null || latest.finished_at === null
          ? '—'
          : formatDuration(latest.started_at, latest.finished_at);
  return (
    <span className="sflow-node-foot">
      <span className="sflow-node-foot-line">
        <span className="sflow-node-foot-label">{running ? '本次' : '上次'}</span>
        <span className="sflow-node-foot-time">{lastRunAt === null ? '—' : formatShortDateTime(lastRunAt, asOf)}</span>
        <StatusBadge status={node.status} live={live} />
        {duration !== null && (
          <b
            className={`sflow-node-foot-right${node.errorCount > 0 ? ' sflow-node-error-count' : ''}`}
            title={duration}
          >
            {duration}
          </b>
        )}
      </span>
      <span className="sflow-node-foot-line">
        <span className="sflow-node-foot-label">下次</span>
        <span className="sflow-node-foot-time">{formatShortDateTime(node.nextRunAt, asOf)}</span>
      </span>
    </span>
  );
};

const FlowNodeCard = ({
  node,
  asOf,
  live,
  selected,
  flash,
  onSelect,
  registerRef,
}: {
  readonly node: FlowNode;
  readonly asOf: string;
  readonly live: boolean;
  readonly selected: boolean;
  readonly flash: FlashColor | undefined;
  readonly onSelect: (key: string) => void;
  readonly registerRef: (key: string, element: HTMLButtonElement | null) => void;
}) => {
  const flashClass = flash === undefined ? '' : `is-flash-${flash}`;
  if (node.kind === 'data') {
    const processing = node.processing;
    const rawPending = processing?.processing_state_counts?.pending_classification ?? null;
    const actionable = processing?.total ?? 0;
    return (
      <button
        type="button"
        ref={(element) => registerRef('hub', element)}
        data-testid="scheduler-flow-node-hub"
        data-node-key="hub"
        aria-pressed={selected}
        onClick={() => onSelect('hub')}
        className={`sflow-node sflow-hub ${selected ? 'is-selected' : ''} ${flashClass}`}
      >
        <span className="sflow-hub-tag">数据节点</span>
        <span className="sflow-hub-label">待分析信号池</span>
        <span className="sflow-hub-count">{actionable}<em>条可分析</em></span>
        <span className="sflow-hub-row">积压 &gt; 1h <b>{processing?.backlog_over_1h ?? 0}</b></span>
        <span className="sflow-hub-row">分类失败 <b>{processing?.classification_failed ?? 0}</b></span>
        {rawPending !== null && rawPending !== actionable && (
          <span
            className="sflow-hub-row sflow-hub-row--muted"
            title="包含已失效、已替代、已撤销等不会进入消费队列的历史待分类信号"
          >
            待分类总数 <b>{formatCount(rawPending)}</b>
          </span>
        )}
        <span className="sflow-hub-foot">
          最近处理 <b>{processing?.last_run.status === 'running' ? '处理中' : processing?.last_run.finished_at === null ? '—' : formatDateTime(processing?.last_run.finished_at ?? '')}</b>
        </span>
      </button>
    );
  }
  const count = memberCountLabel(node);
  return (
    <button
      type="button"
      ref={(element) => registerRef(node.key, element)}
      data-testid={`scheduler-flow-node-${node.key}`}
      data-node-key={node.key}
      aria-pressed={selected}
      onClick={() => onSelect(node.key)}
      className={`sflow-node sflow-node--${node.stage} sflow-state--${node.status} ${!node.present ? 'is-unregistered' : ''} ${selected ? 'is-selected' : ''} ${flashClass}`}
    >
      <span className="sflow-node-top">
        <span className="sflow-node-dot" />
        <span className="sflow-node-name">{node.name}{count === null ? '' : ` ${count}`}</span>
      </span>
      <NodeFoot node={node} asOf={asOf} live={live} />
    </button>
  );
};

const MemberRow = ({member, live}: {readonly member: FlowMember; readonly live: boolean}) => (
  <li className="sflow-member">
    <span className={`sflow-member-dot sflow-state--${member.status}`} />
    <span className="sflow-member-main">
      <span className="sflow-member-name">{member.name}</span>
    </span>
    <StatusBadge status={member.status} live={live} />
    <span className="sflow-member-next">下次 {formatShortDateTime(member.nextRunAt, null)}</span>
  </li>
);

const NodeDetail = ({
  node,
  asOf,
  live,
}: {
  readonly node: FlowNode;
  readonly asOf: string;
  readonly live: boolean;
}) => {
  const latest = node.latestRun;
  if (node.kind === 'data') {
    const processing = node.processing;
    const lastRun = processing?.last_run;
    const rawPending = processing?.processing_state_counts?.pending_classification ?? null;
    const actionable = processing?.total ?? 0;
    const stateCounts = processing?.processing_state_counts ?? {};
    const stateEntries = Object.entries(stateCounts).sort((left, right) => right[1] - left[1]);
    return (
      <section className="sflow-detail" aria-live="polite" data-testid="scheduler-flow-detail">
        <div className="sflow-detail-head">
          <StatusBadge status="data" live={live} />
          <b>{node.name}</b>
          <code>processing</code>
          <span className="sflow-detail-stage">信号池数据节点</span>
        </div>
        <div className="sflow-detail-grid">
          <div><span>可分析</span><b>{actionable}</b></div>
          {rawPending !== null && <div><span>待分类总数</span><b>{formatCount(rawPending)}</b></div>}
          <div><span>积压 &gt; 1h</span><b>{processing?.backlog_over_1h ?? 0}</b></div>
          <div><span>分类失败</span><b>{processing?.classification_failed ?? 0}</b></div>
          <div><span>最老待处理</span><b>{processing?.oldest_pending_age_seconds === null || processing?.oldest_pending_age_seconds === undefined ? '—' : `${Math.floor(processing.oldest_pending_age_seconds / 60)} 分钟`}</b></div>
          <div><span>最近处理</span><b>{lastRun === undefined || lastRun.finished_at === null ? (lastRun?.status === 'running' ? '处理中' : '—') : formatDateTime(lastRun.finished_at)}</b></div>
        </div>
        {rawPending !== null && rawPending > actionable && (
          <p className="sflow-processing-note">
            “可分析”只统计当前会进入消费队列的候选；待分类总数还包含已失效、已替代、已撤销等不会被执行的历史行。
          </p>
        )}
        {stateEntries.length > 0 && (
          <div className="sflow-state-counts" aria-label="信号处理状态分布">
            {stateEntries.map(([state, count]) => (
              <span key={state} className={`sflow-processing--${state}`}>
                <i aria-hidden="true" />
                {PROCESSING_STATE_LABELS[state as keyof typeof PROCESSING_STATE_LABELS] ?? state}
                <b>{formatCount(count)}</b>
              </span>
            ))}
          </div>
        )}
      </section>
    );
  }
  const latestLabel = latest === null
    ? (node.runningWork.length > 0 ? `执行中 · ${formatRunningDuration(node.runningWork[0].started_at, asOf)}` : '—')
    : latest.status === 'running'
      ? `执行中 · ${formatRunningDuration(latest.started_at, asOf)}`
      : `${nodeStatusLabel(latest.status, live)} · ${formatDateTime(latest.finished_at ?? latest.started_at ?? latest.scheduled_run_at)}`;
  return (
    <section className="sflow-detail" aria-live="polite" data-testid="scheduler-flow-detail">
      <div className="sflow-detail-head">
        <StatusBadge status={node.status} live={live} />
        <b>{node.name}</b>
        <span className="sflow-detail-stage">{STAGE_LABEL[node.stage]}</span>
      </div>
      <div className="sflow-detail-grid">
        <div><span>下次执行</span><b>{formatShortDateTime(node.nextRunAt, asOf)}</b></div>
        <div><span>最近一次</span><b>{latestLabel}</b></div>
        <div><span>运行中</span><b>{node.runningCount}</b></div>
        {node.members.length > 1 && <div><span>成员</span><b>{node.members.length}</b></div>}
        {node.runningWork.length > 0 && (
          <div className="is-work">
            <span>当前工作</span>
            <b>{workLabel(node.runningWork[0])}{node.runningWork.length > 1 ? ` +${node.runningWork.length - 1}` : ''}</b>
          </div>
        )}
        {node.errorCount > 0 && <div><span>异常成员</span><b className="is-error">{node.errorCount}</b></div>}
        {node.missedCount > 0 && <div><span>错过成员</span><b className="is-warn">{node.missedCount}</b></div>}
      </div>
      {node.members.length > 1 && (
        <ul className="sflow-members" aria-label={`${node.name} 成员`}>
          {node.members.map((member) => <MemberRow key={member.jobId} member={member} live={live} />)}
        </ul>
      )}
    </section>
  );
};

export const SchedulerFlowMap = ({model, asOf, live}: SchedulerFlowMapProps) => {
  const [selectedKey, setSelectedKey] = useState('consume');
  const [layout, setLayout] = useState<FlowLayout>(EMPTY_LAYOUT);
  const gridRef = useRef<HTMLDivElement | null>(null);
  const nodeRefs = useRef(new Map<string, HTMLButtonElement>());
  const reduceMotion = useReducedMotion() === true;
  const [pageVisible, setPageVisible] = useState(() => document.visibilityState !== 'hidden');
  const [flashes, setFlashes] = useState<Record<string, FlashColor>>({});
  const [triggerVersion, setTriggerVersion] = useState(0);
  const flashRef = useRef<Record<string, FlashColor>>({});
  const flashTimers = useRef(new Map<string, number>());
  const seenRunIds = useRef(new Map<string, string>());
  const initialRunScan = useRef(true);
  const seenProcessingSignature = useRef('');
  const processingBaselineReady = useRef(false);
  const triggerQueueRef = useRef<TriggerRequest[]>([]);

  const registerRef = useCallback((key: string, element: HTMLButtonElement | null) => {
    if (element === null) nodeRefs.current.delete(key);
    else nodeRefs.current.set(key, element);
  }, []);

  const flashNode = useCallback((key: string, color: FlashColor) => {
    // 黄色触发闪优先，避免同一时刻到达的白色环境光点把黄色盖掉。
    if (color === 'white' && flashRef.current[key] === 'yellow') return;
    const next = {...flashRef.current, [key]: color};
    flashRef.current = next;
    setFlashes(next);
    const existing = flashTimers.current.get(key);
    if (existing !== undefined) window.clearTimeout(existing);
    const timer = window.setTimeout(() => {
      if (flashRef.current[key] !== color) return;
      const copy = {...flashRef.current};
      delete copy[key];
      flashRef.current = copy;
      setFlashes(copy);
      flashTimers.current.delete(key);
    }, 700);
    flashTimers.current.set(key, timer);
  }, []);

  useEffect(() => () => {
    for (const timer of flashTimers.current.values()) window.clearTimeout(timer);
    flashTimers.current.clear();
  }, []);

  useEffect(() => {
    const nodes = [...model.nodes, ...model.aux];
    if (!nodes.some((node) => node.key === selectedKey)) setSelectedKey('hub');
  }, [model, selectedKey]);

  useEffect(() => {
    const handleVisibility = () => setPageVisible(document.visibilityState !== 'hidden');
    document.addEventListener('visibilitychange', handleVisibility);
    return () => document.removeEventListener('visibilitychange', handleVisibility);
  }, []);

  // 真实 run 变化 → 一颗黄色光点。首次渲染只登记基线，不补闪历史。
  useEffect(() => {
    let queued = false;
    const routeFor = (key: string): string | null => {
      if (SOURCE_KEYS.some((sourceKey) => sourceKey === key)) return `source-${key}`;
      if (OUTPUT_KEYS.some((outputKey) => outputKey === key)) return `output-${key}`;
      return null;
    };
    for (const node of [...model.nodes, ...model.aux]) {
      const run = node.latestRun;
      const runId = run === null ? '' : String(run.id);
      const previous = seenRunIds.current.get(node.key) ?? '';
      if (runId === previous) continue;
      if (runId === '') {
        seenRunIds.current.delete(node.key);
        continue;
      }
      seenRunIds.current.set(node.key, runId);
      // 消费任务每 20 秒都会产生 run，即使没有信号也不代表真实工作；
      // 改由下方 processing.last_run 的“完成且确实处理过信号”来判断。
      if (node.key === 'consume') continue;
      if (initialRunScan.current || !live || reduceMotion || !pageVisible) continue;
      const routeId = routeFor(node.key);
      if (routeId === null) {
        // 没有出边的独立任务没有黄色光点可到达，直接以黄色闪框表明触发。
        flashNode(node.key, 'yellow');
        continue;
      }
      // 只有黄色光点真正到达目标节点时才闪框；来源节点不在触发瞬间闪。
      triggerQueueRef.current.push({routeId});
      queued = true;
    }

    const processingRun = model.nodes.find((node) => node.key === 'hub')?.processing?.last_run;
    const processingWork = processingRun === undefined
      ? 0
      : processingRun.processed + processingRun.filtered + processingRun.failed;
    const processingSignature = processingRun?.finished_at === null || processingRun?.finished_at === undefined
      ? ''
      : `${processingRun.finished_at}|${processingRun.processed}|${processingRun.filtered}|${processingRun.failed}`;
    if (!processingBaselineReady.current) {
      processingBaselineReady.current = true;
      seenProcessingSignature.current = processingSignature;
    } else if (processingSignature !== seenProcessingSignature.current) {
      seenProcessingSignature.current = processingSignature;
      // 只有真正处理过信号（processed/filtered/failed > 0）的完成轮次才放黄点；
      // 每 20 秒的空转 run 不触发。
      if (processingSignature !== ''
        && processingWork > 0
        && !initialRunScan.current
        && live
        && !reduceMotion
        && pageVisible) {
        triggerQueueRef.current.push({routeId: 'hub-consume'});
        queued = true;
      }
    }

    initialRunScan.current = false;
    if (queued) setTriggerVersion((value) => value + 1);
  }, [model, live, reduceMotion, pageVisible, flashNode]);

  const measure = useCallback(() => {
    const grid = gridRef.current;
    if (grid === null) return;
    setLayout(buildLayout(grid, nodeRefs.current, model.nodes));
  }, [model.nodes]);

  useLayoutEffect(() => {
    measure();
    const grid = gridRef.current;
    if (grid === null || typeof ResizeObserver === 'undefined') return undefined;
    const observer = new ResizeObserver(() => measure());
    observer.observe(grid);
    return () => observer.disconnect();
  }, [measure]);

  const selectedNode = [...model.nodes, ...model.aux].find((node) => node.key === selectedKey)
    ?? model.nodes.find((node) => node.key === 'hub')
    ?? model.nodes[0];
  return (
    <section className="sflow" data-testid="scheduler-flow-map" data-live={live ? 'true' : 'false'}>
      <div className="sflow-toolbar">
        <div className="sflow-legend" aria-hidden="true">
          <span><i className="sflow-legend-dot sflow-state--running" />执行中</span>
          <span><i className="sflow-legend-dot sflow-state--completed" />执行完成</span>
          <span><i className="sflow-legend-dot sflow-state--error" />执行异常</span>
          <span><i className="sflow-legend-dot sflow-state--missed" />错过执行</span>
          <span><i className="sflow-legend-dot sflow-state--unregistered" />无记录</span>
        </div>
      </div>
      <div className="sflow-scroll">
        <div className="sflow-canvas">
          <div className="sflow-grid" ref={gridRef}>
            <div className="sflow-label sflow-label--collect"><b>01</b> 采集 <span /></div>
            <div className="sflow-label sflow-label--signal"><b>02</b> 信号池 <span /></div>
            <div className="sflow-label sflow-label--process"><b>03</b> 信号处理 <span /></div>
            <div className="sflow-label sflow-label--output"><b>04</b> 产出 <span /></div>

            <svg className="sflow-edges" width={layout.width || '100%'} height={layout.height || '100%'} aria-hidden="true">
              {layout.basePaths.map((path) => (
                <path key={path.id} d={path.d} className={`sflow-edge sflow-edge--${pathState(path.state)}`} />
              ))}
            </svg>
            <FlowParticles
              layout={layout}
              live={live}
              reduced={reduceMotion}
              paused={!pageVisible}
              triggerQueueRef={triggerQueueRef}
              triggerVersion={triggerVersion}
              onArrive={(key, kind) => {
                // 白色环境光点不触发节点闪烁；只有真实任务的黄色光点到达时才闪。
                if (kind === 'trigger') flashNode(key, 'yellow');
              }}
            />
            <svg className="sflow-ports" width={layout.width || '100%'} height={layout.height || '100%'} aria-hidden="true">
              {layout.ports.map((port, index) => (
                <g key={`${index}-${port.x}-${port.y}`}>
                  <circle cx={port.x} cy={port.y} r={3} className={`sflow-port sflow-state--${port.state}`} />
                  <circle cx={port.x} cy={port.y} r={5.4} className={`sflow-port-ring sflow-state--${port.state}`} />
                </g>
              ))}
            </svg>

            {model.nodes.map((node) => (
              <div
                key={node.key}
                className={`sflow-slot sflow-slot--${node.key}`}
              >
                <FlowNodeCard
                  node={node}
                  asOf={asOf}
                  live={live}
                  selected={node.key === selectedKey}
                  flash={flashes[node.key]}
                  onSelect={setSelectedKey}
                  registerRef={registerRef}
                />
              </div>
            ))}
          </div>
          <div className="sflow-aux">
            <span className="sflow-aux-label">独立排期</span>
            {model.aux.length === 0 ? (
              <span className="sflow-aux-empty">暂无独立排期任务</span>
            ) : model.aux.map((node) => (
              <button
                key={node.key}
                type="button"
                data-testid={`scheduler-flow-aux-${node.key}`}
                aria-pressed={node.key === selectedKey}
                onClick={() => setSelectedKey(node.key)}
                className={`sflow-aux-chip sflow-state--${node.status} ${node.key === selectedKey ? 'is-selected' : ''} ${flashes[node.key] === undefined ? '' : `is-flash-${flashes[node.key]}`}`}
              >
                <span className="sflow-node-dot" />
                {node.name}
                <span className="sflow-aux-status">{nodeStatusLabel(node.status, live)}</span>
              </button>
            ))}
          </div>
        </div>
      </div>
      {selectedNode !== undefined && <NodeDetail node={selectedNode} asOf={asOf} live={live} />}
    </section>
  );
};
