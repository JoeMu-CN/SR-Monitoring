import {useMemo, useState} from 'react';
import {AnimatePresence, motion} from 'motion/react';
import {ChevronDown, ChevronRight, MapPin, RotateCcw, Search, SearchX} from 'lucide-react';
import {EVENT_TYPE_LABELS, formatProcessingState, formatValidityReasonLabel, formatValidityState, type RiskAlertSummary} from '../api';
import type {RiskItem} from '../types';
import {
  EventValidityGantt,
  formatGanttRange,
  normalizeGroupStatus,
  parseGanttTime,
  riskItemMembers,
  summarizeGanttGroup,
  type GanttGroupStatus,
} from './EventValidityGantt';

const RISK_LEVELS = ['P1', 'P2', 'P3', 'P4'] as const;
/** 无异常的有效期原因：每个当前有效事件都会带，展示出来只是噪音。 */
const SILENT_VALIDITY_REASONS = new Set(['effective_signal_support', 'active']);

/** 风险类型筛选键：优先后端事件大类原始码，缺失时回落到中文标签。 */
const riskTypeKey = (item: RiskItem) => item.eventType ?? item.riskType;

/** 无异常原因没有信息量，卡片只展示有诊断价值的异常原因。 */
const showValidityReason = (reason: RiskItem['validityReason']) =>
  Boolean(reason && !SILENT_VALIDITY_REASONS.has(reason.code));

interface CurrentRisksViewProps {
  riskItems: RiskItem[];
  /**
   * 当前提醒全量计数（服务端聚合，不受列表 limit=100 的单页截断影响）。
   * null 表示汇总不可用：概览卡回落已加载页计数，并显式标注该口径不是全量。
   */
  summary: RiskAlertSummary | null;
  /** 已失效提醒：首次切到「已失效」时由 onLoadExpired 懒加载。 */
  expiredItems?: readonly RiskItem[];
  expiredTotal?: number | null;
  expiredLoading?: boolean;
  expiredError?: string | null;
  onLoadExpired?: () => void;
  onSelectRisk: (item: RiskItem) => void;
}

const countByLevel = (levels: readonly {readonly level: string; readonly count: number}[]) =>
  Object.fromEntries(RISK_LEVELS.map((level) => [
    level,
    levels.find((entry) => entry.level === level)?.count ?? 0,
  ])) as Record<(typeof RISK_LEVELS)[number], number>;

export const CurrentRisksView = ({riskItems, summary, expiredItems, expiredTotal, expiredLoading, expiredError, onLoadExpired, onSelectRisk}: CurrentRisksViewProps) => {
  const [searchTerm, setSearchTerm] = useState('');
  const [selectedLevel, setSelectedLevel] = useState('all');
  const [selectedType, setSelectedType] = useState('all');
  const [statusFilter, setStatusFilter] = useState<'valid' | 'invalid'>('valid');

  // 已失效口径优先使用独立数据源；未接入时保持旧行为（在 current 数据上本地过滤）。
  const invalidItems = expiredItems ?? riskItems.filter((item) => item.status === 'invalid');
  const sourceItems = statusFilter === 'valid' ? riskItems : invalidItems;

  // 已加载单页的分级计数：仅作为汇总不可用时的降级口径。
  const loadedCounts = useMemo(
    () => countByLevel(RISK_LEVELS.map((level) => ({level, count: riskItems.filter((item) => item.level === level).length}))),
    [riskItems],
  );

  // 概览卡一律优先使用全量口径，与风险总览保持一致；无汇总时才用本页计数。
  const overviewCounts = summary === null ? loadedCounts : countByLevel(summary.level_counts);
  const totalCurrent = summary?.total_current ?? riskItems.length;

  // 风险类型选项取已加载页实际出现的类型，标签复用事件类型中文表（与规则引擎口径一致）。
  const typeOptions = useMemo(() => {
    const counts = new Map<string, number>();
    for (const item of sourceItems) {
      const key = riskTypeKey(item);
      counts.set(key, (counts.get(key) ?? 0) + 1);
    }
    return [...counts.entries()]
      .map(([code, count]) => ({code, label: EVENT_TYPE_LABELS[code] ?? code, count}))
      .sort((a, b) => a.label.localeCompare(b.label, 'zh-CN'));
  }, [sourceItems]);

  const filtered = sourceItems.filter((item) => {
    const query = searchTerm.trim().toLowerCase();
    const matchesQuery = !query || [item.companyName, item.riskType, item.summary, item.location ?? '', item.source ?? ''].some((value) => value.toLowerCase().includes(query));
    const matchesType = selectedType === 'all' || riskTypeKey(item) === selectedType;
    return matchesQuery && (selectedLevel === 'all' || item.level === selectedLevel) && matchesType && item.status === statusFilter;
  });

  /**
   * 时间条点击定位到具体事件：事件所属提醒若已在当前页加载，就打开那一条；
   * 否则退回打开本组代表事件（组内成员的详情数据尚未进入列表映射）。
   */
  const openGroupMember = (item: RiskItem, eventId: number) => {
    const member = eventId > 0 ? riskItemMembers(item).find((candidate) => candidate.eventId === eventId) : undefined;
    const target = member !== undefined && member.alertId > 0
      ? sourceItems.find((candidate) => candidate.id === String(member.alertId))
      : undefined;
    onSelectRisk(target ?? item);
  };

  const reset = () => {
    setSearchTerm('');
    setSelectedLevel('all');
    setSelectedType('all');
    setStatusFilter('valid');
  };

  return (
    <div className="space-y-3 sm:space-y-5">
      <section className="space-y-3 rounded-2xl border border-slate-200/80 bg-white/80 p-4 shadow-sm backdrop-blur-md dark:border-slate-700/60 dark:bg-slate-800/60">
        <div className="flex flex-col gap-3 md:flex-row">
          <div className="relative flex-1">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
            <input
              value={searchTerm}
              onChange={(event) => setSearchTerm(event.target.value)}
              placeholder="搜索供应商主体、风险事件、信源或国家地区..."
              className="w-full rounded-xl border border-slate-200/80 bg-slate-100/80 py-2 pl-9 pr-3 text-[13px] outline-none focus:ring-2 focus:ring-[#185fa5]/30 dark:border-slate-700/60 dark:bg-slate-900/60"
            />
          </div>
          <button
            type="button"
            onClick={reset}
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
              onChange={(event) => setSelectedLevel(event.target.value)}
              className="rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-1 font-bold text-[#185fa5] outline-none dark:border-slate-700 dark:bg-slate-900"
            >
              <option value="all">全部</option>
              <option value="P1">P1 严重</option>
              <option value="P2">P2 高风险</option>
              <option value="P3">P3 中度</option>
              <option value="P4">P4 轻微</option>
            </select>
          </label>
          {typeOptions.length > 1 && (
            <label className="flex items-center gap-1.5 text-slate-500">
              风险类型
              <select
                aria-label="风险类型"
                value={selectedType}
                onChange={(event) => setSelectedType(event.target.value)}
                className="rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-1 font-bold text-[#185fa5] outline-none dark:border-slate-700 dark:bg-slate-900"
              >
                <option value="all">全部类型（已加载 {sourceItems.length}）</option>
                {typeOptions.map(({code, label, count}) => (
                  <option key={code} value={code}>
                    {label}（{count}）
                  </option>
                ))}
              </select>
            </label>
          )}
          <div className="flex items-center gap-1 rounded-lg bg-slate-100 p-1 dark:bg-slate-900">
            {(['valid', 'invalid'] as const).map((status) => (
              <button
                key={status}
                type="button"
                onClick={() => {
                  setStatusFilter(status);
                  if (status === 'invalid') onLoadExpired?.();
                }}
                className={`rounded-md px-2.5 py-1 font-semibold ${statusFilter === status ? 'bg-white text-[#185fa5] shadow-sm dark:bg-slate-700' : 'text-slate-500'}`}
              >
                {status === 'valid' ? '当前有效' : '已失效'}
              </button>
            ))}
          </div>
        </div>
      </section>

      <section className="rounded-2xl border border-slate-200/80 bg-white/80 p-4 shadow-sm backdrop-blur-md dark:border-slate-700/60 dark:bg-slate-800/60">
        <div className="flex items-center justify-between border-b border-slate-100 pb-3 dark:border-slate-800">
          <span className="text-[13px] font-semibold text-slate-600 dark:text-slate-300">当前风险概览</span>
          <span className="font-mono text-[13px] font-bold text-[#185fa5]">共 {totalCurrent} 条{summary === null ? '（本页已加载）' : '（全库当前）'}</span>
        </div>
        <div className="grid grid-cols-2 divide-x pt-3 dark:divide-slate-800 sm:grid-cols-4">
          <LevelSummary label="P1 严重" value={overviewCounts.P1} color="text-[#C92A2A]" />
          <LevelSummary label="P2 高风险" value={overviewCounts.P2} color="text-[#D97706]" />
          <LevelSummary label="P3 中度" value={overviewCounts.P3} color="text-[#2563EB]" />
          <LevelSummary label="P4 轻微" value={overviewCounts.P4} color="text-[#64748B]" />
        </div>
      </section>

      <AnimatePresence mode="popLayout">
        {expiredLoading ? (
          <motion.div
            initial={{opacity: 0, scale: 0.98}}
            animate={{opacity: 1, scale: 1}}
            className="rounded-2xl border border-slate-200/80 bg-white/80 p-12 text-center shadow-sm dark:border-slate-700/60 dark:bg-slate-800/60"
            role="status"
          >
            <h3 className="mt-3 text-[15px] font-bold text-slate-700 dark:text-slate-300">正在加载已失效事件</h3>
            <p className="mt-1 text-xs text-slate-400">历史提醒较多，请稍候…</p>
          </motion.div>
        ) : filtered.length === 0 ? (
          <motion.div
            initial={{opacity: 0, scale: 0.98}}
            animate={{opacity: 1, scale: 1}}
            className="rounded-2xl border border-slate-200/80 bg-white/80 p-12 text-center shadow-sm dark:border-slate-700/60 dark:bg-slate-800/60"
          >
            <SearchX className="mx-auto h-10 w-10 text-slate-300" />
            <h3 className="mt-3 text-[15px] font-bold text-slate-700 dark:text-slate-300">
              {expiredError ? '已失效事件加载失败' : '未找到匹配的风险预警记录'}
            </h3>
            <p className="mt-1 text-xs text-slate-400">
              {expiredError ?? '请调整关键词或重置筛选条件。'}
            </p>
            {expiredError && onLoadExpired && (
              <button
                type="button"
                onClick={onLoadExpired}
                className="mt-3 rounded-lg border border-slate-300 px-3 py-1.5 text-[12px] font-bold text-slate-700 hover:bg-slate-100 dark:border-slate-600 dark:text-slate-200 dark:hover:bg-slate-700"
              >
                重试加载已失效事件
              </button>
            )}
          </motion.div>
        ) : (
          <div className="space-y-2">
            {filtered.map((item) => (
              <RiskEventCard
                key={item.id}
                item={item}
                onSelectRisk={onSelectRisk}
                onSelectMember={openGroupMember}
              />
            ))}
          </div>
        )}
      </AnimatePresence>

      <div className="border-t border-slate-200/80 pt-3 text-[12px] text-slate-500 dark:border-slate-800">
        {statusFilter === 'invalid'
          ? `本页显示 ${filtered.length} 条（已加载 ${invalidItems.length} 条），全库已失效共 ${expiredTotal ?? invalidItems.length} 条`
          : summary === null
            ? `本页显示 ${filtered.length} 条，已加载 ${riskItems.length} 条（全库当前计数暂不可用）`
            : `本页显示 ${filtered.length} 条（已加载 ${riskItems.length} 条），全库当前共 ${summary.total_current} 条`}
      </div>
    </div>
  );
};

const GROUP_STATUS_META: Record<GanttGroupStatus, {label: string; className: string}> = {
  active: {label: '有效', className: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300'},
  partial: {label: '部分失效', className: 'bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300'},
  expired: {label: '已失效', className: 'bg-slate-200 text-slate-700 dark:bg-slate-700 dark:text-slate-300'},
};

interface RiskEventCardProps {
  item: RiskItem;
  onSelectRisk: (item: RiskItem) => void;
  onSelectMember: (item: RiskItem, eventId: number) => void;
}

/**
 * 供应商视图单卡：上半区整块可点进详情，下半区承载组内事件有效期甘特图，
 * 避免把时间条嵌进按钮里造成嵌套交互与误触。
 */
const RiskEventCard = ({item, onSelectRisk, onSelectMember}: RiskEventCardProps) => {
  const [expanded, setExpanded] = useState(false);
  // 首次展开后内容保持挂载：收起时内容仍在，grid 行高才能从 1fr 平滑过渡到 0fr。
  const [everExpanded, setEverExpanded] = useState(false);
  const now = useMemo(() => new Date(), []);
  const members = useMemo(() => riskItemMembers(item), [item]);
  const group = useMemo(() => summarizeGanttGroup(members), [members]);
  const hasTimeline = members.some((member) => parseGanttTime(member.startAt) !== null || parseGanttTime(member.expiresAt) !== null);
  const isGroup = members.length > 1;
  const range = formatGanttRange(group.earliestStart, group.latestExpires);
  // 后端已给出组级状态时以服务端口径为准（服务端未部署时回退成员推导）。
  const backendStatus = normalizeGroupStatus((item as unknown as Record<string, unknown>).groupStatus);
  const groupStatus = GROUP_STATUS_META[backendStatus ?? group.status];

  return (
    // 不用 layout 动画：高度变化交给下方 grid 行高过渡，避免 spring 回弹与过冲。
    <motion.article
      initial={{opacity: 0, y: 8}}
      animate={{opacity: 1, y: 0}}
      className="group/card w-full rounded-2xl border border-slate-200/80 bg-white/80 shadow-sm transition hover:border-[#185fa5]/60 hover:shadow-md dark:border-slate-700/60 dark:bg-slate-800/60"
    >
      <button
        type="button"
        onClick={() => onSelectRisk(item)}
        className="block w-full rounded-t-2xl px-4 pt-4 text-left focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-[#185fa5]"
      >
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <RiskBadge level={item.level} name={item.levelName} />
              <ValidityBadge state={item.validityState} />
              <ProcessingBadge state={item.processingState} />
              <span className="truncate text-[15px] font-bold text-slate-900 group-hover/card:text-[#185fa5] dark:text-white">{item.companyName}</span>
            </div>
            {item.location && (
              <div className="mt-1 flex items-center gap-1 text-[11px] text-slate-500">
                <MapPin className="h-3.5 w-3.5" />
                {item.location}
              </div>
            )}
          </div>
          <span className="whitespace-nowrap text-[11px] font-mono text-slate-400">{item.updatedTime}</span>
        </div>
        <p className="mt-2 line-clamp-2 text-[13px] leading-relaxed text-slate-600 dark:text-slate-300">{item.summary}</p>
        <div className="mt-3 flex items-center justify-between border-t border-slate-100 pt-3 text-[11px] dark:border-slate-800">
          <div className="flex flex-wrap items-center gap-1.5">
            {item.source && <SourceBadge name={item.source} />}
            {item.tags?.map((tag) => (
              <span key={tag} className="rounded-full border border-slate-200 bg-slate-50 px-2 py-0.5 font-medium text-[#185fa5] dark:border-slate-700 dark:bg-slate-900">{tag}</span>
            ))}
            <span className="font-mono text-slate-400">AI 置信度 {item.aiConfidence}%</span>
          </div>
          <span className="flex items-center gap-1 font-bold text-[#185fa5]">查看详情<ChevronRight className="h-3.5 w-3.5" /></span>
        </div>
      </button>

      <div className="px-4 pb-4">
        {isGroup && (
          <div className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-slate-500 dark:text-slate-400">
            <span className="font-semibold text-slate-700 dark:text-slate-200">{group.memberCount} 个事件</span>
            <span aria-hidden>·</span>
            <span>{group.sourceCount} 个信源</span>
            {range !== null && (
              <>
                <span aria-hidden>·</span>
                <span>有效期 <span className="font-mono text-slate-600 dark:text-slate-300">{range}</span></span>
              </>
            )}
            <span className={`rounded-full px-2 py-0.5 text-[10px] font-bold ${groupStatus.className}`}>{groupStatus.label}</span>
          </div>
        )}

        <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
          {hasTimeline ? (
            <button
              type="button"
              onClick={() => {
                setExpanded((value) => !value);
                setEverExpanded(true);
              }}
              aria-expanded={expanded}
              className="inline-flex items-center gap-1 rounded-lg px-1.5 py-1 text-[11px] font-bold text-[#185fa5] transition hover:bg-[#185fa5]/5 dark:text-blue-300"
            >
              <ChevronDown className={`h-3.5 w-3.5 transition-transform duration-200 ease-out motion-reduce:transition-none ${expanded ? 'rotate-180' : ''}`} />
              {expanded ? '收起事件有效期' : '展开事件有效期'}
            </button>
          ) : <span />}
        </div>

        {(item.validUntil || item.reviewDueAt || showValidityReason(item.validityReason)) && (
          <dl className="mt-1 flex flex-wrap gap-x-4 gap-y-1 border-t border-slate-100 pt-2 text-[11px] text-slate-500 dark:border-slate-800">
            {item.validUntil && <div><dt className="inline font-bold">截止</dt> <dd className="inline font-mono">{formatDateTime(item.validUntil)}</dd></div>}
            {item.reviewDueAt && <div><dt className="inline font-bold">复核</dt> <dd className="inline font-mono">{formatDateTime(item.reviewDueAt)}</dd></div>}
            {showValidityReason(item.validityReason) && item.validityReason && <div><dt className="inline font-bold">原因</dt> <dd className="inline">{formatValidityReasonLabel(item.validityReason.code, item.validityReason.details)}</dd></div>}
          </dl>
        )}

        {/* 高度过渡：grid 行高 0fr↔1fr 平滑拉伸/收回；不测量高度、不用 spring。 */}
        {hasTimeline && (
          <div
            className={`grid transition-[grid-template-rows] duration-200 ease-out motion-reduce:transition-none ${expanded ? 'grid-rows-[1fr]' : 'grid-rows-[0fr]'}`}
          >
            <div className="overflow-hidden">
              {everExpanded && (
                // 收起后内容仍挂载（保证收起也有高度过渡），但立即退出无障碍树与焦点顺序。
                <div
                  aria-hidden={!expanded}
                  inert={!expanded}
                  className="mt-2 border-t border-slate-100 pt-3 dark:border-slate-800"
                >
                  <EventValidityGantt
                    members={members}
                    variant="full"
                    now={now}
                    onSelectEvent={(eventId) => onSelectMember(item, eventId)}
                  />
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </motion.article>
  );
};

const LevelSummary = ({label, value, color}: {label: string; value: number; color: string}) => (
  <div className="text-center">
    <div className={`font-mono text-xl font-black ${color}`}>{value}</div>
    <div className="text-[11px] text-slate-500">{label}</div>
  </div>
);

const RiskBadge = ({level, name}: {level: RiskItem['level']; name: string}) => (
  <span className={`rounded-md px-2 py-0.5 text-[10px] font-bold text-white ${level === 'P1' ? 'bg-[#C92A2A]' : level === 'P2' ? 'bg-[#D97706]' : level === 'P3' ? 'bg-[#2563EB]' : 'bg-[#64748B]'}`}>
    {level} {name}
  </span>
);

const SourceBadge = ({name}: {name: string}) => (
  <span className="inline-flex items-center gap-1 rounded-full border border-[#185fa5]/30 bg-[#185fa5]/5 px-2 py-0.5 font-mono font-semibold text-[#185fa5] dark:border-blue-400/30 dark:bg-blue-400/10 dark:text-blue-300">
    {name}
  </span>
);

const formatDateTime = (value: string) => (
  new Date(value).toLocaleString('zh-CN', {hour12: false})
);

const VALIDITY_BADGE_CLASS: Record<string, string> = {
  active: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300',
  expired: 'bg-slate-200 text-slate-700 dark:bg-slate-700 dark:text-slate-300',
  revoked: 'bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300',
  superseded: 'bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300',
  conflicted: 'bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-300',
  legacy: 'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300',
  irrelevant: 'bg-zinc-100 text-zinc-600 dark:bg-zinc-900/40 dark:text-zinc-300',
};

const PROCESSING_BADGE_CLASS: Record<string, string> = {
  pending_classification: 'bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300',
  filtered: 'bg-zinc-100 text-zinc-600 dark:bg-zinc-900/40 dark:text-zinc-300',
  no_trigger: 'bg-sky-100 text-sky-700 dark:bg-sky-900/40 dark:text-sky-300',
  classification_failed: 'bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300',
};

/** 生命周期未定（null）时不渲染徽标，避免出现空徽标或 "undefined"。 */
const ValidityBadge = ({state}: {state: string | null | undefined}) => {
  if (!state) return null;
  return (
    <span className={`rounded-full border px-2 py-0.5 text-[10px] font-bold ${VALIDITY_BADGE_CLASS[state] ?? 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300'}`}>
      {formatValidityState(state)}
    </span>
  );
};

/** analyzed 由生命周期徽标承担表达，不单独重复展示。 */
const ProcessingBadge = ({state}: {state: string | null | undefined}) => {
  if (!state || state === 'analyzed') return null;
  return (
    <span className={`rounded-full px-2 py-0.5 text-[10px] font-bold ${PROCESSING_BADGE_CLASS[state] ?? 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300'}`}>
      {formatProcessingState(state)}
    </span>
  );
};
