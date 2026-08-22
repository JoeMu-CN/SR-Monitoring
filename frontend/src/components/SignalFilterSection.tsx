import React, {useEffect, useState} from 'react';
import {api, SignalFilterConfig} from '../api';

interface SignalFilterSectionProps {
  role: 'viewer' | 'admin';
}

/**
 * 信号过滤规则（LLM 前确定性预筛）配置区块 —— 运营可自主维护。
 * 高影响关键词 / 重点关注国家可编辑（PUT /api/v1/signals/filter-config），
 * 清单类信源只读展示；修改立即生效（≤60 秒，TTL 缓存热更新）。
 */
export const SignalFilterSection: React.FC<SignalFilterSectionProps> = ({role}) => {
  const [config, setConfig] = useState<SignalFilterConfig | null>(null);
  const [keywords, setKeywords] = useState<string[]>([]);
  const [countries, setCountries] = useState<string[]>([]);
  const [keywordInput, setKeywordInput] = useState('');
  const [countryInput, setCountryInput] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<{type: 'ok' | 'err'; text: string} | null>(null);

  useEffect(() => {
    api.filterConfig.get()
      .then((cfg) => {
        setConfig(cfg);
        setKeywords(cfg.high_impact);
        setCountries(cfg.priority_countries);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, []);

  const addTag = (
    list: string[], setList: (v: string[]) => void,
    input: string, setInput: (v: string) => void,
  ) => {
    const parts = input.split(/[,，\n]/).map((s) => s.trim()).filter(Boolean);
    if (parts.length === 0) return;
    setList(Array.from(new Set([...list, ...parts])));
    setInput('');
  };

  const removeTag = (list: string[], setList: (v: string[]) => void, tag: string) => {
    setList(list.filter((t) => t !== tag));
  };

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

  const TagInput = ({
    list, setList, input, setInput, placeholder,
  }: {
    list: string[]; setList: (v: string[]) => void;
    input: string; setInput: (v: string) => void; placeholder: string;
  }) => (
    <div className="space-y-1.5">
      <div className="flex gap-1.5">
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ',') {
              e.preventDefault();
              addTag(list, setList, input, setInput);
            }
          }}
          placeholder={placeholder}
          disabled={role !== 'admin'}
          className="flex-1 bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-lg px-2.5 py-1.5 text-[12px] focus:outline-none focus:ring-1 focus:ring-blue-500"
        />
        {role === 'admin' && (
          <button
            type="button"
            onClick={() => addTag(list, setList, input, setInput)}
            className="px-3 py-1.5 border border-[#004782] text-[#004782] dark:text-blue-400 rounded-lg text-[12px] font-bold hover:bg-blue-50 transition-colors"
          >
            添加
          </button>
        )}
      </div>
      <div className="flex flex-wrap gap-1.5">
        {list.map((tag) => (
          <span key={tag} className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 text-[11px] text-slate-700 dark:text-slate-300">
            {tag}
            {role === 'admin' && (
              <button
                type="button"
                onClick={() => removeTag(list, setList, tag)}
                className="text-slate-400 hover:text-red-600 transition-colors text-[12px] leading-none"
                aria-label={`删除 ${tag}`}
              >
                ×
              </button>
            )}
          </span>
        ))}
      </div>
    </div>
  );

  if (loading) {
    return (
      <section className="rounded-2xl border border-slate-200/80 bg-white/80 p-5 shadow-sm dark:border-slate-700/60 dark:bg-slate-800/60">
        <div className="text-[12px] text-slate-500">信号过滤规则加载中…</div>
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
            <span className={`ml-2 text-[10px] font-bold rounded-full px-2 py-0.5 align-middle ${
              config?.source === 'configured' ? 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300' : 'bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400'
            }`}>
              {config?.source === 'configured' ? '已自定义' : '默认规则'}
            </span>
          </h3>
          <p className="text-[11px] text-slate-500 dark:text-slate-400 mt-1">
            LLM 分析前的确定性预筛：命中高影响关键词或重点关注国家（海外供应链）的信号强制放行；
            未命中的国外事件被过滤；清单类信源仅当命中供应商名时进入分析。修改后立即生效（≤60 秒）。
          </p>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div>
          <h4 className="text-[12px] font-bold text-[#424751] dark:text-slate-300 mb-2">高影响关键词</h4>
          <TagInput list={keywords} setList={setKeywords} input={keywordInput} setInput={setKeywordInput} placeholder="输入关键词，回车/逗号添加，如：cbam" />
        </div>
        <div>
          <h4 className="text-[12px] font-bold text-[#424751] dark:text-slate-300 mb-2">重点关注国家（ISO 两字母码）</h4>
          <TagInput list={countries} setList={setCountries} input={countryInput} setInput={setCountryInput} placeholder="如：JP、KR" />
        </div>
      </div>

      <div>
        <h4 className="text-[12px] font-bold text-[#424751] dark:text-slate-300 mb-1.5">清单类信源（只读，供应商名预筛）</h4>
        <div className="flex flex-wrap gap-1.5">
          {(config?.list_sources ?? []).map((code) => (
            <span key={code} className="px-2 py-0.5 rounded-md bg-blue-50 dark:bg-blue-950/40 border border-blue-200 dark:border-blue-900 text-[11px] text-blue-700 dark:text-blue-300 font-mono">{code}</span>
          ))}
        </div>
      </div>

      {msg && (
        <div role="alert" className={`text-[12px] rounded-lg px-3 py-2 ${msg.type === 'ok' ? 'text-emerald-700 bg-emerald-50 border border-emerald-200 dark:text-emerald-300 dark:bg-emerald-950/30 dark:border-emerald-900' : 'text-red-700 bg-red-50 border border-red-200 dark:text-red-300 dark:bg-red-950/30 dark:border-red-900'}`}>
          {msg.text}
        </div>
      )}

      {role === 'admin' && (
        <div className="flex gap-2 justify-end pt-1">
          <button
            type="button"
            onClick={() => void handleReset()}
            disabled={saving}
            className="px-3 py-1.5 border border-slate-300 dark:border-slate-600 text-slate-600 dark:text-slate-300 rounded-lg text-[12px] font-bold hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors disabled:opacity-50"
          >
            重置默认
          </button>
          <button
            type="button"
            onClick={() => void handleSave()}
            disabled={saving}
            className="px-4 py-1.5 bg-[#004782] text-white rounded-lg text-[12px] font-bold shadow-sm hover:bg-[#185fa5] transition-colors disabled:opacity-50"
          >
            {saving ? '保存中…' : '保存规则'}
          </button>
        </div>
      )}
    </section>
  );
};
