import {useCallback, useEffect, useMemo, useRef, useState} from 'react';
import {ArrowLeft, Download, MessageCircleQuestion, RefreshCw, ShieldAlert} from 'lucide-react';
import {ApiError, api, mapRiskAlert, type EventDetailRead, type RiskAlertRead} from '../api';
import type {RiskItem} from '../types';
import {displayStatus, parseGanttTime, toGanttMember, type GanttDisplayStatus} from './EventValidityGantt';
import {RiskDetailEvidenceSections} from './RiskDetailEvidenceSections';
import {ValidityStateBadge} from './ValidityStateBadge';

interface RiskDetailViewProps {
  readonly alertId: string;
  readonly onAskAssistant: (query: string) => void;
  readonly onClose: () => void;
  readonly onExportReport: (risk: RiskItem) => void;
  readonly onRequestError: (error: ApiError) => void;
}

const levelClassName: Record<RiskAlertRead['level'], string> = {
  P1: 'bg-[#C92A2A]', P2: 'bg-[#D97706]', P3: 'bg-[#2563EB]', P4: 'bg-[#64748B]',
};

const levelName: Record<RiskAlertRead['level'], string> = {
  P1: '重大风险', P2: '高风险', P3: '中风险', P4: '低风险',
};

const errorMessage = (caught: unknown, fallback: string): string => caught instanceof Error ? caught.message : fallback;

const formatDateTime = (value: string | null) => (
  value ? new Date(value).toLocaleString('zh-CN', {hour12: false}) : '未提供'
);

/** 同组事件的状态徽标：有效 / 已过期 / 已被新版本替代。 */
const MEMBER_STATUS_META: Record<GanttDisplayStatus, {label: string; className: string}> = {
  current: {label: '有效', className: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300'},
  expired: {label: '已过期', className: 'bg-slate-200 text-slate-600 dark:bg-slate-700 dark:text-slate-300'},
  superseded: {label: '已被新版本替代', className: 'bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300'},
};

const DetailState = ({title, detail, action}: {readonly title: string; readonly detail: string; readonly action?: React.ReactNode}) => (
  <section className="mx-auto flex min-h-[50vh] max-w-xl flex-col items-start justify-center gap-3 rounded-2xl border border-slate-200/80 bg-white/80 p-6 shadow-sm dark:border-slate-700/60 dark:bg-slate-800/60" role="status">
    <h1 className="text-xl font-black tracking-tight text-slate-900 dark:text-white">{title}</h1>
    <p className="text-sm leading-relaxed text-slate-600 dark:text-slate-300">{detail}</p>
    {action}
  </section>
);

export const RiskDetailView = ({alertId, onAskAssistant, onClose, onExportReport, onRequestError}: RiskDetailViewProps) => {
  const requestSequence = useRef(0);
  const [alert, setAlert] = useState<RiskAlertRead | null>(null);
  const [event, setEvent] = useState<EventDetailRead | null>(null);
  const [alertLoading, setAlertLoading] = useState(true);
  const [eventLoading, setEventLoading] = useState(false);
  const [alertError, setAlertError] = useState<ApiError | string | null>(null);
  const [eventError, setEventError] = useState<string | null>(null);

  const loadEvent = useCallback(async (eventId: number, sequence: number) => {
    setEventLoading(true);
    setEventError(null);
    try {
      const nextEvent = await api.event(eventId);
      if (requestSequence.current === sequence) setEvent(nextEvent);
    } catch (caught) {
      if (requestSequence.current !== sequence) return;
      if (caught instanceof ApiError && (caught.status === 401 || caught.status === 403)) onRequestError(caught);
      setEventError(errorMessage(caught, '事件详情加载失败'));
    } finally {
      if (requestSequence.current === sequence) setEventLoading(false);
    }
  }, [onRequestError]);

  useEffect(() => {
    const sequence = requestSequence.current + 1;
    requestSequence.current = sequence;
    setAlert(null);
    setEvent(null);
    setAlertLoading(true);
    setEventLoading(false);
    setAlertError(null);
    setEventError(null);
    const numericAlertId = Number(alertId);
    if (!Number.isInteger(numericAlertId) || numericAlertId < 1) {
      setAlertLoading(false);
      setAlertError('风险提醒编号无效');
      return undefined;
    }
    void (async () => {
      try {
        const nextAlert = await api.alert(numericAlertId);
        if (requestSequence.current !== sequence) return;
        setAlert(nextAlert);
        setAlertLoading(false);
        await loadEvent(nextAlert.event_id, sequence);
      } catch (caught) {
        if (requestSequence.current !== sequence) return;
        if (caught instanceof ApiError && (caught.status === 401 || caught.status === 403)) onRequestError(caught);
        setAlertError(caught instanceof ApiError ? caught : errorMessage(caught, '风险提醒加载失败'));
        setAlertLoading(false);
      }
    })();
    return () => {
      if (requestSequence.current === sequence) requestSequence.current += 1;
    };
  }, [alertId, loadEvent, onRequestError]);

  const retryEvent = () => {
    if (alert === null) return;
    const sequence = requestSequence.current + 1;
    requestSequence.current = sequence;
    void loadEvent(alert.event_id, sequence);
  };

  // 组内事件的有效期增量字段是可选的（后端未部署时缺失）：统一走松散归一，缺失即降级。
  const groupMembers = useMemo(
    () => (alert?.merged_events ?? []).map((item) => toGanttMember(item)),
    [alert],
  );

  // 组内出现多个不同截止时间时，代表事件的有效期信息补一条范围。
  const groupValidityRange = useMemo(() => {
    if (alert === null) return null;
    const timestamps = new Set<number>();
    const selfExpiry = parseGanttTime(alert.valid_until);
    if (selfExpiry !== null) timestamps.add(selfExpiry.getTime());
    for (const member of groupMembers) {
      const expiry = parseGanttTime(member?.expiresAt);
      if (expiry !== null) timestamps.add(expiry.getTime());
    }
    if (timestamps.size < 2) return null;
    const sorted = [...timestamps].sort((left, right) => left - right);
    return `${formatDateTime(new Date(sorted[0]).toISOString())} ~ ${formatDateTime(new Date(sorted[sorted.length - 1]).toISOString())}`;
  }, [alert, groupMembers]);

  if (alertLoading) {
    return (
      <section className="mx-auto flex min-h-[50vh] max-w-xl flex-col items-center justify-center gap-3" role="status">
        <span className="sr-only">正在加载风险提醒详情</span>
      </section>
    );
  }

  if (alertError !== null) {
    const isNotFound = alertError instanceof ApiError && alertError.status === 404;
    return (
      <DetailState
        title={isNotFound ? '风险提醒不存在' : '风险提醒加载失败'}
        detail={isNotFound ? `编号为 ${alertId} 的风险提醒不存在或已被删除。` : errorMessage(alertError, '无法加载风险提醒详情。')}
        action={<button type="button" onClick={onClose} className="rounded-lg border border-[#004782] px-3.5 py-2 text-[13px] font-bold text-[#004782] hover:bg-blue-50 dark:text-blue-400">返回风险列表</button>}
      />
    );
  }

  if (alert === null) return null;

  const mappedRisk = mapRiskAlert(alert);
  return (
    <div className="space-y-5 pb-20 lg:pb-8">
      <header aria-label="风险概览" className="flex flex-col gap-4 rounded-2xl border border-slate-200/80 bg-white/80 p-5 shadow-sm backdrop-blur-md dark:border-slate-800 dark:bg-slate-950/80">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <button type="button" onClick={onClose} className="inline-flex min-h-11 items-center gap-1.5 text-[13px] font-bold text-[#004782] hover:underline dark:text-blue-400">
            <ArrowLeft className="h-4 w-4" /> 返回风险列表
          </button>
          <div className="flex flex-wrap gap-2">
            <button type="button" onClick={() => onAskAssistant(`请分析供应商【${alert.supplier_name}】的风险提醒，并说明当前证据与评分依据。`)} className="inline-flex min-h-11 items-center gap-1.5 rounded-lg border border-[#004782]/30 bg-[#ecf4ff] px-3.5 py-2 text-[13px] font-bold text-[#004782] hover:bg-[#d6e4f3] dark:bg-blue-950/80 dark:text-blue-300">
              <MessageCircleQuestion className="h-4 w-4" /> 询问风险助手
            </button>
            <button type="button" onClick={() => onExportReport(mappedRisk)} className="inline-flex min-h-11 items-center gap-1.5 rounded-lg bg-[#004782] px-3.5 py-2 text-[13px] font-bold text-white hover:bg-[#185fa5]">
              <Download className="h-4 w-4" /> 导出报告
            </button>
          </div>
        </div>

        <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-start lg:gap-x-6">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <span className={`rounded-md px-2.5 py-0.5 text-[12px] font-bold text-white ${levelClassName[alert.level]}`}>{alert.level} {levelName[alert.level]}</span>
              <span className="rounded-full border border-[#185fa5]/30 bg-[#185fa5]/5 px-2 py-0.5 text-[11px] font-semibold text-[#004782] dark:border-blue-400/30 dark:bg-blue-400/10 dark:text-blue-300">{alert.status === 'current' ? '当前有效' : '已失效'}</span>
              <ValidityStateBadge validityState={alert.validity_state} processingState={alert.processing_state} />
            </div>
            <h1 className="mt-3 text-xl font-black tracking-tight text-slate-900 dark:text-white lg:text-2xl">{alert.supplier_name}</h1>
          </div>
          <p className="flex items-baseline gap-1.5 lg:justify-end">
            <span className="text-xs font-semibold text-slate-500 dark:text-slate-400">综合风险评分</span>
            <span className="font-mono text-2xl font-black text-slate-900 dark:text-white">{alert.score}</span>
            <span className="font-mono text-sm text-slate-400">/ 100</span>
          </p>
          <p className="w-full min-w-0 break-words text-sm leading-relaxed text-slate-600 lg:col-span-2 dark:text-slate-300">{alert.event_summary}</p>
        </div>

        <div aria-label="有效期信息" className="border-t border-slate-200/80 pt-3 dark:border-slate-800">
          <h2 className="text-[11px] font-semibold text-slate-500 dark:text-slate-400">有效期信息</h2>
          <dl className="mt-2 grid gap-3 text-xs text-slate-600 sm:grid-cols-2 dark:text-slate-400">
            <div className="min-w-0"><dt className="font-bold">有效期截止</dt><dd className="mt-0.5 break-words">{formatDateTime(alert.valid_until)}</dd></div>
            <div className="min-w-0"><dt className="font-bold">复核时间</dt><dd className="mt-0.5 break-words">{formatDateTime(alert.review_due_at)}</dd></div>
            {groupValidityRange !== null && (
              <div className="min-w-0 sm:col-span-2">
                <dt className="font-bold">组内有效期范围</dt>
                <dd className="mt-0.5 break-words font-mono">{groupValidityRange}</dd>
              </div>
            )}
          </dl>
        </div>
      </header>

      {(alert.merged_count ?? 1) > 1 && (
        <section
          aria-label="合并显示的事件"
          className="rounded-2xl border border-slate-200/80 bg-white/80 p-5 shadow-sm dark:border-slate-800 dark:bg-slate-950/80"
        >
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <h2 className="text-sm font-bold text-slate-900 dark:text-white">
              同组事件 {alert.merged_count} 个
            </h2>
            <p className="text-[11px] text-slate-500 dark:text-slate-400">
              同供应商、同区域、时间重叠且事件类型相同 → 合并展示，等级取最高
            </p>
          </div>
          <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
            当前页为等级最高的代表事件；以下为同组其余事件，事件与提醒均完整保留。
          </p>
          <ul className="mt-3 space-y-2">
            {(alert.merged_events ?? []).map((item, index) => {
              const member = groupMembers[index] ?? null;
              const status = member === null ? null : displayStatus(member);
              const statusMeta = status === null ? null : MEMBER_STATUS_META[status];
              return (
                <li
                  key={item.alert_id}
                  className="flex flex-col gap-1 rounded-xl border border-slate-200/70 bg-slate-50/60 p-3 dark:border-slate-800 dark:bg-slate-900/60"
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <span className={`rounded px-1.5 py-0.5 text-[11px] font-bold text-white ${levelClassName[item.level]}`}>
                      {item.level}
                    </span>
                    <span className="text-[11px] font-semibold text-slate-500 dark:text-slate-400">
                      {item.source_title}
                    </span>
                    <span className="font-mono text-[11px] text-slate-400">
                      {formatDateTime(item.published_at)}
                    </span>
                  </div>
                  <p className="text-xs leading-relaxed text-slate-600 dark:text-slate-300">
                    {item.event_summary}
                  </p>
                  <div className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-slate-500 dark:text-slate-400">
                    {statusMeta !== null && (
                      <span className={`rounded-full px-2 py-0.5 text-[10px] font-bold ${statusMeta.className}`}>
                        {statusMeta.label}
                      </span>
                    )}
                    <span>
                      有效期截止 <span className="font-mono">{formatDateTime(member?.expiresAt ?? null)}</span>
                    </span>
                    {typeof member?.affectedSupplierCount === 'number' && member.affectedSupplierCount > 0 && (
                      <span>影响供应商 <span className="font-mono">{member.affectedSupplierCount}</span> 家</span>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        </section>
      )}

      {eventLoading && (
        <section className="flex items-center gap-2 rounded-2xl border border-slate-200/80 bg-white/80 p-5 shadow-sm dark:border-slate-800 dark:bg-slate-950/80" role="status">
          <span className="sr-only">正在加载事件证据</span>
        </section>
      )}
      {eventError !== null && (
        <section className="flex flex-col items-start gap-3 rounded-2xl border border-red-200 bg-red-50 p-5 dark:border-red-900/60 dark:bg-red-950/30" role="alert">
          <div className="flex items-center gap-2 text-red-700 dark:text-red-300"><ShieldAlert className="h-5 w-5" /><h2 className="font-bold">事件详情加载失败</h2></div>
          <p className="text-sm text-red-700 dark:text-red-300">{eventError}</p>
          <button type="button" onClick={retryEvent} className="inline-flex min-h-11 items-center gap-1.5 rounded-lg border border-red-300 px-3.5 py-2 text-[13px] font-bold text-red-700 hover:bg-red-100 dark:border-red-800 dark:text-red-300">
            <RefreshCw className="h-4 w-4" /> 重试事件详情
          </button>
        </section>
      )}
      {event !== null && <RiskDetailEvidenceSections alert={alert} event={event} />}
    </div>
  );
};
