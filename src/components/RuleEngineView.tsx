import React, { useState } from 'react';
import { Plus, Sliders, Play, Leaf, Globe, Scale, Building2, Sparkles, CheckCircle2 } from 'lucide-react';
import { MonitoringDimension } from '../types';

interface RuleEngineViewProps {
  dimensions: MonitoringDimension[];
  onToggleDimension: (id: string) => void;
  onUpdateDimension: (updatedDim: MonitoringDimension) => void;
}

export const RuleEngineView: React.FC<RuleEngineViewProps> = ({
  dimensions,
  onToggleDimension,
  onUpdateDimension,
}) => {
  const [activeDimId, setActiveDimId] = useState<string>(dimensions[0]?.id || 'dim-01');

  const selectedDim = dimensions.find((d) => d.id === activeDimId) || dimensions[0];

  // Editable configuration state
  const [severityWeight, setSeverityWeight] = useState(selectedDim.severityWeight);
  const [relevanceWeight, setRelevanceWeight] = useState(selectedDim.relevanceWeight);
  const [p1Threshold, setP1Threshold] = useState(selectedDim.thresholds.p1);
  const [p2Threshold, setP2Threshold] = useState(selectedDim.thresholds.p2);
  const [p3Threshold, setP3Threshold] = useState(selectedDim.thresholds.p3);
  const [ttlHours, setTtlHours] = useState(selectedDim.ttlHours);

  // Sandbox testing state
  const [sandboxEventType, setSandboxEventType] = useState('极端天气 (台风/洪水)');
  const [sandboxImpact, setSandboxImpact] = useState<number>(80);
  const [sandboxDistance, setSandboxDistance] = useState<number>(15);
  const [sandboxResult, setSandboxResult] = useState<any>(null);

  // Helper to map string icon name to lucide icon
  const renderDimensionIcon = (iconName: string) => {
    switch (iconName) {
      case 'eco':
        return <Leaf className="w-5 h-5 text-[#007aff]" />;
      case 'public':
        return <Globe className="w-5 h-5 text-[#007aff]" />;
      case 'gavel':
        return <Scale className="w-5 h-5 text-[#007aff]" />;
      case 'account_balance':
        return <Building2 className="w-5 h-5 text-[#007aff]" />;
      default:
        return <Sparkles className="w-5 h-5 text-[#007aff]" />;
    }
  };

  // Update editor values when active dimension changes
  React.useEffect(() => {
    if (selectedDim) {
      setSeverityWeight(selectedDim.severityWeight);
      setRelevanceWeight(selectedDim.relevanceWeight);
      setP1Threshold(selectedDim.thresholds.p1);
      setP2Threshold(selectedDim.thresholds.p2);
      setP3Threshold(selectedDim.thresholds.p3);
      setTtlHours(selectedDim.ttlHours);
      setSandboxResult(null);
    }
  }, [activeDimId, selectedDim]);

  const handleSaveConfig = () => {
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
    onUpdateDimension(updated);
    alert(`规则 [${selectedDim.name}] 配置已成功保存！`);
  };

  const handleRunSandboxTest = () => {
    // Formula simulation: Score = Impact * SeverityWeight + (100 - Distance*2) * RelevanceWeight
    const distFactor = Math.max(0, 100 - sandboxDistance * 2);
    const scoreCalculated = Math.min(
      100,
      Math.round(sandboxImpact * severityWeight + distFactor * relevanceWeight * 0.3)
    );

    let levelCalculated = 'P4';
    if (scoreCalculated >= p1Threshold) levelCalculated = 'P1 极高风险';
    else if (scoreCalculated >= p2Threshold) levelCalculated = 'P2 高风险';
    else if (scoreCalculated >= p3Threshold) levelCalculated = 'P3 中等风险';

    setSandboxResult({
      score: scoreCalculated,
      level: levelCalculated,
      formula: `(${sandboxImpact} × ${severityWeight}) + (距离加权 ${distFactor} × ${relevanceWeight})`,
      recommendation:
        scoreCalculated >= 80
          ? '触发紧急替代供应链机制，通知采购组锁定备用库存。'
          : '进入重点关注观察名单，持续追踪次日预警信号。',
    });
  };

  return (
    <div className="space-y-6 pb-20 lg:pb-8">
      {/* Title */}
      <div>
        <h1 className="text-2xl font-extrabold text-slate-900 dark:text-white tracking-tight">
          规则引擎与权重配置
        </h1>
        <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
          自定义监控维度、计算权重及沙箱仿真评估。
        </p>
      </div>

      {/* Main Grid Layout (Left: Dimensions List, Right: Rule Config + Sandbox) */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        {/* Left Column: Monitoring Dimensions List (4 cols) */}
        <div className="lg:col-span-4 bg-white/80 dark:bg-slate-800/60 backdrop-blur-md border border-slate-200/80 dark:border-slate-700/60 rounded-2xl p-4 shadow-2xs space-y-3 h-fit">
          <div className="flex justify-between items-center pb-2 border-b border-slate-100 dark:border-slate-800">
            <h2 className="font-bold text-[15px] text-slate-900 dark:text-white">监控维度</h2>
            <button
              onClick={() => alert('新建自定义维度功能已就绪')}
              className="p-1 hover:bg-slate-100 dark:hover:bg-slate-800 rounded-lg text-[#007aff] cursor-pointer"
              title="添加新维度"
            >
              <Plus className="w-5 h-5" />
            </button>
          </div>

          <div className="space-y-2">
            {dimensions.map((dim) => {
              const isSelected = dim.id === activeDimId;
              return (
                <div
                  key={dim.id}
                  onClick={() => setActiveDimId(dim.id)}
                  className={`p-3 rounded-2xl border flex items-center justify-between cursor-pointer transition-all ${
                    isSelected
                      ? 'bg-blue-50/80 dark:bg-slate-800 border-[#007aff] shadow-2xs'
                      : 'border-slate-200/60 dark:border-slate-700/60 hover:bg-slate-50 dark:hover:bg-slate-800/50'
                  }`}
                >
                  <div className="flex items-center gap-3">
                    {renderDimensionIcon(dim.icon)}
                    <span className="font-bold text-[14px] text-slate-900 dark:text-white">
                      {dim.name}
                    </span>
                  </div>

                  {/* Toggle switch */}
                  <label
                    onClick={(e) => e.stopPropagation()}
                    className="relative inline-flex items-center cursor-pointer"
                  >
                    <input
                      type="checkbox"
                      checked={dim.enabled}
                      onChange={() => onToggleDimension(dim.id)}
                      className="sr-only peer"
                    />
                    <div className="w-9 h-5 bg-slate-200 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-slate-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-[#007aff]"></div>
                  </label>
                </div>
              );
            })}
          </div>
        </div>

        {/* Right Column: Rule Editor & Sandbox Simulator (8 cols) */}
        <div className="lg:col-span-8 space-y-6">
          {/* Rule Configuration Card */}
          <div className="bg-white/80 dark:bg-slate-800/60 backdrop-blur-md border border-slate-200/80 dark:border-slate-700/60 rounded-2xl p-5 shadow-2xs space-y-5">
            <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-3 pb-3 border-b border-slate-100 dark:border-slate-800">
              <div>
                <h2 className="font-bold text-[18px] text-slate-900 dark:text-white">
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
                  className="px-3 py-1.5 border border-slate-200/80 dark:border-slate-700 text-slate-600 dark:text-slate-300 rounded-xl text-[13px] font-medium hover:bg-slate-50 dark:hover:bg-slate-800 cursor-pointer"
                >
                  取消
                </button>
                <button
                  onClick={handleSaveConfig}
                  className="px-4 py-1.5 bg-[#007aff] hover:bg-[#0062cc] text-white rounded-xl text-[13px] font-bold shadow-2xs transition-colors cursor-pointer"
                >
                  保存配置
                </button>
              </div>
            </div>

            {/* Sliders */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              <div className="space-y-2">
                <div className="flex justify-between text-[13px]">
                  <span className="font-bold text-slate-700 dark:text-slate-300">严重性权重 (SEVERITY)</span>
                  <span className="font-mono font-bold text-[#007aff] dark:text-blue-300">{severityWeight}</span>
                </div>
                <input
                  type="range"
                  min="0.1"
                  max="1.0"
                  step="0.05"
                  value={severityWeight}
                  onChange={(e) => setSeverityWeight(parseFloat(e.target.value))}
                  className="w-full accent-[#007aff] cursor-pointer"
                />
              </div>

              <div className="space-y-2">
                <div className="flex justify-between text-[13px]">
                  <span className="font-bold text-slate-700 dark:text-slate-300">业务关联度 (RELEVANCE)</span>
                  <span className="font-mono font-bold text-[#007aff] dark:text-blue-300">{relevanceWeight}</span>
                </div>
                <input
                  type="range"
                  min="0.1"
                  max="1.0"
                  step="0.05"
                  value={relevanceWeight}
                  onChange={(e) => setRelevanceWeight(parseFloat(e.target.value))}
                  className="w-full accent-[#007aff] cursor-pointer"
                />
              </div>
            </div>

            {/* Risk Level Trigger Thresholds */}
            <div className="space-y-2">
              <label className="text-[13px] font-bold text-slate-700 dark:text-slate-300">
                风险等级触发 (P1-P3)
              </label>

              <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                <div className="p-3 border-2 border-red-200 dark:border-red-900 bg-red-50/40 dark:bg-red-950/30 rounded-2xl space-y-1">
                  <div className="text-[11px] font-bold text-[#ff3b30]">P1 极高风险</div>
                  <div className="flex items-center gap-1 font-mono font-bold text-[14px]">
                    <span>&ge;</span>
                    <input
                      type="number"
                      value={p1Threshold}
                      onChange={(e) => setP1Threshold(Number(e.target.value))}
                      className="w-full bg-white dark:bg-slate-900 border border-red-300 dark:border-red-800 rounded-lg px-2 py-0.5 text-center text-[#ff3b30]"
                    />
                  </div>
                </div>

                <div className="p-3 border-2 border-amber-200 dark:border-amber-900 bg-amber-50/40 dark:bg-amber-950/30 rounded-2xl space-y-1">
                  <div className="text-[11px] font-bold text-[#ff9500]">P2 高风险</div>
                  <div className="flex items-center gap-1 font-mono font-bold text-[14px]">
                    <span>&ge;</span>
                    <input
                      type="number"
                      value={p2Threshold}
                      onChange={(e) => setP2Threshold(Number(e.target.value))}
                      className="w-full bg-white dark:bg-slate-900 border border-amber-300 dark:border-amber-800 rounded-lg px-2 py-0.5 text-center text-[#ff9500]"
                    />
                  </div>
                </div>

                <div className="p-3 border-2 border-blue-200 dark:border-blue-900 bg-blue-50/40 dark:bg-blue-950/30 rounded-2xl space-y-1">
                  <div className="text-[11px] font-bold text-[#007aff]">P3 中等风险</div>
                  <div className="flex items-center gap-1 font-mono font-bold text-[14px]">
                    <span>&ge;</span>
                    <input
                      type="number"
                      value={p3Threshold}
                      onChange={(e) => setP3Threshold(Number(e.target.value))}
                      className="w-full bg-white dark:bg-slate-900 border border-blue-300 dark:border-blue-800 rounded-lg px-2 py-0.5 text-center text-[#007aff]"
                    />
                  </div>
                </div>
              </div>
            </div>

            {/* Event TTL */}
            <div className="space-y-1">
              <label className="text-[13px] font-bold text-slate-700 dark:text-slate-300">事件有效期 (TTL)</label>
              <div className="flex items-center max-w-xs">
                <input
                  type="number"
                  value={ttlHours}
                  onChange={(e) => setTtlHours(Number(e.target.value))}
                  className="bg-slate-100 dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-l-xl p-2 font-mono font-bold text-slate-900 dark:text-white w-24 text-center"
                />
                <span className="bg-blue-50 dark:bg-slate-800 border border-l-0 border-slate-200 dark:border-slate-700 rounded-r-xl px-3 py-2 text-[13px] font-medium text-[#007aff] dark:text-blue-300">
                  小时
                </span>
              </div>
            </div>
          </div>

          {/* Sandbox Testing Box */}
          <div className="bg-white/80 dark:bg-slate-800/60 backdrop-blur-md border border-slate-200/80 dark:border-slate-700/60 rounded-2xl p-5 shadow-2xs space-y-4">
            <div className="flex items-center gap-2 font-bold text-[16px] text-slate-900 dark:text-white">
              <Sliders className="w-5 h-5 text-[#007aff]" />
              <span>沙箱测试 (Sandbox Testing)</span>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
              <div>
                <label className="text-[12px] font-bold text-slate-500">事件类型</label>
                <select
                  value={sandboxEventType}
                  onChange={(e) => setSandboxEventType(e.target.value)}
                  className="w-full bg-slate-100/80 dark:bg-slate-900 border border-slate-200/80 dark:border-slate-700 rounded-xl p-2 text-[13px] font-medium mt-1 text-slate-900 dark:text-white"
                >
                  <option value="极端天气 (台风/洪水)">极端天气 (台风/洪水)</option>
                  <option value="劳工罢工/抗议">劳工罢工/抗议</option>
                  <option value="制裁名单变动">制裁名单变动</option>
                  <option value="财务债务违约">财务债务违约</option>
                </select>
              </div>

              <div>
                <label className="text-[12px] font-bold text-slate-500">初始影响值 (0-100)</label>
                <input
                  type="number"
                  min="0"
                  max="100"
                  value={sandboxImpact}
                  onChange={(e) => setSandboxImpact(Number(e.target.value))}
                  className="w-full bg-slate-100/80 dark:bg-slate-900 border border-slate-200/80 dark:border-slate-700 rounded-xl p-2 text-[13px] font-mono font-bold mt-1 text-slate-900 dark:text-white"
                />
              </div>

              <div>
                <label className="text-[12px] font-bold text-slate-500">供应商距离 (公里)</label>
                <input
                  type="number"
                  min="0"
                  value={sandboxDistance}
                  onChange={(e) => setSandboxDistance(Number(e.target.value))}
                  className="w-full bg-slate-100/80 dark:bg-slate-900 border border-slate-200/80 dark:border-slate-700 rounded-xl p-2 text-[13px] font-mono font-bold mt-1 text-slate-900 dark:text-white"
                />
              </div>
            </div>

            <div className="flex justify-between items-center pt-2">
              <span className="text-[11px] text-slate-400">
                测试数据不会写入生产数据库，仅用于模型仿真计算。
              </span>

              <button
                onClick={handleRunSandboxTest}
                className="px-4 py-2 border-2 border-[#007aff] text-[#007aff] dark:text-blue-400 hover:bg-blue-50/50 dark:hover:bg-slate-800 font-bold text-[13px] rounded-xl flex items-center gap-1.5 transition-colors cursor-pointer"
              >
                <Play className="w-4 h-4 fill-current" />
                <span>评估 (不落库)</span>
              </button>
            </div>

            {/* Sandbox Evaluation Output Overlay */}
            {sandboxResult && (
              <div className="p-4 bg-blue-50/80 dark:bg-blue-950/40 border-2 border-blue-300 dark:border-blue-800 rounded-2xl space-y-2 animate-in fade-in">
                <div className="flex justify-between items-center font-bold">
                  <span className="text-[#007aff] dark:text-blue-300 text-[14px]">仿真计算评估结果</span>
                  <span className="bg-[#ff3b30] text-white px-2 py-0.5 rounded-md text-[12px]">
                    {sandboxResult.level}
                  </span>
                </div>
                <div className="text-[13px] font-mono font-bold text-slate-800 dark:text-slate-100">
                  预估风险得分: <span className="text-2xl text-[#ff3b30]">{sandboxResult.score}</span> / 100
                </div>
                <div className="text-[11px] text-slate-600 dark:text-slate-400 font-mono">
                  公式推断: {sandboxResult.formula}
                </div>
                <div className="text-[12px] text-slate-700 dark:text-slate-200 bg-white dark:bg-slate-900 p-2.5 rounded-xl border border-blue-200 dark:border-blue-900">
                  <strong>推荐智能防范措施:</strong> {sandboxResult.recommendation}
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

