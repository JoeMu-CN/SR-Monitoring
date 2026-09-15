import React, {useMemo, useRef, useState} from 'react';
import {api, ApiError, updateDimensionConfig} from '../api';
import type {DimensionConfigPatchPayload, SandboxCandidate, SandboxRequest, SandboxResult} from '../api';
import type {MonitoringDimension} from '../types';
import type {RuleEngineDimensionDraft, RuleEngineMode} from './RuleEngineContext';
import {
  ASSOCIATION_MAX,
  DEFAULT_ASSOCIATION_SCORES,
  DEFAULT_SEVERITY_SCORES,
  SEVERITY_MAX,
  useRuleEngineContext,
} from './RuleEngineContext';

// 严重程度与关联类型的展示标签（与后端 Severity / MatchType 对齐）
const SEVERITY_LABELS: Record<string, string> = {
  critical: '严重',
  high: '高',
  medium: '中',
  low: '低',
};

const ASSOCIATION_LABELS: Record<string, string> = {
  registry_no: '注册号',
  legal_name: '法人全称',
  alias: '别名',
  site_distance: '地点距离',
  site_text: '地点文本',
  product: '产品',
  country: '国家',
  industry: '行业',
};

/**
 * 分值构成字段 → 中文标签（键名与后端 `scoring.py` 的 `compute_score` 明细一一对应）。
 * 只做展示映射，数值一律取自后端响应，不在前端复算任何公式。
 */
const SCORE_DETAIL_FIELDS: ReadonlyArray<[string, string]> = [
  ['severity', '严重程度'],
  ['association', '关联强度'],
  ['source_credibility', '来源可信度'],
  ['timeliness', '时效性'],
  ['product_relevance', '产品相关性'],
];

/**
 * 两条封顶规则的中文解释（语义依据 `backend/app/risks/scoring.py:183-197` 的
 * `apply_level_cap`：仅命中 country 柱 → P4；总分到 P1 但无主体精确匹配 → P2）。
 */
const LEVEL_CAP_EXPLANATIONS: ReadonlyArray<{key: string; title: string; cap: string; detail: string}> = [
  {
    key: 'country_only_max_p4',
    title: '只命中「国家」柱',
    cap: '最高 P4',
    detail: '事件只与供应商所在国家相关（没有主体、地点、产品、行业等更具体的命中）时，无论算出来多少分，最终等级都会被压到 P4。',
  },
  {
    key: 'weak_association_max_p2',
    title: '未命中主体精确匹配',
    cap: '最高 P2',
    detail: '没有注册号、法人全称或别名这类主体精确匹配时，即使总分已经达到 P1 阈值，最终等级也只到 P2（弱关联封顶）。',
  },
];

const LEVEL_CAP_LABELS: Record<string, string> = {
  country_only_max_p4: '封顶：只命中「国家」柱 → P4',
  weak_association_max_p2: '封顶：无主体精确匹配 → P2',
};

const PREVIEW_EVENT_LABELS: Record<string, string> = {
  weather: '天气',
  geological: '地质灾害',
  logistics: '物流',
  trade_policy: '贸易政策',
  geopolitical: '地缘政治',
  corporate: '企业经营',
  judicial: '司法',
  compliance: '合规',
  other: '其他',
};

const SEVERITY_SAMPLE_LABELS: Record<string, string> = {
  critical: '严重',
  high: '高',
  medium: '中',
  low: '低',
};

/** 维度草稿校验结果（越界 / 阈值顺序 / 阈值范围 / 空匹配柱）。 */
export interface RuleEngineDraftValidation {
  severityOutOfRange: string[];
  associationOutOfRange: string[];
  thresholdOrderInvalid: boolean;
  /** 超出 0-100 范围的阈值键（P1=101 / P3=-1 这类顺序合法但越界的值）。 */
  thresholdOutOfRange: Array<'p1' | 'p2' | 'p3'>;
  matchColumnsEmpty: boolean;
  messages: string[];
}

/** 前端即时校验：与后端 `DimensionConfigPatch` 边界保持一致（severity 0-35、association 0-30、p1>p2>p3、阈值 0-100）。 */
export function validateDimensionDraft(draft: RuleEngineDimensionDraft): RuleEngineDraftValidation {
  const severityOutOfRange = Object.entries(draft.severityScores)
    .filter(([, value]) => !Number.isFinite(value) || value < 0 || value > SEVERITY_MAX)
    .map(([key]) => SEVERITY_LABELS[key] ?? key);
  const associationOutOfRange = Object.entries(draft.associationScores)
    .filter(([, value]) => !Number.isFinite(value) || value < 0 || value > ASSOCIATION_MAX)
    .map(([key]) => ASSOCIATION_LABELS[key] ?? key);
  const {p1, p2, p3} = draft.thresholds;
  const thresholdOrderInvalid =
    !Number.isFinite(p1) || !Number.isFinite(p2) || !Number.isFinite(p3) || !(p1 > p2) || !(p2 > p3);
  // 0-100 边界：输入框的 min/max 属性挡不住直接点「保存配置」，必须与顺序校验同样在发起请求前强制
  const thresholdOutOfRange = (
    [['p1', p1], ['p2', p2], ['p3', p3]] as Array<[('p1' | 'p2' | 'p3'), number]>
  )
    .filter(([, value]) => Number.isFinite(value) && (value < 0 || value > 100))
    .map(([key]) => key);
  const matchColumnsEmpty = draft.matchColumns.length === 0;

  const messages: string[] = [];
  if (severityOutOfRange.length > 0) messages.push(`严重程度分值超出允许范围（0-${SEVERITY_MAX}）：${severityOutOfRange.join('、')}`);
  if (associationOutOfRange.length > 0) messages.push(`关联类型分值超出允许范围（0-${ASSOCIATION_MAX}）：${associationOutOfRange.join('、')}`);
  if (thresholdOrderInvalid) messages.push(`分级阈值必须满足 P1 > P2 > P3（当前 P1=${p1}、P2=${p2}、P3=${p3}），已阻止提交与解算预览`);
  if (thresholdOutOfRange.length > 0) {
    const detail = thresholdOutOfRange.map((key) => `${key.toUpperCase()}=${draft.thresholds[key]}`).join('、');
    messages.push(`分级阈值超出允许范围（0-100）：${detail}，已阻止提交与解算预览`);
  }
  if (matchColumnsEmpty) messages.push('至少保留一个匹配柱，不能全部取消');

  return {severityOutOfRange, associationOutOfRange, thresholdOrderInvalid, thresholdOutOfRange, matchColumnsEmpty, messages};
}

/**
 * 草稿 → 维度 PUT / 预览共用的 patch：复用冻结的 `updateDimensionConfig`，
 * 只包含真正改动的项（未改动时返回空对象，绝不整体覆盖）。
 */
export function dimensionPatchFromDraft(
  dimension: MonitoringDimension,
  draft: RuleEngineDimensionDraft,
): DimensionConfigPatchPayload {
  const updated: MonitoringDimension = {
    ...dimension,
    severityScores: {...draft.severityScores},
    associationScores: {...draft.associationScores},
    thresholds: {
      p1: Number(draft.thresholds.p1),
      p2: Number(draft.thresholds.p2),
      p3: Number(draft.thresholds.p3),
    },
    matchColumns: [...draft.matchColumns],
    eventTypes: [...draft.eventTypes],
  };
  return updateDimensionConfig(dimension, updated) as DimensionConfigPatchPayload;
}

/** 对比行：同一供应商在当前配置与草稿预览下的候选（按 supplier_id 对齐，与数组下标无关）。 */
export interface RuleEnginePreviewComparisonRow {
  supplierId: number;
  supplierName: string;
  matchType: string;
  current: SandboxCandidate | null;
  preview: SandboxCandidate | null;
}

/**
 * 按 `supplier_id` 对齐两次 `/test` 的候选。
 *
 * 两次调用的候选顺序与数量都可能不同（例如改动匹配柱后新增/消失候选），
 * 因此绝不能用数组下标配对；先按当前结果顺序、再补预览独有项，保证渲染稳定。
 */
export function alignCandidatesBySupplierId(
  current: SandboxResult | null,
  preview: SandboxResult | null,
): RuleEnginePreviewComparisonRow[] {
  const currentById = new Map((current?.candidates ?? []).map((candidate) => [candidate.supplier_id, candidate]));
  const previewById = new Map((preview?.candidates ?? []).map((candidate) => [candidate.supplier_id, candidate]));
  const orderedIds: number[] = [];
  for (const candidate of [...(current?.candidates ?? []), ...(preview?.candidates ?? [])]) {
    if (!orderedIds.includes(candidate.supplier_id)) orderedIds.push(candidate.supplier_id);
  }
  return orderedIds.map((supplierId) => {
    const currentCandidate = currentById.get(supplierId) ?? null;
    const previewCandidate = previewById.get(supplierId) ?? null;
    const named = currentCandidate ?? previewCandidate;
    return {
      supplierId,
      supplierName: named?.supplier_name ?? `供应商 #${supplierId}`,
      matchType: previewCandidate?.match_type ?? currentCandidate?.match_type ?? '—',
      current: currentCandidate,
      preview: previewCandidate,
    };
  });
}

function splitValues(value: string): string[] {
  return value.split(/[，,、]/).map((item) => item.trim()).filter(Boolean);
}

function formatDetailValue(value: unknown): string {
  if (typeof value === 'number' && Number.isFinite(value)) return String(value);
  if (typeof value === 'string' && value !== '') return value;
  return '—';
}

/** 与壳组件沙箱面板同字段的样例事件载荷（同一 `context.sample`，只追加草稿字段）。 */
function buildSandboxPayload(
  sample: {
    eventType: string;
    eventSubtype: string;
    severity: 'critical' | 'high' | 'medium' | 'low';
    organization: string;
    registryNo: string;
    location: string;
    region: string;
    city: string;
    countryCode: string;
    district: string;
    products: string;
    industries: string;
    credibility: number;
  },
  summaryPrefix: string,
  eventTypeLabel: string,
): SandboxRequest {
  const countryCode = sample.countryCode.trim().toUpperCase();
  if (countryCode && !/^[A-Z]{2}$/.test(countryCode)) {
    throw new Error('国家/地区代码需填写两位大写字母，例如 CN');
  }
  return {
    event_type: sample.eventType,
    event_subtype: sample.eventSubtype || null,
    severity: sample.severity,
    organizations: sample.organization.trim()
      ? [{name: sample.organization.trim(), aliases: [], registry_no: sample.registryNo.trim() || null}]
      : [],
    locations: sample.location.trim()
      ? [{
          name: sample.location.trim(),
          country_code: countryCode || null,
          region: sample.region.trim() || null,
          city: sample.city.trim() || null,
          district: sample.district.trim() || null,
        }]
      : [],
    affected_products: splitValues(sample.products),
    affected_industries: splitValues(sample.industries),
    summary: `${summaryPrefix}：${eventTypeLabel}`,
    credibility: sample.credibility,
    has_published_at: true,
  };
}

/** 影响说明：该项分值如何作用于总分（不做任何聚合计算，只陈述单项作用与默认值差异）。 */
function impactText(value: number, max: number, defaultValue: number): string {
  if (value === defaultValue) return `直接计入总分，最高 ${max} 分；当前为默认值 ${defaultValue}。`;
  const direction = value > defaultValue ? '高于' : '低于';
  return `直接计入总分，最高 ${max} 分；比默认值 ${defaultValue} ${direction} ${Math.abs(value - defaultValue)} 分。`;
}

function levelBadgeClass(level: string | undefined): string {
  if (level === 'P1') return 'bg-red-600';
  if (level === 'P2') return 'bg-amber-600';
  if (level === 'P3') return 'bg-blue-600';
  return 'bg-slate-500';
}

export interface RuleEngineScoringEditorProps {
  /** 由壳组件控制：仅配置态渲染编辑器本体 */
  mode: RuleEngineMode;
}

/**
 * 配置态-评分与阈值可视化编辑 + 解算预览（组件归属 todo 7）。
 *
 * 写入层：编辑的是**当前选中维度的覆盖**（context.draft），保存仍由壳组件头部
 * 「保存配置」触发（`updateDimension` + `updateDimensionConfig` diff，只发改动项）；
 * 本组件绝不调用 `globalConfig.update` 保存评分，也绝不在未保存时写库。
 *
 * 解算预览对同一样例事件调用两次 `/test`：
 *   1. baseline：不带任何草稿 → 当前生效配置（已保存值）的真实评估；
 *   2. preview：携带 `dimension_key` + 维度草稿 diff（+ 未保存的全局层草稿）→ 未保存配置的真实评估。
 * 两次候选一律按 `supplier_id` 对齐；所有分数/等级/构成都直接渲染后端返回值，
 * 前端不复算任何评分公式（评分/分级/封顶/强制规则全部由后端 `scoring.py` 决定）。
 *
 * 阈值顺序（p1>p2>p3）与 0-100 范围是前端实时门控：违反时给出 `role="alert"` 并禁用解算预览，
 * 且不把无效草稿发往后端；壳组件「保存配置」复用同一个 `validateDimensionDraft`，
 * 非法草稿在任何 API 调用之前就被拦截（后端 422 只作为最后防线）。
 */
export const RuleEngineScoringEditor: React.FC<RuleEngineScoringEditorProps> = ({mode}) => {
  const {dimension, draft, updateDraft, globalDraft, sample, options, optionsError} = useRuleEngineContext();

  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState('');
  const [previewErrorStatus, setPreviewErrorStatus] = useState<number | null>(null);
  const [baseline, setBaseline] = useState<SandboxResult | null>(null);
  const [preview, setPreview] = useState<SandboxResult | null>(null);
  const [previewMeta, setPreviewMeta] = useState<{changedKeys: string[]; includedGlobal: boolean} | null>(null);
  const latestRequest = useRef(0);

  const patch = useMemo(() => dimensionPatchFromDraft(dimension, draft), [dimension, draft]);
  const changedKeys = useMemo(() => Object.keys(patch), [patch]);
  const validation = useMemo(() => validateDimensionDraft(draft), [draft]);
  const hasGlobalDraft = Object.keys(globalDraft).length > 0;
  const eventTypeLabel = options.event_types.find((item) => item.value === sample.eventType)?.label ?? sample.eventType;
  const canPreview = validation.messages.length === 0 && !previewLoading && options.event_types.length > 0;

  const rows = useMemo(() => alignCandidatesBySupplierId(baseline, preview), [baseline, preview]);

  const updateScore = (group: 'severityScores' | 'associationScores', key: string, value: number) => {
    if (group === 'severityScores') {
      updateDraft({severityScores: {...draft.severityScores, [key]: value}});
      return;
    }
    updateDraft({associationScores: {...draft.associationScores, [key]: value}});
  };

  const updateThreshold = (key: 'p1' | 'p2' | 'p3', value: number) => {
    updateDraft({thresholds: {...draft.thresholds, [key]: value}});
  };

  /** 解算预览：先 baseline（不带草稿）→ 再 preview（带草稿），顺序固定且各只发一次。 */
  const handleSolvePreview = async () => {
    if (!canPreview) return;
    const requestId = latestRequest.current + 1;
    latestRequest.current = requestId;
    setPreviewLoading(true);
    setPreviewError('');
    setPreviewErrorStatus(null);
    setBaseline(null);
    setPreview(null);
    setPreviewMeta(null);
    try {
      const basePayload = buildSandboxPayload(sample, '解算预览', eventTypeLabel);
      const baselineResult = await api.testRuleEngine(basePayload);
      const previewResult = await api.testRuleEngine({
        ...basePayload,
        dimension_key: dimension.id,
        draft_config: patch,
        ...(hasGlobalDraft ? {global_config: globalDraft} : {}),
      });
      if (latestRequest.current !== requestId) return;
      setBaseline(baselineResult);
      setPreview(previewResult);
      setPreviewMeta({changedKeys, includedGlobal: hasGlobalDraft});
    } catch (caught) {
      if (latestRequest.current !== requestId) return;
      setPreviewErrorStatus(caught instanceof ApiError ? caught.status : null);
      setPreviewError(
        caught instanceof Error
          ? caught.message
          : '解算预览失败，请检查草稿配置',
      );
    } finally {
      if (latestRequest.current === requestId) setPreviewLoading(false);
    }
  };

  if (mode !== 'config') return null;

  const scaleStops: Array<{level: string; value: number; className: string}> = [
    {level: 'P3', value: draft.thresholds.p3, className: 'bg-blue-500'},
    {level: 'P2', value: draft.thresholds.p2, className: 'bg-amber-500'},
    {level: 'P1', value: draft.thresholds.p1, className: 'bg-red-500'},
  ];

  /**
   * 滑杆的可用区间按 P1 > P2 > P3 动态收窄：拖拽在物理上就无法制造非法顺序，
   * 数字输入仍允许键入越界值（由 role=alert 实时校验 + 预览门控处理）。
   */
  const clampPercent = (value: number) => Math.max(0, Math.min(100, value));
  const sliderBounds: Record<'p1' | 'p2' | 'p3', {min: number; max: number}> = {
    p1: {min: clampPercent(draft.thresholds.p2 + 1), max: 100},
    p2: {min: clampPercent(draft.thresholds.p3 + 1), max: clampPercent(draft.thresholds.p1 - 1)},
    p3: {min: 0, max: clampPercent(draft.thresholds.p2 - 1)},
  };

  return (
    <section className="space-y-5" data-testid="rule-engine-scoring-editor" data-mode={mode}>
      <header className="space-y-1">
        <h3 className="text-[13px] font-bold text-slate-800 dark:text-slate-200">评分与阈值</h3>
        <p className="text-[11px] leading-relaxed text-slate-500 dark:text-slate-400">
          这里改的是当前维度「{dimension.name}」的覆盖值，改完点页面上方「保存配置」才会写入数据库。
          每项都标注了它对总分的作用；拖动滑杆或用输入框精确输入都可以。
        </p>
      </header>

      {/* 严重程度分值 */}
      <section className="space-y-2" data-testid="rule-engine-severity-editor">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h4 className="text-[13px] font-bold text-slate-800 dark:text-slate-200">
            严重程度分值（0-{SEVERITY_MAX}）
          </h4>
          <span className="text-[11px] text-slate-500 dark:text-slate-400">按事件的严重程度直接加分</span>
        </div>
        <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
          {Object.keys(draft.severityScores).map((key) => {
            const label = SEVERITY_LABELS[key] ?? key;
            const value = draft.severityScores[key];
            const defaultValue = DEFAULT_SEVERITY_SCORES[key] ?? value;
            return (
              <div
                key={key}
                className="min-w-0 rounded-xl border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900/40 p-3 space-y-1.5"
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="text-[12px] font-bold text-slate-700 dark:text-slate-200">{label}</span>
                  <div className="flex items-center gap-2">
                    <input
                      type="range"
                      min={0}
                      max={SEVERITY_MAX}
                      step={1}
                      value={value}
                      aria-label={`严重程度 ${label} 滑杆`}
                      onChange={(e) => updateScore('severityScores', key, Number(e.target.value))}
                      className="h-1.5 w-28 accent-[#004782] dark:accent-blue-400"
                    />
                    <input
                      type="number"
                      min={0}
                      max={SEVERITY_MAX}
                      value={value}
                      aria-label={`严重程度 ${label}`}
                      aria-invalid={validation.severityOutOfRange.includes(label)}
                      onChange={(e) => updateScore('severityScores', key, Number(e.target.value))}
                      className="w-16 rounded border border-slate-300 bg-white px-2 py-0.5 text-right font-mono text-[12px] font-bold text-slate-900 dark:border-slate-600 dark:bg-slate-950 dark:text-white"
                    />
                  </div>
                </div>
                <p className="text-[11px] leading-relaxed text-slate-500 dark:text-slate-400">
                  {impactText(value, SEVERITY_MAX, defaultValue)}
                </p>
              </div>
            );
          })}
        </div>
      </section>

      {/* 关联类型分值 */}
      <section className="space-y-2" data-testid="rule-engine-association-editor">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h4 className="text-[13px] font-bold text-slate-800 dark:text-slate-200">
            关联类型分值（0-{ASSOCIATION_MAX}）
          </h4>
          <span className="text-[11px] text-slate-500 dark:text-slate-400">按事件与供应商的关联方式加分</span>
        </div>
        <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
          {Object.keys(draft.associationScores).map((key) => {
            const label = ASSOCIATION_LABELS[key] ?? key;
            const value = draft.associationScores[key];
            const defaultValue = DEFAULT_ASSOCIATION_SCORES[key] ?? value;
            return (
              <div
                key={key}
                className="min-w-0 rounded-xl border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900/40 p-3 space-y-1.5"
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="text-[12px] font-bold text-slate-700 dark:text-slate-200">{label}</span>
                  <div className="flex items-center gap-2">
                    <input
                      type="range"
                      min={0}
                      max={ASSOCIATION_MAX}
                      step={1}
                      value={value}
                      aria-label={`关联类型 ${label} 滑杆`}
                      onChange={(e) => updateScore('associationScores', key, Number(e.target.value))}
                      className="h-1.5 w-28 accent-[#004782] dark:accent-blue-400"
                    />
                    <input
                      type="number"
                      min={0}
                      max={ASSOCIATION_MAX}
                      value={value}
                      aria-label={`关联类型 ${label}`}
                      aria-invalid={validation.associationOutOfRange.includes(label)}
                      onChange={(e) => updateScore('associationScores', key, Number(e.target.value))}
                      className="w-16 rounded border border-slate-300 bg-white px-2 py-0.5 text-right font-mono text-[12px] font-bold text-slate-900 dark:border-slate-600 dark:bg-slate-950 dark:text-white"
                    />
                  </div>
                </div>
                <p className="text-[11px] leading-relaxed text-slate-500 dark:text-slate-400">
                  {impactText(value, ASSOCIATION_MAX, defaultValue)}
                </p>
              </div>
            );
          })}
        </div>
      </section>

      {/* 分级阈值 0-100 刻度尺 */}
      <section className="space-y-3" data-testid="rule-engine-threshold-editor">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h4 className="text-[13px] font-bold text-slate-800 dark:text-slate-200">分级阈值（0-100）</h4>
          <span className="text-[11px] text-slate-500 dark:text-slate-400">
            总分达到对应阈值即进入该等级；必须满足 P1 &gt; P2 &gt; P3
          </span>
        </div>

        <div data-testid="rule-engine-threshold-scale" className="space-y-1.5">
          <div className="flex w-full overflow-hidden rounded-full text-[10px] font-bold leading-5 text-white">
            <div className="bg-slate-400 text-center" style={{width: `${Math.max(0, Math.min(100, draft.thresholds.p3))}%`}}>
              {draft.thresholds.p3 >= 12 && <span className="px-1">P4</span>}
            </div>
            <div
              className="bg-blue-500 text-center"
              style={{width: `${Math.max(0, Math.min(100, draft.thresholds.p2 - draft.thresholds.p3))}%`}}
            >
              {draft.thresholds.p2 - draft.thresholds.p3 >= 12 && <span className="px-1">P3</span>}
            </div>
            <div
              className="bg-amber-500 text-center"
              style={{width: `${Math.max(0, Math.min(100, draft.thresholds.p1 - draft.thresholds.p2))}%`}}
            >
              {draft.thresholds.p1 - draft.thresholds.p2 >= 12 && <span className="px-1">P2</span>}
            </div>
            <div className="bg-red-500 text-center" style={{width: `${Math.max(0, Math.min(100, 100 - draft.thresholds.p1))}%`}}>
              {100 - draft.thresholds.p1 >= 12 && <span className="px-1">P1</span>}
            </div>
          </div>
          <div className="flex justify-between font-mono text-[10px] text-slate-500 dark:text-slate-400">
            <span>0</span>
            <span>{draft.thresholds.p3}</span>
            <span>{draft.thresholds.p2}</span>
            <span>{draft.thresholds.p1}</span>
            <span>100</span>
          </div>
        </div>

        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          {scaleStops.map(({level, value, className}) => (
            <div
              key={level}
              className="space-y-1.5 rounded-xl border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900/40 p-3"
            >
              <div className="flex items-center gap-2">
                <span className={`h-2.5 w-2.5 rounded-full ${className}`} aria-hidden="true" />
                <span className="text-[12px] font-bold text-slate-700 dark:text-slate-200">
                  {level} 阈值
                </span>
              </div>
              <div className="flex items-center gap-2 text-[12px] font-bold text-slate-600 dark:text-slate-300">
                <span aria-hidden="true">≥</span>
                <input
                  type="number"
                  min={0}
                  max={100}
                  aria-label={`${level} 触发阈值`}
                  aria-invalid={
                    validation.thresholdOrderInvalid ||
                    validation.thresholdOutOfRange.includes(level.toLowerCase() as 'p1' | 'p2' | 'p3')
                  }
                  value={value}
                  onChange={(e) => updateThreshold(level.toLowerCase() as 'p1' | 'p2' | 'p3', Number(e.target.value))}
                  className="w-20 rounded border border-slate-300 bg-white px-2 py-0.5 text-center font-mono text-[12px] font-bold text-slate-900 dark:border-slate-600 dark:bg-slate-950 dark:text-white"
                />
                <span className="text-[11px] font-normal text-slate-500 dark:text-slate-400">分</span>
              </div>
              <input
                type="range"
                min={sliderBounds[level.toLowerCase() as 'p1' | 'p2' | 'p3'].min}
                max={sliderBounds[level.toLowerCase() as 'p1' | 'p2' | 'p3'].max}
                step={1}
                value={value}
                aria-label={`${level} 触发阈值滑杆`}
                onChange={(e) => updateThreshold(level.toLowerCase() as 'p1' | 'p2' | 'p3', Number(e.target.value))}
                className="h-1.5 w-full accent-[#004782] dark:accent-blue-400"
              />
              <p className="text-[11px] leading-relaxed text-slate-500 dark:text-slate-400">
                总分 ≥ {value} 分判定为 {level}；低于 {value} 分则看下一档阈值。
              </p>
            </div>
          ))}
        </div>
      </section>

      {validation.messages.length > 0 && (
        <div
          role="alert"
          data-testid="rule-engine-scoring-validation"
          className="space-y-1 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-[12px] text-red-700 dark:border-red-900 dark:bg-red-950/30 dark:text-red-300"
        >
          {validation.messages.map((message) => (
            <p key={message}>{message}</p>
          ))}
        </div>
      )}

      {/* 两条封顶规则说明 */}
      <section className="space-y-2" data-testid="rule-engine-level-caps">
        <h4 className="text-[13px] font-bold text-slate-800 dark:text-slate-200">两条封顶规则（计分之后、强制规则之前生效）</h4>
        <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
          {LEVEL_CAP_EXPLANATIONS.map((rule) => (
            <article
              key={rule.key}
              data-testid={`rule-engine-level-cap-${rule.key}`}
              className="min-w-0 rounded-xl border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900/40 p-3"
            >
              <p className="flex flex-wrap items-center gap-1.5 text-[12px] font-bold text-slate-700 dark:text-slate-200">
                <span>{rule.title}</span>
                <span className="rounded-full bg-blue-50 px-2 py-0.5 text-[10px] font-bold text-[#004782] dark:bg-blue-950/40 dark:text-blue-300">
                  {rule.cap}
                </span>
              </p>
              <p className="mt-1 text-[11px] leading-relaxed text-slate-500 dark:text-slate-400">{rule.detail}</p>
            </article>
          ))}
        </div>
      </section>

      {/* 解算预览 */}
      <section
        data-testid="rule-engine-solve-preview-panel"
        className="space-y-3 rounded-xl border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900/40 p-4"
      >
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="min-w-0">
            <h4 className="text-[13px] font-bold text-slate-800 dark:text-slate-200">解算预览</h4>
            <p className="mt-0.5 text-[11px] leading-relaxed text-slate-500 dark:text-slate-400">
              用「沙箱测试」里的样例事件（当前：{PREVIEW_EVENT_LABELS[sample.eventType] ?? eventTypeLabel} ·
              {SEVERITY_SAMPLE_LABELS[sample.severity] ?? sample.severity}）调用真实引擎评估两次：一次当前生效配置，
              一次带上未保存草稿；只读，不创建任何记录。
            </p>
          </div>
          <button
            type="button"
            data-testid="rule-engine-solve-preview"
            onClick={() => void handleSolvePreview()}
            disabled={!canPreview}
            title={validation.messages.length > 0 ? '存在校验错误，先修正后再解算' : undefined}
            className="inline-flex min-h-9 shrink-0 items-center gap-1.5 rounded-lg border-2 border-[#004782] px-3 py-1.5 text-[12px] font-bold text-[#004782] transition-colors hover:bg-blue-50 disabled:cursor-not-allowed disabled:opacity-50 dark:border-blue-400 dark:text-blue-300 dark:hover:bg-slate-800"
          >
            <span className="material-symbols-outlined text-[18px]" aria-hidden="true">calculate</span>
            {previewLoading ? '解算中…' : '解算预览'}
          </button>
        </div>

        {optionsError && (
          <p className="text-[11px] text-amber-700 dark:text-amber-300">
            事件类型选项加载失败（{optionsError}），解算预览暂不可用。
          </p>
        )}

        {validation.messages.length > 0 && (
          <p className="text-[11px] text-red-700 dark:text-red-300">
            当前草稿未通过校验，已阻止解算与提交；修正后即可预览。
          </p>
        )}

        {previewError && (
          <div
            role="alert"
            data-testid="rule-engine-solve-preview-error"
            className="space-y-1 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-[12px] text-red-700 dark:border-red-900 dark:bg-red-950/30 dark:text-red-300"
          >
            <p className="font-bold">解算预览失败{previewErrorStatus === null ? '' : `（HTTP ${previewErrorStatus}）`}：{previewError}</p>
            <p>该结果未生效，也没有任何配置被保存；请修正草稿冲突或校验错误后重试。</p>
          </div>
        )}

        {(baseline !== null || preview !== null) && (
          <div data-testid="rule-engine-solve-preview-result" className="space-y-3">
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              <div className="rounded-lg border border-slate-200 bg-white px-3 py-2 dark:border-slate-700 dark:bg-slate-950/40">
                <p className="text-[11px] font-bold text-slate-500 dark:text-slate-400">当前（页面已保存的配置）</p>
                <p className="mt-0.5 text-[12px] text-slate-700 dark:text-slate-300">
                  接管维度：{baseline?.dimension?.label ?? '未找到接管维度'} · 命中 {baseline?.candidates.length ?? 0} 个候选
                </p>
              </div>
              <div className="rounded-lg border border-blue-200 bg-blue-50/60 px-3 py-2 dark:border-blue-900 dark:bg-blue-950/20">
                <p className="text-[11px] font-bold text-[#004782] dark:text-blue-300">预览（未保存草稿）</p>
                <p className="mt-0.5 text-[12px] text-slate-700 dark:text-slate-300">
                  接管维度：{preview?.dimension?.label ?? '未找到接管维度'} · 命中 {preview?.candidates.length ?? 0} 个候选
                </p>
              </div>
            </div>

            <p data-testid="rule-engine-solve-preview-meta" className="text-[11px] leading-relaxed text-slate-500 dark:text-slate-400">
              解算输入：当前配置（不带草稿）→ 未保存草稿（
              {previewMeta && previewMeta.changedKeys.length > 0
                ? previewMeta.changedKeys.join('、')
                : '无改动项，两次结果应一致'}
              {previewMeta?.includedGlobal ? '，并携带未保存的全局层草稿' : ''}
              ）。预览结果不会写入数据库。
            </p>

            {rows.length === 0 ? (
              <p className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-[12px] text-slate-600 dark:border-slate-700 dark:bg-slate-950/40 dark:text-slate-300">
                两次评估都没有命中供应商。可在样例事件里补充供应商全称/注册号、地点、产品或行业后重试；这不表示该事件没有风险。
              </p>
            ) : (
              <ul className="space-y-2">
                {rows.map((row) => {
                  const currentLabel = row.current === null ? '未命中' : `${row.current.level} · ${row.current.score} 分`;
                  const previewLabel = row.preview === null ? '未命中' : `${row.preview.level} · ${row.preview.score} 分`;
                  const changed =
                    row.current !== null && row.preview !== null &&
                    (row.current.level !== row.preview.level || row.current.score !== row.preview.score);
                  return (
                    <li
                      key={row.supplierId}
                      data-testid={`rule-engine-preview-candidate-${row.supplierId}`}
                      className="min-w-0 space-y-2 rounded-lg border border-slate-200 bg-white p-3 dark:border-slate-700 dark:bg-slate-950/40"
                    >
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <span className="min-w-0 truncate text-[12px] font-bold text-slate-800 dark:text-white">
                          {row.supplierName}
                          <span className="ml-1 font-mono text-[10px] font-normal text-slate-400">#{row.supplierId}</span>
                        </span>
                        <span className="text-[11px] text-slate-500 dark:text-slate-400">匹配方式：{row.matchType}</span>
                      </div>

                      <div className="grid grid-cols-2 gap-2 text-[12px]">
                        <div className="rounded-md border border-slate-200 px-2 py-1 dark:border-slate-700">
                          <span className="block text-[10px] font-bold text-slate-500 dark:text-slate-400">当前</span>
                          <span className={`mt-0.5 inline-block rounded px-1.5 py-0.5 font-bold text-white ${levelBadgeClass(row.current?.level)}`}>
                            {currentLabel}
                          </span>
                        </div>
                        <div className="rounded-md border border-blue-200 bg-blue-50/50 px-2 py-1 dark:border-blue-900 dark:bg-blue-950/20">
                          <span className="block text-[10px] font-bold text-slate-500 dark:text-slate-400">预览</span>
                          <span className={`mt-0.5 inline-block rounded px-1.5 py-0.5 font-bold text-white ${levelBadgeClass(row.preview?.level)}`}>
                            {previewLabel}
                          </span>
                        </div>
                      </div>

                      <p className="text-[11px] font-bold text-slate-600 dark:text-slate-300">
                        变化：
                        {row.current === null
                          ? '预览新增命中'
                          : row.preview === null
                            ? '预览不再命中'
                            : changed
                              ? `${row.current.level} ${row.current.score} 分 → ${row.preview.level} ${row.preview.score} 分`
                              : '分数与等级均无变化'}
                      </p>

                      <div className="space-y-1">
                        <span className="text-[10px] font-bold text-slate-500 dark:text-slate-400">分值构成（当前 → 预览，直接取自后端评分明细）</span>
                        <div className="flex flex-wrap gap-1.5">
                          {SCORE_DETAIL_FIELDS.map(([key, label]) => {
                            const currentValue = formatDetailValue(row.current?.score_detail?.[key]);
                            const previewValue = formatDetailValue(row.preview?.score_detail?.[key]);
                            const same = currentValue === previewValue;
                            return (
                              <span
                                key={key}
                                className={`rounded-md border px-1.5 py-0.5 font-mono text-[10px] ${
                                  same
                                    ? 'border-slate-200 text-slate-600 dark:border-slate-700 dark:text-slate-300'
                                    : 'border-amber-300 bg-amber-50 text-amber-800 dark:border-amber-700 dark:bg-amber-950/30 dark:text-amber-300'
                                }`}
                              >
                                {label} {same ? currentValue : `${currentValue} → ${previewValue}`}
                              </span>
                            );
                          })}
                          {[row.current, row.preview].map((candidate, index) => {
                            const cap = candidate?.score_detail?.level_cap;
                            if (typeof cap !== 'string') return null;
                            return (
                              <span
                                key={`cap-${index}`}
                                className="rounded-md border border-blue-300 bg-blue-50 px-1.5 py-0.5 text-[10px] font-bold text-[#004782] dark:border-blue-800 dark:bg-blue-950/30 dark:text-blue-300"
                              >
                                {index === 0 ? '当前' : '预览'} {LEVEL_CAP_LABELS[cap] ?? `封顶：${cap}`}
                              </span>
                            );
                          })}
                          {[row.current, row.preview].map((candidate, index) => {
                            const forced = candidate?.score_detail?.forced_rule;
                            if (typeof forced !== 'object' || forced === null) return null;
                            const name = (forced as Record<string, unknown>).name;
                            if (typeof name !== 'string') return null;
                            return (
                              <span
                                key={`forced-${index}`}
                                className="rounded-md border border-red-300 bg-red-50 px-1.5 py-0.5 text-[10px] font-bold text-red-700 dark:border-red-800 dark:bg-red-950/30 dark:text-red-300"
                              >
                                {index === 0 ? '当前' : '预览'} 强制规则命中：{name}
                              </span>
                            );
                          })}
                        </div>
                      </div>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        )}
      </section>
    </section>
  );
};
