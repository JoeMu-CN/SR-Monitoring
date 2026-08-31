import {Building2, CalendarDays, Database, FileText, MapPin, ShieldCheck, Signal, Target} from 'lucide-react';
import type {EventDetailRead, RiskAlertRead} from '../api';

interface RiskDetailEvidenceSectionsProps {
  readonly alert: RiskAlertRead;
  readonly event: EventDetailRead;
}

const formatDateTime = (value: string | null): string => {
  if (value === null) return '时间未披露';
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  }).format(new Date(value));
};

const formatValue = (value: unknown): string => {
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') return String(value);
  return JSON.stringify(value) ?? '未披露';
};

const EvidenceEmptyState = ({label}: {readonly label: string}) => (
  <p className="rounded-lg border border-dashed border-slate-200 bg-slate-50 px-3 py-4 text-center text-xs text-slate-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-400">
    暂无{label}
  </p>
);

const SectionTitle = ({children, icon}: {readonly children: string; readonly icon: React.ReactNode}) => (
  <h2 className="flex items-center gap-2 text-[15px] font-bold text-slate-900 dark:text-white">
    {icon}
    {children}
  </h2>
);

const DetailField = ({label, value}: {readonly label: string; readonly value: string}) => (
  <div className="min-w-0">
    <dt className="text-[11px] font-semibold text-slate-500 dark:text-slate-400">{label}</dt>
    <dd className="mt-1 break-words text-[13px] font-medium text-slate-800 dark:text-slate-200">{value}</dd>
  </div>
);

export const RiskDetailEvidenceSections = ({alert, event}: RiskDetailEvidenceSectionsProps) => (
  <div className="space-y-5 pb-20 lg:pb-8">
    <section className="space-y-3 rounded-2xl border border-slate-200/80 bg-white/80 p-5 shadow-sm backdrop-blur-md dark:border-slate-800 dark:bg-slate-950/80">
      <SectionTitle icon={<FileText className="h-5 w-5 text-[#004782]" />}>
        来源
      </SectionTitle>
      <div className="rounded-xl border border-[#c2c6d2] bg-[#f7f9ff] p-3.5 dark:border-slate-800 dark:bg-slate-900">
        <p className="font-bold text-slate-900 dark:text-white">{alert.source_title || '来源未披露'}</p>
        <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">发布时间：{formatDateTime(alert.published_at)}</p>
        {alert.source_url !== null && (
          <a className="mt-2 block break-all text-xs font-semibold text-[#004782] hover:underline dark:text-blue-400" href={alert.source_url} target="_blank" rel="noreferrer">
            {alert.source_url}
          </a>
        )}
      </div>
    </section>

    <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
      <section className="space-y-3 rounded-2xl border border-slate-200/80 bg-white/80 p-5 shadow-sm backdrop-blur-md dark:border-slate-800 dark:bg-slate-950/80">
        <SectionTitle icon={<CalendarDays className="h-5 w-5 text-[#004782]" />}>
          事件
        </SectionTitle>
        <p className="text-sm leading-relaxed text-slate-700 dark:text-slate-300">{event.summary}</p>
        <dl className="grid grid-cols-2 gap-4 rounded-xl border border-[#c2c6d2] bg-[#f7f9ff] p-3.5 dark:border-slate-800 dark:bg-slate-900">
          <DetailField label="事件类型" value={event.event_subtype ?? event.event_type} />
          <DetailField label="严重性" value={event.severity} />
          <DetailField label="置信度" value={`${Math.round(event.confidence * 100)}%`} />
          <DetailField label="事件编号" value={String(event.id)} />
          <DetailField label="开始时间" value={formatDateTime(event.start_at)} />
          <DetailField label="结束时间" value={formatDateTime(event.end_at)} />
          <DetailField label="创建时间" value={formatDateTime(event.created_at)} />
          <DetailField label="去重键" value={event.dedup_key} />
        </dl>
      </section>

      <section className="space-y-3 rounded-2xl border border-slate-200/80 bg-white/80 p-5 shadow-sm backdrop-blur-md dark:border-slate-800 dark:bg-slate-950/80">
        <SectionTitle icon={<Target className="h-5 w-5 text-[#004782]" />}>
          供应商匹配
        </SectionTitle>
        <dl className="grid grid-cols-2 gap-4 rounded-xl border border-[#c2c6d2] bg-[#f7f9ff] p-3.5 dark:border-slate-800 dark:bg-slate-900">
          <DetailField label="供应商主体" value={alert.supplier_name} />
          <DetailField label="匹配类型" value={alert.match_type} />
        </dl>
        <div>
          <h3 className="text-xs font-bold text-slate-700 dark:text-slate-300">匹配理由</h3>
          {alert.match_reasons.length === 0 ? <EvidenceEmptyState label="匹配理由" /> : (
            <ul className="mt-2 space-y-2">
              {alert.match_reasons.map((reason) => <li key={reason} className="rounded-lg border border-[#c2c6d2]/60 bg-[#f7f9ff] px-3 py-2 text-xs leading-relaxed text-slate-700 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-300">{reason}</li>)}
            </ul>
          )}
        </div>
        <div>
          <h3 className="text-xs font-bold text-slate-700 dark:text-slate-300">匹配证据</h3>
          {alert.match_evidence.length === 0 ? <EvidenceEmptyState label="匹配证据" /> : (
            <ul className="mt-2 space-y-2">
              {alert.match_evidence.map((evidence, index) => (
                <li key={`match-evidence-${index}`} className="rounded-lg border border-[#c2c6d2]/60 bg-[#f7f9ff] px-3 py-2 text-xs text-slate-700 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-300">
                  {Object.entries(evidence).map(([key, value]) => <p key={key} className="break-words"><span className="font-semibold">{key}：</span>{formatValue(value)}</p>)}
                </li>
              ))}
            </ul>
          )}
        </div>
      </section>
    </div>

    <section className="space-y-3 rounded-2xl border border-slate-200/80 bg-white/80 p-5 shadow-sm backdrop-blur-md dark:border-slate-800 dark:bg-slate-950/80">
      <SectionTitle icon={<ShieldCheck className="h-5 w-5 text-[#004782]" />}>
        规则评分
      </SectionTitle>
      {Object.keys(alert.score_detail).length === 0 ? <EvidenceEmptyState label="评分明细" /> : (
        <dl className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {Object.entries(alert.score_detail).map(([key, value]) => (
            <div key={key} className="rounded-xl border border-[#c2c6d2] bg-[#f7f9ff] p-3 dark:border-slate-800 dark:bg-slate-900">
              <dt className="text-[11px] font-semibold text-slate-500 dark:text-slate-400">{key}</dt>
              <dd className="mt-1 break-words font-mono text-sm font-bold text-slate-800 dark:text-slate-200">{formatValue(value)}</dd>
            </div>
          ))}
        </dl>
      )}
    </section>

    <div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
      <section className="space-y-3 rounded-2xl border border-slate-200/80 bg-white/80 p-5 shadow-sm backdrop-blur-md dark:border-slate-800 dark:bg-slate-950/80">
        <SectionTitle icon={<Signal className="h-5 w-5 text-[#004782]" />}>原始信号</SectionTitle>
        {event.signals.length === 0 ? <EvidenceEmptyState label="原始信号" /> : <ul className="space-y-3">{event.signals.map((signal) => (
          <li key={signal.signal_id} className="rounded-xl border border-[#c2c6d2] bg-[#f7f9ff] p-3 dark:border-slate-800 dark:bg-slate-900">
            <p className="font-bold text-slate-900 dark:text-white">{signal.title}</p>
            <p className="mt-1 break-words text-xs leading-relaxed text-slate-600 dark:text-slate-300">{signal.content}</p>
            <p className="mt-2 text-[11px] text-slate-500">{formatDateTime(signal.published_at)}</p>
            {signal.url !== null && <a className="mt-1 block break-all text-xs font-semibold text-[#004782] hover:underline dark:text-blue-400" href={signal.url} target="_blank" rel="noreferrer">{signal.url}</a>}
          </li>
        ))}</ul>}
      </section>

      <section className="space-y-3 rounded-2xl border border-slate-200/80 bg-white/80 p-5 shadow-sm backdrop-blur-md dark:border-slate-800 dark:bg-slate-950/80">
        <SectionTitle icon={<Building2 className="h-5 w-5 text-[#004782]" />}>主体</SectionTitle>
        {event.entities.length === 0 ? <EvidenceEmptyState label="主体证据" /> : <ul className="space-y-3">{event.entities.map((entity, index) => (
          <li key={`${entity.name}-${index}`} className="rounded-xl border border-[#c2c6d2] bg-[#f7f9ff] p-3 text-xs dark:border-slate-800 dark:bg-slate-900">
            <p className="font-bold text-slate-900 dark:text-white">{entity.name}</p>
            <p className="mt-1 text-slate-600 dark:text-slate-300">规范名称：{entity.normalized_name ?? '未披露'}</p>
            <p className="mt-1 text-slate-600 dark:text-slate-300">登记编号：{entity.registry_no ?? '未披露'}</p>
          </li>
        ))}</ul>}
      </section>

      <section className="space-y-3 rounded-2xl border border-slate-200/80 bg-white/80 p-5 shadow-sm backdrop-blur-md dark:border-slate-800 dark:bg-slate-950/80">
        <SectionTitle icon={<MapPin className="h-5 w-5 text-[#004782]" />}>地点</SectionTitle>
        {event.locations.length === 0 ? <EvidenceEmptyState label="地点证据" /> : <ul className="space-y-3">{event.locations.map((location, index) => (
          <li key={`${location.name}-${index}`} className="rounded-xl border border-[#c2c6d2] bg-[#f7f9ff] p-3 text-xs dark:border-slate-800 dark:bg-slate-900">
            <p className="font-bold text-slate-900 dark:text-white">{location.name}</p>
            <p className="mt-1 text-slate-600 dark:text-slate-300">{[location.country_code, location.region, location.city, location.district].filter((part): part is string => part !== null).join(' · ') || '行政区划未披露'}</p>
            <p className="mt-1 text-slate-600 dark:text-slate-300">坐标：{location.latitude ?? '未披露'}，{location.longitude ?? '未披露'}；范围：{location.radius_km ?? '未披露'} km</p>
          </li>
        ))}</ul>}
      </section>
    </div>
  </div>
);
