import {useMemo, useRef, useState, type KeyboardEvent} from 'react';
import {
  AlertTriangle,
  CalendarClock,
  ChevronDown,
  ChevronRight,
  Layers,
  MapPin,
  RefreshCw,
  RotateCcw,
  Search,
  SearchX,
  Users,
} from 'lucide-react';
import {
  EVENT_TYPE_LABELS,
  formatValidityState,
  type RiskEventCluster,
  type RiskEventClusterMember,
  type RiskEventClusterSummary,
  type RiskEventState,
} from '../api';
import type {RiskLevel} from '../types';

/** /risks 的两种视图口径：事件（默认）/ 供应商。 */
export type RiskViewMode = 'events' | 'suppliers';

/** 页签与面板的稳定 id：两个子视图面板据此与共享 tablist 建立 aria 关联。 */
export const riskViewTabId = (mode: RiskViewMode) => `risk-view-tab-${mode}`;
export const riskViewPanelId = (mode: RiskViewMode) => `risk-view-panel-${mode}`;

interface RiskEventsViewProps {
  readonly state: RiskEventState;
  readonly onStateChange: (state: RiskEventState) => void;
  readonly clusters: readonly RiskEventCluster[];
  /** 服务端 total（当前口径的簇总数），可能大于已加载条数。 */
  readonly total: number;
  /** 当前口径汇总；null 表示尚不可用，统计条回落列表口径或占位。 */
  readonly summary: RiskEventClusterSummary | null;
  readonly loading: boolean;
  readonly error: string | null;
  readonly onRetry: () => void;
  readonly onSelectEvent: (eventId: number) => void;
}

const MODE_OPTIONS: ReadonlyArray<{value: RiskViewMode; label: string}> = [
  {value: 'events', label: '事件'},
  {value: 'suppliers', label: '供应商'},
];

const STATE_OPTIONS: ReadonlyArray<{value: RiskEventState; label: string}> = [
  {value: 'current', label: '当前有效'},
  {value: 'expired', label: '已失效'},
];

/** 等级筛选选项：文案与供应商视图的筛选栏保持一致。 */
const LEVEL_OPTIONS: ReadonlyArray<{value: RiskLevel; label: string}> = [
  {value: 'P1', label: 'P1 严重'},
  {value: 'P2', label: 'P2 高风险'},
  {value: 'P3', label: 'P3 中度'},
  {value: 'P4', label: 'P4 轻微'},
];

/**
 * 「事件 / 供应商」子视图页签。
 *
 * 由 App 在 /risks 层统一渲染一次：两个子视图共用同一份导航，位置固定，
 * 切换时只替换下方面板，不会因为各自渲染控件而出现跳动。
 * 视觉规格与情报聚合页（PolicyIntelligenceView）的三个子版块页签一致。
 */
export const RiskViewModeTabs = ({mode, onModeChange}: {
  readonly mode: RiskViewMode;
  readonly onModeChange: (mode: RiskViewMode) => void;
}) => {
  const tabRefs = useRef<Record<RiskViewMode, HTMLButtonElement | null>>({events: null, suppliers: null});

  // 左右方向键在页签间移动（roving tabIndex），Home/End 跳到首尾，与情报聚合页保持同一套键盘行为。
  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const currentIndex = Math.max(MODE_OPTIONS.findIndex((item) => item.value === mode), 0);
    let nextIndex: number;
    switch (event.key) {
      case 'ArrowRight':
        nextIndex = (currentIndex + 1) % MODE_OPTIONS.length;
        break;
      case 'ArrowLeft':
        nextIndex = (currentIndex - 1 + MODE_OPTIONS.length) % MODE_OPTIONS.length;
        break;
      case 'Home':
        nextIndex = 0;
        break;
      case 'End':
        nextIndex = MODE_OPTIONS.length - 1;
        break;
      default:
        return;
    }
    event.preventDefault();
    const nextMode = MODE_OPTIONS[nextIndex].value;
    if (nextMode !== mode) onModeChange(nextMode);
    tabRefs.current[nextMode]?.focus();
  };

  return (
    <div
      role="tablist"
      aria-label="风险事件视图切换"
      data-testid="risk-view-tabs"
      onKeyDown={handleKeyDown}
      // 窄屏横向滚动、不换行：页签保持一行，间距在窄屏收窄。
      className="flex flex-nowrap items-end gap-4 overflow-x-auto border-b border-slate-200/80 dark:border-slate-700/60 sm:gap-6"
    >
      {MODE_OPTIONS.map((option) => {
        const isActive = mode === option.value;
        return (
          <button
            key={option.value}
            ref={(node) => { tabRefs.current[option.value] = node; }}
            type="button"
            role="tab"
            id={riskViewTabId(option.value)}
            aria-selected={isActive}
            aria-controls={riskViewPanelId(option.value)}
            tabIndex={isActive ? 0 : -1}
            onClick={() => onModeChange(option.value)}
            className={`inline-flex shrink-0 items-center gap-1 whitespace-nowrap border-b-[3px] px-0 pb-2.5 pt-2 text-[13.5px] font-bold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#004782] focus-visible:ring-offset-2 dark:focus-visible:ring-blue-400 sm:text-[14px] ${
              isActive
                ? 'border-[#004782] text-[#004782] dark:border-blue-300 dark:text-blue-300'
                : 'border-transparent text-[#424751] hover:text-[#004782] dark:text-slate-300 dark:hover:text-white'
            }`}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
};

/** 等级徽标底色，与供应商视图的等级配色保持同一套。 */
const LEVEL_BADGE: Record<RiskLevel, string> = {
  P1: 'bg-[#C92A2A]',
  P2: 'bg-[#D97706]',
  P3: 'bg-[#2563EB]',
  P4: 'bg-[#64748B]',
};

const CLUSTER_STATUS_LABEL: Record<string, string> = {
  active: '生效中',
  partially_expired: '部分失效',
  expired: '已失效',
};

const CLUSTER_STATUS_CLASS: Record<string, string> = {
  active: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300',
  partially_expired: 'bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300',
  expired: 'bg-slate-200 text-slate-700 dark:bg-slate-700 dark:text-slate-300',
};

const VALIDITY_BADGE_CLASS: Record<string, string> = {
  active: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300',
  expired: 'bg-slate-200 text-slate-700 dark:bg-slate-700 dark:text-slate-300',
  revoked: 'bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300',
  superseded: 'bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300',
  conflicted: 'bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-300',
  legacy: 'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300',
};

const EXPIRED_REASON_LABEL: Record<string, string> = {
  event_expired: '事件已结束',
  alert_expired: '提醒已过期',
  version_superseded: '被新版本替代',
};

const formatMoment = (value: string | null | undefined): string =>
  value ? new Date(value).toLocaleString('zh-CN', {hour12: false}) : '未披露';

/** 有效期区间：两端相同只显示一次，缺失端用「未披露」占位。 */
const formatWindow = (min: string | null, max: string | null): string => {
  if (!min && !max) return '未披露';
  if (!min || !max || min === max) return formatMoment(min ?? max);
  return `${formatMoment(min)} ~ ${formatMoment(max)}`;
};

export const RiskEventsView = ({
  state,
  onStateChange,
  clusters,
  total,
  summary,
  loading,
  error,
  onRetry,
  onSelectEvent,
}: RiskEventsViewProps) => {
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set());
  const [searchTerm, setSearchTerm] = useState('');
  const [selectedLevel, setSelectedLevel] = useState<'all' | RiskLevel>('all');
  const [selectedType, setSelectedType] = useState('all');

  const toggleCluster = (clusterId: string) => {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(clusterId)) next.delete(clusterId);
      else next.add(clusterId);
      return next;
    });
  };

  // 事件类型选项取已加载簇实际出现的类型，中文名排序；没有数据时不渲染该下拉。
  const typeOptions = useMemo(() => {
    const counts = new Map<string, number>();
    for (const cluster of clusters) {
      counts.set(cluster.event_type, (counts.get(cluster.event_type) ?? 0) + 1);
    }
    return [...counts.entries()]
      .map(([code, count]) => ({code, count, label: EVENT_TYPE_LABELS[code] ?? code}))
      .sort((a, b) => a.label.localeCompare(b.label, 'zh-CN'));
  }, [clusters]);

  const filteredClusters = useMemo(() => {
    const query = searchTerm.trim().toLowerCase();
    return clusters.filter((cluster) => {
      if (selectedLevel !== 'all' && cluster.max_level !== selectedLevel) return false;
      if (selectedType !== 'all' && cluster.event_type !== selectedType) return false;
      if (!query) return true;
      // 搜索口径：簇标题、地区、成员事件摘要与信源名称。
      const haystack = [
        cluster.title,
        cluster.region_label,
        ...cluster.members.flatMap((item) => [item.summary, item.source_title]),
      ];
      return haystack.some((value) => value?.toLowerCase().includes(query));
    });
  }, [clusters, searchTerm, selectedLevel, selectedType]);

  // 「已筛选」只标注列表筛选维度；口径切换（当前有效/已失效）不算筛选。
  const filtersActive = searchTerm.trim() !== '' || selectedLevel !== 'all' || selectedType !== 'all';

  /** 重置：清空三个列表筛选，并把有效期口径复位到「当前有效」。 */
  const resetFilters = () => {
    setSearchTerm('');
    setSelectedLevel('all');
    setSelectedType('all');
    onStateChange('current');
  };

  // 统计条固定当前口径：事件/簇/供应商/风险组；汇总不可用时用列表条数与 0 兜底并显式标注。
  const stats = [
    {label: '当前事件', value: summary?.current_events ?? null},
    {label: '事件簇', value: summary?.current_clusters ?? null},
    {label: '供应商', value: summary?.current_suppliers ?? null},
    {label: '风险组', value: summary?.current_groups ?? null},
  ];

  return (
    <div className="space-y-3 sm:space-y-5">
      <section
        aria-label="风险事件筛选"
        className="space-y-3 rounded-2xl border border-slate-200/80 bg-white/80 p-4 shadow-sm backdrop-blur-md dark:border-slate-700/60 dark:bg-slate-800/60"
      >
        <div className="flex flex-col gap-3 md:flex-row">
          <div className="relative flex-1">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
            <input
              value={searchTerm}
              onChange={(event) => setSearchTerm(event.target.value)}
              aria-label="搜索风险事件"
              placeholder="搜索事件、地区、信源或供应商..."
              className="w-full rounded-xl border border-slate-200/80 bg-slate-100/80 py-2 pl-9 pr-3 text-[13px] outline-none focus:ring-2 focus:ring-[#185fa5]/30 dark:border-slate-700/60 dark:bg-slate-900/60"
            />
          </div>
          <button
            type="button"
            onClick={resetFilters}
            className="flex items-center justify-center gap-1.5 rounded-xl border border-slate-200/80 bg-slate-100 px-3 py-2 text-[12px] font-semibold text-slate-700 transition hover:bg-slate-200/70 dark:border-slate-700/80 dark:bg-slate-700/60 dark:text-slate-300"
          >
            <RotateCcw className="h-3.5 w-3.5" />
            重置筛选
          </button>
        </div>
        <div className="flex flex-wrap items-center gap-3 border-t border-slate-100 pt-3 text-[12px] dark:border-slate-800">
          <label className="flex items-center gap-1.5 text-slate-500">
            风险等级
            <select
              aria-label="风险等级"
              value={selectedLevel}
              onChange={(event) => setSelectedLevel(event.target.value as 'all' | RiskLevel)}
              className="rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-1 font-bold text-[#185fa5] outline-none dark:border-slate-700 dark:bg-slate-900"
            >
              <option value="all">全部</option>
              {LEVEL_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>{option.label}</option>
              ))}
            </select>
          </label>
          {typeOptions.length > 0 && (
            <label className="flex items-center gap-1.5 text-slate-500">
              事件类型
              <select
                aria-label="事件类型"
                value={selectedType}
                onChange={(event) => setSelectedType(event.target.value)}
                className="rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-1 font-bold text-[#185fa5] outline-none dark:border-slate-700 dark:bg-slate-900"
              >
                <option value="all">全部类型（已加载 {clusters.length}）</option>
                {typeOptions.map(({code, label, count}) => (
                  <option key={code} value={code}>{label}（{count}）</option>
                ))}
              </select>
            </label>
          )}
          <div className="flex items-center gap-1 rounded-lg bg-slate-100 p-1 dark:bg-slate-900" role="group" aria-label="有效期口径">
            {STATE_OPTIONS.map((option) => (
              <button
                key={option.value}
                type="button"
                aria-pressed={state === option.value}
                onClick={() => onStateChange(option.value)}
                className={`rounded-md px-2.5 py-1 font-semibold ${state === option.value ? 'bg-white text-[#185fa5] shadow-sm dark:bg-slate-700 dark:text-blue-300' : 'text-slate-500'}`}
              >
                {option.label}
              </button>
            ))}
          </div>
        </div>
      </section>

      <section className="space-y-3 rounded-2xl border border-slate-200/80 bg-white/80 p-4 shadow-sm backdrop-blur-md dark:border-slate-700/60 dark:bg-slate-800/60">
        <div className="flex items-center justify-between border-b border-slate-100 pb-3 dark:border-slate-800">
          <span className="text-[13px] font-semibold text-slate-600 dark:text-slate-300">风险事件统计</span>
          {/* 统计卡是全库口径，不随下方筛选变化。 */}
          <span className="text-[11px] text-slate-400">全库口径</span>
        </div>
        <div className="grid grid-cols-2 divide-x pt-3 dark:divide-slate-800 sm:grid-cols-4">
          {stats.map((stat) => (
            <div key={stat.label} className="text-center">
              <div className="font-mono text-xl font-black text-[#185fa5] dark:text-blue-300">{stat.value ?? '—'}</div>
              <div className="text-[11px] text-slate-500">{stat.label}</div>
            </div>
          ))}
        </div>
        {summary === null && (
          <p className="text-[11px] text-slate-400">事件统计暂不可用，数字显示为占位；下方列表仍按已加载数据展示。</p>
        )}
      </section>

      {error ? (
        <section className="rounded-2xl border border-[#ba1a1a]/40 bg-[#ffdad6]/60 p-8 text-center dark:border-red-500/40 dark:bg-red-950/30">
          <AlertTriangle className="mx-auto h-8 w-8 text-[#93000a] dark:text-red-300" />
          <h3 className="mt-3 text-[15px] font-bold text-[#93000a] dark:text-red-200">风险事件加载失败</h3>
          <p className="mt-1 text-xs text-[#93000a]/80 dark:text-red-300/80">{error}</p>
          <button
            type="button"
            onClick={onRetry}
            className="mt-4 inline-flex items-center gap-1.5 rounded-xl border border-slate-200/80 bg-white px-3 py-1.5 text-[12px] font-semibold text-slate-700 transition hover:bg-slate-100 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
          >
            <RefreshCw className="h-3.5 w-3.5" />
            重新加载
          </button>
        </section>
      ) : loading && clusters.length === 0 ? (
        <section className="rounded-2xl border border-slate-200/80 bg-white/80 p-12 text-center shadow-sm dark:border-slate-700/60 dark:bg-slate-800/60">
          <Layers className="mx-auto h-8 w-8 animate-pulse text-slate-300" />
          <p className="mt-3 text-[13px] text-slate-500">正在加载风险事件…</p>
        </section>
      ) : clusters.length === 0 ? (
        <section className="rounded-2xl border border-slate-200/80 bg-white/80 p-12 text-center shadow-sm dark:border-slate-700/60 dark:bg-slate-800/60">
          <SearchX className="mx-auto h-10 w-10 text-slate-300" />
          <h3 className="mt-3 text-[15px] font-bold text-slate-700 dark:text-slate-300">
            {state === 'current' ? '当前暂无有效风险事件' : '暂无已失效的风险事件'}
          </h3>
          <p className="mt-1 text-xs text-slate-400">
            {state === 'current' ? '新事件进入并完成供应商关联后会出现在这里。' : '事件到期或被新版本替代后会归档到这里。'}
          </p>
        </section>
      ) : filteredClusters.length === 0 ? (
        <section className="rounded-2xl border border-slate-200/80 bg-white/80 p-12 text-center shadow-sm dark:border-slate-700/60 dark:bg-slate-800/60">
          <SearchX className="mx-auto h-10 w-10 text-slate-300" />
          <h3 className="mt-3 text-[15px] font-bold text-slate-700 dark:text-slate-300">
            未找到匹配的风险事件，请调整筛选条件
          </h3>
          <p className="mt-1 text-xs text-slate-400">可以清空搜索，或重置等级、类型筛选后再试。</p>
        </section>
      ) : (
        <div className="space-y-2">
          {filteredClusters.map((cluster) => (
            <ClusterCard
              key={cluster.cluster_id}
              cluster={cluster}
              expanded={expanded.has(cluster.cluster_id)}
              onToggle={() => toggleCluster(cluster.cluster_id)}
              onSelectEvent={onSelectEvent}
            />
          ))}
        </div>
      )}

      {!error && clusters.length > 0 && (
        <div className="border-t border-slate-200/80 pt-3 text-[12px] text-slate-500 dark:border-slate-800">
          本页显示 {filteredClusters.length} 条（已加载 {clusters.length} 条），全库共 {total} 条
          {filtersActive ? '（已筛选）' : ''}
        </div>
      )}
    </div>
  );
};

const ClusterCard = ({cluster, expanded, onToggle, onSelectEvent}: {
  readonly cluster: RiskEventCluster;
  readonly expanded: boolean;
  readonly onToggle: () => void;
  readonly onSelectEvent: (eventId: number) => void;
}) => (
  <article className="rounded-2xl border border-slate-200/80 bg-white/80 p-4 shadow-sm transition hover:border-[#185fa5]/50 dark:border-slate-700/60 dark:bg-slate-800/60">
    <div className="flex flex-wrap items-center gap-2">
      <span className={`rounded-md px-2 py-0.5 text-[10px] font-bold text-white ${LEVEL_BADGE[cluster.max_level] ?? LEVEL_BADGE.P4}`}>
        {cluster.max_level}
      </span>
      <ClusterStatusBadge status={cluster.status} />
      <span className="rounded-full border border-slate-200 bg-slate-50 px-2 py-0.5 text-[10px] font-medium text-[#185fa5] dark:border-slate-700 dark:bg-slate-900 dark:text-blue-300">
        {EVENT_TYPE_LABELS[cluster.event_type] ?? cluster.event_type}
      </span>
      <span className="min-w-0 truncate text-[15px] font-bold text-slate-900 dark:text-white">{cluster.title}</span>
    </div>

    <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-slate-500">
      <span className="inline-flex items-center gap-1">
        <MapPin className="h-3.5 w-3.5" />
        {cluster.region_label || '地区未披露'}
      </span>
      <span className="inline-flex items-center gap-1">
        <Layers className="h-3.5 w-3.5" />
        事件 {cluster.event_count}
      </span>
      <span className="inline-flex items-center gap-1">
        <Users className="h-3.5 w-3.5" />
        供应商 {cluster.supplier_count}
      </span>
      <span className="inline-flex items-center gap-1">
        <CalendarClock className="h-3.5 w-3.5" />
        {formatWindow(cluster.expires_min, cluster.expires_max)}
      </span>
    </div>

    <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[10px] text-slate-500">
      {cluster.level_counts.map((entry) => (
        <span key={entry.level} className="rounded-full bg-slate-100 px-2 py-0.5 font-mono dark:bg-slate-900">
          {entry.level} × {entry.count}
        </span>
      ))}
      <button
        type="button"
        aria-expanded={expanded}
        onClick={onToggle}
        className="ml-auto inline-flex items-center gap-1 rounded-lg px-2 py-1 text-[11px] font-bold text-[#185fa5] hover:bg-[#185fa5]/5 dark:text-blue-300"
      >
        {expanded ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
        {expanded ? '收起成员' : `展开成员（${cluster.members.length}）`}
      </button>
    </div>

    {expanded && <MemberList members={cluster.members} onSelectEvent={onSelectEvent} />}
  </article>
);

/** 成员按信源分组：同一信源下的多个事件折叠在一组，避免同一来源刷屏。 */
const MemberList = ({members, onSelectEvent}: {
  readonly members: readonly RiskEventClusterMember[];
  readonly onSelectEvent: (eventId: number) => void;
}) => {
  const groups = useMemo(() => {
    const bySource = new Map<string, {title: string; members: RiskEventClusterMember[]}>();
    for (const member of members) {
      const key = member.source_code || member.source_title || 'unknown';
      const entry = bySource.get(key) ?? {title: member.source_title || member.source_code || '来源未披露', members: []};
      entry.members.push(member);
      bySource.set(key, entry);
    }
    return [...bySource.entries()].map(([key, value]) => ({key, ...value}));
  }, [members]);

  if (members.length === 0) {
    return <p className="mt-3 border-t border-slate-100 pt-3 text-[11px] text-slate-400 dark:border-slate-800">该事件簇暂无成员事件。</p>;
  }

  return (
    <div className="mt-3 space-y-2 border-t border-slate-100 pt-3 dark:border-slate-800">
      {groups.map((group) => (
        <div key={group.key} className="rounded-xl bg-slate-50/70 p-2 dark:bg-slate-900/40">
          <div className="px-1 text-[10px] font-bold uppercase tracking-wide text-slate-400">{group.title}（{group.members.length}）</div>
          <ul className="mt-1 divide-y divide-slate-100 dark:divide-slate-800">
            {group.members.map((member) => (
              <li key={member.event_id}>
                <button
                  type="button"
                  onClick={() => onSelectEvent(member.event_id)}
                  className="group w-full rounded-lg px-1 py-2 text-left transition hover:bg-white dark:hover:bg-slate-800"
                >
                  <div className="flex items-start justify-between gap-2">
                    <span className="line-clamp-2 text-[13px] leading-relaxed text-slate-700 group-hover:text-[#185fa5] dark:text-slate-200">
                      {member.summary}
                    </span>
                    <span className={`whitespace-nowrap rounded-md px-1.5 py-0.5 text-[10px] font-bold text-white ${LEVEL_BADGE[member.max_level] ?? LEVEL_BADGE.P4}`}>
                      {member.max_level}
                    </span>
                  </div>
                  <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-slate-500">
                    <span>发布 {formatMoment(member.published_at)}</span>
                    <span>截止 {formatMoment(member.expires_at)}</span>
                    <span>供应商 {member.supplier_count}</span>
                    <ValidityBadge state={member.validity_state} />
                    {member.expired_reason && (
                      <span className="rounded-full bg-slate-200 px-2 py-0.5 text-slate-600 dark:bg-slate-700 dark:text-slate-300">
                        {EXPIRED_REASON_LABEL[member.expired_reason] ?? member.expired_reason}
                      </span>
                    )}
                    <span className="ml-auto inline-flex items-center gap-1 font-bold text-[#185fa5] dark:text-blue-300">
                      事件详情<ChevronRight className="h-3 w-3" />
                    </span>
                  </div>
                </button>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
};

const ClusterStatusBadge = ({status}: {readonly status: string}) => (
  <span className={`rounded-full px-2 py-0.5 text-[10px] font-bold ${CLUSTER_STATUS_CLASS[status] ?? CLUSTER_STATUS_CLASS.active}`}>
    {CLUSTER_STATUS_LABEL[status] ?? status}
  </span>
);

const ValidityBadge = ({state}: {readonly state: string | null | undefined}) => {
  if (!state) return null;
  return (
    <span className={`rounded-full px-2 py-0.5 font-bold ${VALIDITY_BADGE_CLASS[state] ?? 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300'}`}>
      {formatValidityState(state)}
    </span>
  );
};
