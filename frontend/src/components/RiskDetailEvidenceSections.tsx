import {Building2, CalendarDays, Database, FileText, MapPin, ShieldCheck, Signal, Target} from 'lucide-react';
import type {EventDetailRead, RiskAlertRead} from '../api';
import {ValidityStateBadge} from './ValidityStateBadge';

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

/**
 * 规则评分明细：后端 `score_detail` 的键是英文，且 JSONB 不保证键顺序；
 * 这里统一定义中文标题与分组顺序，把同类小卡片排在一起。
 */
const SCORE_DETAIL_LABELS: Record<string, string> = {
  // 等级判定
  final_level: '最终等级',
  capped_level: '封顶后等级',
  level_cap: '等级封顶原因',
  deterministic_level: '确定性基线等级',
  llm_level: 'LLM 建议等级',
  // 评分构成
  severity: '事件严重程度',
  association: '关联强度',
  source_credibility: '来源可信度',
  timeliness: '时效性',
  product_relevance: '产品相关性',
  // LLM 建议采纳
  llm_adopted: '是否采纳 LLM 建议',
  llm_confidence: 'LLM 置信度',
  llm_theta: 'LLM 采纳阈值',
  llm_rationale: 'LLM 采纳理由',
  // 强制规则
  forced_rule: '强制规则',
  // 规则与维度
  dimension: '监控维度',
  rule_version: '规则版本',
  subtotal: '评分小计',
};

/** 分组即展示顺序：数组顺序 = 组顺序，keys 顺序 = 组内卡片顺序。 */
const SCORE_DETAIL_GROUPS: ReadonlyArray<{title: string; keys: readonly string[]}> = [
  {title: '等级判定', keys: ['final_level', 'capped_level', 'level_cap', 'deterministic_level', 'llm_level']},
  {title: '评分构成', keys: ['severity', 'association', 'source_credibility', 'timeliness', 'product_relevance']},
  {title: 'LLM 建议采纳', keys: ['llm_adopted', 'llm_confidence', 'llm_theta', 'llm_rationale']},
  {title: '强制规则', keys: ['forced_rule']},
  {title: '规则与维度', keys: ['dimension', 'rule_version']},
];

/** 维度键 → 中文名称；与后端维度配置（engine/dimensions）一致。 */
const DIMENSION_LABELS: Record<string, string> = {
  natural: '自然环境',
  geopolitical: '地缘政治与安全',
  economic: '经济与金融',
  policy: '政策与法规',
  industry: '产业与供应市场',
  corporate: '供应商主体',
};

const LEVEL_CAP_LABELS: Record<string, string> = {
  country_only_max_p4: '仅命中国家，最高 P4',
  weak_association_max_p2: '弱关联，最高 P2',
};

/** 按预定义分组切分明细：已知键按分组顺序排列，未知键兜底到「其他」。 */
function groupScoreDetail(detail: Record<string, unknown>): Array<{title: string; entries: Array<[string, unknown]>}> {
  const remaining = new Map<string, unknown>(Object.entries(detail));
  const groups: Array<{title: string; entries: Array<[string, unknown]>}> = [];
  for (const {title, keys} of SCORE_DETAIL_GROUPS) {
    const entries: Array<[string, unknown]> = [];
    for (const key of keys) {
      if (remaining.has(key)) {
        entries.push([key, remaining.get(key)]);
        remaining.delete(key);
      }
    }
    if (entries.length > 0) groups.push({title, entries});
  }
  if (remaining.size > 0) groups.push({title: '其他', entries: [...remaining.entries()]});
  return groups;
}

/** 评分明细标量值的中文/可读化处理。 */
function formatScoreDetailValue(key: string, value: unknown): string {
  if (value === null || value === undefined || value === '') return '—';
  if (typeof value === 'boolean') return value ? '是' : '否';
  if (typeof value === 'string') {
    if (key === 'dimension') return DIMENSION_LABELS[value] ?? value;
    if (key === 'level_cap') return LEVEL_CAP_LABELS[value] ?? value;
    return value;
  }
  return String(value);
}

/** 强制规则命中详情：以中文标签逐项展示，替代原来的裸 JSON。 */
const ForcedRuleDetail = ({value}: {readonly value: Record<string, unknown>}) => (
  <div className="mt-1 space-y-1 text-[12px] font-medium text-slate-700 dark:text-slate-300">
    {value.name !== undefined && (
      <p className="break-words"><span className="font-semibold text-slate-500 dark:text-slate-400">规则名称：</span>{formatValue(value.name)}</p>
    )}
    {value.description !== undefined && (
      <p className="break-words"><span className="font-semibold text-slate-500 dark:text-slate-400">说明：</span>{formatValue(value.description)}</p>
    )}
    {value.reason !== undefined && (
      <p className="break-words"><span className="font-semibold text-slate-500 dark:text-slate-400">原因：</span>{formatValue(value.reason)}</p>
    )}
    {value.original_level !== undefined && (
      <p className="break-words"><span className="font-semibold text-slate-500 dark:text-slate-400">原始等级：</span>{formatValue(value.original_level)}</p>
    )}
    {value.original_score !== undefined && (
      <p className="break-words"><span className="font-semibold text-slate-500 dark:text-slate-400">原始分数：</span>{formatValue(value.original_score)}</p>
    )}
  </div>
);

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
        <div className="space-y-4">
          {groupScoreDetail(alert.score_detail).map((group) => (
            <div key={group.title} className="space-y-2">
              <h3 className="text-[11px] font-bold text-slate-500 dark:text-slate-400">{group.title}</h3>
              <dl className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
                {group.entries.map(([key, value]) => (
                  <div key={key} className="min-w-0 rounded-xl border border-[#c2c6d2] bg-[#f7f9ff] p-3 dark:border-slate-800 dark:bg-slate-900">
                    <dt className="text-[11px] font-semibold text-slate-500 dark:text-slate-400">{SCORE_DETAIL_LABELS[key] ?? key}</dt>
                    {key === 'forced_rule' && value !== null && typeof value === 'object' ? (
                      <dd><ForcedRuleDetail value={value as Record<string, unknown>} /></dd>
                    ) : (
                      <dd className="mt-1 break-words font-mono text-sm font-bold text-slate-800 dark:text-slate-200">{formatScoreDetailValue(key, value)}</dd>
                    )}
                  </div>
                ))}
              </dl>
            </div>
          ))}
        </div>
      )}
    </section>

    <div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
      <section className="space-y-3 rounded-2xl border border-slate-200/80 bg-white/80 p-5 shadow-sm backdrop-blur-md dark:border-slate-800 dark:bg-slate-950/80">
        <SectionTitle icon={<Signal className="h-5 w-5 text-[#004782]" />}>原始信号</SectionTitle>
        {event.signals.length === 0 ? <EvidenceEmptyState label="原始信号" /> : <ul className="space-y-3">{event.signals.map((signal) => (
          <li key={signal.signal_id} className="rounded-xl border border-[#c2c6d2] bg-[#f7f9ff] p-3 dark:border-slate-800 dark:bg-slate-900">
            <div className="flex min-w-0 flex-wrap items-center gap-2">
              <p className="min-w-0 break-words font-bold text-slate-900 dark:text-white">{signal.title}</p>
              <ValidityStateBadge state={signal.validity_state} />
            </div>
            <p className="mt-1 break-words text-xs leading-relaxed text-slate-600 dark:text-slate-300">{signal.content}</p>
            <p className="mt-2 text-[11px] text-slate-500">采集时间：{formatDateTime(signal.collected_at)}</p>
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
