import React from 'react';
import {motion, type MotionProps, useReducedMotion} from 'motion/react';
import {
  CircleAlert,
  CircleCheck,
  CircleDashed,
  Crosshair,
  Flag,
  Gauge,
  Layers,
  Radio,
  Route as RouteIcon,
  ShieldAlert,
  type LucideIcon,
} from 'lucide-react';
import type {DimensionInputsRead, DimensionTraceRead} from '../api';
import type {MonitoringDimension} from '../types';

/**
 * 观察态-规则运行流水线（todo 5）。
 *
 * 用 SVG/CSS + motion 把真实 `dimensionTrace` 讲成七段：
 * ① 信号输入 → ② 事件路由 → ③ 匹配柱 → ④ 得分构成 → ⑤ 总分层 →
 * ⑥ 封顶与强制规则 → ⑦ 输出等级。
 *
 * 约束（计划 todo 5）：
 * - 数据全部来自 props（`dimensionTrace` / `dimensionInputs`），不伪造数值；
 *   无真实样本时给出"当前无真实样本，请选择样例事件"引导，各阶段以占位符降级。
 * - 只使用已有 `motion` + `lucide-react` + SVG/CSS，不引入第三方图形库。
 * - 尊重 `useReducedMotion`（范式同 `RuleEngineView.tsx`）：减少动效时不下发任何位移动画 props。
 * - 深色模式（`dark:` 变体）与 320px 窄屏（`min-w-0` + `overflow-x-auto`）必须可用。
 * - 对缺字段/空轨迹做防御式解析，绝不因脏数据崩溃。
 */

/* ---------- 展示映射（与后端枚举一致，仅用于人话标签） ---------- */

const MATCH_COLUMN_LABELS: Record<string, string> = {
  entity: '主体',
  location: '地点',
  product: '产品',
  country: '国家/区域',
  industry: '行业/原材料',
};

/**
 * `MatchType` → `MatchColumn` 映射。
 *
 * 权威口径：`backend/app/risks/workbench_schemas.py:16-26`——
 * - `MatchColumn = entity | location | product | country | industry`（匹配柱；维度 `match_columns` / routing 用它）；
 * - `MatchType = registry_no | legal_name | alias | site_distance | site_text | product | country | industry`
 *   （素材命中方式；轨迹 `match.match_type` 用它，取自 `matching.py` 各匹配器写入的值）。
 *
 * 两者不是同一枚举：直接拿 `match_type` 与柱名比较，会让 registry_no / legal_name / alias /
 * site_distance / site_text 永远无法点亮「主体」「地点」柱。
 */
const MATCH_TYPE_TO_COLUMN: Record<string, string> = {
  registry_no: 'entity',
  legal_name: 'entity',
  alias: 'entity',
  site_distance: 'location',
  site_text: 'location',
  product: 'product',
  country: 'country',
  industry: 'industry',
};

const EVENT_TYPE_LABELS: Record<string, string> = {
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

const EVENT_SUBTYPE_LABELS: Record<string, string> = {
  weather_alert: '气象预警',
  geological_hazard: '地质灾害',
  armed_conflict: '武装冲突',
  sanctions: '制裁',
  export_control: '出口管制',
  political_instability: '政治不稳定',
  public_security: '公共安全',
  trade_tariff: '关税与一般贸易摩擦',
  regulatory_change: '监管政策变化',
  raw_material_shortage: '原材料短缺',
  transport_disruption: '运输中断',
  corporate_distress: '企业经营异常',
  judicial_case: '司法案件',
  compliance_violation: '合规违规',
};

const SEVERITY_LABELS: Record<string, string> = {
  critical: '严重',
  high: '高',
  medium: '中',
  low: '低',
};

const LEVEL_NAMES: Record<string, string> = {
  P1: '重大风险',
  P2: '高风险',
  P3: '中风险',
  P4: '低风险',
};

const LEVEL_CHIP_CLASS: Record<string, string> = {
  P1: 'bg-[#C92A2A]',
  P2: 'bg-[#D97706]',
  P3: 'bg-[#2563EB]',
  P4: 'bg-[#64748B]',
};

const LEVEL_ZONE_CLASS: Record<string, string> = {
  P1: 'bg-[#C92A2A]',
  P2: 'bg-[#D97706]',
  P3: 'bg-[#2563EB]',
  P4: 'bg-[#94A3B8]',
};

/** 得分五项构成：与 `backend/app/risks/scoring.py:compute_score` 的 detail 键一一对应。 */
const SCORE_COMPONENTS: Array<{key: string; label: string; hint: string; barClass: string}> = [
  {key: 'severity', label: '严重程度', hint: '事件严重度分值', barClass: 'bg-[#C92A2A]'},
  {key: 'association', label: '关联强度', hint: '命中匹配柱的关联分值', barClass: 'bg-[#2563EB]'},
  {key: 'source_credibility', label: '来源可信度', hint: '信源可信度 × 权重', barClass: 'bg-[#0E9F6E]'},
  {key: 'timeliness', label: '时效性', hint: '有发布时间时的时效加分', barClass: 'bg-[#D97706]'},
  {key: 'product_relevance', label: '产品相关性', hint: '命中供应产品时的加分', barClass: 'bg-[#64748B]'},
];

/** `score_detail.level_cap` 两种取值的权威语义（`scoring.py:apply_level_cap`）。 */
const CAP_EXPLANATIONS: Record<string, {title: string; detail: string}> = {
  country_only_max_p4: {
    title: '仅命中「国家/区域」柱',
    detail: '只关联到国家这一层，证据太弱，等级最高只能是 P4。',
  },
  weak_association_max_p2: {
    title: '未命中主体精确匹配',
    detail: '没有命中供应商主体（注册号/法人全称/别名），等级最高只能是 P2。',
  },
};

/* ---------- 防御式解析：脏数据只降级、不抛错 ---------- */

function asArray<T>(value: unknown): T[] {
  return Array.isArray(value) ? (value as T[]) : [];
}

function asText(value: unknown, fallback = ''): string {
  if (typeof value === 'string') return value;
  if (typeof value === 'number' && Number.isFinite(value)) return String(value);
  return fallback;
}

function asNumber(value: unknown, fallback = 0): number {
  const parsed = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

/**
 * 缺失数值探测：只有 API 明确返回有限数字才算"有值"，否则为 null。
 * 用于把"真实 0"与"字段缺失"区分开——缺失渲染 —，不伪造 0。
 */
function asOptionalNumber(value: unknown): number | null {
  if (value === null || value === undefined || value === '') return null;
  const parsed = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

/** 数值展示占位：缺失 → —（em dash），有值 → 原样数字。 */
function formatScore(value: number | null): string {
  return value === null ? '—' : String(value);
}

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function clampPercent(value: number): number {
  return Math.min(Math.max(value, 0), 100);
}

function formatMoment(value: unknown): string {
  const text = asText(value);
  if (!text) return '时间未披露';
  const date = new Date(text);
  if (Number.isNaN(date.getTime())) return '时间未披露';
  return date.toLocaleString('zh-CN', {hour12: false, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit'});
}

/** 减少动效时返回空 props：motion 组件完全静止，也不会留下任何 transform 内联样式。 */
function entranceMotion(reduceMotion: boolean, delay = 0): MotionProps {
  return reduceMotion
    ? {}
    : {
        initial: {opacity: 0, y: 8},
        animate: {opacity: 1, y: 0},
        transition: {duration: 0.22, ease: 'easeOut', delay},
      };
}

function drawMotion(reduceMotion: boolean): MotionProps {
  return reduceMotion
    ? {}
    : {initial: {pathLength: 0}, animate: {pathLength: 1}, transition: {duration: 0.35, ease: 'easeOut'}};
}

/* ---------- 阶段外壳（左侧节点 + SVG 连接线 + 内容卡） ---------- */

interface StageShellProps {
  stageId: string;
  label: string;
  icon: LucideIcon;
  reduceMotion: boolean;
  delay: number;
  isLast: boolean;
  children: React.ReactNode;
}

const StageShell: React.FC<StageShellProps> = ({stageId, label, icon: Icon, reduceMotion, delay, isLast, children}) => (
  <li
    data-stage={stageId}
    data-testid={`rule-engine-pipeline-stage-${stageId}`}
    className="relative flex gap-3 sm:gap-4"
  >
    <div className="flex w-7 shrink-0 flex-col items-center" aria-hidden="true">
      <motion.span
        {...entranceMotion(reduceMotion, delay)}
        className="flex h-7 w-7 items-center justify-center rounded-full border border-[#185fa5]/30 bg-blue-50 text-[#007aff] dark:border-blue-400/30 dark:bg-slate-800 dark:text-blue-300"
      >
        <Icon className="h-3.5 w-3.5" />
      </motion.span>
      {!isLast && (
        // SVG 连接线：不减少动效时用 pathLength 画出推进方向；减少动效时静态直连。
        <svg viewBox="0 0 2 100" preserveAspectRatio="none" className="w-0.5 flex-1" aria-hidden="true">
          <motion.line
            x1="1"
            y1="0"
            x2="1"
            y2="100"
            strokeWidth="2"
            strokeLinecap="round"
            className="stroke-slate-200 dark:stroke-slate-700"
            {...drawMotion(reduceMotion)}
          />
        </svg>
      )}
    </div>
    <div
      data-stage-body
      className="min-w-0 flex-1 space-y-1.5 break-words rounded-xl border border-slate-200 bg-white p-3 dark:border-slate-700 dark:bg-slate-900"
    >
      <p className="text-[11px] font-bold tracking-wide text-[#007aff] dark:text-blue-300">{label}</p>
      {children}
    </div>
  </li>
);

/* ---------- 主组件 ---------- */

export interface RuleEnginePipelineProps {
  dimension: MonitoringDimension;
  trace: DimensionTraceRead | null;
  traceError: string;
  inputs: DimensionInputsRead | null;
  inputsError: string;
  selectedSampleId: number | null;
}

export const RuleEnginePipeline: React.FC<RuleEnginePipelineProps> = ({
  dimension,
  trace,
  traceError,
  inputs,
  inputsError,
  selectedSampleId,
}) => {
  // motion 的 useReducedMotion 类型为 boolean | null：未知时按"不减少"处理，显式 true 才静止。
  const reduceMotion = useReducedMotion() === true;

  if (traceError) {
    return (
      <section
        role="alert"
        data-testid="rule-engine-pipeline"
        className="min-w-0 rounded-2xl border border-red-200 bg-red-50 p-4 text-[12px] text-red-700 dark:border-red-900 dark:bg-red-950/30 dark:text-red-300"
      >
        运行轨迹加载失败：{traceError}
      </section>
    );
  }

  if (trace === null) {
    return (
      <section
        data-testid="rule-engine-pipeline"
        data-available="false"
        className="min-w-0 rounded-2xl border border-slate-200/80 bg-white/80 p-4 shadow-sm dark:border-slate-700/60 dark:bg-slate-800/60"
      >
        <h2 className="text-[15px] font-bold text-[#101d28] dark:text-white">规则运行流水线</h2>
        <p className="mt-2 flex items-center gap-1.5 text-[12px] text-slate-400 dark:text-slate-500">
          <span className="sr-only">运行轨迹加载中…</span>
        </p>
      </section>
    );
  }

  const available = trace.available === true;
  const event = trace.event ?? null;
  const routing = trace.routing ?? null;
  const match = trace.match ?? null;
  const score = trace.score ?? null;
  const detail = asRecord(score?.detail);

  /* —— ① 信号输入 —— */
  const observedSources = asArray<{code?: unknown; name?: unknown; signal_count?: unknown; latest_at?: unknown}>(inputs?.observed);
  const declaredTotal = asNumber(inputs?.declared_total, 0);
  const declaredLinked = asNumber(inputs?.declared_linked, 0);
  const declaredEnabled = asNumber(inputs?.declared_enabled, 0);
  const linkedSources = asArray<MonitoringDimension['dataSources'][number]>(dimension?.dataSources).filter(
    (source) => source?.linked === true,
  );

  /* —— ② 事件路由 —— */
  const rawEventType = asText(event?.event_type);
  const rawEventSubtype = asText(event?.event_subtype);
  const eventTypeLabel = available ? EVENT_TYPE_LABELS[rawEventType] ?? (rawEventType || '未知事件类型') : '等待样例事件';
  // 保持「事件 → 接管维度」在同一文本节点：壳层测试以 /→ 自然环境/ 断言路由行。
  const routingLabel = available ? asText(routing?.label) || '未找到接管维度' : '等待样例事件';

  /* —— ③ 匹配柱 —— */
  const configuredColumns = asArray<string>(routing?.match_columns);
  const enabledColumns = configuredColumns.length > 0 ? configuredColumns : asArray<string>(dimension?.matchColumns);
  const matchTypeText = asText(match?.match_type);
  // `match.match_type` 是 MatchType（registry_no/site_text/…），先经映射归一到
  // MatchColumn（entity/location/…）再与维度启用的匹配柱比较，避免枚举不同名导致漏高亮。
  const hitColumns = new Set(
    matchTypeText
      .split('+')
      .map((item) => item.trim())
      .map((token) => MATCH_TYPE_TO_COLUMN[token])
      .filter((column): column is string => Boolean(column)),
  );
  const matchReasons = asArray<string>(match?.match_reasons).filter((reason) => typeof reason === 'string');
  const evidenceCount = asArray<unknown>(match?.match_evidence).length;

  /* —— ④ 得分构成 —— */
  const componentValues = SCORE_COMPONENTS.map((component) => ({
    ...component,
    value: asOptionalNumber(detail[component.key]),
  }));
  const missingComponentCount = componentValues.filter((component) => component.value === null).length;
  const componentSum = componentValues.reduce((total, component) => total + (component.value ?? 0), 0);

  /* —— ⑤ 总分层：0–100 + P1/P2/P3 刻度 —— */
  // 缺失即 null：不渲染总分标记、不伪造阈值；只有 API 明确返回的 0 才显示为 0。
  const totalRaw = asOptionalNumber(score?.total);
  const total = totalRaw === null ? null : clampPercent(totalRaw);
  const p1Raw = asOptionalNumber(dimension?.thresholds?.p1);
  const p2Raw = asOptionalNumber(dimension?.thresholds?.p2);
  const p3Raw = asOptionalNumber(dimension?.thresholds?.p3);
  const p1 = p1Raw === null ? null : clampPercent(p1Raw);
  const p2 = p2Raw === null ? null : clampPercent(p2Raw);
  const p3 = p3Raw === null ? null : clampPercent(p3Raw);
  const hasAllThresholds = p1 !== null && p2 !== null && p3 !== null;
  const hasAnyThreshold = p1 !== null || p2 !== null || p3 !== null;
  const thresholdsText = hasAllThresholds
    ? `等级刻度：P3 ≥ ${p3} · P2 ≥ ${p2} · P1 ≥ ${p1}（满分 100）`
    : hasAnyThreshold
      ? `等级刻度：P3 ≥ ${p3 ?? '—'} · P2 ≥ ${p2 ?? '—'} · P1 ≥ ${p1 ?? '—'}（部分阈值未提供；满分 100）`
      : '等级刻度：阈值未提供（满分 100）';
  const zones = hasAllThresholds
    ? [
        {key: 'P4', from: 0, to: p3, width: Math.max(0, p3 - 0)},
        {key: 'P3', from: p3, to: p2, width: Math.max(0, p2 - p3)},
        {key: 'P2', from: p2, to: p1, width: Math.max(0, p1 - p2)},
        {key: 'P1', from: p1, to: 100, width: Math.max(0, 100 - p1)},
      ]
    : [];
  const ticks = hasAllThresholds
    ? [
        {key: 'P3', value: p3, label: `P3 ≥ ${p3}`},
        {key: 'P2', value: p2, label: `P2 ≥ ${p2}`},
        {key: 'P1', value: p1, label: `P1 ≥ ${p1}`},
      ]
    : [];

  /* —— ⑥ 封顶与强制规则 —— */
  const levelCapCode = asText(score?.level_cap);
  const capInfo = levelCapCode ? CAP_EXPLANATIONS[levelCapCode] ?? {title: levelCapCode, detail: '命中未收录的封顶规则代码，请对照评分实现核对。'} : null;
  const forcedRule = asRecord(score?.forced_rule);
  const forcedRuleHit = Object.keys(forcedRule).length > 0;

  /* —— ⑦ 输出等级 —— */
  const level = asText(score?.level, '—');
  const levelReason = forcedRuleHit
    ? `命中强制规则「${asText(forcedRule.name, '未命名规则')}」：直接定级并记满分，绕过常规评分。`
    : capInfo
      ? `受封顶规则限制：${capInfo.title}，上限已应用。`
      : available
        ? '按总分与 P1/P2/P3 阈值进行常规分级。'
        : '选择样例事件后显示真实分级结果。';

  return (
    <section
      data-testid="rule-engine-pipeline"
      data-available={available ? 'true' : 'false'}
      data-reduced-motion={reduceMotion ? 'true' : 'false'}
      className="min-w-0 space-y-3 rounded-2xl border border-slate-200/80 bg-white/80 p-4 shadow-sm backdrop-blur-md dark:border-slate-700/60 dark:bg-slate-800/60"
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <h2 className="text-[15px] font-bold text-[#101d28] dark:text-white">规则运行流水线</h2>
          <p className="mt-0.5 text-[11px] leading-relaxed text-slate-500 dark:text-slate-400">
            一条信号如何变成 P1/P2 风险：信号输入 → 事件路由 → 匹配柱 → 得分构成 → 总分 → 封顶/强制规则 → 输出等级
          </p>
        </div>
        <span className="shrink-0 rounded-full border border-slate-200 bg-white px-2 py-0.5 text-[11px] font-bold text-[#424751] dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300">
          {available
            ? `真实样本${selectedSampleId ? ` #${selectedSampleId}` : '（最近一条）'}`
            : '当前无真实样本'}
        </span>
      </div>

      {!available && (
        <div
          data-testid="rule-engine-pipeline-guidance"
          role="status"
          className="flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-[12px] text-amber-800 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-200"
        >
          <CircleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          <p className="break-words">
            当前无真实样本，请选择样例事件。{dimension?.name ?? '该维度'}暂无真实提醒轨迹；下方各阶段按真实字段结构占位，选择样例后会填充真实数值。
          </p>
        </div>
      )}

      <div data-testid="rule-engine-pipeline-scroll" className="overflow-x-auto">
        <ol className="min-w-0 space-y-2.5 p-0.5">
          {/* ① 信号输入 */}
          <StageShell stageId="signal" label="① 信号输入" icon={Radio} reduceMotion={reduceMotion} delay={0} isLast={false}>
            <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[12px] text-slate-700 dark:text-slate-300">
              <span className="font-bold text-slate-800 dark:text-slate-100">{asText(event?.source_name, '来源未披露')}</span>
              <span className="text-slate-300 dark:text-slate-600" aria-hidden="true">·</span>
              <span>{formatMoment(event?.published_at)}</span>
            </p>
            {inputsError ? (
              <p className="text-[11px] text-red-700 dark:text-red-300">输入健康度加载失败：{inputsError}</p>
            ) : inputs === null ? (
              <p className="text-[11px] text-slate-400 dark:text-slate-500">输入健康度加载中…</p>
            ) : (
              <>
                <p className="text-[11px] text-slate-500 dark:text-slate-400">
                  该维度声明的已接入信源 {declaredLinked} 个（共声明 {declaredTotal} 个，其中启用 {declaredEnabled} 个）；
                  近 30 天有信号的信源 {observedSources.length} 个。
                </p>
                {observedSources.length > 0 ? (
                  <ul className="flex flex-wrap gap-1.5">
                    {observedSources.map((source, index) => (
                      <li
                        key={`${asText(source?.code, String(index))}-${index}`}
                        data-testid="rule-engine-pipeline-source"
                        className="rounded-md border border-slate-200 bg-slate-50 px-2 py-0.5 text-[11px] text-slate-700 dark:border-slate-700 dark:bg-slate-950/40 dark:text-slate-300"
                      >
                        {asText(source?.name, asText(source?.code, '未知信源'))} · {asNumber(source?.signal_count, 0)} 条 · {formatMoment(source?.latest_at)}
                      </li>
                    ))}
                  </ul>
                ) : linkedSources.length > 0 ? (
                  <p className="text-[11px] text-slate-500 dark:text-slate-400">
                    近 30 天没有信号：已接入的 {linkedSources.map((source) => source.name).join('、')} 当前没有产出，不会产生新提醒。
                  </p>
                ) : (
                  <p className="text-[11px] text-amber-700 dark:text-amber-300">无已接入信源，当前不会产生提醒。</p>
                )}
              </>
            )}
          </StageShell>

          {/* ② 事件路由 */}
          <StageShell stageId="routing" label="② 事件路由" icon={RouteIcon} reduceMotion={reduceMotion} delay={0.03} isLast={false}>
            <p className="text-[13px] font-bold text-slate-800 dark:text-slate-100">
              {eventTypeLabel} → {routingLabel}
            </p>
            <p className="text-[11px] text-slate-500 dark:text-slate-400">
              事件类型代码：{rawEventType || '—'}
              {rawEventSubtype ? ` / ${EVENT_SUBTYPE_LABELS[rawEventSubtype] ?? rawEventSubtype}` : ''} · 严重程度：
              {SEVERITY_LABELS[asText(event?.severity)] ?? asText(event?.severity, '—')}
              {event && asNumber(event.confidence, 0) > 0 ? ` · 解析置信度 ${Math.round(asNumber(event.confidence, 0) * 100)}%` : ''}
            </p>
            {available && asText(routing?.key) && (
              <p className="text-[11px] text-slate-400 dark:text-slate-500">历史归属维度代码：{asText(routing?.key)}</p>
            )}
          </StageShell>

          {/* ③ 匹配柱 */}
          <StageShell stageId="match" label="③ 匹配柱" icon={Crosshair} reduceMotion={reduceMotion} delay={0.06} isLast={false}>
            {enabledColumns.length > 0 ? (
              <ul className="flex flex-wrap gap-1.5">
                {enabledColumns.map((column) => {
                  const hit = hitColumns.has(column);
                  return (
                    <li
                      key={column}
                      data-testid={`rule-engine-pipeline-column-${column}`}
                      data-hit={hit ? 'true' : 'false'}
                      className={`inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-[11px] font-bold ${
                        hit
                          ? 'border-[#C92A2A] bg-red-50 text-[#C92A2A] dark:border-red-500/60 dark:bg-red-950/30 dark:text-red-300'
                          : 'border-slate-200 bg-white text-slate-400 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-500'
                      }`}
                    >
                      {hit ? <CircleCheck className="h-3 w-3" aria-hidden="true" /> : <CircleDashed className="h-3 w-3" aria-hidden="true" />}
                      {MATCH_COLUMN_LABELS[column] ?? column}
                      {hit ? '（命中）' : ''}
                    </li>
                  );
                })}
              </ul>
            ) : (
              <p className="text-[11px] text-slate-500 dark:text-slate-400">该维度未启用任何匹配柱。</p>
            )}
            <p className="text-[11px] text-slate-500 dark:text-slate-400">
              命中方式：
              {Array.from(hitColumns).map((column) => MATCH_COLUMN_LABELS[column] ?? column).join(' + ') || '未命中可用匹配柱'}
              {matchTypeText ? `（match_type: ${matchTypeText}）` : ''}
              {evidenceCount > 0 ? ` · 匹配证据 ${evidenceCount} 条` : ''}
            </p>
            {matchReasons.length > 0 && (
              <ul className="list-disc space-y-0.5 pl-4 text-[11px] text-slate-500 dark:text-slate-400">
                {matchReasons.slice(0, 4).map((reason, index) => (
                  <li key={`${index}-${reason}`} className="break-words">{reason}</li>
                ))}
              </ul>
            )}
          </StageShell>

          {/* ④ 得分构成 */}
          <StageShell stageId="composition" label="④ 得分构成" icon={Layers} reduceMotion={reduceMotion} delay={0.09} isLast={false}>
            <ul className="space-y-1">
              {componentValues.map((component) => (
                <li
                  key={component.key}
                  data-testid={`rule-engine-pipeline-composition-${component.key}`}
                  data-value={formatScore(component.value)}
                  className="flex items-center gap-2 text-[11px]"
                >
                  <span className={`h-2 w-2 shrink-0 rounded-sm ${component.barClass}`} aria-hidden="true" />
                  <span className="w-16 shrink-0 text-slate-500 dark:text-slate-400">{component.label}</span>
                  <span className="min-w-0 flex-1 truncate text-[10px] text-slate-400 dark:text-slate-500">{component.hint}</span>
                  <span className="shrink-0 font-mono font-bold text-slate-800 dark:text-slate-100">{formatScore(component.value)}</span>
                </li>
              ))}
            </ul>
            <motion.div
              {...entranceMotion(reduceMotion, 0.12)}
              style={{transformOrigin: 'left'}}
              data-testid="rule-engine-pipeline-composition-bar"
              className="flex h-2.5 w-full overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800"
            >
              {componentValues.map((component) => (
                <span
                  key={component.key}
                  className={`${component.barClass} h-full`}
                  style={{width: `${clampPercent(component.value ?? 0)}%`}}
                  aria-hidden="true"
                />
              ))}
            </motion.div>
            <p className="text-[11px] text-slate-500 dark:text-slate-400">
              {missingComponentCount === componentValues.length
                ? '得分明细缺失，五项合计 — 分；'
                : missingComponentCount > 0
                  ? `五项合计 ${componentSum} 分（${missingComponentCount} 项缺失）；`
                  : `五项合计 ${componentSum} 分；`}
              {forcedRuleHit ? '命中强制规则后总分按满分 100 计。' : '总分超过 100 时按 100 分封顶。'}
            </p>
          </StageShell>

          {/* ⑤ 总分层 */}
          <StageShell stageId="total" label="⑤ 总分层" icon={Gauge} reduceMotion={reduceMotion} delay={0.12} isLast={false}>
            <p className="text-[11px] text-slate-500 dark:text-slate-400">{thresholdsText}</p>
            <div data-testid="rule-engine-pipeline-gauge-scroll" className="overflow-x-auto">
              {/* 窄屏不滚出关键信息：刻度数值标签在 sm 以下隐藏（上方文字刻度已给出同样数值），
                  仪表盘最小宽度随之收窄，320px 下也能直接看到当前分数标记。 */}
              <div className="min-w-[150px] px-3 pb-1">
                {/* pt-7（28px）必须留在定位容器上：绝对定位的得分标记(top-0)与指针(top-4 + h-3)
                    都以该容器 padding box 为基准，而彩条是容器内的正常流子元素——只有 padding
                    能把彩条推到 28px 处（= 指针底端），使标记完整落在彩条上方。
                    若把 pt-7 挪回外层包裹，彩条会回到 y=0 与标记重叠并被后绘制覆盖。 */}
                <div className="relative pt-7">
                  {/* 只有 API 明确返回总分才渲染分数标记；字段缺失时整体隐藏，不伪造 0 分。
                      真实位置竖线固定不动；分数字签按边界自适应对齐，避免 0/100 分被滚动容器裁剪 */}
                  {total !== null && (
                    <>
                      <span
                        data-testid="rule-engine-pipeline-gauge-pointer"
                        className="absolute top-4 h-3 w-px -translate-x-1/2 bg-[#004782] dark:bg-blue-400"
                        style={{left: `${total}%`}}
                        aria-hidden="true"
                      />
                      <div
                        data-testid="rule-engine-pipeline-gauge"
                        className={`absolute top-0 ${total > 85 ? '-translate-x-full' : total < 15 ? '' : '-translate-x-1/2'}`}
                        style={{left: `${total}%`}}
                      >
                        <motion.span
                          {...entranceMotion(reduceMotion, 0.16)}
                          className="block whitespace-nowrap rounded-md bg-[#004782] px-1.5 py-0.5 font-mono text-[10px] font-bold text-white shadow-sm dark:bg-blue-600"
                        >
                          {total} 分
                        </motion.span>
                      </div>
                    </>
                  )}
                  <div data-testid="rule-engine-pipeline-gauge-bar" className="relative h-2.5">
                    <div className="flex h-full overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
                      {zones.map((zone) => (
                        <span key={zone.key} className={`${LEVEL_ZONE_CLASS[zone.key]} h-full`} style={{width: `${zone.width}%`}} />
                      ))}
                    </div>
                    {ticks.map((tick) => (
                      <span
                        key={tick.key}
                        data-testid={`rule-engine-pipeline-tick-${tick.key}`}
                        className="absolute top-0 h-2.5 w-px -translate-x-1/2 bg-white/90 dark:bg-slate-900/80"
                        style={{left: `${tick.value}%`}}
                        aria-hidden="true"
                      />
                    ))}
                  </div>
                  <div className="relative mt-0.5 h-4">
                    {ticks.map((tick) => (
                      <span
                        key={tick.key}
                        className="absolute hidden -translate-x-1/2 whitespace-nowrap font-mono text-[10px] text-slate-500 dark:text-slate-400 sm:block"
                        style={{left: `${tick.value}%`}}
                      >
                        {tick.label}
                      </span>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          </StageShell>

          {/* ⑥ 封顶与强制规则 */}
          <StageShell stageId="caps" label="⑥ 封顶与强制规则" icon={ShieldAlert} reduceMotion={reduceMotion} delay={0.15} isLast={false}>
            <div
              data-testid="rule-engine-pipeline-cap"
              data-hit={capInfo ? 'true' : 'false'}
              className={`rounded-lg border px-2.5 py-2 ${
                capInfo
                  ? 'border-[#D97706] bg-amber-50 dark:border-amber-600/70 dark:bg-amber-950/30'
                  : 'border-slate-200 bg-white dark:border-slate-700 dark:bg-slate-900'
              }`}
            >
              <p className={`text-[11px] font-bold ${capInfo ? 'text-[#D97706] dark:text-amber-300' : 'text-slate-400 dark:text-slate-500'}`}>
                等级封顶：{capInfo ? '已命中' : '未命中'}
                {capInfo ? `（${levelCapCode}）` : ''}
              </p>
              {capInfo ? (
                <>
                  <p className="mt-0.5 text-[12px] font-bold text-[#101d28] dark:text-white">{capInfo.title}</p>
                  <p className="text-[11px] text-slate-600 dark:text-slate-300">{capInfo.detail}</p>
                </>
              ) : (
                <p className="mt-0.5 text-[11px] text-slate-500 dark:text-slate-400">
                  未命中封顶规则（仅国家关联最高 P4；无主体精确匹配最高 P2）。
                </p>
              )}
            </div>
            <div
              data-testid="rule-engine-pipeline-forced-rule"
              data-hit={forcedRuleHit ? 'true' : 'false'}
              className={`rounded-lg border px-2.5 py-2 ${
                forcedRuleHit
                  ? 'border-[#C92A2A] bg-red-50 dark:border-red-600/70 dark:bg-red-950/30'
                  : 'border-slate-200 bg-white dark:border-slate-700 dark:bg-slate-900'
              }`}
            >
              <p className={`text-[11px] font-bold ${forcedRuleHit ? 'text-[#C92A2A] dark:text-red-300' : 'text-slate-400 dark:text-slate-500'}`}>
                强制规则：{forcedRuleHit ? '已命中' : '未命中'}
              </p>
              {forcedRuleHit ? (
                <>
                  <p className="mt-0.5 break-words text-[12px] font-bold text-[#101d28] dark:text-white">
                    {asText(forcedRule.name, '未命名规则')}
                  </p>
                  {asText(forcedRule.description) && (
                    <p className="text-[11px] text-slate-600 dark:text-slate-300">{asText(forcedRule.description)}</p>
                  )}
                  {asText(forcedRule.reason) && (
                    <p className="text-[11px] text-slate-500 dark:text-slate-400">原因：{asText(forcedRule.reason)}</p>
                  )}
                  <p className="font-mono text-[10px] text-slate-500 dark:text-slate-400">
                    原等级 {asText(forcedRule.original_level, '—')}（{formatScore(asOptionalNumber(forcedRule.original_score))} 分）→ 命中后按满分 100 计
                  </p>
                </>
              ) : (
                <p className="mt-0.5 text-[11px] text-slate-500 dark:text-slate-400">
                  未命中强制规则；命中时将直接定级并记满分，绕过常规评分。
                </p>
              )}
            </div>
          </StageShell>

          {/* ⑦ 输出等级 */}
          <StageShell stageId="level" label="⑦ 输出等级" icon={Flag} reduceMotion={reduceMotion} delay={0.18} isLast>
            <div className="flex flex-wrap items-center gap-3">
              <span
                data-testid="rule-engine-pipeline-level"
                data-level={level}
                className={`inline-flex h-12 min-w-12 shrink-0 items-center justify-center rounded-xl px-3 text-[20px] font-black text-white dark:ring-1 dark:ring-white/15 ${
                  LEVEL_CHIP_CLASS[level] ?? 'bg-slate-400'
                }`}
              >
                {level}
              </span>
              <div className="min-w-0">
                <p className="text-[13px] font-bold text-slate-900 dark:text-white">
                  {LEVEL_NAMES[level] ?? '等待定级'}
                  {total !== null ? ` · ${total} 分` : ''}
                </p>
                <p className="break-words text-[11px] text-slate-500 dark:text-slate-400">{levelReason}</p>
              </div>
            </div>
            <p className="text-[10px] text-slate-400 dark:text-slate-500">P1 最严重，P4 最轻；同一提醒的等级与总分来自已保存的真实评分。</p>
          </StageShell>
        </ol>
      </div>
    </section>
  );
};
