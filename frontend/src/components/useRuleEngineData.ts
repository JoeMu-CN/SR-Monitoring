import {useCallback, useEffect, useState} from 'react';
import {
  api,
  DimensionInputsRead,
  DimensionTraceRead,
  GlobalScoringConfigRead,
  RuleEngineOptions,
} from '../api';

const EMPTY_OPTIONS: RuleEngineOptions = {match_columns: [], event_types: [], event_subtypes: []};

export interface RuleEngineData {
  /** 维度输入健康度（近 30 天），失败不阻塞页面骨架 */
  inputs: DimensionInputsRead | null;
  inputsError: string;
  /** 维度运行轨迹；切换维度/样例时先清空旧值，避免陈旧数据被当成当前结果 */
  trace: DimensionTraceRead | null;
  traceError: string;
  /** 匹配柱与事件类型选项 */
  options: RuleEngineOptions;
  optionsError: string;
  /** 全局层配置（GET /global-config） */
  globalConfig: GlobalScoringConfigRead | null;
  globalConfigError: string;
  /** 维度数据（输入健康度 + 轨迹）强制重取 */
  refresh: () => void;
  /** 全局配置重取（保存/恢复默认后使用） */
  refreshGlobalConfig: () => Promise<void>;
}

/**
 * 规则引擎页数据获取 hook（壳组件独占调用）。
 *
 * 维度输入健康度、运行轨迹、事件选项、全局配置四路数据相互独立：
 * 任一路失败只置对应错误文案，不抛异常、不阻塞其余区块渲染。
 */
export function useRuleEngineData(dimensionKey: string | undefined, sampleId: number | null): RuleEngineData {
  const [inputs, setInputs] = useState<DimensionInputsRead | null>(null);
  const [inputsError, setInputsError] = useState('');
  const [trace, setTrace] = useState<DimensionTraceRead | null>(null);
  const [traceError, setTraceError] = useState('');
  const [options, setOptions] = useState<RuleEngineOptions>(EMPTY_OPTIONS);
  const [optionsError, setOptionsError] = useState('');
  const [globalConfig, setGlobalConfig] = useState<GlobalScoringConfigRead | null>(null);
  const [globalConfigError, setGlobalConfigError] = useState('');
  const [reloadToken, setReloadToken] = useState(0);

  const refresh = useCallback(() => setReloadToken((current) => current + 1), []);

  useEffect(() => {
    let cancelled = false;
    setInputs(null);
    setInputsError('');
    if (!dimensionKey) return () => { cancelled = true; };
    api.dimensionInputs(dimensionKey)
      .then((data) => { if (!cancelled) setInputs(data); })
      .catch((error: unknown) => {
        if (!cancelled) setInputsError(error instanceof Error ? error.message : '输入健康度加载失败');
      });
    return () => { cancelled = true; };
  }, [dimensionKey, reloadToken]);

  useEffect(() => {
    let cancelled = false;
    setTrace(null);
    setTraceError('');
    if (!dimensionKey) return () => { cancelled = true; };
    api.dimensionTrace(dimensionKey, sampleId ?? undefined)
      .then((data) => { if (!cancelled) setTrace(data); })
      .catch((error: unknown) => {
        if (!cancelled) setTraceError(error instanceof Error ? error.message : '运行轨迹加载失败');
      });
    return () => { cancelled = true; };
  }, [dimensionKey, sampleId, reloadToken]);

  useEffect(() => {
    let cancelled = false;
    void api.ruleEngineOptions()
      .then((data) => {
        if (!cancelled) {
          setOptions(data);
          setOptionsError('');
        }
      })
      .catch((error: unknown) => {
        if (!cancelled) setOptionsError(error instanceof Error ? error.message : '事件选项加载失败');
      });
    return () => { cancelled = true; };
  }, []);

  const refreshGlobalConfig = useCallback(async () => {
    try {
      const data = await api.globalConfig.get();
      setGlobalConfig(data);
      setGlobalConfigError('');
    } catch (error) {
      setGlobalConfigError(error instanceof Error ? error.message : '全局配置加载失败');
    }
  }, []);

  useEffect(() => {
    void refreshGlobalConfig();
  }, [refreshGlobalConfig]);

  return {
    inputs,
    inputsError,
    trace,
    traceError,
    options,
    optionsError,
    globalConfig,
    globalConfigError,
    refresh,
    refreshGlobalConfig,
  };
}
