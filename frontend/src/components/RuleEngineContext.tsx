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
