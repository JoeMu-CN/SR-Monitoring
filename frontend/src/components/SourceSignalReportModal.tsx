import {useEffect, useRef, useState} from 'react';
import {AlertTriangle, CheckCircle2, CircleSlash, Database, Loader2, MinusCircle, ShieldAlert, X, XCircle} from 'lucide-react';
import {
  api,
  ApiError,
  type SourceSignalDetailRead,
  type TycDimensionFinding,
  type TycDimensionStatus,
  type TycRiskReport,
} from '../api';
import {useDialogFocus} from '../hooks/useDialogFocus';

export interface ActiveSourceSignal {
  readonly id: number;
  readonly title: string;
}

interface SourceSignalReportModalProps {
  readonly sourceId: number;
  /** 当前激活的采集记录；null 表示弹窗关闭。 */
  readonly activeSignal: ActiveSourceSignal | null;
  readonly onClose: () => void;
  readonly onRequestError: (error: ApiError) => void;
}

type DimensionGroupKey = 'hit' | 'normal' | 'empty' | 'error' | 'skipped';

interface DimensionGroupMeta {
  readonly key: DimensionGroupKey;
  readonly title: string;
  readonly icon: typeof ShieldAlert;
  readonly headingClass: string;
}

// 分组顺序即阅读顺序：先看命中，再确认正常，最后是未能取到结论的维度。
const GROUP_META: readonly DimensionGroupMeta[] = [
  {key: 'hit', title: '命中风险', icon: ShieldAlert, headingClass: 'text-[#ba1a1a] dark:text-red-300'},
  {key: 'normal', title: '未发现异常', icon: CheckCircle2, headingClass: 'text-emerald-700 dark:text-emerald-300'},
  {key: 'empty', title: '无数据记录', icon: MinusCircle, headingClass: 'text-slate-600 dark:text-slate-300'},
  {key: 'error', title: '查询失败', icon: XCircle, headingClass: 'text-[#ba1a1a] dark:text-red-300'},
  {key: 'skipped', title: '未执行', icon: CircleSlash, headingClass: 'text-amber-700 dark:text-amber-300'},
];

const GROUP_BY_STATUS: Record<TycDimensionStatus, DimensionGroupKey> = {
  success: 'normal',
  empty: 'empty',
  error: 'error',
  quota_exhausted: 'skipped',
  busy: 'skipped',
};

// 状态标签：命中优先级最高；其余按维度状态。文本即状态，图标与色彩只做辅助，不单独承载语义。
const STATUS_LABEL_BY_KEY: Record<DimensionGroupKey, string> = {
  hit: '命中',
  normal: '未发现',
  empty: '无记录',
  error: '失败',
  skipped: '未执行',
};

const STATUS_CHIP_CLASS_BY_KEY: Record<DimensionGroupKey, string> = {
  hit: 'bg-red-100 text-[#ba1a1a] dark:bg-red-950/60 dark:text-red-300',
  normal: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950/60 dark:text-emerald-300',
  empty: 'bg-slate-200 text-slate-700 dark:bg-slate-700 dark:text-slate-300',
  error: 'bg-red-100 text-[#ba1a1a] dark:bg-red-950/60 dark:text-red-300',
  skipped: 'bg-amber-100 text-amber-800 dark:bg-amber-950/60 dark:text-amber-300',
};

const groupOf = (finding: TycDimensionFinding): DimensionGroupKey => (
  finding.hit ? 'hit' : GROUP_BY_STATUS[finding.status]
);

const formatDateTime = (value: string) => new Date(value).toLocaleString('zh-CN', {hour12: false});

const DimensionRow = ({finding}: {readonly finding: TycDimensionFinding}) => {
  const group = groupOf(finding);
  return (
    <li className="rounded-xl border border-slate-200 bg-white p-3 dark:border-slate-700 dark:bg-slate-900">
      <div className="flex flex-wrap items-center gap-2">
        <span className="break-words text-sm font-bold text-[#101d28] dark:text-white">{finding.name}</span>
        <span className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] font-bold ${STATUS_CHIP_CLASS_BY_KEY[group]}`}>
          {STATUS_LABEL_BY_KEY[group]}
        </span>
        {finding.risk_level !== null && (
          <span className="shrink-0 rounded-full bg-amber-100 px-2 py-0.5 text-[11px] font-bold text-amber-800 dark:bg-amber-950/60 dark:text-amber-300">
            {finding.risk_level}
          </span>
        )}
        <span className="ml-auto min-w-0 break-all font-mono text-[11px] text-slate-400 dark:text-slate-500">{finding.key}</span>
      </div>
      <p data-testid="source-signal-dimension-summary" className="mt-1.5 whitespace-pre-wrap break-words text-balance text-sm leading-relaxed text-slate-700 dark:text-slate-300">{finding.summary}</p>
      {finding.evidence_refs.length > 0 && (
        <ul className="mt-2 flex flex-wrap gap-1.5" aria-label={`${finding.name}证据引用`}>
          {finding.evidence_refs.map((reference) => (
            <li key={reference} className="max-w-full break-words rounded-full bg-slate-100 px-2 py-0.5 text-[11px] text-slate-600 dark:bg-slate-800 dark:text-slate-300">
              {reference}
            </li>
          ))}
        </ul>
      )}
    </li>
  );
};

const ReportBody = ({report, truncated}: {readonly report: TycRiskReport; readonly truncated: boolean}) => (
  <div className="space-y-4">
    {truncated && (
      <p className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-200">
        原始报告过大，正文已截断；以下为已保留的报告内容。
      </p>
    )}
    <section aria-label="报告基本信息" className="rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-700 dark:bg-slate-900">
      <h3 className="text-sm font-bold text-[#101d28] dark:text-white">核查报告</h3>
      <dl className="mt-2 grid gap-2 text-xs sm:grid-cols-2">
        <div className="min-w-0"><dt className="font-bold text-[#727782] dark:text-slate-400">企业名称</dt><dd className="break-words text-[#101d28] dark:text-slate-200">{report.company_name}</dd></div>
        <div className="min-w-0"><dt className="font-bold text-[#727782] dark:text-slate-400">供应商编码</dt><dd className="break-all font-mono text-[#101d28] dark:text-slate-200">{report.supplier_code}</dd></div>
        <div className="min-w-0"><dt className="font-bold text-[#727782] dark:text-slate-400">统一社会信用代码</dt><dd className="break-all font-mono text-[#101d28] dark:text-slate-200">{report.credit_code ?? '未提供'}</dd></div>
        <div className="min-w-0"><dt className="font-bold text-[#727782] dark:text-slate-400">登记状态</dt><dd className="break-words text-[#101d28] dark:text-slate-200">{report.reg_status ?? '未提供'}</dd></div>
        <div className="min-w-0"><dt className="font-bold text-[#727782] dark:text-slate-400">报告周期</dt><dd className="break-all font-mono text-[#101d28] dark:text-slate-200">{report.period_key}</dd></div>
        <div className="min-w-0"><dt className="font-bold text-[#727782] dark:text-slate-400">生成时间</dt><dd className="text-[#101d28] dark:text-slate-200">{formatDateTime(report.generated_at)}</dd></div>
      </dl>
    </section>
    {GROUP_META.map(({key, title, icon: GroupIcon, headingClass}) => {
      const findings = report.dimensions.filter((finding) => groupOf(finding) === key);
      if (findings.length === 0) return null;
      return (
        <section key={key} data-testid={`source-signal-report-group-${key}`} aria-label={title}>
          <h3 className={`flex items-center gap-2 text-sm font-bold ${headingClass}`}>
            <GroupIcon aria-hidden="true" className="h-4 w-4 shrink-0" />
            {title}（{findings.length}）
          </h3>
          <ul className="mt-2 space-y-2">
            {findings.map((finding) => <DimensionRow key={finding.key} finding={finding} />)}
          </ul>
        </section>
      );
    })}
    <p data-testid="source-signal-report-attribution" className="flex flex-wrap items-center gap-1.5 border-t border-slate-200 pt-3 text-xs text-slate-600 dark:border-slate-700 dark:text-slate-400">
      <Database aria-hidden="true" className="h-3.5 w-3.5 shrink-0" />数据来源：天眼查
    </p>
  </div>
);

const NoReportBody = ({detail}: {readonly detail: SourceSignalDetailRead}) => (
  <div className="space-y-4">
    <div data-testid="source-signal-report-no-report" className="rounded-xl border border-slate-200 bg-slate-50 p-3.5 text-sm text-slate-700 dark:border-slate-700 dark:bg-slate-800/60 dark:text-slate-300">
      <p className="font-bold text-[#101d28] dark:text-white">该记录没有结构化多维度报告</p>
      <p className="mt-1 break-words">
        以下为该记录的完整正文{detail.report_truncated ? '；原始报告过大，正文已截断' : ''}。
      </p>
    </div>
    <section aria-label="完整正文" className="rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-700 dark:bg-slate-900">
      <h3 className="text-sm font-bold text-[#101d28] dark:text-white">完整正文</h3>
      <p className="mt-2 whitespace-pre-wrap break-words text-sm leading-relaxed text-slate-700 dark:text-slate-300">{detail.content}</p>
    </section>
  </div>
);

export const SourceSignalReportModal = ({sourceId, activeSignal, onClose, onRequestError}: SourceSignalReportModalProps) => {
  const isOpen = activeSignal !== null;
  const {dialogRef} = useDialogFocus({isOpen, onClose});
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const [detail, setDetail] = useState<SourceSignalDetailRead | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(false);
  const [retryKey, setRetryKey] = useState(0);
  const signalId = activeSignal?.id ?? null;

  useEffect(() => {
    if (!isOpen) return;
    closeButtonRef.current?.focus();
  }, [isOpen]);

  // 按需拉取详情；关闭或切换记录时中止旧请求，晚到的响应一律丢弃，避免覆盖新状态。
  useEffect(() => {
    if (signalId === null) return;
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    setDetail(null);
    void api.sourceSignalDetail(sourceId, signalId, controller.signal)
      .then((response) => {
        if (controller.signal.aborted) return;
        setDetail(response);
      })
      .catch((caught: unknown) => {
        if (controller.signal.aborted) return;
        const nextError = caught instanceof Error ? caught : new Error('完整报告加载失败');
        if (nextError instanceof ApiError && (nextError.status === 401 || nextError.status === 403)) {
          onRequestError(nextError);
        }
        setError(nextError);
      })
      .finally(() => {
        if (controller.signal.aborted) return;
        setLoading(false);
      });
    return () => controller.abort();
  }, [onRequestError, retryKey, signalId, sourceId]);

  if (activeSignal === null) return null;

  // 遮罩必须高于 MobileNav（z-50）与其余 z-50 弹层：移动端导航不得覆盖或截获点击。
  return (
    <div data-testid="source-signal-report-overlay" className="fixed inset-0 z-[60] flex items-center justify-center bg-slate-900/60 p-4 backdrop-blur-xs">
      <section
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="source-signal-report-title"
        className="flex max-h-[calc(100dvh-2rem)] w-full max-w-3xl flex-col overflow-hidden rounded-2xl border border-slate-200 bg-[#f7f9ff] shadow-2xl dark:border-slate-700 dark:bg-slate-900"
      >
        <header className="flex items-start justify-between gap-4 border-b border-slate-200 bg-white p-5 dark:border-slate-700 dark:bg-slate-950">
          <div className="min-w-0">
            <h2 id="source-signal-report-title" className="text-lg font-bold text-[#101d28] dark:text-white">采集记录完整报告</h2>
            <p className="mt-1 break-words text-sm text-[#424751] dark:text-slate-400">{activeSignal.title}</p>
          </div>
          <button
            ref={closeButtonRef}
            type="button"
            aria-label="关闭完整报告"
            onClick={onClose}
            className="flex min-h-11 min-w-11 shrink-0 items-center justify-center rounded-lg text-slate-500 hover:bg-slate-100 focus:outline-none focus:ring-2 focus:ring-[#007aff] dark:hover:bg-slate-800"
          >
            <X aria-hidden="true" className="h-5 w-5" />
          </button>
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto p-5">
          {loading && (
            <div role="status" className="flex min-h-[30vh] items-center justify-center gap-2 text-slate-600 dark:text-slate-300">
              <Loader2 aria-hidden="true" className="h-5 w-5 animate-spin" />
              <span>正在加载完整报告…</span>
            </div>
          )}
          {!loading && error !== null && (
            <div role="alert" className="rounded-xl border border-red-200 bg-[#ffdad6] p-4 text-sm text-[#93000a] dark:border-red-900 dark:bg-red-950/50 dark:text-red-200">
              <div className="flex items-start gap-2">
                <AlertTriangle aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0" />
                <p className="break-words font-bold">{error.message}</p>
              </div>
              <div className="mt-3 flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={() => setRetryKey((value) => value + 1)}
                  className="min-h-11 rounded-xl bg-[#007aff] px-4 text-sm font-bold text-white hover:bg-[#0062cc] focus:outline-none focus:ring-2 focus:ring-[#007aff] focus:ring-offset-2"
                >
                  重试
                </button>
                <button
                  type="button"
                  onClick={onClose}
                  className="min-h-11 rounded-xl border border-[#c2c6d2] px-4 text-sm font-bold text-[#424751] hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-[#007aff] dark:border-slate-600 dark:text-slate-200 dark:hover:bg-slate-800"
                >
                  关闭
                </button>
              </div>
            </div>
          )}
          {!loading && error === null && detail !== null && (
            detail.report !== null
              ? <ReportBody report={detail.report} truncated={detail.report_truncated} />
              : <NoReportBody detail={detail} />
          )}
        </div>
      </section>
    </div>
  );
};
