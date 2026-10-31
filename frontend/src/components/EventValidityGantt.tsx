import {useMemo, useState} from 'react';
import type {RiskItem} from '../types';

const HOUR_MS = 60 * 60 * 1000;
/** 轴两端各留 5% 余量；跨度很短时至少留 1 小时，避免单点事件贴在轴边上。 */
const AXIS_PADDING_RATIO = 0.05;
const MIN_AXIS_PADDING_MS = HOUR_MS;
/** 只有一个时间点的事件也要有可点击、可看见的宽度。 */
const MIN_BAR_WIDTH_PERCENT = 1.2;
/** 全量模式下时间轴的最小宽度：窄屏横向滚动而不是压扁时间。 */
const FULL_MIN_WIDTH = 560;

const pad2 = (value: number) => String(value).padStart(2, '0');
const round = (value: number) => Math.round(value * 1000) / 1000;

/** 组内单个事件的展示模型，字段与后端 merged_events 解耦，缺失时一律降级。 */
export interface GanttMember {
  alertId: number;
  eventId: number;
  summary: string;
  sourceTitle: string;
  level: string;
  /** 优先事件开始时间，回退发布时间。 */
  startAt: string | null;
  /** 提醒有效期截止时间。 */
  expiresAt: string | null;
  status: 'current' | 'expired';
  superseded?: boolean;
  /** 事件影响的供应商数；字段未落地时为 undefined。 */
  affectedSupplierCount?: number | null;
}

export type GanttVariant = 'mini' | 'full';

export interface EventValidityGanttProps {
  readonly members: readonly GanttMember[];
  readonly variant?: GanttVariant;
  readonly now?: Date;
  readonly onSelectEvent?: (eventId: number) => void;
}

export interface GanttRow {
  member: GanttMember;
  leftPercent: number;
  widthPercent: number;
  hasRange: boolean;
}

export interface GanttLayout {
  axisStart: Date | null;
  axisEnd: Date | null;
  rows: GanttRow[];
}

export interface GanttTick {
  at: Date;
  percent: number;
  label: string;
}

export type GanttGroupStatus = 'active' | 'partial' | 'expired';

export interface GanttGroupSummary {
  memberCount: number;
  sourceCount: number;
  earliestStart: Date | null;
  latestExpires: Date | null;
  status: GanttGroupStatus;
}

export type GanttDisplayStatus = 'current' | 'expired' | 'superseded';

/* ── 时间解析与格式化 ─────────────────────────────────────────────────────── */

export function parseGanttTime(value: string | Date | null | undefined): Date | null {
  if (!value) return null;
  const date = value instanceof Date ? value : new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

/** 简洁时间：9/1 08:00；带年份用于悬停详情，避免跨年误读。 */
export function formatGanttMoment(value: string | Date | null | undefined, withYear = false): string {
  const date = parseGanttTime(value);
  if (date === null) return '时间未知';
  const monthDay = `${date.getMonth() + 1}/${date.getDate()}`;
  const time = `${pad2(date.getHours())}:${pad2(date.getMinutes())}`;
  return withYear ? `${date.getFullYear()}/${monthDay} ${time}` : `${monthDay} ${time}`;
}

/** 轴端点标签：一天内只给时分，跨天补上日期。 */
export function formatGanttAxisLabel(value: Date, spanMs: number): string {
  if (spanMs <= 24 * HOUR_MS) return `${pad2(value.getHours())}:${pad2(value.getMinutes())}`;
  return formatGanttMoment(value);
}

/** min ~ max 区间文案；只有一端时降级为「自 …」或「至 …」，两端都没有返回 null。 */
export function formatGanttRange(start: Date | null, end: Date | null, withYear = false): string | null {
  if (start !== null && end !== null) {
    if (start.getTime() === end.getTime()) return formatGanttMoment(start, withYear);
    return `${formatGanttMoment(start, withYear)} ~ ${formatGanttMoment(end, withYear)}`;
  }
  if (start !== null) return `自 ${formatGanttMoment(start, withYear)}`;
  if (end !== null) return `至 ${formatGanttMoment(end, withYear)}`;
  return null;
}

/** 左栏摘要按 24 字截断，避免长摘要吃掉时间轴宽度。 */
export function truncateGanttSummary(summary: string, maxLength = 24): string {
  const normalized = summary.replace(/\s+/g, ' ').trim();
  const chars = Array.from(normalized);
  return chars.length > maxLength ? `${chars.slice(0, maxLength).join('')}…` : normalized;
}

export function displayStatus(member: GanttMember): GanttDisplayStatus {
  if (member.superseded) return 'superseded';
  return member.status === 'expired' ? 'expired' : 'current';
}

export const GANTT_STATUS_LABEL: Record<GanttDisplayStatus, string> = {
  current: '有效',
  expired: '已过期',
  superseded: '已被替代',
};

/* ── 布局计算 ─────────────────────────────────────────────────────────────── */

/**
 * 轴范围取组内 min(开始) ~ max(截止)，两端各留 5%（至少 1 小时）。
 * 没有任何时间的成员 hasRange=false，不画条。
 */
export function buildGanttLayout(
  members: readonly GanttMember[],
  _now: Date,
  _variant: GanttVariant,
): GanttLayout {
  const prepared = members.map((member) => ({
    member,
    start: parseGanttTime(member.startAt),
    end: parseGanttTime(member.expiresAt),
  }));

  const times: number[] = [];
  for (const item of prepared) {
    if (item.start !== null) times.push(item.start.getTime());
    if (item.end !== null) times.push(item.end.getTime());
  }
  if (times.length === 0) {
    return {
      axisStart: null,
      axisEnd: null,
      rows: members.map((member) => ({member, leftPercent: 0, widthPercent: 0, hasRange: false})),
    };
  }

  const earliest = Math.min(...times);
  const latest = Math.max(...times);
  const dataSpan = latest - earliest;
  const padding = Math.max(dataSpan * AXIS_PADDING_RATIO, MIN_AXIS_PADDING_MS);
  let axisStartMs = earliest - padding;
  let axisEndMs = latest + padding;
  // 单一时间点（开始=截止）时保证轴至少有一小时。
  if (axisEndMs - axisStartMs < HOUR_MS) {
    const middle = (axisStartMs + axisEndMs) / 2;
    axisStartMs = middle - HOUR_MS / 2;
    axisEndMs = middle + HOUR_MS / 2;
  }

  const total = axisEndMs - axisStartMs;
  const rows = prepared.map(({member, start, end}) => {
    if (start === null && end === null) {
      return {member, leftPercent: 0, widthPercent: 0, hasRange: false};
    }
    // 缺开始按轴起点算，缺截止按轴终点算（未知边界不画到轴外）。
    const startMs = start !== null ? start.getTime() : axisStartMs;
    const endMs = end !== null ? end.getTime() : axisEndMs;
    const left = Math.min(Math.max(((startMs - axisStartMs) / total) * 100, 0), 100);
    const width = Math.min(Math.max(((endMs - startMs) / total) * 100, MIN_BAR_WIDTH_PERCENT), 100 - left);
    return {
      member,
      leftPercent: round(left),
      widthPercent: round(Math.max(width, 0)),
      hasRange: true,
    };
  });

  return {axisStart: new Date(axisStartMs), axisEnd: new Date(axisEndMs), rows};
}

/** 刻度按跨度分档：≤24h 用 3/6 小时，≤7d 用天，更长用日期步进。 */
export function buildGanttTicks(axisStart: Date, axisEnd: Date): GanttTick[] {
  const total = axisEnd.getTime() - axisStart.getTime();
  if (total <= 0) return [];
  const spanHours = total / HOUR_MS;
  const ticks: GanttTick[] = [];

  const push = (date: Date) => {
    ticks.push({
      at: date,
      percent: round(((date.getTime() - axisStart.getTime()) / total) * 100),
      label: spanHours <= 24
        ? `${pad2(date.getHours())}:${pad2(date.getMinutes())}`
        : `${date.getMonth() + 1}/${date.getDate()}`,
    });
  };

  if (spanHours <= 24) {
    const stepHours = spanHours <= 12 ? 3 : 6;
    const cursor = new Date(axisStart);
    cursor.setMinutes(0, 0, 0);
    cursor.setHours(Math.ceil(cursor.getHours() / stepHours) * stepHours);
    while (cursor.getTime() <= axisEnd.getTime() && ticks.length < 12) {
      if (cursor.getTime() >= axisStart.getTime()) push(new Date(cursor));
      cursor.setHours(cursor.getHours() + stepHours);
    }
    return ticks;
  }

  const stepDays = spanHours <= 24 * 7 ? 1 : Math.max(1, Math.ceil(spanHours / 24 / 6));
  const cursor = new Date(axisStart.getFullYear(), axisStart.getMonth(), axisStart.getDate() + 1);
  while (cursor.getTime() <= axisEnd.getTime() && ticks.length < 10) {
    push(new Date(cursor));
    cursor.setDate(cursor.getDate() + stepDays);
  }
  return ticks;
}

/** 后端组级状态（active|partially_expired|expired）归一到徽标口径；无法识别时返回 null。 */
export function normalizeGroupStatus(raw: unknown): GanttGroupStatus | null {
  if (raw === 'active') return 'active';
  if (raw === 'partially_expired' || raw === 'partial') return 'partial';
  if (raw === 'expired') return 'expired';
  return null;
}

export function summarizeGanttGroup(members: readonly GanttMember[]): GanttGroupSummary {
  const sources = new Set<string>();
  const starts: number[] = [];
  const ends: number[] = [];
  let expiredCount = 0;

  for (const member of members) {
    if (member.sourceTitle) sources.add(member.sourceTitle);
    const start = parseGanttTime(member.startAt);
    const end = parseGanttTime(member.expiresAt);
    if (start !== null) starts.push(start.getTime());
    if (end !== null) ends.push(end.getTime());
    if (member.status === 'expired' || member.superseded) expiredCount += 1;
  }

  // 字段全缺失时回退 active，不把未知状态误报成失效。
  const status: GanttGroupStatus = expiredCount === 0
    ? 'active'
    : expiredCount === members.length ? 'expired' : 'partial';

  return {
    memberCount: members.length,
    sourceCount: sources.size,
    earliestStart: starts.length > 0 ? new Date(Math.min(...starts)) : null,
    latestExpires: ends.length > 0 ? new Date(Math.max(...ends)) : null,
    status,
  };
}

/* ── 兼容层：把尚未落地的组内事件字段归一成 GanttMember ──────────────────── */

const readField = (raw: Record<string, unknown>, keys: readonly string[]): unknown => {
  for (const key of keys) {
    const value = raw[key];
    if (value !== undefined && value !== null && value !== '') return value;
  }
  return undefined;
};

const readText = (raw: Record<string, unknown>, keys: readonly string[]): string => {
  const value = readField(raw, keys);
  return typeof value === 'string' ? value : '';
};

const readNumber = (raw: Record<string, unknown>, keys: readonly string[]): number | null => {
  const value = readField(raw, keys);
  const parsed = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : null;
};

const readTime = (raw: Record<string, unknown>, keys: readonly string[]): string | null => {
  const value = readField(raw, keys);
  if (typeof value === 'string') return parseGanttTime(value) !== null ? value : null;
  if (value instanceof Date) return value.toISOString();
  return null;
};

/**
 * 组内事件字段尚未全部进入 api.ts：这里同时接受 camelCase 与 snake_case，
 * 未知或缺失字段一律降级，保证不依赖未落地的类型也能渲染。
 */
export function toGanttMember(
  raw: unknown,
  fallback: Partial<GanttMember> = {},
  now: Date = new Date(),
): GanttMember | null {
  if (raw === null || typeof raw !== 'object') return null;
  const bucket = raw as Record<string, unknown>;

  const statusRaw = readText(bucket, ['status', 'alertStatus', 'alert_status', 'validityState', 'validity_state']).toLowerCase();
  const supersededFlag = readField(bucket, ['superseded', 'versionSuperseded', 'version_superseded', 'supersededBy', 'superseded_by']);
  const superseded = statusRaw === 'superseded' || Boolean(supersededFlag);
  const expiresAt = readTime(bucket, ['expiresAt', 'expires_at', 'validUntil', 'valid_until']) ?? fallback.expiresAt ?? null;

  let status: 'current' | 'expired' = 'current';
  if (['expired', 'invalid', 'revoked', 'legacy', 'irrelevant', 'conflicted', 'superseded'].includes(statusRaw)) {
    status = 'expired';
  } else if (['current', 'valid', 'active'].includes(statusRaw)) {
    status = 'current';
  } else if (fallback.status !== undefined) {
    status = fallback.status;
  } else {
    // 状态字段缺失时按截止时间判断，避免把早已过期的事件画成有效。
    const expiry = parseGanttTime(expiresAt);
    status = expiry !== null && expiry.getTime() < now.getTime() ? 'expired' : 'current';
  }

  return {
    alertId: readNumber(bucket, ['alertId', 'alert_id', 'id']) ?? fallback.alertId ?? 0,
    eventId: readNumber(bucket, ['eventId', 'event_id']) ?? fallback.eventId ?? 0,
    summary: readText(bucket, ['summary', 'eventSummary', 'event_summary']) || fallback.summary || '未提供事件摘要',
    sourceTitle: readText(bucket, ['sourceTitle', 'source_title', 'source']) || fallback.sourceTitle || '来源未披露',
    level: readText(bucket, ['level', 'severity']) || fallback.level || 'P4',
    startAt: readTime(bucket, ['startAt', 'validFrom', 'start_at', 'valid_from', 'eventStartAt', 'event_start_at', 'publishedAt', 'published_at'])
      ?? fallback.startAt
      ?? null,
    expiresAt,
    status,
    superseded: superseded || Boolean(fallback.superseded),
    affectedSupplierCount: readNumber(bucket, ['affectedSupplierCount', 'affected_supplier_count', 'supplierCount', 'supplier_count'])
      ?? fallback.affectedSupplierCount
      ?? null,
  };
}

/**
 * 从列表项还原组内事件：mergedEvents 缺失时用当前卡片自身的 validUntil 画单条，
 * 存在且包含代表事件时以映射数据为准，不重复出现代表事件。
 */
export function riskItemMembers(item: RiskItem): GanttMember[] {
  const bucket = item as unknown as Record<string, unknown>;
  const mergedRaw = Array.isArray(bucket.mergedEvents)
    ? bucket.mergedEvents
    : Array.isArray(bucket.merged_events) ? bucket.merged_events : [];
  const selfAlertId = Number(item.id);
  const selfMember: GanttMember = {
    alertId: Number.isFinite(selfAlertId) ? selfAlertId : 0,
    eventId: readNumber(bucket, ['eventId', 'event_id']) ?? 0,
    summary: item.summary,
    sourceTitle: item.source ?? '来源未披露',
    level: item.level,
    startAt: readTime(bucket, ['eventStartAt', 'event_start_at']),
    expiresAt: item.validUntil ?? null,
    status: item.status === 'invalid' ? 'expired' : 'current',
    superseded: item.validityState === 'superseded',
  };

  const members: GanttMember[] = [];
  for (const raw of mergedRaw) {
    const member = toGanttMember(raw, {level: item.level});
    if (member !== null) members.push(member);
  }

  const selfIndex = members.findIndex((member) => selfMember.alertId > 0 && member.alertId === selfMember.alertId);
  if (selfIndex >= 0) {
    // 映射层已包含代表事件：补齐列表项才知道的字段，避免重复一条。
    const mergedSelf = members[selfIndex];
    members[selfIndex] = {
      ...mergedSelf,
      summary: mergedSelf.summary || selfMember.summary,
      sourceTitle: mergedSelf.sourceTitle === '来源未披露' ? selfMember.sourceTitle : mergedSelf.sourceTitle,
      level: mergedSelf.level || selfMember.level,
      startAt: mergedSelf.startAt ?? selfMember.startAt,
      expiresAt: mergedSelf.expiresAt ?? selfMember.expiresAt,
      status: mergedSelf.status,
    };
    return members;
  }
  return [selfMember, ...members];
}

/* ── 组件 ─────────────────────────────────────────────────────────────────── */

const TONE_CLASS: Record<GanttDisplayStatus, string> = {
  current: 'bg-[#185fa5]',
  expired: 'bg-slate-400 dark:bg-slate-600',
  superseded: 'border border-dashed border-amber-500 bg-amber-500/30',
};

const LEGEND: ReadonlyArray<{status: GanttDisplayStatus; className: string}> = [
  {status: 'current', className: 'bg-[#185fa5]'},
  {status: 'expired', className: 'bg-slate-400 dark:bg-slate-600'},
  {status: 'superseded', className: 'border border-dashed border-amber-500 bg-amber-500/30'},
];

const rowKey = (member: GanttMember, index: number) => `${member.alertId}-${member.eventId}-${index}`;

const barLabel = (member: GanttMember): string => (
  `${member.summary}；信源：${member.sourceTitle}；有效期：${formatGanttMoment(member.startAt)} 至 ${formatGanttMoment(member.expiresAt)}；状态：${GANTT_STATUS_LABEL[displayStatus(member)]}`
);

export const EventValidityGantt = ({members, variant = 'full', now, onSelectEvent}: EventValidityGanttProps) => {
  const fallbackNow = useMemo(() => new Date(), []);
  const reference = now ?? fallbackNow;
  const layout = useMemo(
    () => buildGanttLayout(members, reference, variant),
    [members, reference, variant],
  );

  if (layout.axisStart === null || layout.axisEnd === null) {
    return (
      <p className="rounded-lg border border-dashed border-slate-200 px-3 py-2 text-[11px] text-slate-400 dark:border-slate-700 dark:text-slate-500">
        暂无可展示的有效期
      </p>
    );
  }

  const shared = {axisStart: layout.axisStart, axisEnd: layout.axisEnd, rows: layout.rows, now: reference, onSelectEvent};
  return variant === 'mini'
    ? <MiniTimeline {...shared} />
    : <FullTimeline {...shared} />;
};

interface TimelineProps {
  axisStart: Date;
  axisEnd: Date;
  rows: readonly GanttRow[];
  now: Date;
  onSelectEvent?: (eventId: number) => void;
}

const nowPosition = (axisStart: Date, axisEnd: Date, now: Date): number | null => {
  const total = axisEnd.getTime() - axisStart.getTime();
  if (total <= 0) return null;
  if (now.getTime() < axisStart.getTime() || now.getTime() > axisEnd.getTime()) return null;
  return round(((now.getTime() - axisStart.getTime()) / total) * 100);
};

const Bar = ({row, variant, onSelectEvent, onActivate}: {
  row: GanttRow;
  variant: GanttVariant;
  onSelectEvent?: (eventId: number) => void;
  onActivate?: (member: GanttMember | null) => void;
}) => {
  const label = barLabel(row.member);
  const inner = (
    <span className={`block w-full ${variant === 'mini' ? 'h-1' : 'h-2.5'} rounded-full ${TONE_CLASS[displayStatus(row.member)]}`} />
  );
  const position = {
    left: `${row.leftPercent}%`,
    width: `${row.widthPercent}%`,
  };
  const shellClass = 'absolute inset-y-0 flex items-center rounded-full transition hover:brightness-110 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#185fa5]';

  return (
    <button
      type="button"
      data-testid="gantt-bar"
      aria-label={label}
      title={variant === 'mini' ? label : undefined}
      className={shellClass}
      style={position}
      onClick={(event) => {
        event.stopPropagation();
        onSelectEvent?.(row.member.eventId);
      }}
      onMouseEnter={() => onActivate?.(row.member)}
      onMouseLeave={() => onActivate?.(null)}
      onFocus={() => onActivate?.(row.member)}
      onBlur={() => onActivate?.(null)}
    >
      {inner}
    </button>
  );
};

const MiniTimeline = ({axisStart, axisEnd, rows, now, onSelectEvent}: TimelineProps) => {
  const span = axisEnd.getTime() - axisStart.getTime();
  const nowPercent = nowPosition(axisStart, axisEnd, now);

  return (
    <div role="group" aria-label={`事件有效期概览，共 ${rows.length} 个事件`} className="w-full">
      <div className="relative">
        {rows.map((row, index) => (
          <div key={rowKey(row.member, index)} className="relative h-2.5">
            <span aria-hidden className="absolute inset-x-0 top-1/2 h-1 -translate-y-1/2 rounded-full bg-slate-100 dark:bg-slate-800/80" />
            {row.hasRange && (
              <Bar row={row} variant="mini" onSelectEvent={onSelectEvent} />
            )}
          </div>
        ))}
        {nowPercent !== null && (
          <>
            <span
              data-testid="gantt-today-line"
              aria-hidden
              className="pointer-events-none absolute inset-y-0 w-px border-l border-dashed border-red-500/80"
              style={{left: `${nowPercent}%`}}
            />
            <span
              data-testid="gantt-today-dot"
              aria-hidden
              className="pointer-events-none absolute -bottom-1 h-1.5 w-1.5 -translate-x-1/2 rounded-full bg-red-500"
              style={{left: `${nowPercent}%`}}
            />
          </>
        )}
      </div>
      <div className="mt-1 flex items-center justify-between gap-2 font-mono text-[10px] text-slate-400 dark:text-slate-500">
        <span>{formatGanttAxisLabel(axisStart, span)}</span>
        {nowPercent === null && <span className="font-sans font-semibold text-red-500">今天在范围外</span>}
        <span>{formatGanttAxisLabel(axisEnd, span)}</span>
      </div>
    </div>
  );
};

const FullTimeline = ({axisStart, axisEnd, rows, now, onSelectEvent}: TimelineProps) => {
  const [activeKey, setActiveKey] = useState<string | null>(null);
  const ticks = useMemo(() => buildGanttTicks(axisStart, axisEnd), [axisStart, axisEnd]);
  const nowPercent = nowPosition(axisStart, axisEnd, now);
  const activeIndex = rows.findIndex((row, index) => rowKey(row.member, index) === activeKey);
  const activeMember = activeIndex >= 0 ? rows[activeIndex].member : null;

  return (
    <div role="group" aria-label="事件有效期甘特图" className="w-full">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <ul className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-slate-500 dark:text-slate-400">
          {LEGEND.map((entry) => (
            <li key={entry.status} className="flex items-center gap-1">
              <span aria-hidden className={`inline-block h-2 w-3 rounded-full ${entry.className}`} />
              {GANTT_STATUS_LABEL[entry.status]}
            </li>
          ))}
          <li className="flex items-center gap-1">
            <span aria-hidden className="inline-block h-3 w-px border-l border-dashed border-red-500" />
            今天
          </li>
        </ul>
        {nowPercent === null && (
          <span className="text-[10px] font-semibold text-red-600 dark:text-red-400">今天在范围外</span>
        )}
      </div>

      <div className="mt-2 overflow-x-auto pb-1">
        <div className="min-w-[560px]" style={{minWidth: FULL_MIN_WIDTH}}>
          <div className="flex gap-3">
            <div className="w-[9.5rem] shrink-0" aria-hidden />
            <div className="relative min-w-0 flex-1">
              <div className="relative h-6">
                {ticks.map((tick) => (
                  <span
                    key={tick.at.getTime()}
                    className="absolute top-0 -translate-x-1/2 whitespace-nowrap font-mono text-[10px] text-slate-400 dark:text-slate-500"
                    style={{left: `${tick.percent}%`}}
                  >
                    {tick.label}
                  </span>
                ))}
              </div>
            </div>
          </div>

          <div className="flex gap-3">
            <ul className="w-[9.5rem] shrink-0">
              {rows.map((row, index) => (
                <li key={rowKey(row.member, index)} className="flex h-7 items-center">
                  <div className="min-w-0">
                    <p className="truncate font-mono text-[10px] text-slate-400 dark:text-slate-500" title={row.member.sourceTitle}>
                      {row.member.sourceTitle}
                    </p>
                    <p className="truncate text-[12px] font-medium text-slate-700 dark:text-slate-200" title={row.member.summary}>
                      {truncateGanttSummary(row.member.summary)}
                    </p>
                  </div>
                </li>
              ))}
            </ul>

            <div className="relative min-w-0 flex-1">
              {ticks.map((tick) => (
                <span
                  key={`grid-${tick.at.getTime()}`}
                  aria-hidden
                  className="absolute inset-y-0 border-l border-dashed border-slate-200/80 dark:border-slate-700/60"
                  style={{left: `${tick.percent}%`}}
                />
              ))}
              {nowPercent !== null && (
                <span
                  data-testid="gantt-today-line"
                  aria-hidden
                  className="pointer-events-none absolute inset-y-0 border-l border-dashed border-red-500/80"
                  style={{left: `${nowPercent}%`}}
                />
              )}
              <ul>
                {rows.map((row, index) => (
                  <li key={rowKey(row.member, index)} className="relative h-7">
                    <span aria-hidden className="absolute inset-x-0 top-1/2 h-2.5 -translate-y-1/2 rounded-full bg-slate-100/80 dark:bg-slate-800/60" />
                    {row.hasRange && (
                      <Bar
                        row={row}
                        variant="full"
                        onSelectEvent={onSelectEvent}
                        onActivate={(member) => setActiveKey(member === null ? null : rowKey(member, index))}
                      />
                    )}
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </div>
      </div>

      {activeMember !== null && (
        <div
          className="mt-2 rounded-xl border border-slate-200/70 bg-slate-50/70 px-3 py-2 text-[11px] dark:border-slate-800 dark:bg-slate-900/50"
          data-testid="gantt-detail"
        >
          <p className="font-semibold leading-relaxed text-slate-700 dark:text-slate-200">{activeMember.summary}</p>
          <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-slate-500 dark:text-slate-400">
            <span className="font-mono">{activeMember.sourceTitle}</span>
            <span aria-hidden>·</span>
            <span>{GANTT_STATUS_LABEL[displayStatus(activeMember)]}</span>
            <span aria-hidden>·</span>
            <span className="font-mono">
              {formatGanttMoment(activeMember.startAt, true)} → {formatGanttMoment(activeMember.expiresAt, true)}
            </span>
          </p>
        </div>
      )}
    </div>
  );
};
