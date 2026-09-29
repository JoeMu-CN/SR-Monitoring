import React, {useEffect, useState} from 'react';
import {api, SignalFilterConfig} from '../api';
import type {RuleEngineMode} from './RuleEngineContext';
import {SIGNAL_FILTER_EXPLAINER_COPY} from './ruleEngineExplainerCopy';

interface SignalFilterSectionProps {
  role: 'viewer' | 'admin';
  /** 观察态只读展示说明与当前值；配置态（且 admin）才渲染编辑控件 */
  mode: RuleEngineMode;
}

/** 候选池与 backend/app/signals/relevance.py 的 `_HIGH_IMPACT_KEYWORDS` 默认值逐项一致；仅作「可加入」提示，已选一律以 API 数组为准。 */
const KEYWORD_CANDIDATES: readonly string[] = [
  '制裁', 'sanction', '出口管制', 'export control', '实体清单', 'entity list', '不可靠实体', '出口管制实体',
  'carbon border', 'cbam', 'due diligence', '尽职调查', 'forced labour', 'forced labor', '强迫劳动', 'uflpa', '涉疆',
  '供应中断', '供应链中断', 'supply disruption', 'supply chain disruption', 'lpr', '贷款市场报价', '利率调整', '采购经理指数',
  '采购经理', 'pmi', '货币政策', '环评', '环境影响评价', '督察', '排污', '黑名单', '突发环境事件', '专项整治',
  '涉刑', '停产', '停工', '重大事故', '重大灾害', '台风', '地震', '洪水', '海啸', '火山', '司法', '失信被执行', '刑事立案',
];

/** 主要制造经济体（ISO 3166-1 alpha-2）；GB 为英国官方码，有意保留。 */
const COUNTRY_CANDIDATES: readonly string[] = ['CN', 'US', 'DE', 'JP', 'KR', 'IN', 'MX', 'VN', 'IT', 'FR', 'GB', 'BR', 'ID', 'TR', 'TH', 'TW'];

/** 已选/生效值芯片：信息蓝高亮，与只读清单类信源芯片同一色系，深色模式同步。 */
const SELECTED_CHIP_CLASS = 'inline-flex items-center gap-1 rounded-md border border-blue-200 bg-blue-50 px-2 py-0.5 text-[11px] font-bold text-blue-700 dark:border-blue-900 dark:bg-blue-950/40 dark:text-blue-300';

/** 未选候选：虚线描边弱化，作为按钮提供一键加入与清晰焦点环。 */
const CANDIDATE_CHIP_CLASS = 'inline-flex items-center rounded-md border border-dashed border-slate-300 bg-white px-2 py-0.5 text-[11px] text-slate-600 transition-colors hover:border-blue-300 hover:bg-blue-50 hover:text-blue-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#004782] focus-visible:ring-offset-1 dark:border-slate-600 dark:bg-slate-900 dark:text-slate-300 dark:hover:border-blue-800 dark:hover:bg-blue-950/40 dark:hover:text-blue-300 dark:focus-visible:ring-blue-400';

const GROUP_LABEL_CLASS = 'text-[10px] font-bold text-slate-500 dark:text-slate-400';
const EMPTY_HINT_CLASS = 'text-[11px] text-slate-400 dark:text-slate-500';
const INPUT_CLASS = 'flex-1 bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-lg px-2.5 py-1.5 text-[12px] focus:outline-none focus:ring-1 focus:ring-blue-500';
const ADD_BUTTON_CLASS = 'px-3 py-1.5 border border-[#004782] text-[#004782] dark:text-blue-400 rounded-lg text-[12px] font-bold hover:bg-blue-50 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#004782] focus-visible:ring-offset-1 dark:focus-visible:ring-blue-400';
const REMOVE_BUTTON_CLASS = 'text-[12px] leading-none text-blue-500 hover:text-red-600 dark:text-blue-300 dark:hover:text-red-400';

/** 国家码大小写不敏感；关键词沿用后端 casefold 前的原样比较语义。 */
const sameValue = (a: string, b: string, caseInsensitive: boolean) =>
  (caseInsensitive ? a.toUpperCase() === b.toUpperCase() : a === b);

/** 追加并去重；国家值统一大写，关键词保留输入原样。 */
const mergeValues = (current: string[], incoming: readonly string[], caseInsensitive: boolean): string[] => {
  const next = [...current];
  for (const raw of incoming) {
    const value = caseInsensitive ? raw.trim().toUpperCase() : raw.trim();
    if (value && !next.some((item) => sameValue(item, value, caseInsensitive))) next.push(value);
  }
  return next;
};

/** CJK 视觉 QA：这些语义短语/整句在 390px 窄屏下保持整块不换行，避免拆词与「秒）。」孤行。 */
const NO_BREAK_PHRASES: readonly string[] = [
  '命中高影响关键词或重点关注国家会自动放行；', '国外事件', '当地无任何供应商', '清单类信源', '修改后立即生效（≤60 秒）。',
];

/** 语义短语按文案顺序整块不换行（含边界标点）；其余文本原样保留，不复制整句。 */
const renderExplainerCopy = (copy: string): React.ReactNode[] =>
  copy.split(new RegExp(`(${NO_BREAK_PHRASES.join('|')})`, 'g')).map((part, index) =>
    NO_BREAK_PHRASES.includes(part)
      ? <span key={`${index}-${part}`} className="whitespace-nowrap">{part}</span>
      : part,
  );

interface FilterTagFieldProps {
  /** id 同时用作分组锚点与 testid 后缀；selected 为 API 已选/生效值，候选常量不参与选择判断 */
  id: string; title: string; selected: string[]; candidates: readonly string[];
  canEdit: boolean; caseInsensitive: boolean; input: string; setInput: (value: string) => void;
  add: (values: readonly string[]) => void; remove: (value: string) => void; placeholder: string;
}

/** 单个过滤字段：观察态仅展示已选芯片；配置态分隔「已加入」与「可加入」并保留自由输入。 */
const FilterTagField: React.FC<FilterTagFieldProps> = ({
  id, title, selected, candidates, canEdit, caseInsensitive, input, setInput, add, remove, placeholder,
}) => {
  const headingId = `signal-filter-${id}-title`;
  const available = candidates.filter((c) => !selected.some((v) => sameValue(v, c, caseInsensitive)));
  const commitInput = () => {
    const parts = input.split(/[,，\n]/).map((part) => part.trim()).filter(Boolean);
    if (parts.length === 0) return;
    add(parts);
    setInput('');
  };

  return (
    <div role="group" aria-labelledby={headingId} className="space-y-2" data-testid={`signal-filter-field-${id}`}>
      <h4 id={headingId} className="text-[12px] font-bold text-[#424751] dark:text-slate-300">{title}</h4>
      {/* 观察态只读：不渲染输入框与增删按钮，仅展示当前值 */}
      {canEdit && (
        <div className="flex gap-1.5">
          <input type="text" value={input} placeholder={placeholder} aria-label={`输入${title}`} onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ',') { e.preventDefault(); commitInput(); } }}
            className={INPUT_CLASS} />
          <button type="button" onClick={commitInput} aria-label={`添加${title}`} className={ADD_BUTTON_CLASS}>添加</button>
        </div>
      )}
      <div className="space-y-1.5">
        <div data-testid={`signal-filter-${id}-selected`} className="flex flex-wrap items-center gap-1.5">
          {canEdit && <span className={GROUP_LABEL_CLASS}>已加入</span>}
          {selected.length === 0
            ? <span className={EMPTY_HINT_CLASS}>{canEdit ? '暂无已加入值' : '暂无'}</span>
            : selected.map((tag) => (
              <span key={tag} className={SELECTED_CHIP_CLASS}>
                {tag}
                {canEdit && <button type="button" onClick={() => remove(tag)} aria-label={`删除 ${tag}`} className={REMOVE_BUTTON_CLASS}>×</button>}
              </span>
            ))}
        </div>
        {canEdit && (
          <div data-testid={`signal-filter-${id}-candidates`} className="flex flex-wrap items-center gap-1.5">
            <span className={GROUP_LABEL_CLASS}>可加入</span>
            {available.length === 0
              ? <span className={EMPTY_HINT_CLASS}>候选已全部加入</span>
              : available.map((candidate) => (
                <button key={candidate} type="button" onClick={() => add([candidate])} aria-label={`加入 ${candidate}`} className={CANDIDATE_CHIP_CLASS}>{candidate}</button>
              ))}
          </div>
        )}
      </div>
    </div>
  );
};

/**
 * 信号过滤规则（LLM 前确定性预筛）配置区块：高影响关键词 / 重点关注国家可编辑
 * （PUT /api/v1/signals/filter-config），清单类信源只读；分态门控，读取失败只告警不伪装。
 */
export const SignalFilterSection: React.FC<SignalFilterSectionProps> = ({role, mode}) => {
  const canEdit = role === 'admin' && mode === 'config';
  const [config, setConfig] = useState<SignalFilterConfig | null>(null);
  const [keywords, setKeywords] = useState<string[]>([]);
  const [countries, setCountries] = useState<string[]>([]);
  const [keywordInput, setKeywordInput] = useState('');
  const [countryInput, setCountryInput] = useState('');
  const [loading, setLoading] = useState(true);
  /** 过滤配置读取失败：未知状态不得伪装成默认/空配置 */
  const [loadFailed, setLoadFailed] = useState(false);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<{type: 'ok' | 'err'; text: string} | null>(null);

  useEffect(() => {
    api.filterConfig.get()
      .then((cfg) => {
        setConfig(cfg);
        setKeywords(cfg.high_impact);
        setCountries(cfg.priority_countries);
        setLoadFailed(false);
        setLoading(false);
      })
      .catch(() => {
        setLoadFailed(true);
        setLoading(false);
      });
  }, []);

  const addKeywords = (values: readonly string[]) => setKeywords((prev) => mergeValues(prev, values, false));
  const addCountries = (values: readonly string[]) => setCountries((prev) => mergeValues(prev, values, true));
  const removeKeyword = (tag: string) => setKeywords((prev) => prev.filter((item) => item !== tag));
  const removeCountry = (tag: string) => setCountries((prev) => prev.filter((item) => item !== tag));

  const handleSave = async () => {
    setSaving(true);
    setMsg(null);
    try {
      const updated = await api.filterConfig.update({
        high_impact: keywords,
        priority_countries: countries,
      });
      setConfig(updated);
      setKeywords(updated.high_impact);
      setCountries(updated.priority_countries);
      setMsg({type: 'ok', text: '已保存，过滤规则立即生效。'});
    } catch (error) {
      setMsg({type: 'err', text: error instanceof Error ? error.message : '保存失败'});
    }
    setSaving(false);
  };

  const handleReset = async () => {
    if (!window.confirm('确认重置为默认过滤规则？自定义配置将被清除。')) return;
    setSaving(true);
    setMsg(null);
    try {
      await api.filterConfig.reset();
      const fresh = await api.filterConfig.get();
      setConfig(fresh);
      setKeywords(fresh.high_impact);
      setCountries(fresh.priority_countries);
      setMsg({type: 'ok', text: '已重置为默认过滤规则。'});
    } catch (error) {
      setMsg({type: 'err', text: error instanceof Error ? error.message : '重置失败'});
    }
    setSaving(false);
  };

  const sourceBadgeClass = config?.source === 'configured' ? 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300' : 'bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400';

  if (loading) {
    return (
      <section className="rounded-2xl border border-slate-200/80 bg-white/80 p-5 shadow-sm dark:border-slate-700/60 dark:bg-slate-800/60">
        <div className="flex items-center gap-2 text-[12px] text-slate-500"><span className="sr-only">信号过滤规则加载中…</span></div>
      </section>
    );
  }

  return (
    <section className="space-y-4 rounded-2xl border border-slate-200/80 bg-white/80 p-5 text-[#101d28] shadow-sm backdrop-blur-md dark:border-slate-700/60 dark:bg-slate-800/60">
      <div className="flex items-start gap-3">
        <span className="material-symbols-outlined text-[22px] text-[#004782] mt-0.5">filter_alt</span>
        <div className="flex-1">
          <h3 className="text-[14px] font-bold text-[#101d28] dark:text-white">
            信号过滤规则
            {config && (
              <span className={`ml-2 text-[10px] font-bold rounded-full px-2 py-0.5 align-middle ${sourceBadgeClass}`}>
                {config.source === 'configured' ? '已自定义' : '默认规则'}
              </span>
            )}
            {mode === 'observation' && (
              <span className="ml-2 text-[10px] font-bold rounded-full px-2 py-0.5 align-middle bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400">
                只读查看
              </span>
            )}
          </h3>
          <p className="text-[11px] leading-relaxed text-pretty text-slate-500 dark:text-slate-400 mt-1">
            {renderExplainerCopy(SIGNAL_FILTER_EXPLAINER_COPY)}
          </p>
        </div>
      </div>

      {loadFailed && (
        <div role="alert" className="text-[12px] rounded-lg px-3 py-2 text-red-700 bg-red-50 border border-red-200 dark:text-red-300 dark:bg-red-950/30 dark:border-red-900">过滤配置加载失败，暂时无法确认生效的关键词与重点国家；请刷新页面后重试。</div>
      )}

      {!loadFailed && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <FilterTagField id="keywords" title="高影响关键词" selected={keywords} candidates={KEYWORD_CANDIDATES}
            canEdit={canEdit} caseInsensitive={false} input={keywordInput} setInput={setKeywordInput}
            add={addKeywords} remove={removeKeyword} placeholder="关键词，如：cbam" />
          <FilterTagField id="countries" title="重点关注国家（ISO 两字母码）" selected={countries} candidates={COUNTRY_CANDIDATES}
            canEdit={canEdit} caseInsensitive input={countryInput} setInput={setCountryInput}
            add={addCountries} remove={removeCountry} placeholder="国家码，如：JP、KR" />
        </div>
      )}

      {!loadFailed && (
        <div>
          <h4 className="text-[12px] font-bold text-[#424751] dark:text-slate-300 mb-1.5">清单类信源（只读，供应商名预筛）</h4>
          <div className="flex flex-wrap gap-1.5">
            {(config?.list_sources ?? []).map((code) => (
              <span key={code} className="px-2 py-0.5 rounded-md bg-blue-50 dark:bg-blue-950/40 border border-blue-200 dark:border-blue-900 text-[11px] text-blue-700 dark:text-blue-300 font-mono">{code}</span>
            ))}
          </div>
        </div>
      )}

      {msg && (
        <div role="alert" className={`text-[12px] rounded-lg px-3 py-2 ${msg.type === 'ok' ? 'text-emerald-700 bg-emerald-50 border border-emerald-200 dark:text-emerald-300 dark:bg-emerald-950/30 dark:border-emerald-900' : 'text-red-700 bg-red-50 border border-red-200 dark:text-red-300 dark:bg-red-950/30 dark:border-red-900'}`}>
          {msg.text}
        </div>
      )}

      {canEdit && !loadFailed && (
        <div className="flex gap-2 justify-end pt-1">
          <button type="button" onClick={() => void handleReset()} disabled={saving}
            className="px-3 py-1.5 border border-slate-300 dark:border-slate-600 text-slate-600 dark:text-slate-300 rounded-lg text-[12px] font-bold hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors disabled:opacity-50">
            重置默认
          </button>
          <button type="button" onClick={() => void handleSave()} disabled={saving}
            className="px-4 py-1.5 bg-[#004782] text-white rounded-lg text-[12px] font-bold shadow-sm hover:bg-[#185fa5] transition-colors disabled:opacity-50">
            {saving ? '保存中…' : '保存规则'}
          </button>
        </div>
      )}
    </section>
  );
};
