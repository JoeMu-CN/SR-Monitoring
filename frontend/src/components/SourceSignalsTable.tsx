import {useState} from 'react';
import {ExternalLink, FileText} from 'lucide-react';
import type {SourceSignalListResponse, SourceSignalRead, ValidityAnchorSource, ValidityReasonRead} from '../api';
import {CellTextPopover} from './CellTextPopover';
import {SignalSummaryText} from './SignalSummaryText';
import type {ActiveSourceSignal} from './SourceSignalReportModal';
import {TableColumnResizer} from './TableColumnResizer';
import {ValidityStateBadge} from './ValidityStateBadge';

/** 只有天眼查记录带结构化报告，其余信息源不渲染报告列。 */
const TYC_SOURCE_CODE = 'tianyancha';

type ColumnKey = 'index' | 'title' | 'state' | 'processing' | 'published' | 'collected' | 'validUntil' | 'reviewDue'
  | 'external' | 'reason' | 'summary' | 'source' | 'report';

/** 状态、原文、报告列没有可展开的完整值，序号是行内计算值，因此都不参与浮层。 */
type OverlayKind = Exclude<ColumnKey, 'index' | 'state' | 'processing' | 'source' | 'report'>;

const COLUMN_LABELS: Readonly<Record<ColumnKey, string>> = {
  index: '序号',
  title: '标题', state: '状态', processing: '处理状态',
  published: '发布时间', collected: '采集时间', validUntil: '有效期截止', reviewDue: '复核时间',
  external: '外部编号', reason: '有效期原因', summary: '摘要 / 内容',
  source: '原文', report: '报告',
};

const BASE_COLUMN_ORDER: readonly ColumnKey[] = [
  'index', 'title', 'state', 'processing', 'published', 'collected', 'validUntil', 'reviewDue',
  'external', 'reason', 'summary', 'source',
];

const INITIAL_WIDTHS: Readonly<Record<ColumnKey, number>> = {
  index: 80,
  title: 280, state: 90, processing: 170,
  published: 170, collected: 170, validUntil: 170, reviewDue: 170,
  external: 220, reason: 280, summary: 360,
  source: 100, report: 120,
};

/** 未选中行是浅蓝悬浮态（沿用信息源总览表格行的既有 token），选中后换成更明显的一档。 */
const ROW_BASE = 'h-10 cursor-pointer transition-colors';
const ROW_HOVER = 'hover:bg-[#185fa5]/5 dark:hover:bg-slate-700/40';
const ROW_SELECTED = 'bg-blue-100/70 dark:bg-blue-900/50';

const formatDateTime = (value: string | null) => (value ? new Date(value).toLocaleString('zh-CN', {hour12: false}) : '未提供');

const safeSourceUrl = (value: string | null) => (value !== null && /^https?:\/\//i.test(value) ? value : null);

const summaryOf = (signal: SourceSignalRead) => (signal.summary.trim() ? signal.summary : '暂无摘要');

/* ── 局部中文映射：只覆盖常见枚举，未知值一律保留原文兜底 ─────────────── */

const VALIDITY_REASON_LABELS: Record<string, string> = {
  active: '有效',
  effective_signal_support: '存在有效信号支撑', no_effective_signal_support: '无有效信号支撑',
  deadline_reached: '已到有效期截止', classification_failed: '分类失败待复核',
  classification_resolved: '分类已确认', pending_classification: '等待分类',
  anchor_fallback: '未提供发布时间，改用采集时间计算', policy_resolved: '已按有效期规则计算',
  superseded_by_newer_version: '被更新版本取代', same_authority_time_conflict: '权威时间冲突',
  quarantine_promoted: '隔离后恢复采集', two_consecutive_misses: '连续两次未命中',
  filtered_out: '已过滤',
};

/** legacy 不展示时间依据，因此这里只覆盖实际会渲染的四种时间依据。 */
const ANCHOR_SOURCE_LABELS: Record<Exclude<ValidityAnchorSource, 'legacy'>, string> = {
  published_at: '发布时间', collected_at: '采集时间',
  official_valid_until: '官方有效期截止时间', event_end: '事件结束时间',
};

/**
 * 常见原因取易懂中文，未知原因保留后端原文；时间依据统一说明“依据哪个时间算的”。
 * 行情指标处于观察点积累期时，`filtered_out` 的细节是 market_indicator/accumulating，
 * 这里必须表达为「数据积累中」而不是「已过滤/不相关」。
 */
const formatValidityReason = ({code, anchor_source, details}: ValidityReasonRead): string => {
  if (code === 'filtered_out' && details.filter === 'market_indicator' && details.reason === 'accumulating') {
    return '数据积累中';
  }
  const reason = VALIDITY_REASON_LABELS[code] ?? code;
  // legacy 记录没有时间依据；anchor_fallback 文案已自带计算依据，重复追加会变成两句话说同一件事。
  if (anchor_source === 'legacy' || code === 'anchor_fallback') return reason;
  return `${reason}（以${ANCHOR_SOURCE_LABELS[anchor_source]}为依据）`;
};

/** 单元格与浮层共用同一取值：四个时间字段各自独立，缺失即“未提供”，绝不互相回退。 */
const cellTextOf = (kind: OverlayKind, signal: SourceSignalRead): string => {
  switch (kind) {
    case 'title': return signal.title;
    case 'external': return signal.external_id ?? '未提供';
    case 'reason': return formatValidityReason(signal.validity_reason);
    case 'published': return formatDateTime(signal.published_at);
    case 'collected': return formatDateTime(signal.collected_at);
    case 'validUntil': return formatDateTime(signal.valid_until);
    case 'reviewDue': return formatDateTime(signal.review_due_at);
    case 'summary': return summaryOf(signal);
  }
};

const OverlayBody = ({kind, signal}: {readonly kind: OverlayKind; readonly signal: SourceSignalRead}) => {
  if (kind === 'summary') {
    return (
      <p className="whitespace-pre-wrap break-words text-[13px] leading-relaxed text-slate-700 dark:text-slate-300">
        <SignalSummaryText text={summaryOf(signal)} />
      </p>
    );
  }
  return <p className="break-words font-mono text-[13px] leading-relaxed text-slate-700 dark:text-slate-300">{cellTextOf(kind, signal)}</p>;
};

interface TextCellProps {
  readonly kind: OverlayKind;
  readonly signal: SourceSignalRead;
  readonly testId?: string;
  readonly monospace?: boolean;
  readonly onOpen: (trigger: HTMLElement) => void;
}

const TextCell = ({kind, signal, testId, monospace = false, onOpen}: TextCellProps) => (
  <td data-testid={testId} className="px-2.5 py-1 align-middle">
    <button
      type="button"
      aria-label={COLUMN_LABELS[kind]}
      aria-haspopup="dialog"
      onClick={(event) => onOpen(event.currentTarget)}
      className={`block w-full truncate text-left text-[13px] text-slate-700 hover:underline focus:outline-none focus:ring-2 focus:ring-[#007aff] dark:text-slate-300 ${monospace ? 'font-mono' : ''}`}
    >
      {cellTextOf(kind, signal)}
    </button>
  </td>
);

interface SourceSignalsTableProps {
  readonly source: SourceSignalListResponse['source'];
  readonly items: readonly SourceSignalRead[];
  /** 当前页在后端结果集中的起始下标，序号在此基础上连续编号，跨页不重排。 */
  readonly offset: number;
  readonly onOpenReport: (signal: ActiveSourceSignal) => void;
}

interface ActiveCellOverlay {
  readonly kind: OverlayKind;
  readonly signal: SourceSignalRead;
  readonly trigger: HTMLElement;
}

/**
 * 紧凑采集记录表格：固定行高、整列不换行省略，被省略的文本点击后在锚定浮层展示完整内容。
 * 列宽可用列把手拖拽或键盘左右键调整；拖拽只由列把手自身发起，不会触发单元格浮层。
 * 首列序号按 `offset + 行序` 连续编号，只反映后端返回顺序，前端不自行排序。
 * 整行可点选：单选，选中态由 data-selected 暴露；父级每次重新取数都会卸载本组件，
 * 因此翻页、切换记录范围或信息源后选中态自然清空，也不会把选中行写进地址栏。
 */
export const SourceSignalsTable = ({source, items, offset, onOpenReport}: SourceSignalsTableProps) => {
  const showsReport = source.code === TYC_SOURCE_CODE;
  const columns = showsReport ? [...BASE_COLUMN_ORDER, 'report' as const] : BASE_COLUMN_ORDER;
  const [widths, setWidths] = useState(INITIAL_WIDTHS);
  const [overlay, setOverlay] = useState<ActiveCellOverlay | null>(null);
  const [selectedId, setSelectedId] = useState<number | null>(null);

  const dismissOverlay = () => setOverlay(null);
  const closeOverlay = () => {
    overlay?.trigger.focus();
    setOverlay(null);
  };
  const openOverlay = (kind: OverlayKind, signal: SourceSignalRead, trigger: HTMLElement) => setOverlay({kind, signal, trigger});
  const totalWidth = columns.reduce((sum, column) => sum + widths[column], 0);

  return (
    <div className="overflow-x-auto" onScroll={dismissOverlay}>
      <table aria-label="采集记录" className="border-collapse text-left" style={{width: totalWidth, tableLayout: 'fixed'}}>
        <thead className="bg-slate-100/70 text-[11px] font-bold text-slate-600 dark:bg-slate-800/80 dark:text-slate-300">
          <tr>
            {columns.map((column) => (
              <th
                key={column}
                scope="col"
                aria-label={COLUMN_LABELS[column]}
                style={{width: widths[column]}}
                className="relative border-b border-slate-200 px-2.5 py-2 font-bold dark:border-slate-800"
              >
                <span className="block truncate">{COLUMN_LABELS[column]}</span>
                <TableColumnResizer label={COLUMN_LABELS[column]} width={widths[column]} onResize={(width) => setWidths((current) => ({...current, [column]: width}))} />
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-200/80 dark:divide-slate-700/60">
          {items.map((signal, position) => {
            const sourceUrl = safeSourceUrl(signal.url);
            const selected = selectedId === signal.id;
            const cell = (kind: OverlayKind, testId?: string, monospace?: boolean) => (
              <TextCell key={kind} kind={kind} signal={signal} testId={testId} monospace={monospace} onOpen={(trigger) => openOverlay(kind, signal, trigger)} />
            );
            return (
              // 行本身保持 table 语义（不加 role=button）；点选只换背景，选中行不覆盖悬浮色。
              <tr
                key={signal.id}
                data-selected={selected}
                onClick={() => setSelectedId(signal.id)}
                className={`${ROW_BASE} ${selected ? ROW_SELECTED : ROW_HOVER}`}
              >
                <td className="px-2.5 py-1 align-middle">
                  <span className="block truncate text-center font-mono text-[13px] text-slate-500 dark:text-slate-400">{offset + position + 1}</span>
                </td>
                {cell('title')}
                <td className="px-2.5 py-1 align-middle">
                  <span className="block truncate">
                    {signal.validity_state === null
                      ? <span className="text-[13px] text-slate-400">—</span>
                      : <ValidityStateBadge validityState={signal.validity_state} />}
                  </span>
                </td>
                <td className="px-2.5 py-1 align-middle">
                  <span className="block truncate">
                    {signal.processing_state === 'analyzed'
                      ? <span className="text-[13px] text-slate-400">—</span>
                      : <ValidityStateBadge processingState={signal.processing_state} />}
                  </span>
                </td>
                {cell('published')}
                {cell('collected')}
                {cell('validUntil')}
                {cell('reviewDue')}
                {cell('external', undefined, true)}
                {cell('reason', `source-signal-reason-${signal.id}`)}
                {cell('summary', `source-signal-summary-${signal.id}`)}
                <td className="px-2.5 py-1 align-middle">
                  <span className="block truncate">
                    {sourceUrl === null ? (
                      <span className="text-[13px] text-slate-400">—</span>
                    ) : (
                      <a href={sourceUrl} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-[12px] font-bold text-[#007aff] hover:underline">
                        原文<ExternalLink aria-hidden="true" className="h-3.5 w-3.5" />
                      </a>
                    )}
                  </span>
                </td>
                {showsReport && (
                  <td className="px-2.5 py-1 align-middle">
                    <span className="block truncate">
                      <button
                        type="button"
                        onClick={() => onOpenReport({id: signal.id, title: signal.title})}
                        aria-label={`查看完整报告：${signal.title}`}
                        className="inline-flex items-center gap-1 text-[12px] font-bold text-[#007aff] hover:underline focus:outline-none focus:ring-2 focus:ring-[#007aff]"
                      >
                        <FileText aria-hidden="true" className="h-3.5 w-3.5" />报告
                      </button>
                    </span>
                  </td>
                )}
              </tr>
            );
          })}
        </tbody>
      </table>

      {overlay !== null && (
        <CellTextPopover anchor={overlay.trigger} title={COLUMN_LABELS[overlay.kind]} onClose={closeOverlay}>
          <OverlayBody kind={overlay.kind} signal={overlay.signal} />
        </CellTextPopover>
      )}
    </div>
  );
};