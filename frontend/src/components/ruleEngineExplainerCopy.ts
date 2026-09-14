/**
 * 规则引擎配置项语义说明文案（todo 10 单一事实来源）。
 *
 * 说明层组件 `RuleEngineExplainers.tsx` 与信号过滤区块 `SignalFilterSection.tsx`
 * 都从这里取文案，便于维护与测试；文案必须与后端语义保持一致，对应关系：
 * - 匹配柱语义：`backend/app/risks/engine/config.py`（entity/location/product/country/industry）
 * - 事件类型互斥：`backend/app/risks/workbench_router.py`（占用冲突校验）
 * - 总分公式与两条封顶：`backend/app/risks/scoring.py`
 *   （compute_score / compute_level / apply_level_cap / apply_forced_rules）
 * - 信号过滤优先级：`backend/app/signals/relevance.py`（assess_signal_relevance）
 * - 默认分值/阈值以 `ScoringSettings` 与前端 `RuleEngineContext` 共享常量为准。
 */

/** 单个配置项说明：一句定义 + 一个例子 + 当前值含义。 */
export interface RuleEngineExplainerEntry {
  /** 稳定标识（测试锚点与 React key，不随文案变化） */
  id: string;
  /** 说明模块标题 */
  title: string;
  /** 一句定义 */
  definition: string;
  /** 一个例子 */
  example: string;
  /** 当前值含义（当前默认值的语义与可调边界） */
  current: string;
}

/** 六个配置项说明模块（匹配柱 / 事件类型 / 严重程度 / 关联类型 / 分级阈值 / 信号过滤）。 */
export const RULE_ENGINE_EXPLAINERS: readonly RuleEngineExplainerEntry[] = [
  {
    id: 'match-columns',
    title: '匹配柱',
    definition: '事件与供应商建立关联时可用的证据通道；未启用的柱不参与匹配，至少保留一柱。',
    example: '只启用「主体」时，仅注册号 / 法人全称 / 别名命中的供应商会进入评分。',
    current: '当前默认三柱：主体、地点、产品；「国家」「行业」用于宏观维度。',
  },
  {
    id: 'event-types',
    title: '事件类型',
    definition: '事件的大类，决定由哪个启用维度接管；同一事件类型同时只能由一个启用维度接管。',
    example: '天气由自然环境接管，贸易政策由经济贸易接管。',
    current: '当前接管关系以各维度声明的事件类型为准；与其它维度冲突时保存会被拒绝。',
  },
  {
    id: 'severity-scores',
    title: '严重程度分值',
    definition: '事件自身严重程度带来的基础分，是总分中占比最大的一项。',
    example: '严重 35 分 > 高 28 分 > 中 20 分 > 低 10 分。',
    current: '当前默认 35 / 28 / 20 / 10，可调范围 0–35。',
  },
  {
    id: 'association-scores',
    title: '关联类型分值',
    definition: '供应商与事件关联方式的强度分；同一供应商命中多种关联时取其中最高分计入。',
    example: '注册号精确匹配 30 分，高于产品关键词匹配 12 分。',
    current: '当前默认：注册号 30、法人全称/别名 25、地点 20、产品 12、行业 12、国家 8；可调范围 0–30。',
  },
  {
    id: 'level-thresholds',
    title: '分级阈值',
    definition: '将总分换算为 P1–P4 的分数线；低于 P3 线为 P4。',
    example: '总分 ≥ 85 → P1，65–84 → P2，40–64 → P3，低于 40 → P4。',
    current: '当前默认 85 / 65 / 40，必须满足 P1 > P2 > P3，否则保存会被拒绝。',
  },
  {
    id: 'signal-filter',
    title: '信号过滤规则',
    definition: '调用大模型之前的省钱粗筛：先过滤明确不相关的信号，拿不准一律放行；不改变评分结果。',
    example: '命中「制裁」等高影响关键词或重点关注国家会自动放行；国外事件且当地无任何供应商会被直接过滤；清单类信源只在实体名命中供应商时才进入分析。',
    current: '当前关键词、重点国家与清单类信源见下方「信号过滤规则」区块；修改后 ≤ 60 秒生效。',
  },
];

/**
 * 总分公式说明（对应 scoring.compute_score：各分项相加，上限 100）。
 * 括号内数字是后端 `ScoringSettings` 的默认值（可经全局层/环境变量覆盖），
 * 文案必须保留「按当前配置」的前缀，避免把默认值写成不可调的固定值。
 */
export const TOTAL_SCORE_FORMULA = {
  id: 'total-score',
  title: '总分公式',
  formula: '总分 = 严重程度 + 关联强度 + 来源可信度 + 时效性 + 产品相关性，上限 100 分。',
  parts: [
    '来源可信度 = round(来源可信度 × 权重)，权重按当前配置，默认 0.2',
    '时效性 = 按当前配置计算（默认：有发布时间 10 分；无发布时间 5 分）',
    '产品相关性 = 按当前配置计算（默认：命中供应产品 5 分；否则 0 分）',
  ],
  note: '强制规则命中时直接记满分 100 并按规则等级定级，不再套用上式。',
} as const;

/** 单条封顶规则说明（对应 scoring.apply_level_cap 的两个分支）。 */
export interface LevelCapRuleCopy {
  id: string;
  title: string;
  cap: string;
  explanation: string;
}

/** 两条封顶规则：只命中国家柱 → 最高 P4；未命中主体精确匹配 → 最高 P2。 */
export const LEVEL_CAP_RULES: readonly LevelCapRuleCopy[] = [
  {
    id: 'country-only-max-p4',
    title: '只命中「国家」柱',
    cap: '最高 P4',
    explanation: '仅有国家/区域这一条关联证据时（关联类型恰为 country），即使总分很高也只输出 P4。',
  },
  {
    id: 'weak-association-max-p2',
    title: '未命中主体精确匹配',
    cap: '最高 P2',
    explanation: '没有注册号 / 法人全称 / 别名等主体精确匹配时，达到 P1 线也压为 P2；只封顶 P1，不影响 P3/P4。',
  },
];

/** 封顶与强制规则的先后顺序说明（apply_level_cap 先于 apply_forced_rules）。 */
export const LEVEL_CAP_NOTE = '封顶在强制规则之前生效：强制规则命中时仍按规则等级定级。';

/**
 * 信号过滤区块说明文案（必须覆盖四个要点：
 * 调用大模型之前 / 放行 / 过滤 / 清单类信源）。
 */
export const SIGNAL_FILTER_EXPLAINER_COPY =
  '这是调用大模型之前的省钱粗筛：命中高影响关键词或重点关注国家会自动放行；' +
  '国外事件且当地无任何供应商会被直接过滤；' +
  '清单类信源只在实体名命中供应商时才进入分析。' +
  '修改后立即生效（≤60 秒）。';
