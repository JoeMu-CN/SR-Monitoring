import {useCallback, useEffect, useRef, useState} from 'react';
import {AlertTriangle, Globe, Landmark, Lightbulb, Newspaper} from 'lucide-react';
import {api, ApiError, type IntelligenceCategory, type IntelligenceReportListResponse} from '../api';

/** 每页条数；「加载更多」逐页追加，服务端分页口径不变. */
const PAGE_SIZE = 20;

type IntelligenceTab = IntelligenceCategory;

const TAB_ITEMS: ReadonlyArray<{tab: IntelligenceTab; label: string; hint: string}> = [
  {tab: 'policy', label: '政策情报', hint: '监管、进出口政策与供应链合规法规的宏观解读'},
  {tab: 'industry', label: '行业情报', hint: '原材料、产能、物流与劳动力市场的供需传导'},
];

const formatDateTime = (value: string | null) => (
  value === null ? '—' : new Date(value).toLocaleString('zh-CN', {hour12: false})
);

/** 单个分类的数据加载：常驻挂载的面板各取各的数据，切 Tab 不重放请求. */
const useIntelligenceReports = (tab: IntelligenceTab) => {
  const [data, setData] = useState<IntelligenceReportListResponse | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(true);
  const [retryKey, setRetryKey] = useState(0);
  const requestSequence = useRef(0);

  useEffect(() => {
    const sequence = requestSequence.current + 1;
    requestSequence.current = sequence;
    setLoading(true);
    setError(null);

    void api.intelligence(tab, PAGE_SIZE)
      .then((response) => {
        if (requestSequence.current !== sequence) return;
        setData(response);
        setLoading(false);
      })
      .catch((caught: unknown) => {
        if (requestSequence.current !== sequence) return;
        setError(caught instanceof Error ? caught : new Error('情报加载失败'));
        setLoading(false);
      });

    return () => {
      if (requestSequence.current === sequence) requestSequence.current += 1;
    };
  }, [tab, retryKey]);

  const retry = useCallback(() => setRetryKey((value) => value + 1), []);
  return {data, error, loading, retry};
};

const CategoryBadge = ({tab}: {readonly tab: IntelligenceTab}) => (
  <span className="inline-flex items-center gap-1 rounded-full bg-[#185fa5]/10 px-2 py-0.5 text-[10px] font-bold text-[#185fa5] dark:bg-blue-500/20 dark:text-blue-300">
    {tab === 'policy' ? <Landmark className="h-3 w-3" /> : <Globe className="h-3 w-3" />}
    {tab === 'policy' ? '政策情报' : '行业情报'}
  </span>
);

const IntelligenceCard = ({report, tab}: {readonly report: IntelligenceReportListResponse['items'][number]; readonly tab: IntelligenceTab}) => (
  <article className="space-y-2.5 rounded-2xl border border-slate-200/80 bg-white/80 p-4 shadow-sm backdrop-blur-md dark:border-slate-700/60 dark:bg-slate-800/60">
    <div className="flex flex-wrap items-center gap-2">
      <CategoryBadge tab={tab} />
      <h3 className="min-w-0 flex-1 text-[14px] font-bold text-slate-900 dark:text-white">{report.title}</h3>
    </div>
    <p className="text-[12px] leading-relaxed text-slate-600 dark:text-slate-300">{report.event_summary}</p>
    <dl className="flex flex-wrap gap-x-4 gap-y-1 text-[12px] text-slate-500 dark:text-slate-400">
      <div><dt className="inline font-semibold">信源：</dt><dd className="inline">{report.source_name}</dd></div>
      <div><dt className="inline font-semibold">生成时间：</dt><dd className="inline font-mono">{formatDateTime(report.generated_at)}</dd></div>
      {report.source_url !== null && (
        <div><dd className="inline"><a href={report.source_url} target="_blank" rel="noreferrer" className="text-[#185fa5] underline-offset-2 hover:underline dark:text-blue-300">查看原始公告</a></dd></div>
      )}
    </dl>
    <section aria-label="简要报告" className="rounded-xl bg-slate-50 p-3 text-[13px] leading-relaxed text-slate-700 dark:bg-slate-900/60 dark:text-slate-200">
      <p className="mb-1 flex items-center gap-1 text-[11px] font-bold text-slate-500 dark:text-slate-400"><Newspaper className="h-3.5 w-3.5" />简要报告</p>
      <p>{report.report_zh}</p>
    </section>
    <section aria-label="风险提醒建议" className="rounded-xl border border-amber-200/70 bg-amber-50/70 p-3 dark:border-amber-900/60 dark:bg-amber-950/20">
      <p className="mb-1 flex items-center gap-1 text-[11px] font-bold text-amber-800 dark:text-amber-300"><Lightbulb className="h-3.5 w-3.5" />风险提醒建议</p>
      <ol className="list-decimal space-y-1 pl-5 text-[13px] leading-relaxed text-slate-700 dark:text-slate-200">
        {report.suggestions.map((suggestion, index) => (
          <li key={`${report.id}-suggestion-${index}`}>{suggestion}</li>
        ))}
      </ol>
    </section>
  </article>
);

const IntelligencePanel = ({tab, active}: {readonly tab: IntelligenceTab; readonly active: boolean}) => {
  const {data, error, loading, retry} = useIntelligenceReports(tab);
  const hint = TAB_ITEMS.find((item) => item.tab === tab)?.hint ?? '';

  return (
    <div
      role="tabpanel"
      id={`intelligence-tabpanel-${tab}`}
      aria-labelledby={`intelligence-tab-${tab}`}
      data-testid={`intelligence-tabpanel-${tab}`}
      hidden={!active}
    >
      <p className="mb-3 text-[12px] text-slate-500 dark:text-slate-400">{hint}：报告由调度任务自动生成，不含供应商匹配与风险等级。</p>
      {loading ? (
        <div role="status" className="rounded-2xl border border-slate-200/80 bg-white/80 p-12 text-center shadow-sm dark:border-slate-700/60 dark:bg-slate-800/60">
          <span className="text-[13px] text-slate-500 dark:text-slate-400">正在加载情报报告…</span>
        </div>
      ) : error !== null ? (
        <div role="alert" className="rounded-2xl border border-red-200 bg-red-50 p-6 text-center shadow-sm dark:border-red-900 dark:bg-red-950/30">
          <AlertTriangle className="mx-auto h-8 w-8 text-red-500" />
          <h3 className="mt-2 text-[15px] font-bold text-red-800 dark:text-red-300">情报加载失败</h3>
          <p className="mt-1 text-xs text-red-600 dark:text-red-400">{error instanceof ApiError && error.status === 403 ? '无权查看情报聚合' : error.message}</p>
          <button type="button" onClick={retry} className="mt-3 rounded-xl bg-red-600 px-4 py-2 text-sm font-bold text-white hover:bg-red-700">重试</button>
        </div>
      ) : data === null || data.items.length === 0 ? (
        <div className="rounded-2xl border border-slate-200/80 bg-white/80 p-12 text-center shadow-sm dark:border-slate-700/60 dark:bg-slate-800/60">
          <Newspaper className="mx-auto h-10 w-10 text-slate-300" />
          <h3 className="mt-3 text-[15px] font-bold text-slate-700 dark:text-slate-300">暂无情报报告</h3>
          <p className="mt-1 text-xs text-slate-400">调度任务尚未生成该分类的报告，稍后再来查看。</p>
        </div>
      ) : (
        <div className="space-y-2">
          <p className="text-[12px] text-slate-500 dark:text-slate-400">共 {data.total} 条报告</p>
          {data.items.map((report) => (
            <IntelligenceCard key={report.id} report={report} tab={tab} />
          ))}
        </div>
      )}
    </div>
  );
};

export const PolicyIntelligenceView = () => {
  const [activeTab, setActiveTab] = useState<IntelligenceTab>('policy');
  const tabRefs = useRef<Record<IntelligenceTab, HTMLButtonElement | null>>({policy: null, industry: null});

  const handleTabListKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    const currentIndex = Math.max(TAB_ITEMS.findIndex((item) => item.tab === activeTab), 0);
    let nextIndex: number;
    switch (event.key) {
      case 'ArrowRight':
        nextIndex = (currentIndex + 1) % TAB_ITEMS.length;
        break;
      case 'ArrowLeft':
        nextIndex = (currentIndex - 1 + TAB_ITEMS.length) % TAB_ITEMS.length;
        break;
      case 'Home':
        nextIndex = 0;
        break;
      case 'End':
        nextIndex = TAB_ITEMS.length - 1;
        break;
      default:
        return;
    }
    event.preventDefault();
    const nextTab = TAB_ITEMS[nextIndex].tab;
    if (nextTab !== activeTab) setActiveTab(nextTab);
    tabRefs.current[nextTab]?.focus();
  };

  return (
    <div className="space-y-3 pb-20 sm:space-y-5 lg:pb-8">
      <div>
        <h1 className="text-xl font-black tracking-tight text-slate-900 dark:text-white lg:text-2xl">情报聚合</h1>
        <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">政策与行业维度的宏观情报解读：简要报告与风险提醒建议</p>
      </div>

      <section className="rounded-2xl border border-slate-200/80 bg-white/80 p-4 shadow-sm backdrop-blur-md dark:border-slate-700/60 dark:bg-slate-800/60">
        <div
          role="tablist"
          aria-label="情报分类切换"
          data-testid="intelligence-tabs"
          onKeyDown={handleTabListKeyDown}
          className="flex items-end gap-6 border-b border-slate-200/80 dark:border-slate-700/60"
        >
          {TAB_ITEMS.map((item) => {
            const isActive = activeTab === item.tab;
            return (
              <button
                key={item.tab}
                ref={(node) => { tabRefs.current[item.tab] = node; }}
                type="button"
                role="tab"
                id={`intelligence-tab-${item.tab}`}
                data-testid={`intelligence-tab-${item.tab}`}
                aria-selected={isActive}
                aria-controls={`intelligence-tabpanel-${item.tab}`}
                tabIndex={isActive ? 0 : -1}
                onClick={() => setActiveTab(item.tab)}
                className={`inline-flex items-center gap-1 border-b-[3px] px-0 pb-2.5 pt-2 text-[13.5px] font-bold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#004782] focus-visible:ring-offset-2 dark:focus-visible:ring-blue-400 sm:text-[14px] ${
                  isActive
                    ? 'border-[#004782] text-[#004782] dark:border-blue-300 dark:text-blue-300'
                    : 'border-transparent text-[#424751] hover:text-[#004782] dark:text-slate-300 dark:hover:text-white'
                }`}
              >
                {item.label}
              </button>
            );
          })}
        </div>

        <div className="pt-3">
          <IntelligencePanel tab="policy" active={activeTab === 'policy'} />
          <IntelligencePanel tab="industry" active={activeTab === 'industry'} />
        </div>
      </section>
    </div>
  );
};