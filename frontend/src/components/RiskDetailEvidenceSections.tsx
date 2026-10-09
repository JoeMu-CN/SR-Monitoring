import {Building2, CalendarDays, FileText, MapPin, ShieldCheck, Signal, Target} from 'lucide-react';
import {VALIDITY_STATE_LABELS, type EventDetailRead, type EventLocationEvidence, type RiskAlertRead, type ValidityReasonRead} from '../api';
import {ValidityStateBadge} from './ValidityStateBadge';

interface RiskDetailEvidenceSectionsProps {
  readonly alert: RiskAlertRead;
  readonly event: EventDetailRead;
}

/* ── 局部中文映射：只覆盖常见枚举，未知值一律保留原文兜底 ─────────────── */

const EVENT_TYPE_LABELS: Record<string, string> = {
  weather: '天气', geological: '地质灾害', logistics: '物流', trade_policy: '贸易政策',
  geopolitical: '地缘政治', corporate: '企业经营', judicial: '司法', compliance: '合规', other: '其他',
};

const EVENT_SUBTYPE_LABELS: Record<string, string> = {
  weather_alert: '天气预警', geological_hazard: '地质灾害', transport_disruption: '交通中断',
  raw_material_shortage: '原材料短缺', trade_tariff: '贸易措施', export_control: '出口管制',
  sanctions: '制裁', regulatory_change: '监管变更', political_instability: '政治动荡',
  public_security: '公共安全', armed_conflict: '武装冲突', corporate_distress: '经营困难',
  judicial_case: '司法案件', compliance_violation: '合规违规', other: '其他',
};

const SEVERITY_LABELS: Record<string, string> = {critical: '重大', high: '高', medium: '中', low: '低'};

const MATCH_TYPE_LABELS: Record<string, string> = {
  registry_no: '注册编号', legal_name: '法人全称', alias: '供应商别名', site_text: '生产地点文本',
  site_distance: '生产地点距离', product: '供应产品', industry: '供应行业', country: '国别',
};

const EVIDENCE_KEY_LABELS: Record<string, string> = {
  object_type: '对象类型', supplier_id: '供应商编号', site_id: '生产地点编号', site_name: '生产地点',
  event_location: '事件地点', method: '匹配方式', district: '区县', precision: '匹配粒度',
  registry_no: '注册编号', legal_name: '法人全称', alias: '供应商别名', alias_id: '别名编号',
  product_id: '产品编号', product_name: '产品名称', keyword: '命中关键词', country_code: '国家代码',
};

const EVIDENCE_VALUE_LABELS: Record<string, string> = {
  supplier: '供应商', alias: '供应商别名', site: '生产地点', product: '产品',
  industry: '行业', country: '国家', text: '文本精确匹配', district: '区县级',
};

const VALIDITY_REASON_LABELS: Record<string, string> = {
  active: '有效', effective_signal_support: '存在有效信号支撑', no_effective_signal_support: '无有效信号支撑',
  deadline_reached: '已到有效期截止', classification_failed: '分类失败待复核',
  classification_resolved: '分类已确认', pending_classification: '等待分类',
  anchor_fallback: '时间锚点降级', policy_resolved: '策略已解析',
  superseded_by_newer_version: '被更新版本取代', same_authority_time_conflict: '权威时间冲突',
  quarantine_promoted: '隔离后恢复采集', two_consecutive_misses: '连续两次未命中',
};

const ANCHOR_LABELS: Record<string, string> = {
  published_at: '发布时间', collected_at: '采集时间', official_valid_until: '官方有效期截止',
  event_end: '事件结束时间', legacy: '历史锚点',
};

const EXPIRY_KIND_LABELS: Record<string, string> = {finite: '有确定截止', unbounded: '无确定截止', legacy: '历史截止'};

const PANEL_CLASS = 'space-y-3 rounded-2xl border border-slate-200/80 bg-white/80 p-5 shadow-sm backdrop-blur-md dark:border-slate-800 dark:bg-slate-950/80';
const SUBTLE_PANEL_CLASS = 'rounded-xl border border-[#c2c6d2] bg-[#f7f9ff] p-3.5 dark:border-slate-800 dark:bg-slate-900';

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

/** 常见枚举取中文，未知值保留原文，绝不吞掉后端新增枚举。 */
const label = (labels: Record<string, string>, value: string): string => labels[value] ?? value;

const formatEventCategory = (event: EventDetailRead): string => {
  const raw = event.event_subtype ?? event.event_type;
  const map = event.event_subtype === null ? EVENT_TYPE_LABELS : EVENT_SUBTYPE_LABELS;
  return label(map, raw);
};

/** `match_type` 可能是 `legal_name+site_text` 这类组合，逐段映射后再拼接。 */
const formatMatchType = (value: string): string => value
  .split('+')
  .map((part) => label(MATCH_TYPE_LABELS, part))
  .join(' + ');

const formatEvidenceValue = (key: string, value: unknown): string => {
  if (key === 'object_type' || key === 'method' || key === 'precision') {
    if (typeof value === 'string') return label(EVIDENCE_VALUE_LABELS, value);
  }
  return formatValue(value);
};

const formatValidityReason = (reason: ValidityReasonRead): string => {
  const code = label(VALIDITY_REASON_LABELS, reason.code);
  const anchor = label(ANCHOR_LABELS, reason.anchor_source);
  return reason.anchor_source === 'legacy' ? code : `${code}（锚定${anchor}）`;
};

const formatValidityState = (state: string): string => VALIDITY_STATE_LABELS[state as keyof typeof VALIDITY_STATE_LABELS] ?? state;

/** title 与 content 只在归一化空白后完全相等时视为同一段文本；否则正文完整保留换行。 */
const normalizeWhitespace = (value: string): string => value.replace(/\s+/g, ' ').trim();

/** 坐标与半径只在后端真的给出数值时展示（0 是有效坐标），全为 null 时整行隐藏。 */
const formatLocationMetrics = (location: EventLocationEvidence): string | null => {
  const hasLatitude = location.latitude !== null;
  const hasLongitude = location.longitude !== null;
  const parts: string[] = [];
  if (hasLatitude && hasLongitude) {
    parts.push(`坐标：${location.latitude}，${location.longitude}`);
  } else if (hasLatitude) {
    parts.push(`纬度：${location.latitude}`);
  } else if (hasLongitude) {
    parts.push(`经度：${location.longitude}`);
  }
  if (location.radius_km !== null) parts.push(`范围：${location.radius_km} km`);
  return parts.length > 0 ? parts.join('；') : null;
};

const formatAdministrativeArea = (location: EventLocationEvidence): string =>
  [location.country_code, location.region, location.city, location.district]
    .filter((part): part is string => part !== null)
    .join(' · ');

/**
 * 规则评分明细：后端 `score_detail` 的键是英文，且 JSONB 不保证键顺序；
 * 这里统一定义中文标题与分组顺序，未知键兜底到「其他」，一个都不丢。
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

/**
 * 等级判定四项按后端 `risks/scoring.py: resolve_level` 的真实执行顺序排列：
 * deterministic_level(基线) → llm_level(建议采纳合并) → capped_level(弱关联封顶)
 * → final_level(强制规则后的最终结果)。
 * `level_cap` 只是封顶命中的辅助说明，不参与四项排序，跟随其后整行展示。
 */
const LEVEL_DETAIL_KEYS: readonly string[] = ['deterministic_level', 'llm_level', 'capped_level', 'final_level'];

/** 分组即展示顺序：数组顺序 = 组顺序，keys 顺序 = 组内展示顺序；`levelRow` 走固定四列单行。 */
const SCORE_DETAIL_GROUPS: ReadonlyArray<{title: string; keys: readonly string[]; levelRow?: boolean}> = [
  {title: '等级判定', keys: [...LEVEL_DETAIL_KEYS, 'level_cap'], levelRow: true},
  {title: '评分构成', keys: ['severity', 'association', 'source_credibility', 'timeliness', 'product_relevance']},
  {title: 'LLM 建议采纳', keys: ['llm_adopted', 'llm_confidence', 'llm_theta', 'llm_rationale']},
  {title: '强制规则', keys: ['forced_rule']},
  {title: '规则与维度', keys: ['dimension', 'rule_version']},
];

/**
 * 等级判定四项固定四列同行。
 * 外层 min-w-0 overflow-x-auto 只在本区内滚动，min-width 落在内部 dl 上，
 * 因此不会撑破面板与页面；sm 起内部恢复 min-w-0 与容器均分。标题 nowrap 防中文孤字。
 */
const LEVEL_ROW_SCROLL_CLASS = 'min-w-0 overflow-x-auto';
const LEVEL_ROW_CLASS = 'grid w-full min-w-[22rem] grid-cols-4 gap-x-3 gap-y-2 sm:min-w-0';
const DEFAULT_ROW_CLASS = 'grid grid-cols-1 gap-x-4 gap-y-2 sm:grid-cols-2 lg:grid-cols-3';

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

interface ScoreDetailGroup {
  readonly title: string;
  readonly entries: ReadonlyArray<readonly [string, unknown]>;
  readonly levelRow: boolean;
}

/** 按预定义分组切分明细：已知键按分组顺序排列，未知键兜底到「其他」。 */
function groupScoreDetail(detail: Record<string, unknown>): ScoreDetailGroup[] {
  const remaining = new Map<string, unknown>(Object.entries(detail));
  const groups: Array<{title: string; entries: Array<[string, unknown]>; levelRow: boolean}> = [];
  for (const {title, keys, levelRow = false} of SCORE_DETAIL_GROUPS) {
    const entries: Array<[string, unknown]> = [];
    for (const key of keys) {
      if (remaining.has(key)) {
        entries.push([key, remaining.get(key)]);
        remaining.delete(key);
      }
    }
    if (entries.length > 0) groups.push({title, entries, levelRow});
  }
  if (remaining.size > 0) groups.push({title: '其他', entries: [...remaining.entries()], levelRow: false});
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

/** 长文本（采纳理由等）占满整行，避免被挤进半栏窄列。 */
const isLongScoreText = (key: string, value: unknown): boolean =>
  key !== 'forced_rule' && typeof value === 'string' && value.length > 24;

/** 强制规则命中详情：以中文标签逐项展示，替代原来的裸 JSON。 */
const ForcedRuleDetail = ({value}: {readonly value: Record<string, unknown>}) => (
  <div className="space-y-1 text-[12px] font-medium text-slate-700 dark:text-slate-300">
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

const EvidenceEmptyState = ({label: text}: {readonly label: string}) => (
  <p className="rounded-lg border border-dashed border-slate-200 bg-slate-50 px-3 py-3 text-xs text-slate-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-400">
    暂无{text}
  </p>
);

const SectionTitle = ({children, icon}: {readonly children: string; readonly icon: React.ReactNode}) => (
  <h2 className="flex items-center gap-2 text-[15px] font-bold text-slate-900 dark:text-white">
    {icon}
    {children}
  </h2>
);

const SubsectionTitle = ({children}: {readonly children: string}) => (
  <h3 className="text-[11px] font-bold text-slate-500 dark:text-slate-400">{children}</h3>
);

const DetailField = ({label: text, value}: {readonly label: string; readonly value: string}) => (
  <div className="min-w-0">
    <dt className="text-[11px] font-semibold text-slate-500 dark:text-slate-400">{text}</dt>
    <dd className="mt-0.5 break-words text-[13px] font-medium text-slate-800 dark:text-slate-200">{value}</dd>
  </div>
);

const ExternalLink = ({href, children}: {readonly href: string; readonly children: string}) => (
  <a className="inline-block text-xs font-semibold text-[#004782] hover:underline dark:text-blue-400" href={href} target="_blank" rel="noreferrer">
    {children}
  </a>
);

const MatchEvidenceList = ({evidence}: {readonly evidence: ReadonlyArray<Record<string, unknown>>}) => {
  if (evidence.length === 0) return <EvidenceEmptyState label="匹配证据" />;
  return (
    <ul className="space-y-2">
      {evidence.map((item, index) => (
        <li key={`match-evidence-${index}`} className="rounded-lg border border-[#c2c6d2]/60 bg-[#f7f9ff] px-3 py-2 text-xs text-slate-700 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-300">
          {Object.entries(item).map(([key, value]) => (
            <p key={key} className="break-words">
              <span className="font-semibold">{label(EVIDENCE_KEY_LABELS, key)}：</span>{formatEvidenceValue(key, value)}
            </p>
          ))}
        </li>
      ))}
    </ul>
  );
};

const EventEvidence = ({alert, event}: RiskDetailEvidenceSectionsProps) => (
  <div className="space-y-5">
    <section className={PANEL_CLASS}>
      <SectionTitle icon={<CalendarDays className="h-5 w-5 text-[#004782]" />}>事件与来源</SectionTitle>
      <dl className={`grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3 ${SUBTLE_PANEL_CLASS}`}>
        <DetailField label="事件类型" value={formatEventCategory(event)} />
        <DetailField label="严重性" value={label(SEVERITY_LABELS, event.severity)} />
        <DetailField label="置信度" value={`${Math.round(event.confidence * 100)}%`} />
        <DetailField label="开始时间" value={formatDateTime(event.start_at)} />
        <DetailField label="结束时间" value={formatDateTime(event.end_at)} />
        <DetailField label="创建时间" value={formatDateTime(event.created_at)} />
      </dl>
      <div className={SUBTLE_PANEL_CLASS}>
        <p className="flex items-center gap-1.5 text-[11px] font-bold text-slate-500 dark:text-slate-400">
          <FileText className="h-3.5 w-3.5" aria-hidden="true" />
          来源
        </p>
        <p className="mt-1 font-bold text-slate-900 dark:text-white">{alert.source_title || '来源未披露'}</p>
        <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">发布时间：{formatDateTime(alert.published_at)}</p>
        {alert.source_url !== null && <p className="mt-1.5"><ExternalLink href={alert.source_url}>查看来源原文</ExternalLink></p>}
      </div>
    </section>

    <section className={PANEL_CLASS}>
      <SectionTitle icon={<Signal className="h-5 w-5 text-[#004782]" />}>原始信号</SectionTitle>
      {event.signals.length === 0 ? <EvidenceEmptyState label="原始信号" /> : (
        <ul className="space-y-3">
          {event.signals.map((signal) => (
            <li key={signal.signal_id} className={SUBTLE_PANEL_CLASS}>
              <div className="flex min-w-0 flex-wrap items-center gap-2">
                <p className="min-w-0 break-words font-bold text-slate-900 dark:text-white">{signal.title}</p>
                <ValidityStateBadge state={signal.validity_state} />
              </div>
              {/* 标题与正文归一化空白后相同则只呈现一次，否则正文完整保留换行 */}
              {normalizeWhitespace(signal.title) !== normalizeWhitespace(signal.content) && (
                <p className="mt-1 whitespace-pre-wrap break-words text-xs leading-relaxed text-slate-600 dark:text-slate-300">{signal.content}</p>
              )}
              <p className="mt-2 text-[11px] text-slate-500 dark:text-slate-400">
                采集时间：{formatDateTime(signal.collected_at)}　发布时间：{formatDateTime(signal.published_at)}
              </p>
              {signal.url !== null && <p className="mt-1"><ExternalLink href={signal.url}>查看信号原文</ExternalLink></p>}
            </li>
          ))}
        </ul>
      )}
    </section>
  </div>
);

const SupplierAssociation = ({alert, event}: RiskDetailEvidenceSectionsProps) => (
  <div className="space-y-3">
    <section className={PANEL_CLASS}>
      <SectionTitle icon={<Target className="h-5 w-5 text-[#004782]" />}>供应商关联</SectionTitle>
      <dl className={`grid grid-cols-2 gap-x-4 gap-y-3 ${SUBTLE_PANEL_CLASS}`}>
        <DetailField label="供应商名称" value={alert.supplier_name} />
        <DetailField label="匹配类型" value={formatMatchType(alert.match_type)} />
      </dl>
      <div>
        <SubsectionTitle>匹配理由</SubsectionTitle>
        {alert.match_reasons.length === 0 ? <div className="mt-2"><EvidenceEmptyState label="匹配理由" /></div> : (
          <ul className="mt-2 space-y-2">
            {alert.match_reasons.map((reason) => (
              <li key={reason} className="rounded-lg border border-[#c2c6d2]/60 bg-[#f7f9ff] px-3 py-2 text-xs leading-relaxed text-slate-700 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-300">{reason}</li>
            ))}
          </ul>
        )}
      </div>
      <div>
        <SubsectionTitle>匹配证据</SubsectionTitle>
        <div className="mt-2"><MatchEvidenceList evidence={alert.match_evidence} /></div>
      </div>
    </section>

    {event.entities.length > 0 && (
      <section className={PANEL_CLASS}>
        <SectionTitle icon={<Building2 className="h-5 w-5 text-[#004782]" />}>关联主体</SectionTitle>
        <ul className="space-y-3">
          {event.entities.map((entity, index) => (
            <li key={`${entity.name}-${index}`} className={`${SUBTLE_PANEL_CLASS} text-xs`}>
              <p className="font-bold text-slate-900 dark:text-white">{entity.name}</p>
              <p className="mt-1 break-words text-slate-600 dark:text-slate-300">规范名称：{entity.normalized_name ?? '未披露'}</p>
              <p className="mt-0.5 break-words text-slate-600 dark:text-slate-300">登记编号：{entity.registry_no ?? '未披露'}</p>
            </li>
          ))}
        </ul>
      </section>
    )}

    {event.locations.length > 0 && (
      <section className={PANEL_CLASS}>
        <SectionTitle icon={<MapPin className="h-5 w-5 text-[#004782]" />}>关联地点</SectionTitle>
        <ul className="space-y-3">
          {event.locations.map((location, index) => {
            const metrics = formatLocationMetrics(location);
            return (
              <li key={`${location.name}-${index}`} className={`${SUBTLE_PANEL_CLASS} text-xs`}>
                <p className="font-bold text-slate-900 dark:text-white">{location.name}</p>
                <p className="mt-1 break-words text-slate-600 dark:text-slate-300">
                  {formatAdministrativeArea(location) || '行政区划未披露'}
                </p>
                {metrics !== null && <p className="mt-0.5 break-words text-slate-600 dark:text-slate-300">{metrics}</p>}
              </li>
            );
          })}
        </ul>
      </section>
    )}
  </div>
);

const ScoreDetailSection = ({alert}: {readonly alert: RiskAlertRead}) => (
  <section className={PANEL_CLASS}>
    <SectionTitle icon={<ShieldCheck className="h-5 w-5 text-[#004782]" />}>规则评分</SectionTitle>
    {Object.keys(alert.score_detail).length === 0 ? <EvidenceEmptyState label="评分明细" /> : (
      <div className="divide-y divide-slate-200/80 dark:divide-slate-800">
        {groupScoreDetail(alert.score_detail).map((group, groupIndex) => (
          <div key={group.title} className={`space-y-2 ${groupIndex === 0 ? 'pt-0' : 'pt-4'} pb-4 last:pb-0`}>
            <SubsectionTitle>{group.title}</SubsectionTitle>
            {/* 等级组的滚动容器只包住本组，min-width 落在内层 dl 上，不会撑破面板与页面。 */}
            <div className={group.levelRow ? LEVEL_ROW_SCROLL_CLASS : undefined}>
              <dl className={group.levelRow ? LEVEL_ROW_CLASS : DEFAULT_ROW_CLASS}>
                {group.entries.map(([key, value]) => {
                // 辅助说明（level_cap/forced_rule）与长文一样整宽另起一行，不占四个等级的位。
                const wide = key === 'level_cap' || key === 'forced_rule' || isLongScoreText(key, value);
                return (
                  <div key={key} className={`min-w-0 ${wide ? (group.levelRow ? 'col-span-4' : 'sm:col-span-2 lg:col-span-3') : ''}`}>
                    <dt className={`text-[11px] font-semibold text-slate-500 dark:text-slate-400 ${group.levelRow ? 'whitespace-nowrap' : ''}`}>{label(SCORE_DETAIL_LABELS, key)}</dt>
                    {key === 'forced_rule' && value !== null && typeof value === 'object' ? (
                      <dd className="mt-1"><ForcedRuleDetail value={value as Record<string, unknown>} /></dd>
                    ) : (
                      <dd className={`break-words text-[13px] font-medium text-slate-800 dark:text-slate-200 ${wide ? 'mt-1 whitespace-pre-wrap' : 'font-mono font-bold'}`}>
                        {formatScoreDetailValue(key, value)}
                      </dd>
                    )}
                  </div>
                );
              })}
              </dl>
            </div>
          </div>
        ))}
      </div>
    )}
  </section>
);

const TechnicalDetails = ({alert, event}: RiskDetailEvidenceSectionsProps) => (
  <section className={PANEL_CLASS}>
    <details>
      <summary className="cursor-pointer text-[15px] font-bold text-slate-900 dark:text-white">
        技术明细
      </summary>
      <dl className="mt-3 grid gap-x-4 gap-y-2 text-xs sm:grid-cols-2 lg:grid-cols-3">
        <div className="min-w-0"><dt className="font-semibold text-slate-500 dark:text-slate-400">提醒编号</dt><dd className="break-all font-mono">{alert.id}</dd></div>
        <div className="min-w-0"><dt className="font-semibold text-slate-500 dark:text-slate-400">事件编号</dt><dd className="break-all font-mono">{event.id}</dd></div>
        <div className="min-w-0"><dt className="font-semibold text-slate-500 dark:text-slate-400">事件去重键</dt><dd className="break-all font-mono">{event.dedup_key}</dd></div>
        <div className="min-w-0"><dt className="font-semibold text-slate-500 dark:text-slate-400">事件类型（原始值）</dt><dd className="break-all font-mono">{event.event_type}</dd></div>
        <div className="min-w-0"><dt className="font-semibold text-slate-500 dark:text-slate-400">事件细类（原始值）</dt><dd className="break-all font-mono">{event.event_subtype ?? '未提供'}</dd></div>
        <div className="min-w-0"><dt className="font-semibold text-slate-500 dark:text-slate-400">事件严重性（原始值）</dt><dd className="break-all font-mono">{event.severity}</dd></div>
        <div className="min-w-0"><dt className="font-semibold text-slate-500 dark:text-slate-400">提醒匹配类型（原始值）</dt><dd className="break-all font-mono">{alert.match_type}</dd></div>
        <div className="min-w-0"><dt className="font-semibold text-slate-500 dark:text-slate-400">提醒评分规则版本</dt><dd className="break-all font-mono">{formatScoreDetailValue('rule_version', alert.score_detail.rule_version)}</dd></div>
        <div className="min-w-0"><dt className="font-semibold text-slate-500 dark:text-slate-400">提醒失效方式</dt><dd className="break-words">{label(EXPIRY_KIND_LABELS, alert.expiry_kind)}</dd></div>
        <div className="min-w-0"><dt className="font-semibold text-slate-500 dark:text-slate-400">提醒失效时间</dt><dd className="break-words">{formatDateTime(alert.expires_at)}</dd></div>
        <div className="min-w-0"><dt className="font-semibold text-slate-500 dark:text-slate-400">提醒有效期状态</dt><dd className="break-words">{formatValidityState(alert.validity_state)}</dd></div>
        <div className="min-w-0"><dt className="font-semibold text-slate-500 dark:text-slate-400">提醒有效期截止</dt><dd className="break-words">{formatDateTime(alert.valid_until)}</dd></div>
        <div className="min-w-0"><dt className="font-semibold text-slate-500 dark:text-slate-400">提醒复核时间</dt><dd className="break-words">{formatDateTime(alert.review_due_at)}</dd></div>
        <div className="min-w-0"><dt className="font-semibold text-slate-500 dark:text-slate-400">提醒有效期策略版本</dt><dd className="break-all font-mono">{alert.validity_policy_version ?? '未生成'}</dd></div>
        <div className="min-w-0"><dt className="font-semibold text-slate-500 dark:text-slate-400">提醒有效期原因</dt><dd className="break-words">{formatValidityReason(alert.validity_reason)}</dd></div>
        <div className="min-w-0"><dt className="font-semibold text-slate-500 dark:text-slate-400">事件有效期状态</dt><dd className="break-words">{formatValidityState(event.validity_state)}</dd></div>
        <div className="min-w-0"><dt className="font-semibold text-slate-500 dark:text-slate-400">事件有效期截止</dt><dd className="break-words">{formatDateTime(event.valid_until)}</dd></div>
        <div className="min-w-0"><dt className="font-semibold text-slate-500 dark:text-slate-400">事件复核时间</dt><dd className="break-words">{formatDateTime(event.review_due_at)}</dd></div>
        <div className="min-w-0"><dt className="font-semibold text-slate-500 dark:text-slate-400">事件有效期策略版本</dt><dd className="break-all font-mono">{event.validity_policy_version ?? '未生成'}</dd></div>
        <div className="min-w-0"><dt className="font-semibold text-slate-500 dark:text-slate-400">事件有效期原因</dt><dd className="break-words">{formatValidityReason(event.validity_reason)}</dd></div>
      </dl>
    </details>
  </section>
);

export const RiskDetailEvidenceSections = ({alert, event}: RiskDetailEvidenceSectionsProps) => (
  <div className="space-y-5 pb-20 lg:pb-8">
    <div className="grid grid-cols-1 items-start gap-5 lg:grid-cols-3">
      <div className="lg:col-span-2"><EventEvidence alert={alert} event={event} /></div>
      <div className="lg:col-span-1"><SupplierAssociation alert={alert} event={event} /></div>
    </div>
    <ScoreDetailSection alert={alert} />
    <TechnicalDetails alert={alert} event={event} />
  </div>
);