import {useEffect, useState} from 'react';
import {useParams} from 'react-router-dom';
import {AlertTriangle, ArrowLeft, Building2, CalendarClock, ChevronRight, MapPin, RefreshCw} from 'lucide-react';
import {
  api,
  ApiError,
  EVENT_TYPE_LABELS,
  formatValidityState,
  type EventDetailRead,
  type EventSupplierAlert,
} from '../api';
import type {RiskLevel} from '../types';

interface EventDetailViewProps {
  readonly onBack: () => void;
  /** 跳到供应商维度的提醒详情 /risks/:alertId。 */
  readonly onSelectAlert: (alertId: string) => void;
  /** 401/403 等权限类错误交由 App 统一处理；其余错误页面内自渲染。 */
  readonly onRequestError?: (error: ApiError) => void;
}

const LEVEL_BADGE: Record<RiskLevel, string> = {
  P1: 'bg-[#C92A2A]',
  P2: 'bg-[#D97706]',
  P3: 'bg-[#2563EB]',
  P4: 'bg-[#64748B]',
};

const VALIDITY_BADGE_CLASS: Record<string, string> = {
  active: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300',
  expired: 'bg-slate-200 text-slate-700 dark:bg-slate-700 dark:text-slate-300',
  revoked: 'bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300',
  superseded: 'bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300',
  conflicted: 'bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-300',
  legacy: 'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300',
};

const formatMoment = (value: string | null | undefined): string =>
  value ? new Date(value).toLocaleString('zh-CN', {hour12: false}) : '未披露';

export const EventDetailView = ({onBack, onSelectAlert, onRequestError}: EventDetailViewProps) => {
  const {eventId} = useParams();
  const numericId = Number(eventId);
  const validId = Number.isInteger(numericId) && numericId > 0;
  const [event, setEvent] = useState<EventDetailRead | null>(null);
  const [loading, setLoading] = useState(validId);
  const [error, setError] = useState<string | null>(validId ? null : '事件编号无效');
  const [reloadToken, setReloadToken] = useState(0);

  useEffect(() => {
    if (!validId) return;
    let cancelled = false;
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    api.eventDetail(numericId)
      .then((detail) => { if (!cancelled) setEvent(detail); })
      .catch((caught) => {
        if (cancelled) return;
        if (caught instanceof ApiError) {
          onRequestError?.(caught);
          // 401/403 已由 App 统一处理（登出或全局横幅），页面内不再重复提示。
          if (caught.status === 401 || caught.status === 403) return;
        }
        setError(caught instanceof Error ? caught.message : '事件详情加载失败');
      })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; controller.abort(); };
  }, [numericId, validId, reloadToken, onRequestError]);

  const backButton = (
    <button
      type="button"
      onClick={onBack}
      className="inline-flex items-center gap-1.5 rounded-xl border border-slate-200/80 bg-white px-3 py-1.5 text-[12px] font-semibold text-slate-700 transition hover:bg-slate-100 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
    >
      <ArrowLeft className="h-3.5 w-3.5" />
      返回风险事件
    </button>
  );

  if (loading) {
    return (
      <div className="space-y-4 pb-20">
        {backButton}
        <section className="rounded-2xl border border-slate-200/80 bg-white/80 p-12 text-center shadow-sm dark:border-slate-700/60 dark:bg-slate-800/60">
          <p className="text-[13px] text-slate-500">正在加载事件详情…</p>
        </section>
      </div>
    );
  }

  if (error || !event) {
    return (
      <div className="space-y-4 pb-20">
        {backButton}
        <section className="rounded-2xl border border-[#ba1a1a]/40 bg-[#ffdad6]/60 p-8 text-center dark:border-red-500/40 dark:bg-red-950/30">
          <AlertTriangle className="mx-auto h-8 w-8 text-[#93000a] dark:text-red-300" />
          <h1 className="mt-3 text-[15px] font-bold text-[#93000a] dark:text-red-200">事件详情不可用</h1>
          <p className="mt-1 text-xs text-[#93000a]/80 dark:text-red-300/80">{error ?? '事件不存在或已被删除'}</p>
          {validId && (
            <button
              type="button"
              onClick={() => setReloadToken((token) => token + 1)}
              className="mt-4 inline-flex items-center gap-1.5 rounded-xl border border-slate-200/80 bg-white px-3 py-1.5 text-[12px] font-semibold text-slate-700 transition hover:bg-slate-100 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
            >
              <RefreshCw className="h-3.5 w-3.5" />
              重新加载
            </button>
          )}
        </section>
      </div>
    );
  }

  const regionText = event.locations
    .map((location) => location.name || [location.region, location.city].filter(Boolean).join(' '))
    .filter(Boolean)
    .join('、');

  return (
    <div className="space-y-4 pb-20 sm:space-y-5 lg:pb-8">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-black tracking-tight text-slate-900 dark:text-white lg:text-2xl">事件详情</h1>
        {backButton}
      </div>

      <section className="space-y-3 rounded-2xl border border-slate-200/80 bg-white/80 p-4 shadow-sm backdrop-blur-md dark:border-slate-700/60 dark:bg-slate-800/60">
        <div className="flex flex-wrap items-center gap-2">
          <span className="rounded-full border border-slate-200 bg-slate-50 px-2 py-0.5 text-[10px] font-medium text-[#185fa5] dark:border-slate-700 dark:bg-slate-900 dark:text-blue-300">
            {EVENT_TYPE_LABELS[event.event_type] ?? event.event_type}
          </span>
          {event.event_subtype && (
            <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] text-slate-600 dark:bg-slate-900 dark:text-slate-300">
              {EVENT_TYPE_LABELS[event.event_subtype] ?? event.event_subtype}
            </span>
          )}
          <ValidityBadge state={event.validity_state} />
        </div>
        <p className="text-[15px] font-semibold leading-relaxed text-slate-900 dark:text-white">{event.summary}</p>
        <dl className="grid grid-cols-1 gap-x-6 gap-y-2 border-t border-slate-100 pt-3 text-[11px] text-slate-500 dark:border-slate-800 sm:grid-cols-2">
          <div className="flex items-center gap-1.5">
            <MapPin className="h-3.5 w-3.5" />
            <dt className="font-bold">地区</dt>
            <dd>{regionText || '未披露'}</dd>
          </div>
          <div className="flex items-center gap-1.5">
            <CalendarClock className="h-3.5 w-3.5" />
            <dt className="font-bold">事件窗口</dt>
            <dd>{formatMoment(event.start_at)} ~ {formatMoment(event.end_at)}</dd>
          </div>
          <div className="flex items-center gap-1.5">
            <dt className="font-bold">有效截止</dt>
            <dd className="font-mono">{formatMoment(event.valid_until)}</dd>
          </div>
          <div className="flex items-center gap-1.5">
            <dt className="font-bold">置信度</dt>
            <dd className="font-mono">{Math.round(event.confidence * 1000) / 10}%</dd>
          </div>
        </dl>
      </section>

      <section className="rounded-2xl border border-slate-200/80 bg-white/80 p-4 shadow-sm backdrop-blur-md dark:border-slate-700/60 dark:bg-slate-800/60">
        <h2 className="border-b border-slate-100 pb-3 text-[13px] font-semibold text-slate-600 dark:border-slate-800 dark:text-slate-300">
          信源信号（{event.signals.length}）
        </h2>
        {event.signals.length === 0 ? (
          <p className="py-6 text-center text-[12px] text-slate-400">暂无信源信号。</p>
        ) : (
          <ul className="divide-y divide-slate-100 dark:divide-slate-800">
            {event.signals.map((signal) => (
              <li key={signal.signal_id} className="py-2.5">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="min-w-0 flex-1 text-[13px] font-semibold text-slate-800 dark:text-slate-200">
                    {signal.url ? (
                      <a href={signal.url} target="_blank" rel="noreferrer" className="hover:text-[#185fa5] hover:underline">
                        {signal.title}
                      </a>
                    ) : signal.title}
                  </span>
                  <ValidityBadge state={signal.validity_state} />
                </div>
                <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-[10px] text-slate-500">
                  <span>发布 {formatMoment(signal.published_at)}</span>
                  <span>采集 {formatMoment(signal.collected_at)}</span>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="rounded-2xl border border-slate-200/80 bg-white/80 p-4 shadow-sm backdrop-blur-md dark:border-slate-700/60 dark:bg-slate-800/60">
        <h2 className="flex items-center gap-1.5 border-b border-slate-100 pb-3 text-[13px] font-semibold text-slate-600 dark:border-slate-800 dark:text-slate-300">
          <Building2 className="h-4 w-4" />
          受影响供应商（{event.suppliers?.length ?? 0}）
        </h2>
        {(event.suppliers?.length ?? 0) === 0 ? (
          <p className="py-6 text-center text-[12px] text-slate-400">暂无受影响供应商。</p>
        ) : (
          <ul className="divide-y divide-slate-100 dark:divide-slate-800">
            {event.suppliers?.map((supplier) => (
              <SupplierRow
                key={`${supplier.supplier_id}-${supplier.alert_id ?? 'na'}`}
                supplier={supplier}
                onSelectAlert={onSelectAlert}
              />
            ))}
          </ul>
        )}
      </section>
    </div>
  );
};

const SupplierRow = ({supplier, onSelectAlert}: {
  readonly supplier: EventSupplierAlert;
  readonly onSelectAlert: (alertId: string) => void;
}) => {
  const alertId = supplier.alert_id ?? null;
  const content = (
    <>
      <div className="flex flex-wrap items-center gap-2">
        <span className={`rounded-md px-2 py-0.5 text-[10px] font-bold text-white ${LEVEL_BADGE[supplier.level] ?? LEVEL_BADGE.P4}`}>
          {supplier.level}
        </span>
        <span className="min-w-0 truncate text-[13px] font-bold text-slate-900 dark:text-white">{supplier.supplier_name}</span>
        {supplier.version_superseded && (
          <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-bold text-amber-800 dark:bg-amber-900/40 dark:text-amber-300">
            已被新版本替代
          </span>
        )}
      </div>
      <div className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1 text-[10px] text-slate-500">
        <span>分数 {supplier.score}</span>
        <span>报警状态 {supplier.alert_status}</span>
        <span>截止 {formatMoment(supplier.expires_at)}</span>
        {alertId !== null && (
          <span className="ml-auto inline-flex items-center gap-1 font-bold text-[#185fa5] dark:text-blue-300">
            查看提醒<ChevronRight className="h-3 w-3" />
          </span>
        )}
      </div>
    </>
  );

  if (alertId === null) {
    return <li className="py-2.5">{content}</li>;
  }

  return (
    <li>
      <button
        type="button"
        onClick={() => onSelectAlert(String(alertId))}
        className="group w-full rounded-lg py-2.5 text-left transition hover:bg-slate-50 dark:hover:bg-slate-900/40"
      >
        {content}
      </button>
    </li>
  );
};

const ValidityBadge = ({state}: {readonly state: string | null | undefined}) => {
  if (!state) return null;
  return (
    <span className={`rounded-full px-2 py-0.5 text-[10px] font-bold ${VALIDITY_BADGE_CLASS[state] ?? 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300'}`}>
      {formatValidityState(state)}
    </span>
  );
};
