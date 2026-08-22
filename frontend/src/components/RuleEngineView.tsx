import React, { useEffect, useState } from 'react';
import {AnimatePresence, motion, useReducedMotion} from 'motion/react';
import {api, RuleEngineOptions, SandboxResult} from '../api';
import { MonitoringDimension } from '../types';
import {SignalFilterSection} from './SignalFilterSection';

interface RuleEngineViewProps {
  dimensions: MonitoringDimension[];
  onToggleDimension: (id: string) => Promise<void>;
  onUpdateDimension: (updatedDim: MonitoringDimension) => Promise<void>;
  role: 'viewer' | 'admin';
}

export const RuleEngineView: React.FC<RuleEngineViewProps> = ({
  dimensions,
  onToggleDimension,
  onUpdateDimension,
  role,
}) => {
  const [activeDimId, setActiveDimId] = useState<string>(dimensions[0]?.id || 'dim-01');

  const selectedDim = dimensions.find((d) => d.id === activeDimId) || dimensions[0];

  // Editable configuration state
  const [severityWeight, setSeverityWeight] = useState(selectedDim?.severityWeight ?? 0.5);
  const [relevanceWeight, setRelevanceWeight] = useState(selectedDim?.relevanceWeight ?? 0.5);
  const [p1Threshold, setP1Threshold] = useState(selectedDim?.thresholds.p1 ?? 85);
  const [p2Threshold, setP2Threshold] = useState(selectedDim?.thresholds.p2 ?? 65);
  const [p3Threshold, setP3Threshold] = useState(selectedDim?.thresholds.p3 ?? 40);
  const [ttlHours, setTtlHours] = useState(selectedDim?.ttlHours ?? 336);

  // Sandbox testing state
  const reduceMotion = useReducedMotion();
  const [sandboxOptions, setSandboxOptions] = useState<RuleEngineOptions>({match_columns: [], event_types: [], event_subtypes: []});
  const [sandboxEventType, setSandboxEventType] = useState('weather');
  const [sandboxEventSubtype, setSandboxEventSubtype] = useState('');
  const [sandboxSeverity, setSandboxSeverity] = useState<'critical' | 'high' | 'medium' | 'low'>('high');
  const [sandboxOrganization, setSandboxOrganization] = useState('');
  const [sandboxRegistryNo, setSandboxRegistryNo] = useState('');
  const [sandboxLocation, setSandboxLocation] = useState('');
  const [sandboxRegion, setSandboxRegion] = useState('');
  const [sandboxCity, setSandboxCity] = useState('');
  const [sandboxCountryCode, setSandboxCountryCode] = useState('');
  const [sandboxDistrict, setSandboxDistrict] = useState('');
  const [sandboxProducts, setSandboxProducts] = useState('');
  const [sandboxIndustries, setSandboxIndustries] = useState('');
  const [sandboxCredibility, setSandboxCredibility] = useState(80);
  const [sandboxResult, setSandboxResult] = useState<SandboxResult | null>(null);
  const [sandboxError, setSandboxError] = useState('');
  const [sandboxLoading, setSandboxLoading] = useState(false);
  const [sandboxOpen, setSandboxOpen] = useState(false);

  useEffect(() => {
    void api.ruleEngineOptions()
      .then(setSandboxOptions)
      .catch((error: unknown) => setSandboxError(error instanceof Error ? error.message : '事件选项加载失败'));
  }, []);

  // Update editor values when active dimension changes
  React.useEffect(() => {
    if (selectedDim) {
      setSeverityWeight(selectedDim.severityWeight);
      setRelevanceWeight(selectedDim.relevanceWeight);
      setP1Threshold(selectedDim.thresholds.p1);
      setP2Threshold(selectedDim.thresholds.p2);
      setP3Threshold(selectedDim.thresholds.p3);
      setTtlHours(selectedDim.ttlHours);
      if (selectedDim.source?.event_types[0]) setSandboxEventType(selectedDim.source.event_types[0]);
      setSandboxResult(null);
      setSandboxError('');
    }
  }, [activeDimId, selectedDim]);

  const handleSaveConfig = async () => {
    if (!selectedDim) return;
    const updated: MonitoringDimension = {
      ...selectedDim,
      severityWeight,
      relevanceWeight,
      thresholds: {
        p1: Number(p1Threshold),
        p2: Number(p2Threshold),
        p3: Number(p3Threshold),
      },
      ttlHours: Number(ttlHours),
    };
    await onUpdateDimension(updated);
    alert(`规则 [${selectedDim.name}] 配置已成功保存！`);
  };

  const splitValues = (value: string) => value.split(/[，,、]/).map((item) => item.trim()).filter(Boolean);

  const handleRunSandboxTest = async () => {
    setSandboxLoading(true);
    setSandboxError('');
    setSandboxResult(null);
    try {
      const countryCode = sandboxCountryCode.trim().toUpperCase();
      if (countryCode && !/^[A-Z]{2}$/.test(countryCode)) throw new Error('国家/地区代码需填写两位大写字母，例如 CN');
      const result = await api.testRuleEngine({
        event_type: sandboxEventType,
        event_subtype: sandboxEventSubtype || null,
        severity: sandboxSeverity,
        organizations: sandboxOrganization.trim() ? [{name: sandboxOrganization.trim(), aliases: [], registry_no: sandboxRegistryNo.trim() || null}] : [],
        locations: sandboxLocation.trim() ? [{name: sandboxLocation.trim(), country_code: countryCode || null, region: sandboxRegion.trim() || null, city: sandboxCity.trim() || null, district: sandboxDistrict.trim() || null}] : [],
        affected_products: splitValues(sandboxProducts),
        affected_industries: splitValues(sandboxIndustries),
        summary: `规则沙箱：${sandboxOptions.event_types.find((item) => item.value === sandboxEventType)?.label ?? sandboxEventType}`,
        credibility: sandboxCredibility,
        has_published_at: true,
      });
      setSandboxResult(result);
    } catch (error) {
      setSandboxError(error instanceof Error ? error.message : '沙箱评估失败');
    } finally {
      setSandboxLoading(false);
    }
  };

  if (!selectedDim) {
    return <div className="bg-white dark:bg-slate-900 border border-[#c2c6d2] rounded-xl p-8 text-center text-slate-500">暂无可用监控维度</div>;
  }

  return (
    <div className="space-y-5 pb-20 lg:pb-8">
      {/* Title */}
      <div className="flex flex-col sm:flex-row justify-between gap-3">
        <div>
        <h1 className="text-xl font-black text-slate-900 dark:text-white tracking-tight lg:text-2xl">
          规则引擎与权重配置
        </h1>
        <p className="text-xs text-[#424751] dark:text-slate-400 mt-0.5">
          自定义监控维度、计算权重及沙箱仿真评估。
        </p>
        </div>
        <div className="flex items-center gap-2 text-xs"><span className="text-slate-500">当前权限：{role === 'admin' ? '规则管理' : '只读'}</span></div>
      </div>

      {/* Main Grid Layout (Left: Dimensions List, Right: Rule Config + Sandbox) */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        {/* Left Column: Monitoring Dimensions List (4 cols) */}
        <div className="lg:col-span-4 space-y-3 h-fit">
          <div className="space-y-3 rounded-2xl border border-slate-200/80 bg-white/80 p-4 shadow-sm backdrop-blur-md dark:border-slate-700/60 dark:bg-slate-800/60">
          <div className="flex justify-between items-center pb-2 border-b border-slate-100 dark:border-slate-800">
            <h2 className="font-bold text-[15px] text-[#101d28] dark:text-white">监控维度</h2>
            <button
              disabled
              className="p-1 rounded-lg text-slate-300 cursor-not-allowed"
              title="当前版本不新增自定义维度"
            >
              <span className="material-symbols-outlined text-[20px]">add</span>
            </button>
          </div>

          <div className="space-y-2">
            {dimensions.map((dim) => {
              const isSelected = dim.id === activeDimId;
              return (
                <div
                  key={dim.id}
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
                      <div className="text-[11px] text-slate-500 mt-0.5 truncate">
                        {dim.contentItems.slice(0, 3).join(' · ') || '待配置监控内容'}
                      </div>
                    </div>
                  </div>

                  {/* Toggle switch */}
                  <label
                    onClick={(e) => e.stopPropagation()}
                    className="relative inline-flex items-center cursor-pointer"
                  >
                    <input
                      type="checkbox"
                      checked={dim.enabled}
                      onChange={() => { if (role === 'admin') void onToggleDimension(dim.id); }}
                      disabled={role !== 'admin'}
                      className="sr-only peer"
                    />
                    <div className="w-9 h-5 bg-slate-200 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-slate-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-[#004782]"></div>
                  </label>
                </div>
              );
            })}
          </div>
          </div>

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
        </div>

        {/* Right Column: Rule Editor & Sandbox Simulator (8 cols) */}
        <AnimatePresence mode="wait" initial={false}>
        <motion.div
          key={selectedDim.id}
          initial={reduceMotion ? false : {opacity: 0, y: 6}}
          animate={{opacity: 1, y: 0}}
          exit={reduceMotion ? {opacity: 1} : {opacity: 0, y: -4}}
          transition={{duration: reduceMotion ? 0 : 0.18, ease: 'easeOut'}}
          className="lg:col-span-8 space-y-6"
        >
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
                  onClick={() => {
                    setSeverityWeight(selectedDim.severityWeight);
                    setRelevanceWeight(selectedDim.relevanceWeight);
                  }}
                  className="px-3 py-1.5 border border-[#c2c6d2] text-[#424751] rounded-lg text-[13px] font-medium hover:bg-slate-50"
                >
                  取消
                </button>
                <button
                  onClick={() => void handleSaveConfig()}
                  disabled={role !== 'admin'}
                  className="px-4 py-1.5 bg-[#004782] text-white rounded-lg text-[13px] font-bold shadow-sm hover:bg-[#185fa5] transition-colors"
                >
                  保存配置
                </button>
              </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <section className="rounded-xl bg-[#f7f9ff] dark:bg-slate-950/40 border border-slate-200 dark:border-slate-800 p-3">
                <h3 className="text-[12px] font-bold text-[#424751] dark:text-slate-300 mb-2">具体监控内容</h3>
                <div className="flex flex-wrap gap-1.5">
                  {selectedDim.contentItems.map((item) => (
                    <span key={item} className="px-2 py-1 rounded-md bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 text-[11px] text-slate-700 dark:text-slate-300">{item}</span>
                  ))}
                </div>
              </section>
              <section className="rounded-xl bg-[#f7f9ff] dark:bg-slate-950/40 border border-slate-200 dark:border-slate-800 p-3">
                <h3 className="text-[12px] font-bold text-[#424751] dark:text-slate-300 mb-2">引用数据源</h3>
                <div className="space-y-1.5">
                  {selectedDim.dataSources.map((source) => (
                    <div key={source.code} className="flex items-center justify-between gap-2 text-[11px]">
                      <span className="text-slate-700 dark:text-slate-300">{source.name}</span>
                      <span className={`shrink-0 rounded-full px-2 py-0.5 font-bold ${
                        source.status === 'connected' ? 'bg-emerald-100 text-emerald-700' :
                        source.status === 'external_tool' ? 'bg-blue-100 text-blue-700' : 'bg-slate-200 text-[#424751]'
                      }`}>
                        {source.status === 'connected' ? '已接入' : source.status === 'external_tool' ? '外部核查工具' : '规划中'}
                      </span>
                    </div>
                  ))}
                </div>
              </section>
            </div>

            {/* Sliders */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              <div className="space-y-2">
                <div className="flex justify-between text-[13px]">
                  <span className="font-bold text-[#424751]">严重性权重 (SEVERITY)</span>
                  <span className="font-mono font-bold text-[#004782]">{severityWeight}</span>
                </div>
                <input
                  type="range"
                  min="0.1"
                  max="1.0"
                  step="0.05"
                  value={severityWeight}
                  onChange={(e) => setSeverityWeight(parseFloat(e.target.value))}
                  className="w-full accent-[#004782] cursor-pointer"
                />
              </div>

              <div className="space-y-2">
                <div className="flex justify-between text-[13px]">
                  <span className="font-bold text-[#424751]">业务关联度 (RELEVANCE)</span>
                  <span className="font-mono font-bold text-[#004782]">{relevanceWeight}</span>
                </div>
                <input
                  type="range"
                  min="0.1"
                  max="1.0"
                  step="0.05"
                  value={relevanceWeight}
                  onChange={(e) => setRelevanceWeight(parseFloat(e.target.value))}
                  className="w-full accent-[#004782] cursor-pointer"
                />
              </div>
            </div>

            {/* Risk Level Trigger Thresholds */}
            <div className="space-y-2">
              <label className="text-[13px] font-bold text-[#424751]">
                风险等级触发 (P1-P3)
              </label>

              <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                <div className="p-3 border-2 border-red-200 bg-red-50/40 rounded-xl space-y-1">
                  <div className="text-[11px] font-bold text-[#C92A2A]">P1 极高风险</div>
                  <div className="flex items-center gap-1 font-mono font-bold text-[14px]">
                    <span>&ge;</span>
                    <input
                      type="number"
                      value={p1Threshold}
                      onChange={(e) => setP1Threshold(Number(e.target.value))}
                      className="w-full bg-white border border-red-300 rounded px-2 py-0.5 text-center text-[#C92A2A]"
                    />
                  </div>
                </div>

                <div className="p-3 border-2 border-amber-200 bg-amber-50/40 rounded-xl space-y-1">
                  <div className="text-[11px] font-bold text-[#D97706]">P2 高风险</div>
                  <div className="flex items-center gap-1 font-mono font-bold text-[14px]">
                    <span>&ge;</span>
                    <input
                      type="number"
                      value={p2Threshold}
                      onChange={(e) => setP2Threshold(Number(e.target.value))}
                      className="w-full bg-white border border-amber-300 rounded px-2 py-0.5 text-center text-[#D97706]"
                    />
                  </div>
                </div>

                <div className="p-3 border-2 border-blue-200 bg-blue-50/40 rounded-xl space-y-1">
                  <div className="text-[11px] font-bold text-[#2563EB]">P3 中等风险</div>
                  <div className="flex items-center gap-1 font-mono font-bold text-[14px]">
                    <span>&ge;</span>
                    <input
                      type="number"
                      value={p3Threshold}
                      onChange={(e) => setP3Threshold(Number(e.target.value))}
                      className="w-full bg-white border border-blue-300 rounded px-2 py-0.5 text-center text-[#2563EB]"
                    />
                  </div>
                </div>
              </div>
            </div>

            {/* Event TTL */}
            <div className="space-y-1">
              <label className="text-[13px] font-bold text-[#424751]">事件有效期 (TTL)</label>
              <div className="flex items-center max-w-xs">
                <input
                  type="number"
                  value={ttlHours}
                  onChange={(e) => setTtlHours(Number(e.target.value))}
                  className="bg-[#f7f9ff] border border-[#c2c6d2] rounded-l-lg p-2 font-mono font-bold text-[#101d28] w-24 text-center"
                />
                <span className="bg-[#dceaf9] border border-l-0 border-[#c2c6d2] rounded-r-lg px-3 py-2 text-[13px] font-medium text-[#004782]">
                  小时
                </span>
              </div>
            </div>
          </div>

          <SignalFilterSection role={role} />

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
                <p className="text-[11px] text-slate-500 mt-0.5">
                  保存规则前，用样例事件验证当前配置会命中哪些真实供应商、得到多少分和什么等级；不创建事件、提醒或其他业务记录。
                </p>
              </div>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
              <div>
                <label className="text-[12px] font-bold text-slate-500">事件大类</label>
                <select
                  value={sandboxEventType}
                  onChange={(e) => setSandboxEventType(e.target.value)}
                  className="w-full bg-[#f7f9ff] border border-[#c2c6d2] rounded-lg p-2 text-[13px] font-medium mt-1"
                >
                  {sandboxOptions.event_types.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                </select>
              </div>

              <div>
                <label className="text-[12px] font-bold text-slate-500">事件细类（可选）</label>
                <select
                  value={sandboxEventSubtype}
                  onChange={(e) => setSandboxEventSubtype(e.target.value)}
                  className="w-full bg-[#f7f9ff] border border-[#c2c6d2] rounded-lg p-2 text-[13px] font-medium mt-1"
                >
                  <option value="">不指定</option>
                  {sandboxOptions.event_subtypes.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                </select>
              </div>

              <div>
                <label className="text-[12px] font-bold text-slate-500">严重程度</label>
                <select
                  value={sandboxSeverity}
                  onChange={(e) => setSandboxSeverity(e.target.value as typeof sandboxSeverity)}
                  className="w-full bg-[#f7f9ff] border border-[#c2c6d2] rounded-lg p-2 text-[13px] font-medium mt-1"
                >
                  <option value="critical">严重</option><option value="high">高</option><option value="medium">中</option><option value="low">低</option>
                </select>
              </div>

              <div>
                <label className="text-[12px] font-bold text-slate-500">供应商名称</label>
                <input value={sandboxOrganization} onChange={(e) => setSandboxOrganization(e.target.value)} placeholder="如：某某科技有限公司"
                  className="w-full bg-[#f7f9ff] border border-[#c2c6d2] rounded-lg p-2 text-[13px] mt-1" />
              </div>

              <div>
                <label className="text-[12px] font-bold text-slate-500">统一信用代码 / 注册号</label>
                <input value={sandboxRegistryNo} onChange={(e) => setSandboxRegistryNo(e.target.value)} placeholder="用于主体精确匹配"
                  className="w-full bg-[#f7f9ff] border border-[#c2c6d2] rounded-lg p-2 text-[13px] mt-1" />
              </div>

              <div>
                <label className="text-[12px] font-bold text-slate-500">事件地点</label>
                <input value={sandboxLocation} onChange={(e) => setSandboxLocation(e.target.value)} placeholder="如：上海市"
                  className="w-full bg-[#f7f9ff] border border-[#c2c6d2] rounded-lg p-2 text-[13px] mt-1" />
              </div>

              <div>
                <label className="text-[12px] font-bold text-slate-500">省州地区</label>
                <input value={sandboxRegion} onChange={(e) => setSandboxRegion(e.target.value)} placeholder="如：上海市"
                  className="w-full bg-[#f7f9ff] border border-[#c2c6d2] rounded-lg p-2 text-[13px] mt-1" />
              </div>

              <div>
                <label className="text-[12px] font-bold text-slate-500">城市</label>
                <input value={sandboxCity} onChange={(e) => setSandboxCity(e.target.value)} placeholder="如：上海市"
                  className="w-full bg-[#f7f9ff] border border-[#c2c6d2] rounded-lg p-2 text-[13px] mt-1" />
              </div>

              <div>
                <label className="text-[12px] font-bold text-slate-500">国家/地区代码</label>
                <input value={sandboxCountryCode} onChange={(e) => setSandboxCountryCode(e.target.value)} maxLength={2} placeholder="如：CN"
                  className="w-full bg-[#f7f9ff] border border-[#c2c6d2] rounded-lg p-2 text-[13px] font-mono mt-1" />
              </div>

              <div>
                <label className="text-[12px] font-bold text-slate-500">区县</label>
                <input value={sandboxDistrict} onChange={(e) => setSandboxDistrict(e.target.value)} placeholder="如：浦东新区"
                  className="w-full bg-[#f7f9ff] border border-[#c2c6d2] rounded-lg p-2 text-[13px] mt-1" />
              </div>

              <div>
                <label className="text-[12px] font-bold text-slate-500">受影响产品</label>
                <input value={sandboxProducts} onChange={(e) => setSandboxProducts(e.target.value)} placeholder="多个值用逗号分隔"
                  className="w-full bg-[#f7f9ff] border border-[#c2c6d2] rounded-lg p-2 text-[13px] mt-1" />
              </div>

              <div>
                <label className="text-[12px] font-bold text-slate-500">受影响行业</label>
                <input value={sandboxIndustries} onChange={(e) => setSandboxIndustries(e.target.value)} placeholder="多个值用逗号分隔"
                  className="w-full bg-[#f7f9ff] border border-[#c2c6d2] rounded-lg p-2 text-[13px] mt-1" />
              </div>

              <div>
                <label className="text-[12px] font-bold text-slate-500">来源可信度 (0-100)</label>
                <input type="number" min="0" max="100" value={sandboxCredibility} onChange={(e) => setSandboxCredibility(Number(e.target.value))}
                  className="w-full bg-[#f7f9ff] border border-[#c2c6d2] rounded-lg p-2 text-[13px] font-mono font-bold mt-1"
                />
              </div>
            </div>

            {sandboxError && <div role="alert" className="text-[12px] text-red-700 bg-red-50 border border-red-200 rounded-lg px-3 py-2">{sandboxError}</div>}

            <div className="flex flex-col sm:flex-row justify-between sm:items-center gap-3 pt-2">
              <span className="text-[11px] text-slate-500">至少填写主体、地点、产品或行业中的一项，才能观察到供应商候选命中。</span>

              <button
                onClick={() => void handleRunSandboxTest()}
                disabled={sandboxLoading || sandboxOptions.event_types.length === 0}
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
                {sandboxResult.message && <p className="text-[12px] text-slate-600">{sandboxResult.message}</p>}
                {sandboxResult.candidates.length === 0 ? (
                  <div className="text-[12px] text-slate-600 bg-white dark:bg-slate-900 p-3 rounded-lg border border-blue-200 dark:border-blue-800">
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
                    <div className="text-[11px] text-slate-500">匹配方式：{candidate.match_type} · 关联分：{candidate.association_score}</div>
                    <div className="text-[12px] text-slate-700 dark:text-slate-300">{candidate.reasons.join('；') || '命中当前规则'}</div>
                  </div>
                ))}
              </div>
            )}
          </motion.div>
          )}
          </AnimatePresence>
        </motion.div>
        </AnimatePresence>
      </div>
    </div>
  );
};
