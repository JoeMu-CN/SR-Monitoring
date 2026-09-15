import React, {useEffect, useState} from 'react';
import {AnimatePresence, motion, useReducedMotion} from 'motion/react';
import {api, ApiError, GlobalScoringPatchPayload, SandboxResult} from '../api';
import {MonitoringDimension} from '../types';
import {SignalFilterSection} from './SignalFilterSection';
import {RuleEngineDimensionSources} from './RuleEngineDimensionSources';
import {RuleEngineMatchColumns} from './RuleEngineMatchColumns';
import {RuleEngineEventTypes} from './RuleEngineEventTypes';
import {RuleEngineForcedRules} from './RuleEngineForcedRules';
import {RuleEngineScoringEditor, validateDimensionDraft} from './RuleEngineScoringEditor';
import {RuleEnginePipeline} from './RuleEnginePipeline';
import {RuleEngineRuleMatrix} from './RuleEngineRuleMatrix';
import {RuleEngineExplainers} from './RuleEngineExplainers';
import {useRuleEngineData} from './useRuleEngineData';
import {
  defaultSampleEvent,
  draftFromDimension,
  RuleEngineContext,
  RuleEngineContextValue,
  RuleEngineDimensionDraft,
  RuleEngineEventTypeConflict,
  RuleEngineMode,
  RuleEngineSampleEvent,
} from './RuleEngineContext';
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
  const [globalDraft, setGlobalDraft] = useState<GlobalScoringPatchPayload>({});
  const [globalSaving, setGlobalSaving] = useState(false);
  const [globalSaveError, setGlobalSaveError] = useState('');

  // —— 沙箱/解算预览共享的样例事件 ——
  const [sample, setSample] = useState<RuleEngineSampleEvent>(() => defaultSampleEvent());
  const [sandboxResult, setSandboxResult] = useState<SandboxResult | null>(null);
  const [sandboxError, setSandboxError] = useState('');
  const [sandboxLoading, setSandboxLoading] = useState(false);
  const [sandboxOpen, setSandboxOpen] = useState(false);

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
    return <div className="bg-white dark:bg-slate-900 border border-[#c2c6d2] rounded-xl p-8 text-center text-slate-500">暂无可用监控维度</div>;
  }

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
    globalSaving,
    globalSaveError,
    saveGlobalConfig,
    sample,
    updateSample,
    options: data.options,
    optionsError: data.optionsError,
  };

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
                  ? 'border-[#004782] bg-[#ecf4ff] text-[#004782] dark:border-blue-300 dark:bg-slate-800 dark:text-blue-300'
                  : 'border-[#c2c6d2] bg-white text-[#101d28] hover:bg-[#f7f9ff] dark:border-slate-700 dark:bg-slate-900 dark:text-white dark:hover:bg-slate-800'
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

        {/* Main Grid Layout (Left: Dimensions List, Right: Observation / Config) */}
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
          {/* Left Column: Monitoring Dimensions List (4 cols) */}
          <div className="lg:col-span-4 space-y-3 h-fit">
            <div className="space-y-3 rounded-2xl border border-slate-200/80 bg-white/80 p-4 shadow-sm backdrop-blur-md dark:border-slate-700/60 dark:bg-slate-800/60">
              <div className="flex justify-between items-center pb-2 border-b border-slate-100 dark:border-slate-800">
                <h2 className="font-bold text-[15px] text-[#101d28] dark:text-white">监控维度</h2>
                {effectiveMode === 'config' && (
                  <button
                    disabled
                    className="p-1 rounded-lg text-slate-300 cursor-not-allowed"
                    title="当前版本不新增自定义维度"
                  >
                    <span className="material-symbols-outlined text-[20px]">add</span>
                  </button>
                )}
              </div>

              <div className="space-y-2">
                {dimensions.map((dim) => {
                  const isSelected = dim.id === activeDimId;
                  return (
                    <div
                      key={dim.id}
                      data-testid={`rule-engine-dimension-${dim.id}`}
                      onClick={() => setActiveDimId(dim.id)}
                      className={`p-3 rounded-xl border flex items-center justify-between cursor-pointer transition-all ${
                        isSelected
                          ? 'bg-[#ecf4ff] dark:bg-slate-800 border-[#004782] shadow-2xs'
                          : 'border-[#c2c6d2]/60 hover:bg-slate-50 dark:hover:bg-slate-800/50'
                      }`}
                    >
                      <div className="flex items-start gap-3 min-w-0">
                        <span className="material-symbols-outlined text-[#004782] text-[22px]">
                          {dim.icon}
                        </span>
                        <div className="min-w-0">
                          <div className="font-bold text-[14px] text-[#101d28] dark:text-white">{dim.name}</div>
                          {/* 「具体监控内容」卡片已按 todo 9 移除：完整清单改挂维度项悬浮/详情，
                              文本节点保留全部条目（truncate 仅做视觉裁剪），信息不丢失 */}
                          <div
                            className="text-[11px] text-slate-500 mt-0.5 truncate"
                            title={dim.contentItems.length > 0 ? dim.contentItems.join(' · ') : undefined}
                          >
                            {dim.contentItems.join(' · ') || '待配置监控内容'}
                          </div>
                          {isSelected && (
                            <div className="text-[11px] mt-0.5">
                              {data.inputsError ? (
                                <span className="text-red-700 dark:text-red-300">输入健康度加载失败</span>
                              ) : data.inputs === null ? (
                                <span className="inline-flex items-center gap-1 text-slate-400 dark:text-slate-500"><span className="material-symbols-outlined animate-spin text-[12px] leading-none" aria-hidden="true">progress_activity</span><span className="sr-only">输入健康度加载中…</span></span>
                              ) : data.inputs.has_input ? (
                                <span className="text-slate-600 dark:text-slate-300">近 30 天 {data.inputs.observed.length} 个信源有输入</span>
                              ) : (
                                <span className="text-slate-500 dark:text-slate-400">当前无输入</span>
                              )}
                            </div>
                          )}
                        </div>
                      </div>

                      {/* 观察态：只读启停状态；配置态：管理员启停开关 */}
                      {effectiveMode === 'config' ? (
                        <label
                          onClick={(e) => e.stopPropagation()}
                          className="relative inline-flex items-center cursor-pointer"
                        >
                          <input
                            type="checkbox"
                            aria-label={`${dim.name} 启用状态`}
                            checked={dim.enabled}
                            onChange={() => { if (isAdmin) void onToggleDimension(dim.id); }}
                            disabled={!isAdmin}
                            className="sr-only peer"
                          />
                          <div className="w-9 h-5 bg-slate-200 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-slate-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-[#004782]"></div>
                        </label>
                      ) : (
                        <span
                          className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-bold ${
                            dim.enabled
                              ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300'
                              : 'bg-slate-200 text-[#424751] dark:bg-slate-800 dark:text-slate-300'
                          }`}
                        >
                          {dim.enabled ? '已启用' : '已停用'}
                        </span>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>

            {/* 沙箱测试开关：仅配置态提供（评估接口为 rule_manage 权限，viewer 不可用） */}
            {effectiveMode === 'config' && (
              <button
                type="button"
                aria-expanded={sandboxOpen}
                aria-controls="rule-engine-sandbox"
                onClick={() => setSandboxOpen((open) => !open)}
                className={`w-full min-h-12 px-4 py-3 rounded-xl border font-bold text-[14px] flex items-center justify-between gap-3 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#004782] focus-visible:ring-offset-2 ${
                  sandboxOpen
                    ? 'bg-[#ecf4ff] dark:bg-slate-800 border-[#004782] text-[#004782] dark:text-blue-300'
                    : 'bg-white dark:bg-slate-900 border-[#c2c6d2] dark:border-slate-800 text-[#101d28] dark:text-white hover:bg-[#f7f9ff] dark:hover:bg-slate-800'
                }`}
              >
                <span className="flex items-center gap-2">
                  <span className="material-symbols-outlined text-[20px] text-[#004782] dark:text-blue-300">science</span>
                  沙箱测试
                </span>
                <span className="material-symbols-outlined text-[20px]" aria-hidden="true">
                  {sandboxOpen ? 'expand_less' : 'expand_more'}
                </span>
              </button>
            )}
          </div>

          {/* Right Column：不用 AnimatePresence mode="wait"，避免切换时新面板延迟挂载 */}
          <motion.div
            key={`${selectedDim.id}-${effectiveMode}`}
            initial={reduceMotion ? false : {opacity: 0, y: 6}}
            animate={{opacity: 1, y: 0}}
            transition={{duration: reduceMotion ? 0 : 0.18, ease: 'easeOut'}}
            className="lg:col-span-8 space-y-6 min-w-0"
          >
              {effectiveMode === 'observation' ? (
                <div data-testid="rule-engine-observation" data-mode={effectiveMode} className="space-y-6">
                  {data.globalConfigError && (
                    <div role="alert" className="text-[12px] text-red-700 bg-red-50 border border-red-200 rounded-lg px-3 py-2 dark:text-red-300 dark:bg-red-950/30 dark:border-red-900">
                      全局配置加载失败：{data.globalConfigError}
                    </div>
                  )}
                  <RuleEnginePipeline
                    dimension={selectedDim}
                    trace={data.trace}
                    traceError={data.traceError}
                    inputs={data.inputs}
                    inputsError={data.inputsError}
                    selectedSampleId={selectedSampleId}
                  />
                  <RuleEngineRuleMatrix
                    dimensions={dimensions}
                    options={data.options}
                    optionsError={data.optionsError}
                    samples={data.trace?.samples ?? []}
                    selectedSampleId={selectedSampleId}
                    onSelectSample={setSelectedSampleId}
                    activeEventType={data.trace?.event?.event_type ?? null}
                  />
                  <RuleEngineDimensionSources dimension={selectedDim} inputs={data.inputs} inputsError={data.inputsError} />
                  <RuleEngineExplainers mode={effectiveMode} />
                  {/* 信号过滤：观察态只读说明，配置态才出现编辑控件（mode 门控在本任务落地） */}
                  <SignalFilterSection role={role} mode={effectiveMode} />
                </div>
              ) : (
                <div data-testid="rule-engine-config" data-mode={effectiveMode} className="space-y-6">
                  {/* Rule Configuration Card */}
                  <div className="space-y-5 rounded-2xl border border-slate-200/80 bg-white/80 p-5 shadow-sm backdrop-blur-md dark:border-slate-700/60 dark:bg-slate-800/60">
                    <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-3 pb-3 border-b border-slate-100 dark:border-slate-800">
                      <div>
                        <h2 className="font-bold text-[18px] text-[#101d28] dark:text-white">
                          {selectedDim.name} 规则配置
                        </h2>
                        <span className="text-[11px] font-mono text-slate-400 font-bold">
                          ID: {selectedDim.ruleId}
                        </span>
                      </div>

                      <div className="flex gap-2">
                        <button
                          onClick={resetDraft}
                          className="px-3 py-1.5 border border-[#c2c6d2] text-[#424751] rounded-lg text-[13px] font-medium hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
                        >
                          取消
                        </button>
                        <button
                          onClick={() => void handleSaveConfig()}
                          disabled={!isAdmin || saving}
                          className="px-4 py-1.5 bg-[#004782] text-white rounded-lg text-[13px] font-bold shadow-sm hover:bg-[#185fa5] transition-colors disabled:opacity-60"
                        >
                          {saving ? '保存中…' : '保存配置'}
                        </button>
                      </div>
                    </div>

                    {/* 监控内容改挂左栏维度项悬浮/详情；信源列表为两态共用的只读信息（todo 9：具体监控内容独立卡片已移除） */}
                    <RuleEngineDimensionSources dimension={selectedDim} inputs={data.inputs} inputsError={data.inputsError} />

                    {/* 匹配柱与事件类型配置 */}
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

                    {/* 强制规则（全局层）：只读展示；todo 8 替换为编辑器 */}
                    <RuleEngineForcedRules mode={effectiveMode} />

                    {/* 评分矩阵与阈值：todo 7 替换为可视化编辑器与解算预览 */}
                    <RuleEngineScoringEditor mode={effectiveMode} />

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
                        <a href={routePaths.sources} className="text-[#004782] underline underline-offset-2 hover:text-[#2563EB] dark:text-blue-300">数据源</a>
                        的有效期配置中管理。
                      </p>
                    </div>
                  </div>

                  <SignalFilterSection role={role} mode={effectiveMode} />

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
                              className="w-full bg-[#f7f9ff] border border-[#c2c6d2] rounded-lg p-2 text-[13px] font-medium mt-1 dark:bg-slate-900 dark:border-slate-700 dark:text-white"
                            >
                              {data.options.event_types.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                            </select>
                          </div>

                          <div>
                            <label className="text-[12px] font-bold text-slate-500 dark:text-slate-400">事件细类（可选）</label>
                            <select
                              value={sample.eventSubtype}
                              onChange={(e) => updateSample({eventSubtype: e.target.value})}
                              className="w-full bg-[#f7f9ff] border border-[#c2c6d2] rounded-lg p-2 text-[13px] font-medium mt-1 dark:bg-slate-900 dark:border-slate-700 dark:text-white"
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
                              className="w-full bg-[#f7f9ff] border border-[#c2c6d2] rounded-lg p-2 text-[13px] font-medium mt-1 dark:bg-slate-900 dark:border-slate-700 dark:text-white"
                            >
                              <option value="critical">严重</option><option value="high">高</option><option value="medium">中</option><option value="low">低</option>
                            </select>
                          </div>

                          <div>
                            <label className="text-[12px] font-bold text-slate-500 dark:text-slate-400">供应商名称</label>
                            <input value={sample.organization} onChange={(e) => updateSample({organization: e.target.value})} placeholder="如：某某科技有限公司"
                              className="w-full bg-[#f7f9ff] border border-[#c2c6d2] rounded-lg p-2 text-[13px] mt-1 dark:bg-slate-900 dark:border-slate-700 dark:text-white" />
                          </div>

                          <div>
                            <label className="text-[12px] font-bold text-slate-500 dark:text-slate-400">统一信用代码 / 注册号</label>
                            <input value={sample.registryNo} onChange={(e) => updateSample({registryNo: e.target.value})} placeholder="用于主体精确匹配"
                              className="w-full bg-[#f7f9ff] border border-[#c2c6d2] rounded-lg p-2 text-[13px] mt-1 dark:bg-slate-900 dark:border-slate-700 dark:text-white" />
                          </div>

                          <div>
                            <label className="text-[12px] font-bold text-slate-500 dark:text-slate-400">事件地点</label>
                            <input value={sample.location} onChange={(e) => updateSample({location: e.target.value})} placeholder="如：上海市"
                              className="w-full bg-[#f7f9ff] border border-[#c2c6d2] rounded-lg p-2 text-[13px] mt-1 dark:bg-slate-900 dark:border-slate-700 dark:text-white" />
                          </div>

                          <div>
                            <label className="text-[12px] font-bold text-slate-500 dark:text-slate-400">省州地区</label>
                            <input value={sample.region} onChange={(e) => updateSample({region: e.target.value})} placeholder="如：上海市"
                              className="w-full bg-[#f7f9ff] border border-[#c2c6d2] rounded-lg p-2 text-[13px] mt-1 dark:bg-slate-900 dark:border-slate-700 dark:text-white" />
                          </div>

                          <div>
                            <label className="text-[12px] font-bold text-slate-500 dark:text-slate-400">城市</label>
                            <input value={sample.city} onChange={(e) => updateSample({city: e.target.value})} placeholder="如：上海市"
                              className="w-full bg-[#f7f9ff] border border-[#c2c6d2] rounded-lg p-2 text-[13px] mt-1 dark:bg-slate-900 dark:border-slate-700 dark:text-white" />
                          </div>

                          <div>
                            <label className="text-[12px] font-bold text-slate-500 dark:text-slate-400">国家/地区代码</label>
                            <input value={sample.countryCode} onChange={(e) => updateSample({countryCode: e.target.value})} maxLength={2} placeholder="如：CN"
                              className="w-full bg-[#f7f9ff] border border-[#c2c6d2] rounded-lg p-2 text-[13px] font-mono mt-1 dark:bg-slate-900 dark:border-slate-700 dark:text-white" />
                          </div>

                          <div>
                            <label className="text-[12px] font-bold text-slate-500 dark:text-slate-400">区县</label>
                            <input value={sample.district} onChange={(e) => updateSample({district: e.target.value})} placeholder="如：浦东新区"
                              className="w-full bg-[#f7f9ff] border border-[#c2c6d2] rounded-lg p-2 text-[13px] mt-1 dark:bg-slate-900 dark:border-slate-700 dark:text-white" />
                          </div>

                          <div>
                            <label className="text-[12px] font-bold text-slate-500 dark:text-slate-400">受影响产品</label>
                            <input value={sample.products} onChange={(e) => updateSample({products: e.target.value})} placeholder="多个值用逗号分隔"
                              className="w-full bg-[#f7f9ff] border border-[#c2c6d2] rounded-lg p-2 text-[13px] mt-1 dark:bg-slate-900 dark:border-slate-700 dark:text-white" />
                          </div>

                          <div>
                            <label className="text-[12px] font-bold text-slate-500 dark:text-slate-400">受影响行业</label>
                            <input value={sample.industries} onChange={(e) => updateSample({industries: e.target.value})} placeholder="多个值用逗号分隔"
                              className="w-full bg-[#f7f9ff] border border-[#c2c6d2] rounded-lg p-2 text-[13px] mt-1 dark:bg-slate-900 dark:border-slate-700 dark:text-white" />
                          </div>

                          <div>
                            <label className="text-[12px] font-bold text-slate-500 dark:text-slate-400">来源可信度 (0-100)</label>
                            <input type="number" min="0" max="100" value={sample.credibility} onChange={(e) => updateSample({credibility: Number(e.target.value)})}
                              className="w-full bg-[#f7f9ff] border border-[#c2c6d2] rounded-lg p-2 text-[13px] font-mono font-bold mt-1 dark:bg-slate-900 dark:border-slate-700 dark:text-white"
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
      </div>
    </RuleEngineContext.Provider>
  );
};
