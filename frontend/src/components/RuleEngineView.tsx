import React, {useCallback, useEffect, useRef, useState} from 'react';
import {AnimatePresence, motion, useReducedMotion} from 'motion/react';
import {api, ApiError, GlobalScoringPatchPayload, SandboxResult} from '../api';
import {MonitoringDimension} from '../types';
import {SignalFilterSection} from './SignalFilterSection';
import {RuleEngineDimensionSources} from './RuleEngineDimensionSources';
import {RuleEngineMatchColumns} from './RuleEngineMatchColumns';
import {RuleEngineEventTypes} from './RuleEngineEventTypes';
import {RuleEngineForcedRules, readForcedRules} from './RuleEngineForcedRules';
import {RuleEngineScoringEditor, validateDimensionDraft} from './RuleEngineScoringEditor';
import {RuleEnginePipeline} from './RuleEnginePipeline';
import {RuleEngineRuleMatrix} from './RuleEngineRuleMatrix';
import {RuleEngineSampleSelector} from './RuleEngineSampleSelector';
import {RuleEngineExplainers} from './RuleEngineExplainers';
import {useRuleEngineData} from './useRuleEngineData';
import {
  ASSOCIATION_MAX,
  defaultSampleEvent,
  draftFromDimension,
  GLOBAL_DRAFT_NOT_LOADED_ERROR,
  GlobalDraftGate,
  RuleEngineContext,
  RuleEngineContextValue,
  RuleEngineDimensionDraft,
  RuleEngineEventTypeConflict,
  RuleEngineMode,
  RuleEngineSampleEvent,
  SEVERITY_MAX,
} from './RuleEngineContext';
import {RULE_ENGINE_EXPLAINERS} from './ruleEngineExplainerCopy';
import {routePaths} from '../routes';

interface EventTypeConflict {
  event_type: string;
  dimension: string;
}

function isEventTypeConflict(detail: unknown): detail is {message: string; conflicts: EventTypeConflict[]} {
  return (
    typeof detail === 'object' &&
    detail !== null &&
    'conflicts' in detail &&
    Array.isArray((detail as {conflicts: unknown}).conflicts)
  );
}

function extractConflicts(detail: {conflicts: EventTypeConflict[]}): EventTypeConflict[] {
  return detail.conflicts;
}

function splitValues(value: string): string[] {
  return value.split(/[，,、]/).map((item) => item.trim()).filter(Boolean);
}

/**
 * 观察态只读配置摘要的标签映射（口径与 `RuleEngineRuleMatrix.tsx` 的 MATCH_COLUMN_LABELS /
 * SEVERITY_LABELS / ASSOCIATION_LABELS 一致）。在本文件内本地定义而非从矩阵表导入：
 * 摘要与矩阵表是两块独立展示，不为此建立组件耦合；未知值一律回退原值，绝不隐藏数据。
 */
const SUMMARY_MATCH_COLUMN_LABELS: Record<string, string> = {
  entity: '主体',
  location: '地点',
  product: '产品',
  country: '国家/区域',
  industry: '行业/原材料',
};
const SUMMARY_SEVERITY_LABELS: Record<string, string> = {critical: '严重', high: '高', medium: '中', low: '低'};
const SUMMARY_SEVERITY_ORDER: ReadonlyArray<string> = ['critical', 'high', 'medium', 'low'];
/**
 * 观察态只读摘要的「关联类型分值」6 组展示映射（口径对齐 `RuleEngineRuleMatrix.tsx` 的
 * `ASSOCIATION_GROUPS`：8 个关联键合并为 6 组，组内取最高分计入）。与 Matrix 一样在本文件
 * 本地定义、不做跨组件 import，避免摘要与矩阵表互相耦合；未知键不回退展示、绝不臆造数值。
 */
const SUMMARY_ASSOCIATION_GROUPS: ReadonlyArray<{label: string; keys: ReadonlyArray<string>}> = [
  {label: '注册号', keys: ['registry_no']},
  {label: '法人全称 / 别名', keys: ['legal_name', 'alias']},
  {label: '地点', keys: ['site_distance', 'site_text']},
  {label: '产品', keys: ['product']},
  {label: '行业', keys: ['industry']},
  {label: '国家', keys: ['country']},
];
const SUMMARY_THRESHOLD_KEYS: ReadonlyArray<'p1' | 'p2' | 'p3'> = ['p1', 'p2', 'p3'];
/** 阈值等级 chip 配色（P1 红 / P2 琥珀 / P3 蓝，与规则矩阵表的等级 chip 同款）。 */
const SUMMARY_THRESHOLD_CHIP_CLASS: Record<'p1' | 'p2' | 'p3', string> = {
  p1: 'bg-red-100 text-red-700 dark:bg-red-950/60 dark:text-red-300',
  p2: 'bg-amber-100 text-amber-700 dark:bg-amber-950/60 dark:text-amber-300',
  p3: 'bg-blue-100 text-[#004782] dark:bg-blue-950/60 dark:text-blue-300',
};

/** 只读进度条宽度百分比：真实数值 / 上限 × 100，越界与非有限值收敛到 0–100。 */
function summaryBarPercent(value: unknown, max: number): number {
  if (typeof value !== 'number' || !Number.isFinite(value) || max <= 0) return 0;
  return Math.max(0, Math.min(100, (value / max) * 100));
}

/** 关联分组取最高分：全组缺失时返回 null（渲染为「—」），不把缺失误显示成 0。 */
function summaryGroupMax(scores: Record<string, number>, keys: ReadonlyArray<string>): number | null {
  const values = keys
    .map((key) => scores[key])
    .filter((value): value is number => typeof value === 'number' && Number.isFinite(value));
  return values.length > 0 ? Math.max(...values) : null;
}

/**
 * 一级 Tab：维度视图（默认）/ 全局规则。
 * hash 只是同步镜像，不承载其它路由语义：`#dimension` 与 `#global`，其余一律回落维度视图。
 */
export type RuleEngineTab = 'dimension' | 'global';

/** hash → Tab 的映射：只认精确的 `#global`；空值、`#dimension`、`#nonsense` 等一律回落维度视图 */
export function parseTabFromHash(hash: string): RuleEngineTab {
  return hash === '#global' ? 'global' : 'dimension';
}

/**
 * Tab → hash 的写入。必须用 history.replaceState：不新增历史记录，
 * 也不会像 `location.hash = ...` 那样触发锚点跳转/整页滚动。
 * 受限环境可能拒绝改写历史，同步失败只静默忽略，不影响 Tab 切换本身。
 */
export function writeTabToHash(tab: RuleEngineTab): void {
  const target = `#${tab}`;
  if (window.location.hash === target) return;
  try {
    window.history.replaceState(null, '', target);
  } catch {
    // 忽略：hash 仅是镜像，写失败不阻断内存状态切换
  }
}

/**
 * 一级 Tab 的静态契约：文案、DOM id、aria-controls 目标 id 全部由 `tab` 值派生
 * （`rule-engine-tab-${tab}` / `rule-engine-tabpanel-${tab}`），避免同一约定在多处重复。
 * tabpanel 本体由 todo 3 创建，本任务先按约定写好引用。
 */
const TAB_ITEMS: ReadonlyArray<{tab: RuleEngineTab; label: string}> = [
  {tab: 'dimension', label: '维度视图'},
  {tab: 'global', label: '全局规则'},
];

interface ScopeCardHeaderProps {
  /** 圆形序号徽标（配置=①，证据=②），装饰性元素，对读屏隐藏 */
  num: string;
  /** 标题元素 id：观察态/配置态互斥渲染，同一时刻只有一支存在，可安全复用同一 id */
  titleId: string;
  /** 区块标题（「配置」/「证据」） */
  title: string;
  /** 当前维度名：同时用于「归属于」与 scope-note */
  dimensionName: string;
  /** 归属说明句（逐字对齐原型 scheme-b-two-tabs.html:654、:757） */
  note: string;
}

/**
 * 维度级区块（①配置 / ②证据）的统一卡头（原型 scheme-b-two-tabs.html:650-654、753-757）。
 *
 * 为什么维度级从属关系要显式写在卡头上：
 * ①配置与②证据在视觉上与全局层卡片同构，用户切维度、切 Tab 或深链直达后，可能在滚动中
 * 失去「这块内容作用于哪个维度」的上下文；只靠左栏选中态暗示归属并不够——左栏可能不在
 * 视口内，窄屏下也可能被折叠。把「序号 + 标题 + 维度级副标 + 归属于：<维度名>」以及一句
 * 归属说明直接写进卡头，让每个维度级区块在自身位置自证从属关系：人可读的文案负责「一眼看懂」，
 * 容器上的 data-dimension 负责「机械可校验」，两层表达同一事实、互相兜底。
 */
const ScopeCardHeader: React.FC<ScopeCardHeaderProps> = ({num, titleId, title, dimensionName, note}) => (
  <header className="space-y-2">
    <div className="flex flex-wrap items-center gap-2.5">
      <span
        aria-hidden="true"
        className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-[#004782]/35 bg-[#eef6ff] font-mono text-[13px] font-bold leading-none text-[#004782] dark:border-blue-300/40 dark:bg-slate-700 dark:text-blue-300"
      >
        {num}
      </span>
      <h2 id={titleId} className="text-[15px] font-bold text-[#101d28] dark:text-white">{title}</h2>
      <span className="rounded-full border border-slate-200 bg-slate-50 px-2 py-0.5 text-[10.5px] font-bold text-slate-500 dark:border-slate-700 dark:bg-slate-900/40 dark:text-slate-400">
        维度级
      </span>
      <span className="ml-auto text-[11px] text-slate-500 dark:text-slate-400">
        归属于：<b className="font-bold text-[#101d28] dark:text-white">{dimensionName}</b>
      </span>
    </div>
    <p className="border-b border-dashed border-slate-200 pb-3 text-[11px] leading-relaxed text-slate-500 dark:border-slate-700 dark:text-slate-400">
      {note}
    </p>
  </header>
);

/**
 * SignalFilterSection 实际渲染的过滤组数（原型 gs-meta「确定性粗筛 · 3 组」的真实口径）：
 * 高影响关键词 / 重点关注国家 / 清单类信源三块，与组件内部结构一一对应。
 * 该组件增删过滤分组时需同步更新此常量——数值来源仅此一处，不散落在文案里。
 */
const SIGNAL_FILTER_GROUP_COUNT = 3;

interface GlobalSectionCardProps {
  /** 分节序号（原型 gs-index 的 1/2/3） */
  index: number;
  /** 分节标题（原型 gs-title） */
  title: string;
  /** 分节副标（原型 gs-meta），数值由调用方按实际数据拼接 */
  meta: string;
  children: React.ReactNode;
}

/**
 * 全局层可折叠分节卡（原型 scheme-b-two-tabs.html:834-840、:899-905、:1008-1014）。
 *
 * 为什么用原生 `<details>/<summary>`：折叠语义、键盘操作（Enter/Space）与展开态暴露
 * 全部由浏览器内建，无需自研控件；与原型同构。默认 `open` 展开——用户折叠后 React
 * 不会重置（`open` prop 值恒为 true，diff 无变化则不动 DOM 的展开状态）。
 * `group-open` 控制 chevron 方向：展开时指向下（rotate-45），收起时指向右（-rotate-45）。
 */
const GlobalSectionCard: React.FC<GlobalSectionCardProps> = ({index, title, meta, children}) => (
  <details
    open
    className="group rounded-2xl border border-slate-200/80 bg-white/80 shadow-sm backdrop-blur-md dark:border-slate-700/60 dark:bg-slate-800/60"
  >
    <summary className="group/summary flex cursor-pointer list-none flex-wrap items-center gap-2.5 rounded-2xl p-3.5 [&::-webkit-details-marker]:hidden">
      <span
        aria-hidden="true"
        className="inline-flex h-[22px] w-[22px] shrink-0 items-center justify-center rounded-lg border border-[#004782]/35 bg-[#eef6ff] font-mono text-[11px] font-bold text-[#004782] dark:border-blue-300/40 dark:bg-slate-700 dark:text-blue-300"
      >
        {index}
      </span>
      <span className="text-[13.5px] font-bold text-[#101d28] transition-colors group-hover/summary:text-[#004782] dark:text-white dark:group-hover/summary:text-blue-300">
        {title}
      </span>
      <span className="text-[11px] text-slate-500 dark:text-slate-400">{meta}</span>
      <span
        aria-hidden="true"
        className="ml-auto h-2 w-2 shrink-0 -rotate-45 border-b-[1.5px] border-r-[1.5px] border-slate-400 transition-transform group-open:rotate-45 dark:border-slate-500"
      />
    </summary>
    <div className="border-t border-slate-200 p-4 dark:border-slate-700">{children}</div>
  </details>
);

interface RuleEngineViewProps {
  dimensions: MonitoringDimension[];
  onToggleDimension: (id: string) => Promise<void>;
  onUpdateDimension: (updatedDim: MonitoringDimension) => Promise<void>;
  role: 'viewer' | 'admin';
}

/**
 * 规则引擎页壳组件（todo 4 冻结挂载点）。
 *
 * 双态：observation（默认，只读解释）/ config（管理配置；viewer 不可进入）。
 * 壳组件独占职责：
 * - 模式状态与切换按钮（role !== 'admin' 时禁用）；
 * - 一级 Tab（维度视图 / 全局规则）状态与 URL hash 同步（Tab 栏渲染属 todo 2）；
 * - 选中维度、维度草稿、全局层草稿（context 提供给子组件）；
 * - 保存维度草稿（沿用父级 onUpdateDimension 契约）、全局层 PUT 与刷新；
 * - 数据获取（useRuleEngineData：输入健康度/轨迹/选项/全局配置）。
 * 子组件文件归属见 .omo 计划 todo 4 的冻结映射，各自只改自己的文件。
 */
export const RuleEngineView: React.FC<RuleEngineViewProps> = ({
  dimensions,
  onToggleDimension,
  onUpdateDimension,
  role,
}) => {
  const isAdmin = role === 'admin';
  const [mode, setMode] = useState<RuleEngineMode>('observation');
  // 有效模式由当前角色派生：会话中途降级（admin→viewer）时，mode 状态可能仍是 'config'，
  // 但 viewer 绝不允许停留在配置态（渲染分支与全部写控件门控统一使用 effectiveMode）。
  const effectiveMode: RuleEngineMode = isAdmin ? mode : 'observation';
  const [activeDimId, setActiveDimId] = useState<string>(dimensions[0]?.id || 'dim-01');
  // —— 一级 Tab（维度视图 / 全局规则）：本任务只落状态与 hash 同步，Tab 栏 UI 由 todo 2 渲染 ——
  // 初值直接同步读 hash：带 #global 打开/刷新时首帧即落全局视图，避免先渲染维度视图再闪跳
  const [activeTab, setActiveTab] = useState<RuleEngineTab>(() => parseTabFromHash(window.location.hash));
  const reduceMotion = useReducedMotion();

  const selectedDim = dimensions.find((d) => d.id === activeDimId) || dimensions[0];

  // —— 数据获取：维度输入健康度 / 轨迹 / 选项 / 全局配置（任一路失败只提示对应区块） ——
  const [selectedSampleId, setSelectedSampleId] = useState<number | null>(null);
  const data = useRuleEngineData(selectedDim?.id, selectedSampleId);

  // —— 维度草稿（配置态编辑器读写；与已保存值 diff 后保存） ——
  const [draft, setDraft] = useState<RuleEngineDimensionDraft>(() => draftFromDimension(selectedDim));
  const [saving, setSaving] = useState(false);
  const [configError, setConfigError] = useState('');
  const [configConflicts, setConfigConflicts] = useState<RuleEngineEventTypeConflict[]>([]);

  // —— 全局层草稿（解算预览 todo 7 与强制规则编辑器 todo 8 共用） ——
  const globalDraftRevisionRef = useRef(0);
  const [globalDraftValidity, setGlobalDraftValidityState] = useState<{valid: boolean; error: string}>({
    // 默认非法（F2 窗口 1）：全局配置未加载时 rows 为空只是「尚未同步」，
    // 必须保持门控关闭，直到强制规则编辑器真正加载并校验 globalConfig。
    valid: false,
    error: GLOBAL_DRAFT_NOT_LOADED_ERROR,
  });
  const [globalDraft, setGlobalDraftState] = useState<GlobalScoringPatchPayload>({});
  const [globalSaving, setGlobalSaving] = useState(false);
  const [globalSaveError, setGlobalSaveError] = useState('');
  // 全局层草稿有效性由强制规则编辑器上报（校验失败时它不会写 globalDraft，但旧规则仍在），
  // 解算预览据此门控：非法期间禁用预览并给出 role=alert，绝不发送陈旧草稿。
  // 有效性/修订号另存 ref（同步更新）：解算预览在 await 之后需复核最新状态，
  // 闭包中的 state 值会停留在点击那一刻；revision 单调递增以识别等待期间的任何变化。
  const globalDraftValidityRef = useRef(globalDraftValidity);
  const setGlobalDraft = useCallback<React.Dispatch<React.SetStateAction<GlobalScoringPatchPayload>>>((action) => {
    globalDraftRevisionRef.current += 1;
    setGlobalDraftState(action);
  }, []);
  const setGlobalDraftValidity = useCallback((valid: boolean, error = '') => {
    const current = globalDraftValidityRef.current;
    if (current.valid === valid && current.error === error) return;
    globalDraftValidityRef.current = {valid, error};
    globalDraftRevisionRef.current += 1;
    setGlobalDraftValidityState({valid, error});
  }, []);
  const readGlobalDraftGate = useCallback((): GlobalDraftGate => ({
    valid: globalDraftValidityRef.current.valid,
    error: globalDraftValidityRef.current.error,
    revision: globalDraftRevisionRef.current,
  }), []);

  // —— 沙箱/解算预览共享的样例事件 ——
  const [sample, setSample] = useState<RuleEngineSampleEvent>(() => defaultSampleEvent());
  const [sandboxResult, setSandboxResult] = useState<SandboxResult | null>(null);
  const [sandboxError, setSandboxError] = useState('');
  const [sandboxLoading, setSandboxLoading] = useState(false);
  const [sandboxOpen, setSandboxOpen] = useState(false);

  // 外部改 hash（地址栏手改、外链或其它脚本写 location.hash）时回读并同步；
  // 点击 Tab 走 replaceState，不会触发 hashchange，所以 handleTabChange 里需显式 setActiveTab。
  useEffect(() => {
    const syncTabFromHash = () => setActiveTab(parseTabFromHash(window.location.hash));
    window.addEventListener('hashchange', syncTabFromHash);
    return () => window.removeEventListener('hashchange', syncTabFromHash);
  }, []);

  // Update editor values when active dimension changes（草稿与样例同步回已保存值）
  useEffect(() => {
    if (!selectedDim) return;
    setDraft(draftFromDimension(selectedDim));
    setConfigError('');
    setConfigConflicts([]);
    setSample(defaultSampleEvent(selectedDim.source?.event_types[0] ?? selectedDim.eventTypes[0]));
    setSandboxResult(null);
    setSandboxError('');
    setSelectedSampleId(null);
  }, [activeDimId, selectedDim]);

  const updateDraft = (patch: Partial<RuleEngineDimensionDraft>) => {
    setDraft((current) => ({...current, ...patch}));
  };

  const resetDraft = () => {
    if (selectedDim) setDraft(draftFromDimension(selectedDim));
    setConfigError('');
    setConfigConflicts([]);
  };

  const updateSample = (patch: Partial<RuleEngineSampleEvent>) => {
    setSample((current) => ({...current, ...patch}));
  };

  /** 切换一级 Tab：先落内存状态，再把 hash 镜像为 #dimension / #global（replaceState 不滚动、不堆历史） */
  const handleTabChange = useCallback((tab: RuleEngineTab) => {
    // 运行时兜底：任何非 'global' 的值都按维度视图处理，状态与地址栏绝不出现非法值
    const next: RuleEngineTab = tab === 'global' ? 'global' : 'dimension';
    setActiveTab(next);
    writeTabToHash(next);
  }, []);

  // —— Tab 栏键盘导航（WAI-ARIA tabs 模式） ——
  // roving tabindex：整个 tablist 只保留一个可 Tab 键进入的停靠点（激活 tab tabIndex=0，
  // 其余 -1），页面 Tab 顺序不会被两个并列 Tab 占成两站；进入后改用左右方向键在 tab 间移动。
  const tabRefs = useRef<Record<RuleEngineTab, HTMLButtonElement | null>>({dimension: null, global: null});
  // automatic activation：焦点移动即激活（并按既有约定经 handleTabChange 写 hash）。
  // 两个 Tab 都只切换常驻面板、无网络/计算成本，比手动激活少一次 Enter/Space；
  // 激活后立即把焦点落到新 tab，键盘与读屏用户不会丢失位置。
  const handleTabListKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    const currentIndex = Math.max(
      TAB_ITEMS.findIndex((item) => item.tab === activeTab),
      0,
    );
    let nextIndex: number;
    switch (event.key) {
      case 'ArrowRight':
        nextIndex = (currentIndex + 1) % TAB_ITEMS.length;
        break;
      case 'ArrowLeft':
        nextIndex = (currentIndex - 1 + TAB_ITEMS.length) % TAB_ITEMS.length;
        break;
      case 'Home':
        nextIndex = 0;
        break;
      case 'End':
        nextIndex = TAB_ITEMS.length - 1;
        break;
      default:
        // 其余按键（Tab 离开、Enter/Space 触发按钮自身 click 等）交给浏览器默认行为
        return;
    }
    // 方向键/Home/End 默认会滚动页面，tablist 内必须拦截
    event.preventDefault();
    const nextTab = TAB_ITEMS[nextIndex].tab;
    // 已激活的 tab 只需把焦点搬回它（例如 Home 落在当前项），避免重复 replaceState
    if (nextTab !== activeTab) handleTabChange(nextTab);
    tabRefs.current[nextTab]?.focus();
  };

  const handleSaveConfig = async () => {
    if (!selectedDim) return;
    // 保存路径复用评分编辑器的完整校验（severity 0-35、association 0-30、阈值 0-100、
    // p1>p2>p3、至少一柱）：任何非法草稿都在发出 API 请求之前被拦截，后端 422 只作最后防线
    const draftValidation = validateDimensionDraft(draft);
    if (draftValidation.messages.length > 0) {
      setConfigError(draftValidation.messages.join('；'));
      return;
    }
    const updated: MonitoringDimension = {
      ...selectedDim,
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
    setSaving(true);
    try {
      await onUpdateDimension(updated);
      setConfigConflicts([]);
      alert(`规则 [${selectedDim.name}] 配置已成功保存！`);
    } catch (caught) {
      if (caught instanceof ApiError && isEventTypeConflict(caught.detail)) {
        setConfigError(caught.detail.message);
        setConfigConflicts(extractConflicts(caught.detail));
      } else {
        setConfigError(caught instanceof Error ? caught.message : '规则配置保存失败');
        setConfigConflicts([]);
      }
    } finally {
      setSaving(false);
    }
  };

  /** 合并写入全局层（todo 8 编辑器调用）；成功后刷新全局配置 */
  const saveGlobalConfig = async (
    patch: GlobalScoringPatchPayload,
    confirmDisableForcedRules = false,
  ): Promise<boolean> => {
    setGlobalSaving(true);
    setGlobalSaveError('');
    try {
      await api.globalConfig.update(patch, confirmDisableForcedRules);
      setGlobalDraft({});
      void data.refreshGlobalConfig();
      return true;
    } catch (caught) {
      setGlobalSaveError(caught instanceof Error ? caught.message : '全局配置保存失败');
      return false;
    } finally {
      setGlobalSaving(false);
    }
  };

  const handleRunSandboxTest = async () => {
    setSandboxLoading(true);
    setSandboxError('');
    setSandboxResult(null);
    try {
      const countryCode = sample.countryCode.trim().toUpperCase();
      if (countryCode && !/^[A-Z]{2}$/.test(countryCode)) throw new Error('国家/地区代码需填写两位大写字母，例如 CN');
      const result = await api.testRuleEngine({
        event_type: sample.eventType,
        event_subtype: sample.eventSubtype || null,
        severity: sample.severity,
        organizations: sample.organization.trim() ? [{name: sample.organization.trim(), aliases: [], registry_no: sample.registryNo.trim() || null}] : [],
        locations: sample.location.trim() ? [{name: sample.location.trim(), country_code: countryCode || null, region: sample.region.trim() || null, city: sample.city.trim() || null, district: sample.district.trim() || null}] : [],
        affected_products: splitValues(sample.products),
        affected_industries: splitValues(sample.industries),
        summary: `规则沙箱：${data.options.event_types.find((item) => item.value === sample.eventType)?.label ?? sample.eventType}`,
        credibility: sample.credibility,
        has_published_at: true,
      });
      setSandboxResult(result);
    } catch (error) {
      setSandboxError(error instanceof Error ? error.message : '沙箱评估失败');
    } finally {
      setSandboxLoading(false);
    }
  };

  // 冻结的 context 承载点：后续 todo 7/8 只读这里下发的草稿与保存函数，不改壳组件签名
  if (!selectedDim) {
    return <div className="bg-white dark:bg-slate-900 border border-[#e2e8f0] rounded-xl p-8 text-center text-slate-500">暂无可用监控维度</div>;
  }

  // 维度序号（原型「维度 N / M」，scheme-b-two-tabs.html:620）：N 为当前维度在 dimensions
  // 中的 1 基序号；activeDimId 失配回落 dimensions[0] 时 N=1。dimensions 为空已在早退拦截。
  const dimensionIndex = Math.max(1, dimensions.findIndex((d) => d.id === selectedDim.id) + 1);

  const contextValue: RuleEngineContextValue = {
    mode: effectiveMode,
    role,
    dimension: selectedDim,
    draft,
    updateDraft,
    resetDraft,
    saving,
    configError,
    configConflicts,
    saveDraft: handleSaveConfig,
    globalConfig: data.globalConfig,
    globalConfigError: data.globalConfigError,
    refreshGlobalConfig: data.refreshGlobalConfig,
    globalDraft,
    setGlobalDraft,
    globalDraftValid: globalDraftValidity.valid,
    globalDraftError: globalDraftValidity.error,
    setGlobalDraftValidity,
    readGlobalDraftGate,
    globalSaving,
    globalSaveError,
    saveGlobalConfig,
    sample,
    updateSample,
    options: data.options,
    optionsError: data.optionsError,
  };

  // —— 全局层三个折叠分节的清单（原型 :834/:899/:1008）：计数由本数组长度派生（Tab 徽标与
  //    global-head 计数同源，不写死数字），meta 数值全部取实际数据——语义项数来自
  //    RULE_ENGINE_EXPLAINERS，强制规则条数来自全局配置快照；矩阵表是独立卡片，不在数组内。
  const globalSections: ReadonlyArray<{key: string; title: string; meta: string; body: React.ReactNode}> = [
    {
      key: 'explainers',
      title: '规则语义说明',
      meta: `${RULE_ENGINE_EXPLAINERS.length} 项配置的语义 · 只读`,
      body: <RuleEngineExplainers mode={effectiveMode} embedded />,
    },
    {
      key: 'signal-filter',
      title: '信号过滤规则',
      meta: `调用大模型之前的确定性粗筛 · ${SIGNAL_FILTER_GROUP_COUNT} 组`,
      body: <SignalFilterSection role={role} mode={effectiveMode} embedded />,
    },
    {
      key: 'forced-rules',
      title: '全局强制规则',
      meta: data.globalConfig === null
        ? '命中即直接定级并记满分 · 加载中'
        : `命中即直接定级并记满分 · ${readForcedRules(data.globalConfig).length} 条`,
      body: <RuleEngineForcedRules mode={effectiveMode} embedded />,
    },
  ];
  const globalSectionCount = globalSections.length;

  /**
   * ② 证据（维度级）卡（#16/#17/#18/#19）：观察态与配置态渲染同一结构、同一顺序——
   * 引用信源 → 样例事件（选择器）→ 运行轨迹（嵌入块），与 demo 证据网格（scheme-b-two-tabs.html:759-807）
   * 一致；≥1280px（Tailwind `xl`）按 1.6fr / 1fr 两列排布、运行轨迹跨全宽，窄屏回落单列。
   * 两个分支互斥渲染，同一时刻只有一支存在，元素可安全在两支中复用（保证两态顺序绝不分叉）。
   * 样例选择器复用壳层唯一的 selectedSampleId / setSelectedSampleId：选中后由 useRuleEngineData
   * 带 sampleId 重取轨迹，驱动本卡运行轨迹与全局 Tab 矩阵表高亮。
   */
  const evidenceCard = (
    <div data-testid="rule-engine-scope-evidence" data-dimension={selectedDim.id} aria-labelledby="rule-engine-scope-evidence-title" className="space-y-4 rounded-2xl border border-slate-200/80 bg-white/80 p-5 shadow-sm backdrop-blur-md dark:border-slate-700/60 dark:bg-slate-800/60">
      <ScopeCardHeader
        num="②"
        titleId="rule-engine-scope-evidence-title"
        title="证据"
        dimensionName={selectedDim.name}
        note={`只展示『${selectedDim.name}』自己的信源、轨迹与样例，不与其它维度混排。`}
      />
      <div className="grid grid-cols-1 gap-2.5 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)] xl:items-start xl:gap-x-4">
        <div data-testid="rule-engine-evidence-sources" className="min-w-0">
          <RuleEngineDimensionSources dimension={selectedDim} inputs={data.inputs} inputsError={data.inputsError} />
        </div>
        <div data-testid="rule-engine-evidence-sample" className="min-w-0">
          <RuleEngineSampleSelector
            options={data.options}
            samples={data.trace?.samples ?? []}
            selectedSampleId={selectedSampleId}
            onSelectSample={setSelectedSampleId}
          />
        </div>
        <div data-testid="rule-engine-evidence-timeline" className="min-w-0 xl:col-span-2">
          <RuleEnginePipeline
            dimension={selectedDim}
            trace={data.trace}
            traceError={data.traceError}
            inputs={data.inputs}
            inputsError={data.inputsError}
            selectedSampleId={selectedSampleId}
            embedded
          />
        </div>
      </div>
    </div>
  );

  return (
    <RuleEngineContext.Provider value={contextValue}>
      <div className="space-y-5 pb-20 lg:pb-8">
        {/* Title */}
        <div className="flex flex-col sm:flex-row justify-between gap-3">
          <div>
            <h1 className="text-xl font-black text-slate-900 dark:text-white tracking-tight lg:text-2xl">
              规则引擎与权重配置
            </h1>
            <p className="text-xs text-[#424751] dark:text-slate-400 mt-0.5">
              {effectiveMode === 'observation'
                ? '用真实最近的提醒讲清一条信号如何一路变成 P1/P2 风险；切换模式后可编辑配置。'
                : '自定义监控维度、计算权重及沙箱仿真评估。'}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <button
              type="button"
              data-testid="rule-engine-mode-toggle"
              aria-pressed={effectiveMode === 'config'}
              disabled={!isAdmin}
              title={isAdmin ? undefined : '只读账号仅可查看运行方式'}
              onClick={() => setMode((current) => (current === 'observation' ? 'config' : 'observation'))}
              className={`inline-flex min-h-9 items-center gap-1.5 rounded-lg border px-3 py-1.5 text-[12px] font-bold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#004782] focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-60 ${
                effectiveMode === 'config'
                  ? 'border-[#004782] bg-[#eef6ff] text-[#004782] dark:border-blue-300 dark:bg-slate-800 dark:text-blue-300'
                  : 'border-[#e2e8f0] bg-white text-[#101d28] hover:bg-[#f7f9ff] dark:border-slate-700 dark:bg-slate-900 dark:text-white dark:hover:bg-slate-800'
              }`}
            >
              <span className="material-symbols-outlined text-[16px]" aria-hidden="true">
                {effectiveMode === 'observation' ? 'tune' : 'visibility'}
              </span>
              {effectiveMode === 'observation' ? '进入配置模式' : '返回观察模式'}
            </button>
            <span className="text-slate-500 dark:text-slate-400">当前权限：{isAdmin ? '规则管理' : '只读'}</span>
          </div>
        </div>

        {/* Tab 栏与面板容器：维度选择器已迁入维度 Tab（#1，对齐原型 scheme-b-two-tabs.html:605-614），
            页面级左栏维度列表与其中重复的沙箱入口整体移除；页面收敛为单列布局。 */}
        <div className="min-w-0 space-y-6">
            {/* 一级 Tab 栏（下划线式，原型 scheme-b-two-tabs.html:589-594 + CSS :131-145）：
                与「观察态/配置态」模式切换正交——Tab 决定看哪一层内容，模式只决定是否可编辑。
                保持既有无障碍契约：role=tablist / role=tab / aria-selected / aria-controls /
                roving tabIndex / 方向键自动激活（handleTabListKeyDown）全部不变。
                Tab 文案只保留标签本身：全局 Tab 旁的计数胶囊已按需求移除，
                 分节数仅在 global-head 的「N 项」处展示（仍取 globalSectionCount）。 */}
            <div
              role="tablist"
              aria-label="规则引擎视图切换"
              data-testid="rule-engine-tabs"
              onKeyDown={handleTabListKeyDown}
              className="flex items-end gap-6 border-b border-slate-200/80 dark:border-slate-700/60"
            >
              {TAB_ITEMS.map((item) => {
                const isActive = activeTab === item.tab;
                return (
                  <button
                    key={item.tab}
                    ref={(node) => { tabRefs.current[item.tab] = node; }}
                    type="button"
                    role="tab"
                    id={`rule-engine-tab-${item.tab}`}
                    data-testid={`rule-engine-tab-${item.tab}`}
                    aria-selected={isActive}
                    aria-controls={`rule-engine-tabpanel-${item.tab}`}
                    // roving tabindex：只有激活 tab 可被 Tab 键停靠，另一个交给方向键
                    tabIndex={isActive ? 0 : -1}
                    onClick={() => handleTabChange(item.tab)}
                    className={`inline-flex items-center gap-1 border-b-[3px] px-0 pb-2.5 pt-2 text-[13.5px] font-bold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#004782] focus-visible:ring-offset-2 dark:focus-visible:ring-blue-400 sm:text-[14px] ${
                      isActive
                        ? 'border-[#004782] text-[#004782] dark:border-blue-300 dark:text-blue-300'
                        : 'border-transparent text-[#424751] hover:text-[#004782] dark:text-slate-300 dark:hover:text-white'
                    }`}
                  >
                    {item.label}
                  </button>
                );
              })}
            </div>

            {/* 维度视图面板（tabpanel）：必须常驻挂载、切换 Tab 只切 hidden，绝不条件卸载。
                两个面板共享 useRuleEngineData 的同一次请求与壳层草稿状态：若按 activeTab 卸载/重挂，
                每次切 Tab 都会重放全量数据请求（并重置面板内子组件状态），这是本页面刻意避免的。 */}
            <div
              role="tabpanel"
              id="rule-engine-tabpanel-dimension"
              aria-labelledby="rule-engine-tab-dimension"
              data-testid="rule-engine-tabpanel-dimension"
              hidden={activeTab !== 'dimension'}
            >
            {/* 维度视图说明段（原型 scheme-b-two-tabs.html:601-603）：先交代「公有内容已移至全局规则 Tab」，
                让用户进入维度视图即知道此处只保留维度私有内容，不会把全局项的缺席误读为功能缺失 */}
            <p className="mb-4 text-[11px] leading-relaxed text-slate-500 dark:text-slate-400">
              维度视图只保留『当前维度自己的』配置与证据；规则语义说明、信号过滤与全局强制规则等公有内容已全部移至『全局规则』Tab，不再随维度重复。
            </p>
            {/* 维度选择 chips（#1，原型 scheme-b-two-tabs.html:605-614）：由页面级左栏迁入维度 Tab 内、
                维度头卡之前；切到全局 Tab 时随本面板 hidden 一并隐藏，两个 Tab 不共享维度选择器。
                保留既有无障碍契约：role=group 分组名「监控维度」、原生 button、aria-pressed 选中态、
                Enter/Space 触发与 focus-visible 焦点环；已停用维度沿用 demo 的虚线描边 + 「已停用」徽标。
                监控内容清单改挂 chip 悬浮提示，信息不丢失（原左栏列表项的 title 行为）。 */}
            <div
              data-testid="rule-engine-dimension-chips"
              role="group"
              aria-label="监控维度"
              className="mb-4 flex flex-wrap gap-2"
            >
              {dimensions.map((dim) => {
                const isSelected = dim.id === activeDimId;
                return (
                  <button
                    key={dim.id}
                    type="button"
                    data-testid={`rule-engine-dimension-${dim.id}`}
                    aria-pressed={isSelected}
                    onClick={() => setActiveDimId(dim.id)}
                    title={dim.contentItems.length > 0 ? dim.contentItems.join(' · ') : undefined}
                    className={`inline-flex items-center gap-1.5 rounded-xl border px-3.5 py-2 text-[12.5px] font-semibold leading-snug transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#004782] focus-visible:ring-offset-2 dark:focus-visible:ring-blue-400 ${
                      isSelected
                        ? 'border-[#004782] bg-[#eef6ff] text-[#004782] dark:border-blue-300 dark:bg-slate-800 dark:text-blue-300'
                        : `bg-white text-[#424751] hover:border-[#004782] hover:text-[#004782] dark:bg-slate-900 dark:text-slate-300 dark:hover:border-blue-300 dark:hover:text-blue-300 ${
                            dim.enabled ? 'border-[#e2e8f0] dark:border-slate-700' : 'border-dashed border-[#e2e8f0] dark:border-slate-700'
                          }`
                    }`}
                  >
                    {dim.name}
                    {!dim.enabled && (
                      <span className="rounded-full border border-[#e2e8f0] bg-slate-100 px-1.5 py-0.5 text-[10px] font-bold text-slate-500 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-400">
                        已停用
                      </span>
                    )}
                  </button>
                );
              })}
            </div>
            {/* 维度头卡（原型 scheme-b-two-tabs.html:617-645）：序号 / 名称 / ID / 启停 /
                接管事件类型 chips / 右侧三块 stats（输入状态+备注、引用信源、启用匹配柱）。
                只读概览，两种模式（观察/配置）均可见；启停开关随维度选择器一起从左栏迁入头卡
                （仅配置态 admin 可操作，观察态为只读徽标），不再有页面级左栏。
                位置在 motion.div（key=维度-模式）之外：切维度/切模式不重放它的入场动画，且它始终先于两态内容出现。 */}
            <div
              data-testid="rule-engine-dimension-header"
              aria-label={`${selectedDim.name} 维度概览（只读）`}
              className="mb-2 rounded-2xl border border-slate-200/80 bg-white/80 p-5 shadow-sm backdrop-blur-md dark:border-slate-700/60 dark:bg-slate-800/60"
            >
              <div className="grid grid-cols-1 gap-x-8 gap-y-4 xl:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono text-[11px] font-semibold tracking-wide text-slate-500 dark:text-slate-400">
                      维度 {dimensionIndex} / {dimensions.length}
                    </span>
                    {/* 启停控件：观察态为只读徽标；配置态（admin）为可操作开关。
                        开关原在左栏维度列表内，随维度选择器迁入头卡；onToggleDimension 与权限门控不变。 */}
                    {effectiveMode === 'config' ? (
                      <label className="relative inline-flex cursor-pointer items-center">
                        <input
                          type="checkbox"
                          aria-label={`${selectedDim.name} 启用状态`}
                          checked={selectedDim.enabled}
                          onChange={() => { if (isAdmin) void onToggleDimension(selectedDim.id); }}
                          disabled={!isAdmin}
                          className="sr-only peer"
                        />
                        <div className="w-9 h-5 bg-slate-200 dark:bg-slate-700 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-slate-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-[#004782]"></div>
                      </label>
                    ) : (
                      <span
                        className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-bold ${
                          selectedDim.enabled
                            ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300'
                            : 'bg-slate-200 text-[#424751] dark:bg-slate-800 dark:text-slate-300'
                        }`}
                      >
                        {selectedDim.enabled ? '已启用' : '已停用'}
                      </span>
                    )}
                  </div>
                  <h2 className="mt-1.5 text-[22px] font-bold leading-tight tracking-tight text-[#101d28] dark:text-white">
                    {selectedDim.name}
                  </h2>
                  <div className="mt-1 font-mono text-[11px] font-bold text-slate-500 dark:text-slate-400">
                    ID: {selectedDim.ruleId}
                  </div>
                  {/* 接管事件类型 chip 行：value → label 用 data.options.event_types 映射，找不到或
                      选项未加载时回退 value；chip 内附 mono code（对齐原型 .chip-code） */}
                  <div className="mt-3 flex flex-wrap items-start gap-2" data-testid="rule-engine-dim-event-types">
                    <span className="pt-0.5 text-[10.5px] font-bold tracking-wide text-slate-500 dark:text-slate-400">
                      接管事件类型
                    </span>
                    <div className="flex flex-wrap gap-1.5">
                      {selectedDim.eventTypes.map((eventType) => (
                        <span
                          key={eventType}
                          className="inline-flex items-center gap-1 rounded-xl border border-[#004782]/30 bg-[#eef6ff] px-2 py-0.5 text-[11px] font-bold text-[#004782] dark:border-blue-300/40 dark:bg-slate-700 dark:text-blue-300"
                        >
                          {data.options.event_types.find((option) => option.value === eventType)?.label ?? eventType}
                          <code className="font-mono text-[10px] opacity-70">{eventType}</code>
                        </span>
                      ))}
                      {selectedDim.eventTypes.length === 0 && (
                        <span className="text-[11px] text-slate-400 dark:text-slate-500">—</span>
                      )}
                    </div>
                  </div>
                </div>

                {/* 右侧三块 stats：内层小卡与原型 .dim-stat 同构（10.5px 标签 + 13px 值 + mono 备注行） */}
                <dl className="grid min-w-0 grid-cols-[repeat(auto-fit,minmax(140px,1fr))] content-start gap-3">
                  <div className="min-w-0 rounded-xl border border-slate-200/80 bg-[#f8fafc] p-3 dark:border-slate-700 dark:bg-slate-900/40">
                    <dt className="text-[10.5px] font-bold tracking-wide text-slate-500 dark:text-slate-400">输入状态</dt>
                    <dd className="mt-1 text-[13px] font-bold text-[#101d28] dark:text-white">
                      {data.inputsError ? (
                        <span className="text-red-700 dark:text-red-300">输入健康度加载失败</span>
                      ) : data.inputs === null ? (
                        <span className="font-normal text-slate-500 dark:text-slate-400">输入健康度加载中…</span>
                      ) : data.inputs.has_input ? (
                        <span>近 30 天 {data.inputs.observed.length} 个信源有输入</span>
                      ) : (
                        <span className="font-normal text-slate-500 dark:text-slate-400">当前无输入</span>
                      )}
                    </dd>
                    {/* 备注行：各信源条数摘要（原型 dim-stat-note，scheme-b-two-tabs.html:634）；
                        break-words + min-w-0 保证 CJK/英文混排（如「USGS 地震 3 条」）不溢出小卡 */}
                    {!data.inputsError && data.inputs !== null && data.inputs.has_input && data.inputs.observed.length > 0 && (
                      <dd
                        data-testid="rule-engine-dim-input-note"
                        className="mt-0.5 break-words font-mono text-[11px] font-normal text-slate-500 dark:text-slate-400"
                      >
                        {data.inputs.observed.map((item) => `${item.name} ${item.signal_count} 条`).join(' · ')}
                      </dd>
                    )}
                  </div>
                  {/* 引用信源：N = 已接入（linked）信源数，M = 声明信源总数（原型 :638） */}
                  <div className="min-w-0 rounded-xl border border-slate-200/80 bg-[#f8fafc] p-3 dark:border-slate-700 dark:bg-slate-900/40">
                    <dt className="text-[10.5px] font-bold tracking-wide text-slate-500 dark:text-slate-400">引用信源</dt>
                    <dd data-testid="rule-engine-dim-source-count" className="mt-1 break-words font-mono text-[13px] font-bold text-[#101d28] dark:text-white">
                      {selectedDim.dataSources.filter((source) => source.linked).length} / {selectedDim.dataSources.length} 已接入
                    </dd>
                  </div>
                  {/* 启用匹配柱：value → 中文标签（SUMMARY_MATCH_COLUMN_LABELS），· 连接（原型 :642） */}
                  <div className="min-w-0 rounded-xl border border-slate-200/80 bg-[#f8fafc] p-3 dark:border-slate-700 dark:bg-slate-900/40">
                    <dt className="text-[10.5px] font-bold tracking-wide text-slate-500 dark:text-slate-400">启用匹配柱</dt>
                    <dd data-testid="rule-engine-dim-match-columns" className="mt-1 break-words text-[13px] font-bold text-[#101d28] dark:text-white">
                      {selectedDim.matchColumns.map((column) => SUMMARY_MATCH_COLUMN_LABELS[column] ?? column).join(' · ') || '—'}
                    </dd>
                  </div>
                </dl>
              </div>
            </div>
            {/* 维度路由提示行（原型 scheme-b-two-tabs.html:646）：显式声明路由边界，避免把矩阵表中
                存在的某事件类型误读为「本维度会处理它」 */}
            <p data-testid="rule-engine-dim-note" className="mb-4 text-[11px] leading-relaxed text-slate-500 dark:text-slate-400">
              该维度仅处理其声明的事件类型；未声明的类型不会路由到本维度。
            </p>
            {/* 内容区：不用 AnimatePresence mode="wait"，避免切换时新面板延迟挂载 */}
            <motion.div
              key={`${selectedDim.id}-${effectiveMode}`}
              initial={reduceMotion ? false : {opacity: 0, y: 6}}
              animate={{opacity: 1, y: 0}}
              transition={{duration: reduceMotion ? 0 : 0.18, ease: 'easeOut'}}
              className="space-y-6 min-w-0"
            >
              {effectiveMode === 'observation' ? (
                <div data-testid="rule-engine-observation" data-mode={effectiveMode} className="space-y-6">
                  {data.globalConfigError && (
                    <div role="alert" className="text-[12px] text-red-700 bg-red-50 border border-red-200 rounded-lg px-3 py-2 dark:text-red-300 dark:bg-red-950/30 dark:border-red-900">
                      全局配置加载失败：{data.globalConfigError}
                    </div>
                  )}
                  {/* ①② 两块维度级内容在 ≥1280px 并排（原型 .dim-layout，scheme-b-two-tabs.html:647、CSS :257）：
                      1.05fr / 1fr、items-start，窄屏回落单列；grid 只做布局，两个锚点元素自身的
                      testid / data-dimension 语义保持不变。 */}
                  <div className="grid grid-cols-1 items-start gap-6 xl:grid-cols-[minmax(0,1.05fr)_minmax(0,1fr)] xl:gap-4">
                  {/* ① 配置（维度级）锚点：观察态只读配置摘要的归属容器（摘要内容由 todo 13 填充）。
                      data-dimension 是机械可校验的归属标记——切维度后必须同步为新的 selectedDim.id，
                      让「这段内容属于哪个维度」不依赖页面文案判断。 */}
                  <div data-testid="rule-engine-scope-config" data-dimension={selectedDim.id} aria-labelledby="rule-engine-scope-config-title" className="space-y-4 rounded-2xl border border-slate-200/80 bg-white/80 p-5 shadow-sm backdrop-blur-md dark:border-slate-700/60 dark:bg-slate-800/60">
                    <ScopeCardHeader
                      num="①"
                      titleId="rule-engine-scope-config-title"
                      title="配置"
                      dimensionName={selectedDim.name}
                      note={`以下为『${selectedDim.name}』的维度级配置，仅在其接管的事件被处理时生效。`}
                    />
                    {/* todo 13：观察态只读配置摘要。
                        为什么只读：观察态的职责是「如实讲清当前生效的规则」，而不是修改它；可编辑控件
                        全部只属于配置态（rule-engine-config-panel），这里只用文本与 chip，不出现
                        button/input/select/textarea。
                        为什么取 selectedDim 的已保存值而非 draft：draft 是配置态编辑中的未保存草稿，
                        管理员改完草稿后切回观察态时 draft 可能领先于数据库；观察态若读草稿，用户会
                        误以为「页面显示的就是已生效配置」。摘要必须展示已保存、真正生效的值。 */}
                    <section
                      data-testid="rule-engine-config-summary"
                      aria-label={`${selectedDim.name} 维度级配置摘要（只读）`}
                      className="space-y-5"
                    >
                      <header className="flex flex-wrap items-baseline justify-between gap-2 pb-3 border-b border-slate-100 dark:border-slate-800">
                        <h2 className="font-bold text-[16px] text-[#101d28] dark:text-white">配置摘要</h2>
                        <span className="text-[11px] text-slate-500 dark:text-slate-400">当前生效值 · 只读</span>
                      </header>

                      {/* 1. 启用匹配柱 */}
                      <div data-testid="rule-engine-summary-match-columns" className="space-y-1.5">
                        <h3 className="text-[13px] font-bold text-[#424751] dark:text-slate-300">启用匹配柱</h3>
                        <div className="flex flex-wrap gap-1.5">
                          {selectedDim.matchColumns.map((column) => (
                            <span
                              key={column}
                              className="rounded-xl border border-[#004782]/30 bg-[#eef6ff] px-2 py-0.5 text-[11px] font-bold text-[#004782] dark:border-blue-300/40 dark:bg-slate-700 dark:text-blue-300"
                            >
                              {SUMMARY_MATCH_COLUMN_LABELS[column] ?? column}
                            </span>
                          ))}
                          {selectedDim.matchColumns.length === 0 && (
                            <span className="text-[11px] text-slate-400 dark:text-slate-500">—</span>
                          )}
                        </div>
                      </div>

                      {/* 2. 接管事件类型：value → label 用 data.options.event_types 映射，找不到或选项未加载时回退 value */}
                      <div data-testid="rule-engine-summary-event-types" className="space-y-1.5">
                        <h3 className="text-[13px] font-bold text-[#424751] dark:text-slate-300">接管事件类型</h3>
                        <div className="flex flex-wrap gap-1.5">
                          {selectedDim.eventTypes.map((eventType) => (
                            <span
                              key={eventType}
                              className="rounded-xl border border-slate-200 bg-slate-50 px-2 py-0.5 text-[11px] font-medium text-slate-700 dark:border-slate-700 dark:bg-slate-900/40 dark:text-slate-300"
                            >
                              {data.options.event_types.find((option) => option.value === eventType)?.label ?? eventType}
                            </span>
                          ))}
                          {selectedDim.eventTypes.length === 0 && (
                            <span className="text-[11px] text-slate-400 dark:text-slate-500">—</span>
                          )}
                        </div>
                      </div>

                      {/* 3. 严重程度分值：行式进度条（原型 scheme-b-two-tabs.html:666-674）。
                          为什么观察态用 bar 而非数字宫格：严重程度是同一量纲（0–35）上的少数几档，
                          bar 让「哪一档更重、差距多大」一眼可比，mono 数值右对齐后信息密度与原型对齐；
                          数字宫格只能逐格读数字，横向比较成本高。条宽按真实值 / 35 计算，不写死。 */}
                      <div data-testid="rule-engine-summary-severity" className="space-y-2">
                        <h3 className="flex flex-wrap items-baseline gap-2 text-[13px] font-bold text-[#424751] dark:text-slate-300">
                          严重程度分值
                          <span className="text-[10.5px] font-normal text-slate-500 dark:text-slate-400">
                            继承全局默认 · 可调范围 0–{SEVERITY_MAX}
                          </span>
                        </h3>
                        <div className="grid gap-1.5">
                          {SUMMARY_SEVERITY_ORDER.map((key) => {
                            const value = selectedDim.severityScores[key];
                            return (
                              <div
                                key={key}
                                className="grid grid-cols-[88px_minmax(0,1fr)_40px] items-center gap-2.5 sm:grid-cols-[100px_minmax(0,1fr)_40px]"
                              >
                                <span className="truncate text-[11.5px] font-bold text-slate-600 dark:text-slate-300">
                                  {SUMMARY_SEVERITY_LABELS[key] ?? key}
                                  <i className="ml-1 font-mono text-[10px] font-normal text-slate-400 dark:text-slate-500">{key}</i>
                                </span>
                                <span
                                  aria-hidden="true"
                                  className="h-1.5 overflow-hidden rounded-full border border-slate-200 bg-slate-100 dark:border-slate-700 dark:bg-slate-800"
                                >
                                  <span
                                    className="block h-full rounded-full bg-[#004782] dark:bg-blue-400"
                                    style={{width: `${summaryBarPercent(value, SEVERITY_MAX)}%`}}
                                  />
                                </span>
                                <span
                                  data-testid={`rule-engine-summary-severity-${key}`}
                                  className="text-right font-mono text-[12px] font-bold text-[#101d28] dark:text-white"
                                >
                                  {value ?? '—'}
                                </span>
                              </div>
                            );
                          })}
                        </div>
                      </div>

                      {/* 4. 关联类型分值：6 组行式进度条，宽屏两列（原型 :675-685）。
                          组内多键（法人全称/别名、地点距离/地点文本）取最高分，与评分引擎
                          「命中多种关联时取最高分计入」的语义一致；条宽按真实值 / 30 计算。
                          两列用「容器查询」而非视口断点：本卡在左栏 + 双卡并排下宽度远小于视口，
                          容器 < 420px 时若强行两列，固定列（标签 + 数值）会把 bar 列压成 0 宽；
                          容器 ≥ 420px（约 1920 视口起）再分两列，每列仍 ≥ 200px。 */}
                      <div data-testid="rule-engine-summary-association" className="@container space-y-2">
                        <h3 className="flex flex-wrap items-baseline gap-2 text-[13px] font-bold text-[#424751] dark:text-slate-300">
                          关联类型分值
                          <span className="text-[10.5px] font-normal text-slate-500 dark:text-slate-400">
                            继承全局默认 · 命中多种关联时取最高分计入
                          </span>
                        </h3>
                        <div className="grid gap-1.5 @min-[420px]:grid-cols-2 @min-[420px]:gap-x-5">
                          {SUMMARY_ASSOCIATION_GROUPS.map((group) => {
                            const value = summaryGroupMax(selectedDim.associationScores, group.keys);
                            return (
                              <div
                                key={group.label}
                                className="grid grid-cols-[88px_minmax(0,1fr)_40px] items-center gap-2.5 sm:grid-cols-[100px_minmax(0,1fr)_40px]"
                              >
                                <span className="truncate text-[11.5px] font-bold text-slate-600 dark:text-slate-300">{group.label}</span>
                                <span
                                  aria-hidden="true"
                                  className="h-1.5 overflow-hidden rounded-full border border-slate-200 bg-slate-100 dark:border-slate-700 dark:bg-slate-800"
                                >
                                  <span
                                    className="block h-full rounded-full bg-[#004782] dark:bg-blue-400"
                                    style={{width: `${summaryBarPercent(value, ASSOCIATION_MAX)}%`}}
                                  />
                                </span>
                                <span className="text-right font-mono text-[12px] font-bold text-[#101d28] dark:text-white">
                                  {value ?? '—'}
                                </span>
                              </div>
                            );
                          })}
                        </div>
                      </div>

                      {/* 5. 分级阈值：单行三档「总分 ≥ N」（原型 :686-693），低于 P3 线即 P4 */}
                      <div data-testid="rule-engine-summary-thresholds" className="space-y-2">
                        <h3 className="flex flex-wrap items-baseline gap-2 text-[13px] font-bold text-[#424751] dark:text-slate-300">
                          分级阈值
                          <span className="text-[10.5px] font-normal text-slate-500 dark:text-slate-400">低于 P3 线为 P4</span>
                        </h3>
                        <div className="flex flex-wrap gap-x-4 gap-y-1.5">
                          {SUMMARY_THRESHOLD_KEYS.map((key) => (
                            <span key={key} className="inline-flex items-center gap-1.5 text-[12px]">
                              <span className={`rounded px-1.5 py-0.5 text-[11px] font-bold ${SUMMARY_THRESHOLD_CHIP_CLASS[key]}`}>
                                {key.toUpperCase()}
                              </span>
                              <span className="font-mono text-[12px] font-bold text-[#101d28] dark:text-white">
                                总分 ≥ <span data-testid={`rule-engine-summary-threshold-${key}`}>{selectedDim.thresholds[key]}</span>
                              </span>
                            </span>
                          ))}
                        </div>
                      </div>
                    </section>
                  </div>
                  {/* ② 证据（维度级）锚点：本维度自己的运行轨迹、引用信源与样例都属「证据」。
                      观察态/配置态两支互斥渲染，故两支各挂同一 evidenceCard；同一时刻只存在一支。 */}
                  {evidenceCard}
                  </div>
                  {/* 规则矩阵表 / 规则语义说明 / 信号过滤已迁入全局面板（全维度共用，全页唯一），
                      本分支不再挂载，避免同一语义出现两份实例与重复请求 */}
                </div>
              ) : (
                <div data-testid="rule-engine-config" data-mode={effectiveMode} className="space-y-6">
                  {/* ①② 与观察态同一布局：≥1280px 两列（1.05fr / 1fr，items-start），窄屏回落单列 */}
                  <div className="grid grid-cols-1 items-start gap-6 xl:grid-cols-[minmax(0,1.05fr)_minmax(0,1fr)] xl:gap-4">
                  {/* ① 配置（维度级）锚点：包裹整张维度规则配置卡（头部 + 匹配柱/事件类型 + 评分编辑器 + configError + 提醒失效）。
                      data-dimension 是机械可校验的归属标记——切维度后必须同步为新的 selectedDim.id，
                      让「这段内容属于哪个维度」不依赖页面文案判断。 */}
                  <div data-testid="rule-engine-scope-config" data-dimension={selectedDim.id} aria-labelledby="rule-engine-scope-config-title" className="space-y-4 rounded-2xl border border-slate-200/80 bg-white/80 p-5 shadow-sm backdrop-blur-md dark:border-slate-700/60 dark:bg-slate-800/60">
                    <ScopeCardHeader
                      num="①"
                      titleId="rule-engine-scope-config-title"
                      title="配置"
                      dimensionName={selectedDim.name}
                      note={`以下为『${selectedDim.name}』的维度级配置，仅在其接管的事件被处理时生效。`}
                    />
                    {/* 维度级可编辑控件主体集中在一个 panel（todo 12）：
                        ① 该容器只在配置态渲染；观察态渲染只读摘要（todo 13），不出现本容器，
                           机械校验据此即可确认「可编辑控件只属于配置态、不属于观察态」；
                        ② 匹配柱 / 事件类型 / 评分与阈值等维度级可编辑控件都包含在这个边界内，
                           校验控件归属时无需依赖页面文案判断；
                        ③ 沙箱测试是独立工具，不属于维度级配置主体，保持在 panel 之外。 */}
                    <div data-testid="rule-engine-config-panel">
                    {/* 配置卡：外层 scope-config 已是卡片，这里不再重复卡边框，只保留卡内纵向节奏 */}
                    <div className="space-y-5">
                      {/* config-panel-head：一行标题（原型 scheme-b-two-tabs.html:698-701）。
                          规则 ID 是页面既有信息，保留在同一行右侧的 mono 位；原卡头的
                          取消/保存按钮已按原型 :742-747 移到卡底 config-actions。 */}
                      <div className="flex flex-wrap items-baseline justify-between gap-2 pb-3 border-b border-slate-100 dark:border-slate-800">
                        <h2 className="font-bold text-[18px] text-[#101d28] dark:text-white">
                          维度级配置 · {selectedDim.name}
                        </h2>
                        <span className="text-[11px] font-mono text-slate-400 font-bold">
                          ID: {selectedDim.ruleId}
                        </span>
                      </div>

                      {/* 组①「匹配柱」（原型四组之一）与一并保留的「事件类型」：复用的两个子组件自身
                          即「组标题 + field-unit 提示 + 勾选控件」的完整小卡，外层不再重复包装标题，
                          避免同一面板出现两份同名标题（既有测试以「匹配柱」标题定位该区块）。 */}
                      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <RuleEngineMatchColumns
                          options={data.options.match_columns}
                          value={draft.matchColumns}
                          onChange={(next) => updateDraft({matchColumns: next})}
                          disabled={!isAdmin}
                        />
                        <RuleEngineEventTypes
                          options={data.options.event_types}
                          value={draft.eventTypes}
                          onChange={(next) => updateDraft({eventTypes: next})}
                          disabled={!isAdmin}
                        />
                      </div>

                      {/* 强制规则（全局层）已迁入全局面板（全维度共用，全页唯一）：配置卡不再挂载副本 */}

                      {/* 组②③④「严重程度分值 / 关联类型分值 / 分级阈值」：整块复用 RuleEngineScoringEditor，
                          滑杆与数字输入逻辑零重写。要让四组「在视觉上成为四个小卡」而子组件文件不可改，
                          只在外层作用域给子组件内部的编辑器节注入统一的小卡样式（圆角/边框/内边距）；
                          解算预览节自身已是卡片，用 :not 排除，避免同属性样式互相覆盖。 */}
                      <div
                        data-testid="rule-engine-scoring-groups"
                        className="[&>section>section:not([data-testid='rule-engine-solve-preview-panel'])]:rounded-xl [&>section>section:not([data-testid='rule-engine-solve-preview-panel'])]:border [&>section>section:not([data-testid='rule-engine-solve-preview-panel'])]:border-slate-200 [&>section>section:not([data-testid='rule-engine-solve-preview-panel'])]:bg-[#f8fafc] [&>section>section:not([data-testid='rule-engine-solve-preview-panel'])]:p-3 dark:[&>section>section:not([data-testid='rule-engine-solve-preview-panel'])]:border-slate-800 dark:[&>section>section:not([data-testid='rule-engine-solve-preview-panel'])]:bg-slate-950/40"
                      >
                        <RuleEngineScoringEditor mode={effectiveMode} />
                      </div>

                      {configError && (
                        <div role="alert" className="text-[12px] text-red-700 bg-red-50 border border-red-200 rounded-lg px-3 py-2 space-y-1 dark:text-red-300 dark:bg-red-950/30 dark:border-red-900">
                          <p>{configError}</p>
                          {configConflicts.length > 0 && (
                            <ul className="list-disc pl-4 space-y-0.5">
                              {configConflicts.map((conflict) => (
                                <li key={`${conflict.event_type}-${conflict.dimension}`}>
                                  事件类型「{conflict.event_type}」已被维度「{conflict.dimension}」占用
                                </li>
                              ))}
                            </ul>
                          )}
                        </div>
                      )}

                      {/* 提醒失效说明（由信号有效期策略决定，只读） */}
                      <div className="space-y-1">
                        <label className="text-[13px] font-bold text-[#424751] dark:text-slate-300">提醒失效</label>
                        <p className="text-[12px] text-slate-500 leading-relaxed dark:text-slate-400">
                          提醒失效由信号有效期策略决定，不再在此单独配置。请在
                          <a href={routePaths.sources} className="text-[#004782] underline underline-offset-2 hover:text-[#2563EB] dark:text-blue-300">信息源</a>
                          的有效期配置中管理。
                        </p>
                      </div>

                      {/* config-actions 卡底按钮行（原型 :742-747）：保存配置/取消 从卡头移到卡底，
                          新增与左栏开关共用同一 `sandboxOpen` 状态的「沙箱测试」入口；右侧 note 说明
                          控件可操作但未保存前不影响现有生效配置。保存/取消行为仍走既有
                          handleSaveConfig / resetDraft，未改变。 */}
                      <div className="flex flex-wrap items-center gap-2 border-t border-slate-100 pt-4 dark:border-slate-800">
                        <button
                          onClick={() => void handleSaveConfig()}
                          disabled={!isAdmin || saving}
                          className="px-4 py-1.5 bg-[#004782] text-white rounded-lg text-[13px] font-bold shadow-sm hover:bg-[#185fa5] transition-colors disabled:opacity-60"
                        >
                          {saving ? '保存中…' : '保存配置'}
                        </button>
                        <button
                          onClick={resetDraft}
                          className="px-3 py-1.5 border border-[#e2e8f0] text-[#424751] rounded-lg text-[13px] font-medium hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
                        >
                          取消
                        </button>
                        <button
                          type="button"
                          aria-expanded={sandboxOpen}
                          aria-controls="rule-engine-sandbox"
                          onClick={() => setSandboxOpen((open) => !open)}
                          className="inline-flex items-center gap-1.5 px-3 py-1.5 border border-[#e2e8f0] text-[#424751] rounded-lg text-[13px] font-medium hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
                        >
                          <span className="material-symbols-outlined text-[16px]" aria-hidden="true">science</span>
                          沙箱测试
                        </button>
                        <span className="text-[10.5px] text-slate-500 dark:text-slate-400 sm:ml-auto">
                          控件可操作，未保存前不会影响现有生效配置
                        </span>
                      </div>
                    </div>
                    </div>
                  </div>

                  {/* ② 证据（维度级）锚点：与观察态同一 evidenceCard（信源 → 样例 → 运行轨迹），
                      两态结构与顺序绝不分叉；同一时刻只渲染这一支，DimensionSources 全页只出现一次。 */}
                  {evidenceCard}
                  </div>

                  <AnimatePresence initial={false}>
                    {sandboxOpen && (
                      <motion.div
                        id="rule-engine-sandbox"
                        initial={reduceMotion ? false : {opacity: 0, y: -6}}
                        animate={{opacity: 1, y: 0}}
                        exit={reduceMotion ? {opacity: 0} : {opacity: 0, y: -6}}
                        transition={{duration: reduceMotion ? 0 : 0.18, ease: 'easeOut'}}
                        className="space-y-4 rounded-2xl border border-slate-200/80 bg-white/80 p-5 text-[#101d28] shadow-sm backdrop-blur-md dark:border-slate-700/60 dark:bg-slate-800/60"
                      >
                        <div className="flex items-start gap-3">
                          <span className="material-symbols-outlined text-[22px] text-[#004782] mt-0.5">science</span>
                          <div>
                            <h2 className="font-bold text-[16px] text-[#101d28] dark:text-white">沙箱测试</h2>
                            <p className="text-[11px] text-slate-500 mt-0.5 dark:text-slate-400">
                              保存规则前，用样例事件验证当前配置会命中哪些真实供应商、得到多少分和什么等级；不创建事件、提醒或其他业务记录。
                            </p>
                          </div>
                        </div>

                        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                          <div>
                            <label className="text-[12px] font-bold text-slate-500 dark:text-slate-400">事件大类</label>
                            <select
                              value={sample.eventType}
                              onChange={(e) => updateSample({eventType: e.target.value})}
                              className="w-full bg-[#f8fafc] border border-[#e2e8f0] rounded-lg p-2 text-[13px] font-medium mt-1 dark:bg-slate-900 dark:border-slate-700 dark:text-white"
                            >
                              {data.options.event_types.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                            </select>
                          </div>

                          <div>
                            <label className="text-[12px] font-bold text-slate-500 dark:text-slate-400">事件细类（可选）</label>
                            <select
                              value={sample.eventSubtype}
                              onChange={(e) => updateSample({eventSubtype: e.target.value})}
                              className="w-full bg-[#f8fafc] border border-[#e2e8f0] rounded-lg p-2 text-[13px] font-medium mt-1 dark:bg-slate-900 dark:border-slate-700 dark:text-white"
                            >
                              <option value="">不指定</option>
                              {data.options.event_subtypes.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                            </select>
                          </div>

                          <div>
                            <label className="text-[12px] font-bold text-slate-500 dark:text-slate-400">严重程度</label>
                            <select
                              value={sample.severity}
                              onChange={(e) => updateSample({severity: e.target.value as RuleEngineSampleEvent['severity']})}
                              className="w-full bg-[#f8fafc] border border-[#e2e8f0] rounded-lg p-2 text-[13px] font-medium mt-1 dark:bg-slate-900 dark:border-slate-700 dark:text-white"
                            >
                              <option value="critical">严重</option><option value="high">高</option><option value="medium">中</option><option value="low">低</option>
                            </select>
                          </div>

                          <div>
                            <label className="text-[12px] font-bold text-slate-500 dark:text-slate-400">供应商名称</label>
                            <input value={sample.organization} onChange={(e) => updateSample({organization: e.target.value})} placeholder="如：某某科技有限公司"
                              className="w-full bg-[#f8fafc] border border-[#e2e8f0] rounded-lg p-2 text-[13px] mt-1 dark:bg-slate-900 dark:border-slate-700 dark:text-white" />
                          </div>

                          <div>
                            <label className="text-[12px] font-bold text-slate-500 dark:text-slate-400">统一信用代码 / 注册号</label>
                            <input value={sample.registryNo} onChange={(e) => updateSample({registryNo: e.target.value})} placeholder="用于主体精确匹配"
                              className="w-full bg-[#f8fafc] border border-[#e2e8f0] rounded-lg p-2 text-[13px] mt-1 dark:bg-slate-900 dark:border-slate-700 dark:text-white" />
                          </div>

                          <div>
                            <label className="text-[12px] font-bold text-slate-500 dark:text-slate-400">事件地点</label>
                            <input value={sample.location} onChange={(e) => updateSample({location: e.target.value})} placeholder="如：上海市"
                              className="w-full bg-[#f8fafc] border border-[#e2e8f0] rounded-lg p-2 text-[13px] mt-1 dark:bg-slate-900 dark:border-slate-700 dark:text-white" />
                          </div>

                          <div>
                            <label className="text-[12px] font-bold text-slate-500 dark:text-slate-400">省州地区</label>
                            <input value={sample.region} onChange={(e) => updateSample({region: e.target.value})} placeholder="如：上海市"
                              className="w-full bg-[#f8fafc] border border-[#e2e8f0] rounded-lg p-2 text-[13px] mt-1 dark:bg-slate-900 dark:border-slate-700 dark:text-white" />
                          </div>

                          <div>
                            <label className="text-[12px] font-bold text-slate-500 dark:text-slate-400">城市</label>
                            <input value={sample.city} onChange={(e) => updateSample({city: e.target.value})} placeholder="如：上海市"
                              className="w-full bg-[#f8fafc] border border-[#e2e8f0] rounded-lg p-2 text-[13px] mt-1 dark:bg-slate-900 dark:border-slate-700 dark:text-white" />
                          </div>

                          <div>
                            <label className="text-[12px] font-bold text-slate-500 dark:text-slate-400">国家/地区代码</label>
                            <input value={sample.countryCode} onChange={(e) => updateSample({countryCode: e.target.value})} maxLength={2} placeholder="如：CN"
                              className="w-full bg-[#f8fafc] border border-[#e2e8f0] rounded-lg p-2 text-[13px] font-mono mt-1 dark:bg-slate-900 dark:border-slate-700 dark:text-white" />
                          </div>

                          <div>
                            <label className="text-[12px] font-bold text-slate-500 dark:text-slate-400">区县</label>
                            <input value={sample.district} onChange={(e) => updateSample({district: e.target.value})} placeholder="如：浦东新区"
                              className="w-full bg-[#f8fafc] border border-[#e2e8f0] rounded-lg p-2 text-[13px] mt-1 dark:bg-slate-900 dark:border-slate-700 dark:text-white" />
                          </div>

                          <div>
                            <label className="text-[12px] font-bold text-slate-500 dark:text-slate-400">受影响产品</label>
                            <input value={sample.products} onChange={(e) => updateSample({products: e.target.value})} placeholder="多个值用逗号分隔"
                              className="w-full bg-[#f8fafc] border border-[#e2e8f0] rounded-lg p-2 text-[13px] mt-1 dark:bg-slate-900 dark:border-slate-700 dark:text-white" />
                          </div>

                          <div>
                            <label className="text-[12px] font-bold text-slate-500 dark:text-slate-400">受影响行业</label>
                            <input value={sample.industries} onChange={(e) => updateSample({industries: e.target.value})} placeholder="多个值用逗号分隔"
                              className="w-full bg-[#f8fafc] border border-[#e2e8f0] rounded-lg p-2 text-[13px] mt-1 dark:bg-slate-900 dark:border-slate-700 dark:text-white" />
                          </div>

                          <div>
                            <label className="text-[12px] font-bold text-slate-500 dark:text-slate-400">来源可信度 (0-100)</label>
                            <input type="number" min="0" max="100" value={sample.credibility} onChange={(e) => updateSample({credibility: Number(e.target.value)})}
                              className="w-full bg-[#f8fafc] border border-[#e2e8f0] rounded-lg p-2 text-[13px] font-mono font-bold mt-1 dark:bg-slate-900 dark:border-slate-700 dark:text-white"
                            />
                          </div>
                        </div>

                        {sandboxError && <div role="alert" className="text-[12px] text-red-700 bg-red-50 border border-red-200 rounded-lg px-3 py-2 dark:text-red-300 dark:bg-red-950/30 dark:border-red-900">{sandboxError}</div>}

                        <div className="flex flex-col sm:flex-row justify-between sm:items-center gap-3 pt-2">
                          <span className="text-[11px] text-slate-500 dark:text-slate-400">至少填写主体、地点、产品或行业中的一项，才能观察到供应商候选命中。</span>

                          <button
                            onClick={() => void handleRunSandboxTest()}
                            disabled={sandboxLoading || data.options.event_types.length === 0}
                            className="px-4 py-2 border-2 border-[#004782] text-[#004782] dark:text-blue-400 hover:bg-blue-50 disabled:opacity-50 font-bold text-[13px] rounded-lg flex items-center justify-center gap-1.5 transition-colors"
                          >
                            <span className="material-symbols-outlined text-[18px]">play_arrow</span>
                            <span>{sandboxLoading ? '评估中…' : '按当前规则评估（不落库）'}</span>
                          </button>
                        </div>

                        {sandboxResult && (
                          <div className="p-4 bg-blue-50/80 dark:bg-blue-950/20 border border-blue-300 dark:border-blue-900 rounded-xl space-y-3">
                            <div className="flex flex-wrap justify-between items-center gap-2">
                              <span className="text-[#004782] dark:text-blue-300 text-[14px] font-bold">真实规则评估结果</span>
                              <span className="text-[11px] font-bold bg-white dark:bg-slate-900 border border-blue-200 dark:border-blue-800 rounded-full px-2.5 py-1">
                                路由维度：{sandboxResult.dimension?.label ?? '未找到接管维度'}
                              </span>
                            </div>
                            {sandboxResult.message && <p className="text-[12px] text-slate-600 dark:text-slate-300">{sandboxResult.message}</p>}
                            {sandboxResult.candidates.length === 0 ? (
                              <div className="text-[12px] text-slate-600 bg-white dark:bg-slate-900 p-3 rounded-lg border border-blue-200 dark:border-blue-800 dark:text-slate-300">
                                未命中供应商。可补充供应商全称/注册号、地点、产品或行业后重试；这不表示该事件没有风险。
                              </div>
                            ) : sandboxResult.candidates.slice(0, 5).map((candidate) => (
                              <div key={candidate.supplier_id} className="bg-white dark:bg-slate-900 p-3 rounded-lg border border-blue-200 dark:border-blue-800 space-y-1.5">
                                <div className="flex justify-between items-center gap-3">
                                  <span className="text-[13px] font-bold text-slate-800 dark:text-white">{candidate.supplier_name}</span>
                                  <span className={`px-2 py-0.5 rounded text-[11px] font-bold text-white ${candidate.level === 'P1' ? 'bg-red-600' : candidate.level === 'P2' ? 'bg-amber-600' : candidate.level === 'P3' ? 'bg-blue-600' : 'bg-slate-500'}`}>
                                    {candidate.level} · {candidate.score} 分
                                  </span>
                                </div>
                                <div className="text-[11px] text-slate-500 dark:text-slate-400">匹配方式：{candidate.match_type} · 关联分：{candidate.association_score}</div>
                                <div className="text-[12px] text-slate-700 dark:text-slate-300">{candidate.reasons.join('；') || '命中当前规则'}</div>
                              </div>
                            ))}
                          </div>
                        )}
                      </motion.div>
                    )}
                  </AnimatePresence>
                </div>
              )}
            </motion.div>
            </div>

            {/* 全局规则面板（tabpanel）：与维度面板平级、同样常驻，只切 hidden。 */}
            <div
              role="tabpanel"
              id="rule-engine-tabpanel-global"
              aria-labelledby="rule-engine-tab-global"
              data-testid="rule-engine-tabpanel-global"
              hidden={activeTab !== 'global'}
            >
              {/* 全局 Tab 说明段（原型 scheme-b-two-tabs.html:814-816）：进入即交代本 Tab 聚合的
                  四块公有内容及「全页只呈现一份」的口径，避免与维度视图的私有内容混淆 */}
              <p className="mb-4 max-w-[900px] text-[12.5px] leading-relaxed text-slate-500 dark:text-slate-400">
                「全局规则」集中展示对所有维度生效的公有内容：规则语义说明、信号过滤、全局强制规则与规则矩阵表。此处内容与维度选择无关，全页只呈现一份。
              </p>

              {/* 配置态提示条（原型 :818-820）：仅在配置模式显示；告知本页可编辑项与保存入口的所在位置 */}
              {effectiveMode === 'config' && (
                <div role="note" className="mb-3 rounded-xl border border-dashed border-[#004782]/45 bg-[#eef6ff] px-3.5 py-2.5 text-[12px] text-[#004782] dark:border-blue-300/45 dark:bg-slate-800 dark:text-blue-300">
                  配置模式已开启：本页表格中的勾选与筛选项可直接编辑；『保存配置 / 取消 / 沙箱测试』入口位于『维度视图』的配置面板。
                </div>
              )}

              {/* 全局层：四件（规则语义说明 → 信号过滤规则 → 全局强制规则 → 规则矩阵表，顺序依据已批准原型）
                  物理移出维度内容区并只此一处挂载，原因：
                  1) 它们本就与具体维度无关（全局配置/过滤规则/矩阵表是全维度共用），留在维度区会暗示「每个维度各自配置」，
                     造成语义歧义；2) 每个挂载实例都会各自发起全局配置/信号过滤请求，按维度或按 Tab 复制副本会重复请求；
                  3) 容器位于维度面板的 motion key（`${selectedDim.id}-${effectiveMode}`）之外，
                     切维度/切模式不会重挂，子组件状态不重置；全页每个 testid 恰好一次。 */}
              <section
                data-testid="rule-engine-global-layer"
                aria-label="全维度共用规则"
                className="space-y-6 min-w-0"
              >
                {/* global-head（原型 :824-832）：保留「全维度共用」徽标与既有说明句，补标题
                    「全维度共用规则」与右侧「N 项」计数；计数与 Tab 徽标同源（实际折叠分节数） */}
                <div className="flex items-start gap-3 rounded-2xl border border-slate-200/80 bg-white/80 p-4 shadow-sm backdrop-blur-md dark:border-slate-700/60 dark:bg-slate-800/60 sm:p-5">
                  <span className="inline-flex shrink-0 items-center gap-1 self-start rounded-full border border-blue-200 bg-blue-50 px-2.5 py-0.5 text-[11px] font-bold text-[#004782] dark:border-blue-900 dark:bg-blue-950/40 dark:text-blue-300">
                    <span className="material-symbols-outlined text-[14px]" aria-hidden="true">public</span>
                    全维度共用
                  </span>
                  <div className="min-w-0 flex-1">
                    <h2 className="text-[16px] font-bold text-[#101d28] dark:text-white">全维度共用规则</h2>
                    <p className="mt-1 text-[11.5px] leading-relaxed text-slate-500 dark:text-slate-400">
                      以下内容对所有监控维度统一生效，与当前选中的具体维度无关。
                    </p>
                  </div>
                  <span className="shrink-0 font-mono text-[12px] font-bold text-slate-500 dark:text-slate-400">{globalSectionCount} 项</span>
                </div>

                {/* 三个折叠分节（原型 :834-840、:899-905、:1008-1014）：默认展开；矩阵表保持独立卡片、不折叠。
                    子组件以 embedded 渲染（去掉自身卡片描边/底色与标题），标题统一由分节卡 summary 承担 */}
                {globalSections.map((section, index) => (
                  <GlobalSectionCard key={section.key} index={index + 1} title={section.title} meta={section.meta}>
                    {section.body}
                  </GlobalSectionCard>
                ))}

                <RuleEngineRuleMatrix
                  dimensions={dimensions}
                  options={data.options}
                  optionsError={data.optionsError}
                  samples={data.trace?.samples ?? []}
                  activeEventType={data.trace?.event?.event_type ?? null}
                />
              </section>
            </div>
        </div>
      </div>
    </RuleEngineContext.Provider>
  );
};
