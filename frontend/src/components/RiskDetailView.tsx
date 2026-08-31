import {useCallback, useEffect, useRef, useState} from 'react';
import {ArrowLeft, Download, MessageCircleQuestion, RefreshCw, ShieldAlert} from 'lucide-react';
import {ApiError, api, mapRiskAlert, type EventDetailRead, type RiskAlertRead} from '../api';
import type {RiskItem} from '../types';
import {RiskDetailEvidenceSections} from './RiskDetailEvidenceSections';

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

  if (alertLoading) return <DetailState title="正在加载风险提醒详情" detail="正在取得提醒记录与对应事件证据。" />;

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
      <header className="flex flex-col gap-4 rounded-2xl border border-slate-200/80 bg-white/80 p-5 shadow-sm backdrop-blur-md dark:border-slate-800 dark:bg-slate-950/80 md:flex-row md:items-start md:justify-between">
        <div className="min-w-0">
          <button type="button" onClick={onClose} className="mb-3 inline-flex min-h-11 items-center gap-1.5 text-[13px] font-bold text-[#004782] hover:underline dark:text-blue-400">
            <ArrowLeft className="h-4 w-4" /> 返回风险列表
          </button>
          <div className="flex flex-wrap items-center gap-2">
            <span className={`rounded-md px-2.5 py-0.5 text-[12px] font-bold text-white ${levelClassName[alert.level]}`}>{alert.level} {levelName[alert.level]}</span>
            <span className="rounded-full border border-[#185fa5]/30 bg-[#185fa5]/5 px-2 py-0.5 text-[11px] font-semibold text-[#004782] dark:border-blue-400/30 dark:bg-blue-400/10 dark:text-blue-300">{alert.status === 'current' ? '当前有效' : '已失效'}</span>
          </div>
          <h1 className="mt-3 text-xl font-black tracking-tight text-slate-900 dark:text-white lg:text-2xl">{alert.supplier_name}</h1>
          <p className="mt-2 max-w-3xl text-sm leading-relaxed text-slate-600 dark:text-slate-300">{alert.event_summary}</p>
        </div>
        <div className="flex flex-wrap gap-2 md:justify-end">
          <button type="button" onClick={() => onAskAssistant(`请分析供应商【${alert.supplier_name}】的风险提醒，并说明当前证据与评分依据。`)} className="inline-flex min-h-11 items-center gap-1.5 rounded-lg border border-[#004782]/30 bg-[#ecf4ff] px-3.5 py-2 text-[13px] font-bold text-[#004782] hover:bg-[#d6e4f3] dark:bg-blue-950/80 dark:text-blue-300">
            <MessageCircleQuestion className="h-4 w-4" /> 询问风险助手
          </button>
          <button type="button" onClick={() => onExportReport(mappedRisk)} className="inline-flex min-h-11 items-center gap-1.5 rounded-lg bg-[#004782] px-3.5 py-2 text-[13px] font-bold text-white hover:bg-[#185fa5]">
            <Download className="h-4 w-4" /> 导出报告
          </button>
        </div>
      </header>

      <section className="rounded-2xl border border-slate-200/80 bg-white/80 p-5 shadow-sm backdrop-blur-md dark:border-slate-800 dark:bg-slate-950/80">
        <div className="flex items-center gap-3">
          <span className={`flex h-12 w-12 items-center justify-center rounded-xl text-lg font-black text-white ${levelClassName[alert.level]}`}>{alert.level}</span>
          <div>
            <p className="text-xs font-semibold text-slate-500 dark:text-slate-400">综合风险评分</p>
            <p className="font-mono text-2xl font-black text-slate-900 dark:text-white">{alert.score}<span className="ml-1 text-sm text-slate-400">/ 100</span></p>
          </div>
        </div>
      </section>

      {eventLoading && <DetailState title="正在加载事件证据" detail="提醒详情已加载，正在取得事件、信号、主体与地点证据。" />}
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
