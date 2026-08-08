import React, { useState } from 'react';
import { MonitoringDimension } from '../types';

interface RuleEngineViewProps {
  dimensions: MonitoringDimension[];
  onToggleDimension: (id: string) => Promise<void>;
  onUpdateDimension: (updatedDim: MonitoringDimension) => Promise<void>;
}

export const RuleEngineView: React.FC<RuleEngineViewProps> = ({
  dimensions,
  onToggleDimension,
  onUpdateDimension,
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
  const [sandboxEventType, setSandboxEventType] = useState('极端天气 (台风/洪水)');
  const [sandboxImpact, setSandboxImpact] = useState<number>(80);
  const [sandboxDistance, setSandboxDistance] = useState<number>(15);
  const [sandboxResult, setSandboxResult] = useState<any>(null);

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
          ? '生成高等级当前风险提醒，并持续跟踪后续公开信号。'
          : '保留为当前观察信息，等待更多确定性证据。',
    });
  };

  if (!selectedDim) {
    return <div className="bg-white dark:bg-slate-900 border border-[#c2c6d2] rounded-xl p-8 text-center text-slate-500">暂无可用监控维度</div>;
  }

  return (
    <div className="space-y-6 pb-20 lg:pb-8">
      {/* Title */}
      <div>
        <h1 className="text-2xl font-bold text-[#101d28] dark:text-white tracking-tight">
          规则引擎与权重配置
        </h1>
        <p className="text-xs text-[#424751] dark:text-slate-400 mt-0.5">
          自定义监控维度、计算权重及沙箱仿真评估。
        </p>
      </div>

      {/* Main Grid Layout (Left: Dimensions List, Right: Rule Config + Sandbox) */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        {/* Left Column: Monitoring Dimensions List (4 cols) */}
        <div className="lg:col-span-4 bg-white dark:bg-slate-900 border border-[#c2c6d2] dark:border-slate-800 rounded-xl p-4 shadow-2xs space-y-3 h-fit">
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
                  <div className="flex items-center gap-3">
                    <span className="material-symbols-outlined text-[#004782] text-[22px]">
                      {dim.icon}
                    </span>
                    <span className="font-bold text-[14px] text-[#101d28] dark:text-white">
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
                      onChange={() => void onToggleDimension(dim.id)}
                      className="sr-only peer"
                    />
                    <div className="w-9 h-5 bg-slate-200 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-slate-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-[#004782]"></div>
                  </label>
                </div>
              );
            })}
          </div>
        </div>

        {/* Right Column: Rule Editor & Sandbox Simulator (8 cols) */}
        <div className="lg:col-span-8 space-y-6">
          {/* Rule Configuration Card */}
          <div className="bg-white dark:bg-slate-900 border border-[#c2c6d2] dark:border-slate-800 rounded-xl p-5 shadow-2xs space-y-5">
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
                  className="px-4 py-1.5 bg-[#004782] text-white rounded-lg text-[13px] font-bold shadow-sm hover:bg-[#185fa5] transition-colors"
                >
                  保存配置
                </button>
              </div>
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

          {/* Sandbox Testing Box */}
          <div className="bg-white dark:bg-slate-900 border border-[#c2c6d2] dark:border-slate-800 rounded-xl p-5 shadow-2xs space-y-4">
            <div className="flex items-center gap-2 font-bold text-[16px] text-[#101d28] dark:text-white">
              <span className="material-symbols-outlined text-[22px] text-[#004782]">grid_view</span>
              <span>沙箱测试 (Sandbox Testing)</span>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
              <div>
                <label className="text-[12px] font-bold text-slate-500">事件类型</label>
                <select
                  value={sandboxEventType}
                  onChange={(e) => setSandboxEventType(e.target.value)}
                  className="w-full bg-[#f7f9ff] border border-[#c2c6d2] rounded-lg p-2 text-[13px] font-medium mt-1"
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
                  className="w-full bg-[#f7f9ff] border border-[#c2c6d2] rounded-lg p-2 text-[13px] font-mono font-bold mt-1"
                />
              </div>

              <div>
                <label className="text-[12px] font-bold text-slate-500">供应商距离 (公里)</label>
                <input
                  type="number"
                  min="0"
                  value={sandboxDistance}
                  onChange={(e) => setSandboxDistance(Number(e.target.value))}
                  className="w-full bg-[#f7f9ff] border border-[#c2c6d2] rounded-lg p-2 text-[13px] font-mono font-bold mt-1"
                />
              </div>
            </div>

            <div className="flex justify-between items-center pt-2">
              <span className="text-[11px] text-slate-400">
                测试数据不会写入生产数据库，仅用于模型仿真计算。
              </span>

              <button
                onClick={handleRunSandboxTest}
                className="px-4 py-2 border-2 border-[#004782] text-[#004782] dark:text-blue-400 hover:bg-blue-50 font-bold text-[13px] rounded-lg flex items-center gap-1.5 transition-colors"
              >
                <span className="material-symbols-outlined text-[18px]">play_arrow</span>
                <span>评估 (不落库)</span>
              </button>
            </div>

            {/* Sandbox Evaluation Output Overlay */}
            {sandboxResult && (
              <div className="p-4 bg-blue-50/80 border-2 border-blue-300 rounded-xl space-y-2 animate-in fade-in">
                <div className="flex justify-between items-center font-bold">
                  <span className="text-[#004782] text-[14px]">仿真计算评估结果</span>
                  <span className="bg-[#C92A2A] text-white px-2 py-0.5 rounded text-[12px]">
                    {sandboxResult.level}
                  </span>
                </div>
                <div className="text-[13px] font-mono font-bold text-slate-800">
                  预估风险得分: <span className="text-2xl text-[#C92A2A]">{sandboxResult.score}</span> / 100
                </div>
                <div className="text-[11px] text-slate-600 font-mono">
                  公式推导: {sandboxResult.formula}
                </div>
                <div className="text-[12px] text-slate-700 bg-white p-2.5 rounded-lg border border-blue-200">
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
