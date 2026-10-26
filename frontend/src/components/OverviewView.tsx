import {useCallback, useEffect, useMemo, useRef, useState} from 'react';
import {useSearchParams} from 'react-router-dom';
import {AlertTriangle, RefreshCw} from 'lucide-react';
import {api, ApiError, mapRiskAlert, type DashboardAnalytics, type DashboardSummary, type DashboardWindowDays} from '../api';
import {displaySourceName} from '../sourceDisplay';
import type {RiskItem, RiskLevel} from '../types';
import type {MonitoringHealthSnapshot} from '../useMonitoringHealth';
import {MonitoringHealthIndicator} from './MonitoringHealthBanner';
import {OverviewMap} from './OverviewMap';
import {OverviewTrend} from './OverviewTrend';

interface OverviewViewProps {
  readonly onSelectRisk: (item: RiskItem) => void;
  readonly onViewAllRisks: () => void;
  readonly onRequestError: (error: ApiError) => void;
  readonly monitoringHealth: MonitoringHealthSnapshot;
}

// 时间窗口白名单：仅 7/90 写入 URL query，缺省 30 不落 query，其他值一律规范化回 30。
const DAY_WINDOWS = [7, 30, 90] as const;
const DEFAULT_WINDOW: DashboardWindowDays = 30;
const SOURCE_BAR_COLORS = ['#38bdf8', '#64748B', '#f59e0b', '#ff4d4f', '#2563EB'] as const;
const REFRESH_INTERVAL_MS = 60_000;

const LEVEL_SEGMENTS = [
  {level: 'P1', label: 'P1 严重', color: 'bg-[#ff4d4f]', text: '#ff4d4f'},
  {level: 'P2', label: 'P2 高风险', color: 'bg-[#ffa940]', text: '#ffa940'},
  {level: 'P3', label: 'P3 中度', color: 'bg-[#4da3ff]', text: '#4da3ff'},
  {level: 'P4', label: 'P4 轻微关注', color: 'bg-[#7f8ea3]', text: '#7f8ea3'},
] as const;

const LEVEL_RANK: Record<RiskLevel, number> = {P1: 0, P2: 1, P3: 2, P4: 3};

const riskLevelRank = (level: RiskLevel | null): number => (level === null ? 9 : LEVEL_RANK[level]);

const EVENT_TYPE_LABELS: Record<string, string> = {
  weather: '自然灾害',
  earthquake: '地震灾害',
  compliance: '合规制裁',
  sanction: '合规制裁',
  supply: '供应中断',
  supply_chain: '供应中断',
  finance: '财务风险',
  financial: '财务风险',
  logistics: '物流中断',
  geopolitics: '地缘政治',
  policy: '政策变化',
  market: '市场波动',
  judicial: '司法风险',
  corporate: '企业风险',
  environmental: '环境风险',
  labor: '劳资风险',
  cyber: '网络安全',
  energy: '能源风险',
};

const typeLabel = (eventType: string): string => EVENT_TYPE_LABELS[eventType] ?? eventType;

const parseWindow = (raw: string | null): DashboardWindowDays => (raw === '7' ? 7 : raw === '90' ? 90 : DEFAULT_WINDOW);

// 规范查询串：只有 7/90 允许携带 days，缺省 30 与任何非法值都不写 days。
const isCanonicalDaysParam = (raw: string | null): boolean => raw === null || raw === '7' || raw === '90';

const formatAsOf = (value: string): string => Number.isNaN(new Date(value).getTime()) ? '未知' : new Date(value).toLocaleString('zh-CN', {hour12: false});

const formatClock = (value: Date): string => value.toLocaleTimeString('zh-CN', {hour12: false});

const formatSourceTime = (value: string | null): string => (
  value === null || Number.isNaN(new Date(value).getTime())
    ? '—'
    : new Date(value).toLocaleTimeString('zh-CN', {hour12: false, hour: '2-digit', minute: '2-digit'})
);

const sourceHealthMeta = (source: DashboardSummary['sources'][number]): {label: string; dot: string; text: string} => {
  if (!source.enabled) return {label: '停用', dot: 'bg-slate-500', text: 'text-slate-400'};
  if (source.last_run_status === 'failed') return {label: '异常', dot: 'bg-[#ff4d4f]', text: 'text-[#ff7875]'};
  if (source.last_run_status === 'running') return {label: '运行中', dot: 'bg-sky-400', text: 'text-sky-300'};
  if (source.last_run_status === 'succeeded' || source.last_run_status === 'success') {
    return {label: '正常', dot: 'bg-emerald-400', text: 'text-emerald-300'};
  }
  return {label: '待运行', dot: 'bg-amber-400', text: 'text-amber-300'};
};

const RiskStreamRow = ({
  item,
  onSelect,
  duplicate = false,
}: {
  readonly item: RiskItem;
  readonly onSelect: (item: RiskItem) => void;
  readonly duplicate?: boolean;
}) => (
  <button
    type="button"
    tabIndex={duplicate ? -1 : undefined}
    onClick={() => onSelect(item)}
    className="flex w-full items-start gap-2 py-2.5 text-left transition-colors hover:bg-sky-400/5"
  >
    <span className={`mt-0.5 rounded px-1 py-0.5 font-mono text-[9px] font-bold ${item.level === 'P1' ? 'bg-[#ff4d4f]/15 text-[#ff7875]' : item.level === 'P2' ? 'bg-[#ffa940]/15 text-[#ffc069]' : item.level === 'P3' ? 'bg-[#4da3ff]/15 text-[#69b4ff]' : 'bg-slate-400/15 text-slate-300'}`}>{item.level}</span>
    <span className="min-w-0 flex-1">
      <span className="block truncate text-[11px] font-bold text-sky-50">{item.companyName}</span>
      <span className="mt-1 flex items-center justify-between gap-2">
        <span className="min-w-0 truncate text-[10px] text-sky-300/70">{item.riskType}</span>
        <span className="shrink-0 font-mono text-[9px] text-sky-500/70">{item.updatedTime}</span>
      </span>
    </span>
  </button>
);

export const OverviewView = ({onSelectRisk, onViewAllRisks, onRequestError, monitoringHealth}: OverviewViewProps) => {
  const [searchParams, setSearchParams] = useSearchParams();
  const rawDays = searchParams.get('days');
  const days = parseWindow(rawDays);
  const canonicalDaysParam = isCanonicalDaysParam(rawDays);

  const requestSequence = useRef(0);
  const analyticsSequence = useRef(0);
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [analytics, setAnalytics] = useState<DashboardAnalytics | null>(null);
  const [loadError, setLoadError] = useState<Error | null>(null);
  const [analyticsFailed, setAnalyticsFailed] = useState(false);
  const [isFetching, setIsFetching] = useState(true);
  const [isAnalyticsFetching, setIsAnalyticsFetching] = useState(true);
  const [retryTick, setRetryTick] = useState(0);
  const [clock, setClock] = useState(() => new Date());

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

  // 地图与趋势是增强数据：失败时保留 KPI、告警流与信源健康，不进入全局错误态。
  useEffect(() => {
    const sequence = analyticsSequence.current + 1;
    analyticsSequence.current = sequence;
    setIsAnalyticsFetching(true);
    setAnalyticsFailed(false);

    void api.dashboardAnalytics(days)
      .then((response) => {
        if (analyticsSequence.current !== sequence) return;
        setAnalytics(response);
        setIsAnalyticsFetching(false);
      })
      .catch((caught: unknown) => {
        if (analyticsSequence.current !== sequence) return;
        if (caught instanceof ApiError && caught.status === 401) {
          onRequestError(caught);
          return;
        }
        setAnalyticsFailed(true);
        setIsAnalyticsFetching(false);
      });

    return () => {
      if (analyticsSequence.current === sequence) analyticsSequence.current += 1;
    };
  }, [days, retryTick, onRequestError]);

  // 大屏常驻：页面可见时每 60 秒静默刷新，切换窗口立即刷新。
  useEffect(() => {
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') setRetryTick((value) => value + 1);
    }, REFRESH_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    const timer = window.setInterval(() => setClock(new Date()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  const retry = useCallback(() => setRetryTick((value) => value + 1), []);

  const selectWindow = (next: DashboardWindowDays) => {
    if (next === days) return;
    const params = new URLSearchParams(searchParams);
    if (next === DEFAULT_WINDOW) params.delete('days');
    else params.set('days', String(next));
    setSearchParams(params);
  };

  const recentRisks = useMemo(
    () => (summary ? summary.recent_alerts.slice(0, 10).map(mapRiskAlert) : []),
    [summary],
  );
  const streamAutoScroll = recentRisks.length > 4;
  const streamDuration = `${Math.max(16, recentRisks.length * 2.6)}s`;

  if (summary === null) {
    if (loadError !== null) return <OverviewLoadError message={loadError.message} onRetry={retry} />;
    return (
      <div role="status" className="-m-4 grid min-h-screen place-items-center bg-[#050b16] sm:-m-6">
        <div className="flex flex-col items-center gap-4">
          <div className="relative h-16 w-16">
            <span className="absolute inset-0 rounded-full border border-sky-400/15" />
            <span className="absolute inset-0 animate-spin rounded-full border-2 border-transparent border-t-sky-400" />
            <span className="absolute inset-2.5 rounded-full border border-sky-400/25" />
            <span className="absolute inset-2.5 animate-spin rounded-full border-2 border-transparent border-b-cyan-300 [animation-direction:reverse] [animation-duration:1.8s]" />
            <span className="absolute inset-0 grid place-items-center"><span className="h-2 w-2 animate-pulse rounded-full bg-sky-300 shadow-[0_0_14px_#38bdf8]" /></span>
          </div>
          <div className="text-center">
            <p className="text-[12px] tracking-[0.24em] text-sky-300/80">正在接入风险态势…</p>
            <p className="mt-1 text-[9px] tracking-[0.18em] text-sky-500/60">CONNECTING TO RISK COMMAND</p>
          </div>
          <span className="sr-only">正在加载总览汇总…</span>
        </div>
      </div>
    );
  }

  const levelCount = (level: RiskLevel): number => summary.level_counts.find((item) => item.level === level)?.count ?? 0;
  // 零值级别不进入环形图：没有 count 的段不应画出可见弧长。
  const donutCircumference = 2 * Math.PI * 38;
  const donutSegments = LEVEL_SEGMENTS
    .map((segment) => ({...segment, count: levelCount(segment.level)}))
    .filter((segment) => segment.count > 0);
  let donutOffset = 0;
  const sourceRows = summary.source_distribution;
  const healthySourceCount = summary.sources.filter(
    (source) => source.enabled && (source.last_run_status === 'succeeded' || source.last_run_status === 'success'),
  ).length;
  const maxSourceCount = Math.max(1, ...sourceRows.map((row) => row.count));
  const typeRows = summary.type_distribution.slice(0, 5);
  const maxTypeCount = Math.max(1, ...typeRows.map((row) => row.count));
  const refreshFailed = loadError !== null;
  const riskPoints = analytics?.risk_points ?? [];
  // 地图点位包含“有供应商但暂无风险”的空心省；排行只列真正命中提醒的地区。
  // 排行口径：最高等级优先，同等级内按总风险数降序。
  const regionRows = riskPoints
    .filter((point) => point.alert_count > 0)
    .slice()
    .sort((a, b) => riskLevelRank(a.max_level) - riskLevelRank(b.max_level) || b.alert_count - a.alert_count)
    .slice(0, 8);
  // 排行右侧按等级分列：只生成实际出现的等级列，空值也占位，保证同等级纵向对齐。
  const regionLevels = LEVEL_SEGMENTS.filter((segment) => regionRows.some(
    (point) => (point.level_counts.find((item) => item.level === segment.level)?.count ?? 0) > 0,
  ));
  const trendPoints = analytics?.trend ?? [];

  return (
    <div className="dark relative -m-4 overflow-hidden bg-[#050b16] p-3 text-slate-200 sm:-m-6 sm:p-4 xl:flex xl:h-screen xl:min-h-0 xl:flex-col">
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0"
        style={{
          backgroundImage: 'linear-gradient(rgba(59,130,246,0.06) 1px, transparent 1px), linear-gradient(90deg, rgba(59,130,246,0.06) 1px, transparent 1px)',
          backgroundSize: '28px 28px',
          maskImage: 'radial-gradient(ellipse at center, #000 20%, transparent 82%)',
          WebkitMaskImage: 'radial-gradient(ellipse at center, #000 20%, transparent 82%)',
        }}
      />
      <div aria-hidden="true" className="pointer-events-none absolute -left-32 -top-40 h-96 w-96 rounded-full bg-sky-500/10 blur-[90px]" />
      <div aria-hidden="true" className="pointer-events-none absolute -bottom-40 -right-24 h-96 w-96 rounded-full bg-indigo-500/10 blur-[90px]" />

      <div className="relative xl:flex xl:min-h-0 xl:flex-1 xl:flex-col">
        <header className="flex shrink-0 flex-col gap-3 border-b border-sky-400/10 pb-3 xl:flex-row xl:items-center xl:justify-between">
          <div className="flex items-center gap-3">
            <div className="grid h-9 w-9 place-items-center rounded-md border border-sky-400/50 bg-gradient-to-br from-sky-400/20 to-transparent font-mono text-[12px] font-black text-sky-200 shadow-[inset_0_0_16px_rgba(56,189,248,0.2)]">SR</div>
            <div>
              <div className="flex items-center gap-2">
                <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-400 shadow-[0_0_8px_#34d399]" />
                <h1 className="text-lg font-black tracking-[0.08em] text-sky-50">供应链风险监控总览大屏</h1>
              </div>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-3">
            <MonitoringHealthIndicator snapshot={monitoringHealth} hasCurrentRisks={summary.total_current > 0} />
            <div className="flex items-center gap-2 rounded-md border border-sky-400/15 bg-sky-400/5 px-2.5 py-1.5 text-[10px] text-sky-300/80">
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-400" />
              实时监测
              <span className="h-2.5 w-px bg-sky-400/20" />
              数据截至：<span className="font-mono text-sky-100">{formatAsOf(summary.as_of)}</span>
            </div>
            <span className="font-mono text-sm tracking-[0.08em] text-sky-200">{formatClock(clock)}</span>
            {isFetching && !refreshFailed && <span role="status" className="text-[10px] text-sky-400/70">更新中…</span>}
            <div className="flex items-center rounded-lg border border-sky-400/15 bg-sky-400/5 p-1 text-[11px] font-semibold text-sky-300/80">
              {DAY_WINDOWS.map((range) => (
                <button
                  key={range}
                  type="button"
                  aria-pressed={days === range}
                  onClick={() => selectWindow(range)}
                  className={`rounded-md px-3 py-1 transition ${days === range ? 'bg-sky-400 font-bold text-[#04111f] shadow-[0_0_14px_rgba(56,189,248,0.35)]' : 'hover:text-sky-100'}`}
                >
                  {range} 天
                </button>
              ))}
            </div>
          </div>
        </header>

        {refreshFailed && (
          <div role="status" className="mt-3 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-amber-400/40 bg-amber-400/10 px-3 py-2 text-[11px] text-amber-200">
            <span className="flex items-center gap-1.5">
              <AlertTriangle aria-hidden="true" className="h-3.5 w-3.5 shrink-0" />
              当前显示旧数据 · 刷新失败：{loadError?.message}
            </span>
            <button type="button" onClick={retry} className="shrink-0 rounded-md border border-amber-400/50 px-2.5 py-1 font-bold hover:bg-amber-400/10">重试</button>
          </div>
        )}

        <div className="mt-3 grid gap-3 xl:grid-cols-[260px_minmax(0,1fr)_300px] xl:min-h-0 xl:flex-1">
          <aside className="flex min-h-0 flex-col gap-3">
            <div className="overview-kpi-grid grid grid-cols-2 gap-2">
              <Kpi label="活跃供应商" value={summary.active_supplier_total} suffix="监控中" tone="sky" testId="overview-metric-active" />
              <Kpi label="高危预警 P1" value={levelCount('P1')} suffix="需处置" tone="red" testId="overview-metric-p1" />
              <Kpi label="高风险 P2" value={levelCount('P2')} suffix="跟踪中" tone="amber" testId="overview-metric-p2" />
              <Kpi label="当前风险总计" value={summary.total_current} suffix="当前有效" tone="slate" testId="overview-metric-total" />
            </div>

            <section data-testid="severity-bar" className="rounded-xl border border-sky-400/15 bg-[linear-gradient(180deg,rgba(12,30,54,0.82),rgba(6,14,27,0.62))] p-3">
              <div className="mb-2 flex items-center justify-between">
                <h2 className="text-[12px] font-bold tracking-[0.08em] text-sky-100">风险等级分布</h2>
                <span className="font-mono text-[9px] text-sky-500/70">当前有效</span>
              </div>
              <div className="flex items-center gap-3">
                <div className="relative h-[96px] w-[96px] shrink-0">
                  <svg viewBox="0 0 100 100" className="h-full w-full -rotate-90" aria-hidden="true">
                    <circle cx="50" cy="50" r="38" fill="none" stroke="rgba(56,189,248,0.1)" strokeWidth="10" />
                    {donutSegments.map((segment) => {
                      const length = donutCircumference * (segment.count / Math.max(1, summary.total_current));
                      const dashOffset = -donutOffset;
                      donutOffset += length;
                      return (
                        <circle
                          key={segment.level}
                          data-testid={`severity-segment-${segment.level}`}
                          cx="50"
                          cy="50"
                          r="38"
                          fill="none"
                          stroke={segment.text}
                          strokeWidth="10"
                          strokeDasharray={`${length} ${donutCircumference - length}`}
                          strokeDashoffset={dashOffset}
                        />
                      );
                    })}
                  </svg>
                  <div className="absolute inset-0 flex flex-col items-center justify-center">
                    <b className="font-mono text-xl font-black leading-none text-sky-50">{summary.total_current}</b>
                    <span className="mt-1 text-[8px] text-sky-500/70">当前风险</span>
                  </div>
                </div>
                <ul className="min-w-0 flex-1 space-y-1.5 text-[10px]">
                  {LEVEL_SEGMENTS.map((segment) => (
                    <li key={segment.level} className="flex items-center gap-2">
                      <i className="h-1.5 w-1.5 shrink-0 rounded-sm" style={{background: segment.text}} />
                      <span className="min-w-0 flex-1 truncate text-sky-200/80">{segment.label}</span>
                      <b className="font-mono text-[10px] text-sky-50">{levelCount(segment.level)}</b>
                    </li>
                  ))}
                </ul>
              </div>
              {summary.total_current === 0 && <p className="mt-1 text-center text-[10px] text-sky-400/60">当前无有效风险提醒</p>}
              {summary.history_may_be_partial && (
                <p className="mt-1 flex items-start gap-1.5 text-[9px] text-amber-300/80">
                  <AlertTriangle aria-hidden="true" className="mt-0.5 h-3 w-3 shrink-0" />
                  <span>时间窗口超出提醒保留期（{summary.retention_window_days} 天），更早的新增提醒可能已被清理，历史可能不完整。</span>
                </p>
              )}
            </section>

            <section data-testid="overview-source-distribution" className="flex max-h-[180px] flex-none flex-col rounded-xl border border-sky-400/15 bg-[linear-gradient(180deg,rgba(12,30,54,0.82),rgba(6,14,27,0.62))] p-3">
              <div className="mb-1 flex flex-none items-center justify-between">
                <h2 className="text-[12px] font-bold tracking-[0.08em] text-sky-100">风险信源分布</h2>
                <span className="font-mono text-[9px] text-sky-500/70">{sourceRows.length} 个信源</span>
              </div>
              <div className="max-h-[145px] space-y-2 overflow-y-auto pr-1">
                {sourceRows.length === 0 ? (
                  <div className="text-[11px] text-sky-400/60">暂无信源分布数据</div>
                ) : sourceRows.map((row, index) => (
                  <div key={row.source_id}>
                    <div className="mb-1 flex items-center gap-2 text-[10px] font-medium text-sky-200/85">
                      <span className="min-w-0 flex-1 truncate">{displaySourceName(row.name)}</span>
                      <span className="shrink-0 font-mono font-bold text-sky-300">{row.count} 条</span>
                    </div>
                    <div className="h-1.5 w-full overflow-hidden rounded-full bg-sky-400/10">
                      <div className="h-full rounded-full" style={{width: `${Math.round((row.count / maxSourceCount) * 100)}%`, background: SOURCE_BAR_COLORS[index % SOURCE_BAR_COLORS.length]}} />
                    </div>
                  </div>
                ))}
              </div>
            </section>

            <section className="overview-health-card scroll-card flex min-h-0 flex-1 flex-col rounded-xl border border-sky-400/15 bg-[linear-gradient(180deg,rgba(12,30,54,0.82),rgba(6,14,27,0.62))] p-3">
              <div className="mb-1 flex flex-none items-center justify-between">
                <h2 className="text-[12px] font-bold tracking-[0.08em] text-sky-100">信源采集健康</h2>
                <span className="font-mono text-[9px] text-sky-500/70">{healthySourceCount}/{summary.sources.length} 正常</span>
              </div>
              <div className="overview-scroll flex min-h-0 flex-1 flex-col gap-1 overflow-y-auto pr-1">
                {summary.sources.length === 0 ? (
                  <div className="text-[11px] text-sky-400/60">暂无信源运行数据</div>
                ) : summary.sources.map((source) => {
                  const meta = sourceHealthMeta(source);
                  return (
                    <div key={source.id} className="flex min-h-[24px] flex-1 items-center gap-2 rounded-md bg-sky-400/[0.03] px-2 text-[9px]">
                      <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${meta.dot}`} />
                      <span className="min-w-0 flex-1 truncate text-sky-200/85">{displaySourceName(source.name)}</span>
                      <span className={`shrink-0 font-mono text-[8px] ${meta.text}`}>{meta.label}</span>
                      <span className="shrink-0 font-mono text-[8px] text-sky-500/60">{formatSourceTime(source.last_run_at)}</span>
                    </div>
                  );
                })}
              </div>
            </section>
          </aside>

          <section className="flex min-h-0 flex-col gap-3">
            {analyticsFailed && (
              <p className="rounded-lg border border-amber-400/30 bg-amber-400/10 px-3 py-1.5 text-[10px] text-amber-200/90">
                地图与趋势数据暂不可用，KPI 与告警流不受影响。
              </p>
            )}
            <div className="flex min-h-[340px] flex-none flex-col xl:min-h-0 xl:flex-1">
              <OverviewMap
                riskPoints={riskPoints}
                riskSites={analytics?.risk_sites ?? []}
                unlocatedCount={analytics?.unlocated_alert_count ?? 0}
                loading={isAnalyticsFetching}
              />
            </div>
            <div className="h-[170px] flex-none">
              <OverviewTrend points={trendPoints} loading={isAnalyticsFetching} />
            </div>
          </section>

          <aside className="flex min-h-0 flex-col gap-3">
            <section className="overview-live-card flex h-[330px] flex-none flex-col rounded-xl border border-sky-400/15 bg-[linear-gradient(180deg,rgba(12,30,54,0.82),rgba(6,14,27,0.62))]">
              <div className="flex items-center justify-between border-b border-sky-400/10 px-3.5 py-2">
                <div className="flex items-baseline gap-2">
                  <h2 className="text-[12px] font-bold tracking-[0.08em] text-sky-100">实时风险流</h2>
                  <span className="flex items-center gap-1 text-[9px] font-bold tracking-[0.16em] text-[#ff7875]">
                    <i className="h-1.5 w-1.5 animate-pulse rounded-full bg-[#ff4d4f] shadow-[0_0_6px_#ff4d4f]" />LIVE
                  </span>
                </div>
                <button type="button" onClick={onViewAllRisks} className="text-[10px] font-bold text-sky-400 hover:text-sky-200">全部风险 →</button>
              </div>
              {recentRisks.length === 0 ? (
                <div className="flex flex-1 items-center justify-center px-3.5">
                  <p className="py-8 text-[11px] text-sky-400/60">暂无当前风险提醒</p>
                </div>
              ) : streamAutoScroll ? (
                <div className="overview-risk-stream flex flex-1 flex-col overflow-hidden px-3.5">
                  <div className="overview-risk-stream-track flex flex-col" style={{animationDuration: streamDuration}}>
                    <div className="divide-y divide-sky-400/10">
                      {recentRisks.map((item) => (
                        <RiskStreamRow key={`main-${item.id}`} item={item} onSelect={onSelectRisk} />
                      ))}
                    </div>
                    <div aria-hidden="true" className="pointer-events-none divide-y divide-sky-400/10">
                      {recentRisks.map((item) => (
                        <RiskStreamRow key={`duplicate-${item.id}`} item={item} onSelect={onSelectRisk} duplicate />
                      ))}
                    </div>
                  </div>
                </div>
              ) : (
                <div className="flex flex-1 flex-col divide-y divide-sky-400/10 overflow-y-auto px-3.5">
                  {recentRisks.map((item) => (
                    <RiskStreamRow key={item.id} item={item} onSelect={onSelectRisk} />
                  ))}
                </div>
              )}
            </section>

            <section className="flex-none rounded-xl border border-sky-400/15 bg-[linear-gradient(180deg,rgba(12,30,54,0.82),rgba(6,14,27,0.62))] p-3">
              <div className="mb-2 flex items-center justify-between">
                <h2 className="text-[12px] font-bold tracking-[0.08em] text-sky-100">风险类型</h2>
                <span className="font-mono text-[9px] text-sky-500/70">当前有效</span>
              </div>
              <div className="space-y-1.5">
                {typeRows.length === 0 ? (
                  <p className="text-[11px] text-sky-400/60">暂无类型分布</p>
                ) : typeRows.map((row, index) => (
                  <div key={row.event_type} className="grid grid-cols-[58px_minmax(0,1fr)_24px] items-center gap-2 text-[10px] text-sky-200/80">
                    <span className="truncate">{typeLabel(row.event_type)}</span>
                    <span className="h-1.5 overflow-hidden rounded-full bg-sky-400/10">
                      <span className="block h-full rounded-full" style={{width: `${Math.round((row.count / maxTypeCount) * 100)}%`, background: index === 0 ? '#ff4d4f' : index === 1 ? '#ffa940' : '#38bdf8'}} />
                    </span>
                    <b className="text-right font-mono text-[10px] text-sky-100">{row.count}</b>
                  </div>
                ))}
              </div>
            </section>

            <section className="overview-ranking-card scroll-card flex min-h-0 flex-1 flex-col rounded-xl border border-sky-400/15 bg-[linear-gradient(180deg,rgba(12,30,54,0.82),rgba(6,14,27,0.62))] p-3">
              <div className="mb-2 flex flex-none items-center justify-between">
                <h2 className="text-[12px] font-bold tracking-[0.08em] text-sky-100">风险地区排行</h2>
                <span className="font-mono text-[9px] text-sky-500/70">TOP {regionRows.length}</span>
              </div>
              <div className="overview-scroll flex min-h-0 flex-1 flex-col gap-1 overflow-y-auto">
                {regionRows.length === 0 ? (
                  <p className="text-[11px] text-sky-400/60">暂无地区点位数据</p>
                ) : regionRows.map((point, index) => (
                  <div
                    key={`${point.name}-${index}`}
                    title={`${point.name}：总风险 ${point.alert_count} 条`}
                    className="flex min-h-[24px] flex-1 items-center gap-2 rounded-md bg-sky-400/[0.03] px-2 text-[9px]"
                  >
                    <span className="w-3 shrink-0 font-mono text-[8px] text-sky-500/60">{index + 1}</span>
                    <span className="min-w-0 flex-1 truncate text-sky-200/85">{point.name}</span>
                    <span
                      className="grid shrink-0 items-center gap-x-2"
                      style={{gridTemplateColumns: `repeat(${Math.max(1, regionLevels.length)}, 40px)`}}
                    >
                      {regionLevels.map((segment) => {
                        const count = point.level_counts.find((item) => item.level === segment.level)?.count ?? 0;
                        return (
                          <span key={segment.level} className="inline-flex items-baseline justify-end gap-1.5 font-mono text-[8px]">
                            {count > 0 ? (
                              <>
                                <span className="font-bold" style={{color: segment.text}}>{segment.level}</span>
                                <b className="text-[9px] text-sky-100">{count}</b>
                              </>
                            ) : null}
                          </span>
                        );
                      })}
                    </span>
                  </div>
                ))}
              </div>
            </section>
          </aside>
        </div>
      </div>
    </div>
  );
};

const KPI_TONE = {
  sky: {value: 'text-sky-100', dot: 'bg-sky-400', border: 'border-sky-400/15'},
  red: {value: 'text-[#ff7875]', dot: 'bg-[#ff4d4f]', border: 'border-[#ff4d4f]/20'},
  amber: {value: 'text-[#ffc069]', dot: 'bg-[#ffa940]', border: 'border-[#ffa940]/20'},
  slate: {value: 'text-white dark:text-white', dot: 'bg-slate-400', border: 'border-slate-400/15'},
} as const;

const Kpi = ({label, value, suffix, tone, testId}: {
  readonly label: string;
  readonly value: number;
  readonly suffix: string;
  readonly tone: keyof typeof KPI_TONE;
  readonly testId: string;
}) => {
  const colors = KPI_TONE[tone];
  return (
    <div className={`flex h-full flex-col justify-center gap-1.5 rounded-lg border bg-[linear-gradient(160deg,rgba(20,48,86,0.62),rgba(7,17,32,0.45))] p-2.5 ${colors.border}`}>
      <div className="flex items-center justify-between gap-1">
        <span className="truncate text-[10px] text-sky-300/70">{label}</span>
        <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${colors.dot}`} />
      </div>
      <div className="mt-1 flex flex-wrap items-baseline gap-x-1.5">
        <span data-testid={testId} className={`font-mono text-2xl font-black leading-none ${colors.value}`}>{value}</span>
        <span className="text-[9px] text-sky-500/70">{suffix}</span>
      </div>
    </div>
  );
};

const OverviewLoadError = ({message, onRetry}: {readonly message: string; readonly onRetry: () => void}) => (
  <section role="alert" className="dark mx-auto flex min-h-screen max-w-xl flex-col items-start justify-center gap-4 rounded-2xl border border-sky-400/20 bg-[#0a1a2e] p-6 text-slate-200">
    <h1 className="text-xl font-black tracking-tight text-white">总览汇总加载失败</h1>
    <p className="text-sm leading-relaxed text-slate-300">{message}</p>
    <button type="button" onClick={onRetry} className="flex items-center gap-2 rounded-lg bg-sky-500 px-4 py-2 text-sm font-bold text-[#04111f] hover:bg-sky-400">
      <RefreshCw className="h-4 w-4" />重试
    </button>
  </section>
);
