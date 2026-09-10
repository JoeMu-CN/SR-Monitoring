import {useCallback, useEffect, useMemo, useRef, useState} from 'react';
import {useSearchParams} from 'react-router-dom';
import {AlertTriangle, ChevronRight, Info, ShieldCheck} from 'lucide-react';
import {motion} from 'motion/react';
import {api, ApiError, mapRiskAlert, type DashboardSummary, type DashboardWindowDays} from '../api';
import type {RiskItem, RiskLevel} from '../types';

interface OverviewViewProps {
  readonly onSelectRisk: (item: RiskItem) => void;
  readonly onViewAllRisks: () => void;
  readonly onRequestError: (error: ApiError) => void;
}

// 时间窗口白名单：仅 7/90 写入 URL query，缺省 30 不落 query，其他值一律规范化回 30。
const DAY_WINDOWS = [7, 30, 90] as const;
const DEFAULT_WINDOW: DashboardWindowDays = 30;
const SOURCE_BAR_COLORS = ['bg-[#185fa5]', 'bg-[#64748B]', 'bg-[#D97706]', 'bg-[#C92A2A]', 'bg-[#2563EB]'] as const;

const LEVEL_SEGMENTS = [
  {level: 'P1', label: 'P1 严重', color: 'bg-[#C92A2A]'},
  {level: 'P2', label: 'P2 高风险', color: 'bg-[#D97706]'},
  {level: 'P3', label: 'P3 中度', color: 'bg-[#2563EB]'},
  {level: 'P4', label: 'P4 轻微关注', color: 'bg-[#64748B]'},
] as const;

const parseWindow = (raw: string | null): DashboardWindowDays => (raw === '7' ? 7 : raw === '90' ? 90 : DEFAULT_WINDOW);

// 规范查询串：只有 7/90 允许携带 days，缺省 30 与任何非法值都不写 days。
const isCanonicalDaysParam = (raw: string | null): boolean => raw === null || raw === '7' || raw === '90';

const formatAsOf = (value: string): string => Number.isNaN(new Date(value).getTime()) ? '未知' : new Date(value).toLocaleString('zh-CN', {hour12: false});

// 顶部"全网风险严重程度分布"条+最近风险提醒是核心，"风险类型分布/数据源节点"与"数据源运行提示"
// 已迁出或并入"数据源清单"页面，避免在风险总览上堆叠太多维护性信息。
export const OverviewView = ({onSelectRisk, onViewAllRisks, onRequestError}: OverviewViewProps) => {
  const [searchParams, setSearchParams] = useSearchParams();
  const rawDays = searchParams.get('days');
  const days = parseWindow(rawDays);
  const canonicalDaysParam = isCanonicalDaysParam(rawDays);

  const requestSequence = useRef(0);
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [loadError, setLoadError] = useState<Error | null>(null);
  const [isFetching, setIsFetching] = useState(true);
  const [retryTick, setRetryTick] = useState(0);

  // 非白名单 days（含显式 30 与非法值）以 replace 规范化，避免后退回到脏 URL。
  useEffect(() => {
    if (canonicalDaysParam) return;
    const next = new URLSearchParams(searchParams);
    next.delete('days');
    setSearchParams(next, {replace: true});
  }, [canonicalDaysParam, searchParams, setSearchParams]);

  // 总览自管的汇总请求：独立于 App.loadData 的 Promise.all 与全局 loading；
  // 请求序号确保快速切换 days 时旧响应不覆盖新窗口。
  useEffect(() => {
    const sequence = requestSequence.current + 1;
    requestSequence.current = sequence;
    setIsFetching(true);
    setLoadError(null);

    void api.dashboardSummary(days)
      .then((response) => {
        if (requestSequence.current !== sequence) return;
        setSummary(response);
        setIsFetching(false);
      })
      .catch((caught: unknown) => {
        if (requestSequence.current !== sequence) return;
        // 401 走 App 统一会话失效处理并退出；非 401 只在组件内呈现，不进入全局错误门。
        if (caught instanceof ApiError && caught.status === 401) {
          onRequestError(caught);
          return;
        }
        setLoadError(caught instanceof Error ? caught : new Error('总览汇总加载失败'));
        setIsFetching(false);
      });

    return () => {
      if (requestSequence.current === sequence) requestSequence.current += 1;
    };
  }, [days, retryTick, onRequestError]);

  const retry = useCallback(() => setRetryTick((value) => value + 1), []);

  const selectWindow = (next: DashboardWindowDays) => {
    if (next === days) return;
    const params = new URLSearchParams(searchParams);
    if (next === DEFAULT_WINDOW) params.delete('days');
    else params.set('days', String(next));
    setSearchParams(params);
  };

  const recentRisks = useMemo(
    () => (summary ? summary.recent_alerts.slice(0, 5).map(mapRiskAlert) : []),
    [summary],
  );

  if (summary === null) {
    if (loadError !== null) return <OverviewLoadError message={loadError.message} onRetry={retry} />;
    return <div role="status" className="flex min-h-[50vh] items-center justify-center text-sm text-slate-600 dark:text-slate-300">正在加载总览汇总…</div>;
  }

  const levelCount = (level: RiskLevel): number => summary.level_counts.find((item) => item.level === level)?.count ?? 0;
  // 零值级别不进入条形图：Math.max(1, count) 会给 count=0 可见宽度，伪造比例。
  const severitySegments = LEVEL_SEGMENTS
    .map((segment) => ({...segment, count: levelCount(segment.level)}))
    .filter((segment) => segment.count > 0);
  const sourceRows = summary.source_distribution.slice(0, 5);
  const maxSourceCount = Math.max(1, ...sourceRows.map((row) => row.count));
  const refreshFailed = loadError !== null;

  return <div className="space-y-5 pb-20 lg:pb-8">
    <div className="flex flex-col items-start justify-between gap-3 sm:flex-row sm:items-center">
      <div>
        <h1 className="text-xl font-black tracking-tight text-slate-900 dark:text-white lg:text-2xl">全网供应链风险概览</h1>
        <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">当前有效风险与重点供应商分级</p>
      </div>
      <div className="flex items-center gap-3">
        {isFetching && !refreshFailed && <span role="status" className="text-[11px] font-medium text-slate-400">更新中…</span>}
        <div className="flex items-center rounded-xl border border-black/5 bg-slate-200/70 p-1 text-xs font-semibold text-slate-700 dark:border-white/5 dark:bg-slate-800/80 dark:text-slate-300">
          {DAY_WINDOWS.map((range) => (
            <button key={range} type="button" aria-pressed={days === range} onClick={() => selectWindow(range)} className={`rounded-lg px-3 py-1 transition ${days === range ? 'bg-white font-bold text-slate-900 shadow-sm dark:bg-slate-700 dark:text-white' : 'hover:text-slate-900 dark:hover:text-white'}`}>{range} 天</button>
          ))}
        </div>
        <span className="hidden text-[11px] font-mono text-slate-400 md:inline">数据截至：{formatAsOf(summary.as_of)}</span>
      </div>
    </div>

    {refreshFailed && (
      <div role="status" className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-[#D97706]/40 bg-[#fff7ed] px-3 py-2 text-[12px] text-[#9a3412] dark:border-[#D97706]/30 dark:bg-[#D97706]/10 dark:text-[#fdba74]">
      <span className="flex items-center gap-1.5"><AlertTriangle aria-hidden="true" className="h-3.5 w-3.5 shrink-0"/>当前显示旧数据 · 刷新失败：{loadError?.message}</span>
      <button type="button" onClick={retry} className="shrink-0 rounded-lg border border-[#D97706]/50 px-2.5 py-1 font-bold hover:bg-[#D97706]/10">重试</button>
      </div>
    )}

    <div className="grid grid-cols-2 gap-3.5 lg:grid-cols-4">
      <Metric label="活跃供应商" value={summary.active_supplier_total} tone="blue" suffix="监控中" testId="overview-metric-active"/>
      <Metric label="高危预警 (P1)" value={levelCount('P1')} tone="red" suffix="当前有效" testId="overview-metric-p1"/>
      <Metric label="高风险 (P2)" value={levelCount('P2')} tone="amber" suffix="当前有效" testId="overview-metric-p2"/>
      <Metric label="当前风险总计" value={summary.total_current} tone="slate" suffix="只读提醒" testId="overview-metric-total"/>
    </div>

    <section className="rounded-2xl border border-slate-200/80 bg-white/80 p-4 shadow-sm backdrop-blur-md dark:border-slate-700/60 dark:bg-slate-800/60 lg:p-5">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-[15px] font-bold text-slate-900 dark:text-white">全网风险严重程度分布</h2>
        <span className="text-[11px] font-mono text-slate-400">当前有效快照 · 不随时间窗口变化</span>
      </div>
      <div className="flex h-12 w-full overflow-hidden rounded-xl text-white shadow-sm" data-testid="severity-bar">
        {severitySegments.length === 0 ? (
          <div className="flex w-full items-center justify-center bg-slate-100 text-[12px] font-semibold text-slate-400 dark:bg-slate-800 dark:text-slate-500">当前无有效风险提醒</div>
        ) : severitySegments.map((segment, index) => (
          <div key={segment.level} data-testid={`severity-segment-${segment.level}`} aria-label={`${segment.label}：${segment.count} 条`} className={`flex min-w-0 items-center justify-center gap-2 border-white/20 px-2 font-mono ${segment.color} ${index < severitySegments.length - 1 ? 'border-r' : ''}`} style={{flex: segment.count}}>
            <span className="truncate text-[10px] font-extrabold tracking-wider">{segment.label}</span>
            <span className="text-base font-black">{segment.count}</span>
          </div>
        ))}
      </div>
      <div className="mt-3 flex flex-wrap items-center justify-between gap-x-4 gap-y-1 text-[12px] font-medium text-slate-500 dark:text-slate-400">
        <span>总监控企业：<strong className="font-mono text-slate-900 dark:text-white">{summary.supplier_total}</strong> 家</span>
        <span>当前风险提醒：<strong className="font-mono text-[#C92A2A]">{summary.total_current}</strong> 条</span>
      </div>
      <p data-testid="overview-period-new" className="mt-2 text-[12px] text-slate-500 dark:text-slate-400">最近 {summary.window_days} 天新增提醒（含已失效）：<strong className="font-mono text-slate-700 dark:text-slate-200">{summary.period_new_count}</strong> 条</p>
      {summary.history_may_be_partial && (
        <p className="mt-1 flex items-start gap-1.5 text-[11px] text-[#D97706]"><AlertTriangle aria-hidden="true" className="mt-0.5 h-3.5 w-3.5 shrink-0"/><span>时间窗口超出提醒保留期（{summary.retention_window_days} 天），更早的新增提醒可能已被清理，历史可能不完整。</span></p>
      )}
    </section>

    <div className="grid grid-cols-1 gap-5 lg:grid-cols-12">
      <section className="flex flex-col overflow-hidden rounded-2xl border border-slate-200/80 bg-white/80 shadow-sm backdrop-blur-md dark:border-slate-700/60 dark:bg-slate-800/60 lg:col-span-8">
        <div className="flex items-center justify-between border-b border-slate-200/80 bg-slate-50/60 p-3.5 dark:border-slate-700/60 dark:bg-slate-900/30">
          <div className="flex items-center gap-2">
            <h2 className="text-[15px] font-bold text-slate-900 dark:text-white">最近风险提醒</h2>
            <span className="rounded-full bg-[#185fa5] px-2 py-0.5 text-[10px] font-extrabold text-white">{recentRisks.length} 条</span>
          </div>
          <button type="button" onClick={onViewAllRisks} className="flex items-center gap-0.5 text-[12px] font-bold text-[#185fa5] hover:underline">查看完整风险中心<ChevronRight className="h-4 w-4"/></button>
        </div>
        {recentRisks.length === 0 ? (
          <div className="my-auto flex flex-col items-center justify-center p-10 text-center">
            <div className="mb-3 flex h-14 w-14 items-center justify-center rounded-2xl border border-blue-100 bg-blue-50 text-[#185fa5] dark:border-slate-700 dark:bg-slate-800"><ShieldCheck className="h-7 w-7"/></div>
            <h3 className="text-[15px] font-bold text-slate-900 dark:text-white">暂无当前风险提醒</h3>
            <p className="mt-1 max-w-sm text-[12px] text-slate-500">完成数据源采集后，新的风险信号会显示在这里。</p>
          </div>
        ) : (
          <>
            <div className="hidden overflow-x-auto md:block">
              <table className="w-full border-collapse text-left">
                <thead className="border-b border-slate-200/80 bg-slate-100/70 text-[11px] font-bold uppercase text-slate-500 dark:border-slate-700/60 dark:bg-slate-800/80 dark:text-slate-400">
                  <tr>
                    <th className="p-3 pl-4">供应商主体</th>
                    <th className="p-3">级别</th>
                    <th className="p-3">风险类型</th>
                    <th className="p-3 text-right">AI 置信度</th>
                    <th className="p-3">更新时间</th>
                    <th className="p-3 pr-4 text-center">详情</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100 text-[13px] dark:divide-slate-800/60">
                  {recentRisks.map((item) => (
                    <tr key={item.id} onClick={() => onSelectRisk(item)} className="cursor-pointer transition-colors hover:bg-[#185fa5]/5 dark:hover:bg-slate-700/40">
                      <td className="p-3 pl-4 font-bold text-slate-900 dark:text-white"><span className="block max-w-[180px] truncate sm:max-w-none">{item.companyName}</span></td>
                      <td className="p-3"><RiskBadge level={item.level}/></td>
                      <td className="p-3 font-medium text-slate-600 dark:text-slate-300">{item.riskType}</td>
                      <td className="p-3 text-right font-mono font-bold text-[#185fa5] dark:text-blue-400">{item.aiConfidence}%</td>
                      <td className="p-3 text-[12px] font-mono text-slate-400">{item.updatedTime}</td>
                      <td className="p-3 pr-4 text-center">
                        <button type="button" title="查看详情" onClick={(event) => { event.stopPropagation(); onSelectRisk(item); }} className="rounded-lg p-1 text-[#185fa5] hover:bg-blue-100/60"><Info className="h-4 w-4"/></button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="divide-y divide-slate-100 md:hidden dark:divide-slate-800/60">
              {recentRisks.map((item) => (
                <button key={item.id} type="button" onClick={() => onSelectRisk(item)} className="flex w-full flex-col gap-2 p-3.5 text-left transition-colors hover:bg-[#185fa5]/5 dark:hover:bg-slate-700/40">
                  <div className="flex items-center justify-between gap-2">
                    <span className="min-w-0 truncate font-bold text-slate-900 dark:text-white">{item.companyName}</span>
                    <RiskBadge level={item.level}/>
                  </div>
                  <div className="flex items-center justify-between gap-2 text-[12px] text-slate-500 dark:text-slate-400">
                    <span className="min-w-0 truncate">{item.riskType}</span>
                    <span className="shrink-0 font-mono font-bold text-[#185fa5] dark:text-blue-400">AI {item.aiConfidence}%</span>
                  </div>
                  <span className="font-mono text-[11px] text-slate-400">{item.updatedTime}</span>
                </button>
              ))}
            </div>
          </>
        )}
      </section>
      <section data-testid="overview-source-distribution" className="rounded-2xl border border-slate-200/80 bg-white/80 p-4 shadow-sm backdrop-blur-md dark:border-slate-700/60 dark:bg-slate-800/60 lg:col-span-4">
        <h3 className="mb-1 text-[14px] font-bold text-slate-900 dark:text-white">风险信源分布</h3>
        <p className="mb-3 text-[11px] text-slate-400">各来源可重复归属，计数之和可能大于当前提醒总数（非互斥，不作占比）</p>
        <div className="space-y-3">
          {sourceRows.length === 0 ? (
            <div className="text-[12px] text-slate-400">暂无信源分布数据</div>
          ) : sourceRows.map((row, index) => (
            <div key={row.source_id}>
              <div className="mb-1 flex items-center gap-2 text-[12px] font-medium">
                <span className="min-w-0 flex-1 truncate">{row.name}</span>
                <span className="shrink-0 font-mono font-bold text-[#185fa5]">{row.count} 条</span>
              </div>
              <div className="h-2 w-full overflow-hidden rounded-full bg-slate-100 dark:bg-slate-700">
                <div className={`h-full rounded-full ${SOURCE_BAR_COLORS[index % SOURCE_BAR_COLORS.length]}`} style={{width: `${Math.round((row.count / maxSourceCount) * 100)}%`}}/>
              </div>
            </div>
          ))}
        </div>
      </section>
    </div>
  </div>;
};

const OverviewLoadError = ({message, onRetry}: {readonly message: string; readonly onRetry: () => void}) => (
  <section role="alert" className="mx-auto flex min-h-[50vh] max-w-xl flex-col items-start justify-center gap-4 rounded-2xl border border-slate-200/80 bg-white/80 p-6 shadow-sm dark:border-slate-700/60 dark:bg-slate-800/60">
    <h1 className="text-xl font-black tracking-tight text-slate-900 dark:text-white">总览汇总加载失败</h1>
    <p className="text-sm leading-relaxed text-slate-600 dark:text-slate-300">{message}</p>
    <button type="button" onClick={onRetry} className="rounded-xl bg-[#185fa5] px-4 py-2 text-sm font-bold text-white hover:bg-[#004782]">重试</button>
  </section>
);

// 卡片描边与数值语义色分开成表：数值色必须整体使用（slate 含 dark:text-white），
// 不得从组合串 split 取第一个 class——那会丢掉深色变量，深色模式下总计不可读。
const METRIC_CARD_CLASSES = {blue: 'border-slate-200/80', red: 'border-red-200/80', amber: 'border-amber-200/80', slate: 'border-slate-200/80'} as const;
const METRIC_VALUE_CLASSES = {blue: 'text-[#185fa5]', red: 'text-[#C92A2A]', amber: 'text-[#D97706]', slate: 'text-slate-900 dark:text-white'} as const;

const Metric = ({label, value, tone, suffix, testId}: {readonly label: string; readonly value: number; readonly tone: 'blue' | 'red' | 'amber' | 'slate'; readonly suffix: string; readonly testId?: string}) => {
  return (
    <motion.div whileHover={{y: -2}} className={`rounded-2xl border bg-white/80 p-4 shadow-sm backdrop-blur-md dark:bg-slate-800/60 ${METRIC_CARD_CLASSES[tone]}`}>
      <div className="flex items-center justify-between">
        <span className="text-[12px] font-semibold text-slate-500 dark:text-slate-400">{label}</span>
        <span className={`h-2 w-2 rounded-full ${tone === 'red' ? 'bg-[#C92A2A]' : tone === 'amber' ? 'bg-[#D97706]' : tone === 'blue' ? 'bg-[#185fa5]' : 'bg-slate-400'}`}/>
      </div>
      <div className="mt-2 flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <span data-testid={testId} className={`font-mono text-2xl font-black lg:text-3xl ${METRIC_VALUE_CLASSES[tone]}`}>{value}</span>
        <span className="whitespace-nowrap rounded-md bg-slate-100 px-1.5 py-0.5 text-[11px] font-bold text-slate-500 dark:bg-slate-700">{suffix}</span>
      </div>
    </motion.div>
  );
};

const RiskBadge = ({level}: {readonly level: RiskItem['level']}) => (
  <span className={`rounded-md px-2 py-0.5 text-[10px] font-bold text-white shadow-sm ${level === 'P1' ? 'bg-[#C92A2A]' : level === 'P2' ? 'bg-[#D97706]' : level === 'P3' ? 'bg-[#2563EB]' : 'bg-[#64748B]'}`}>{level}</span>
);
