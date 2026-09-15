import React from 'react';
import type {GlobalScoringConfigRead, GlobalScoringPatchPayload, RuleEngineOptions} from '../api';
import type {MonitoringDimension} from '../types';

/**
 * 规则引擎页共享状态（由 RuleEngineView 壳组件承载）。
 *
 * 冻结约定（todo 4）：
 * - 壳组件持有维度草稿与全局层草稿，配置态子组件（评分编辑器、强制规则编辑器）
 *   与解算预览通过本 context 读写，避免各自改壳组件签名。
 * - 观察态子组件（流水线、矩阵表）通过 props 接收轨迹/选项，不直接读本 context。
 */
export type RuleEngineMode = 'observation' | 'config';

// 后端 ScoringSettings 默认分值（severity 0-35，association 0-30）
export const SEVERITY_MAX = 35;
export const ASSOCIATION_MAX = 30;
export const DEFAULT_SEVERITY_SCORES: Record<string, number> = {critical: 35, high: 28, medium: 20, low: 10};
export const DEFAULT_ASSOCIATION_SCORES: Record<string, number> = {
  registry_no: 30, legal_name: 25, alias: 25, site_distance: 20, site_text: 20, product: 12, country: 8, industry: 12,
};

/** 当前选中维度的可编辑草稿（未保存）。 */
export interface RuleEngineDimensionDraft {
  severityScores: Record<string, number>;
  associationScores: Record<string, number>;
  thresholds: {p1: number; p2: number; p3: number};
  matchColumns: string[];
  eventTypes: string[];
}

/** 事件类型冲突（后端 422 detail.conflicts 的逐项展示）。 */
export interface RuleEngineEventTypeConflict {
  event_type: string;
  dimension: string;
}

/**
 * 全局配置尚未加载（加载中或加载失败）时的草稿门控原因。
 *
 * 此期间强制规则编辑器的 rows 为空并不代表「服务端就是空表」，只是尚未同步；
 * 解算预览必须保持禁用，绝不能把 `forced_rules: []` 当成合法草稿发出。
 */
export const GLOBAL_DRAFT_NOT_LOADED_ERROR = '全局配置未加载，暂不能解算预览';

/**
 * 全局草稿门控的实时快照。
 *
 * `globalDraftValid`/`globalDraftError` 是渲染用的状态值；解算预览等异步流程在
 * `await` 之后不能读闭包里的它们（值停留在点击那一刻），必须通过 `readGlobalDraftGate`
 * 同步读取，并用单调递增的 `revision` 判断等待期间草稿是否发生过变化。
 */
export interface GlobalDraftGate {
  /** 强制规则表当前草稿是否可用（默认 false，直到编辑器真正加载并校验 globalConfig） */
  valid: boolean;
  /** 不可用原因；可用时为空串 */
  error: string;
  /** 单调修订号：`globalDraft` 或其有效性任何一次变化都会 +1 */
  revision: number;
}

/** 解算预览与沙箱测试共用的样例事件字段（与后端 /test 请求字段一一对应）。 */
export interface RuleEngineSampleEvent {
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
}

export interface RuleEngineContextValue {
  mode: RuleEngineMode;
  role: 'viewer' | 'admin';
  /** 当前选中维度（已保存值，用于 diff 与保存） */
  dimension: MonitoringDimension;
  /** 维度配置草稿：评分/阈值编辑器编辑，保存与解算预览共用 */
  draft: RuleEngineDimensionDraft;
  updateDraft: (patch: Partial<RuleEngineDimensionDraft>) => void;
  resetDraft: () => void;
  saving: boolean;
  configError: string;
  configConflicts: RuleEngineEventTypeConflict[];
  /** 保存维度草稿（沿用父级 onUpdateDimension 契约，含越界/空柱前置校验） */
  saveDraft: () => Promise<void>;
  /** 全局层权威配置（GET /global-config，只含全局层） */
  globalConfig: GlobalScoringConfigRead | null;
  globalConfigError: string;
  refreshGlobalConfig: () => Promise<void>;
  /** 全局层草稿：解算预览（todo 7）与强制规则编辑器（todo 8）共用 */
  globalDraft: GlobalScoringPatchPayload;
  setGlobalDraft: React.Dispatch<React.SetStateAction<GlobalScoringPatchPayload>>;
  /**
   * 全局层草稿是否与当前强制规则表一致且通过校验。
   *
   * **默认非法**：在强制规则编辑器真正加载 `globalConfig` 并校验过真实行之前保持
   * false（全局配置加载中/加载失败时 `globalConfig === null`，rows 为空并不代表
   * 服务端就是空表）。强制规则编辑器校验失败时也不会写 `globalDraft`（避免把 422
   * 发给后端），但 `globalDraft` 仍保留上一次合法的旧规则；解算预览必须依据本标志
   * 禁用，绝不能用陈旧草稿冒充当前表格。
   */
  globalDraftValid: boolean;
  /** 全局层草稿非法原因（解算预览以 role="alert" 呈现）；合法时为空串 */
  globalDraftError: string;
  /** 由强制规则编辑器在上报草稿有效性的同一副作用点调用 */
  setGlobalDraftValidity: (valid: boolean, error?: string) => void;
  /**
   * 同步读取全局草稿门控（valid/error/单调修订号）。
   *
   * 解算预览在 baseline `await` 之后必须重新调用本函数复核：等待期间草稿被改坏或
   * 改动时中止第二次请求，迟到的响应也不渲染（闭包里的 context 值不会更新）。
   */
  readGlobalDraftGate: () => GlobalDraftGate;
  globalSaving: boolean;
  globalSaveError: string;
  /** 合并写入全局层（PUT /global-config）；返回是否成功 */
  saveGlobalConfig: (patch: GlobalScoringPatchPayload, confirmDisableForcedRules?: boolean) => Promise<boolean>;
  /** 共享样例事件：沙箱评估与解算预览输入一致 */
  sample: RuleEngineSampleEvent;
  updateSample: (patch: Partial<RuleEngineSampleEvent>) => void;
  options: RuleEngineOptions;
  optionsError: string;
}

export const RuleEngineContext = React.createContext<RuleEngineContextValue | null>(null);

export function useRuleEngineContext(): RuleEngineContextValue {
  const value = React.useContext(RuleEngineContext);
  if (value === null) {
    throw new Error('useRuleEngineContext 必须在 RuleEngineView 提供的上下文内使用');
  }
  return value;
}

/** 由已保存维度生成新的草稿（切换维度或保存成功后调用）。 */
export function draftFromDimension(dimension: MonitoringDimension | undefined): RuleEngineDimensionDraft {
  return {
    severityScores: {...(dimension?.severityScores ?? {})},
    associationScores: {...(dimension?.associationScores ?? {})},
    thresholds: {
      p1: dimension?.thresholds.p1 ?? 85,
      p2: dimension?.thresholds.p2 ?? 65,
      p3: dimension?.thresholds.p3 ?? 40,
    },
    matchColumns: [...(dimension?.matchColumns ?? [])],
    eventTypes: [...(dimension?.eventTypes ?? [])],
  };
}

/** 样例事件默认值；eventType 优先取当前维度声明的第一个事件类型。 */
export function defaultSampleEvent(eventType = 'weather'): RuleEngineSampleEvent {
  return {
    eventType,
    eventSubtype: '',
    severity: 'high',
    organization: '',
    registryNo: '',
    location: '',
    region: '',
    city: '',
    countryCode: '',
    district: '',
    products: '',
    industries: '',
    credibility: 80,
  };
}
