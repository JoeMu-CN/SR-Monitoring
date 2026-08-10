import React, { useState } from 'react';
import { motion } from 'motion/react';
import {
  ResponsiveContainer,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  BarChart,
  Bar,
  PieChart,
  Pie,
  Cell,
} from 'recharts';
import {
  TrendingUp,
  PieChart as PieIcon,
  Layers,
  Activity,
  Maximize2,
  Minimize2,
  RefreshCw,
  Info,
  ShieldAlert,
} from 'lucide-react';
import {
  mock30DayTrendData,
  mockRiskCategoryData,
  mockSupplierTierData,
  mockDataSourcePerformance,
} from '../data/mockChartData';

interface DataVisualizationDashboardProps {
  isDarkMode?: boolean;
}

export const DataVisualizationDashboard: React.FC<DataVisualizationDashboardProps> = ({
  isDarkMode = true,
}) => {
  const [timeRange, setTimeRange] = useState<'7' | '30' | '90' | '180'>('30');
  const [chartDimension, setChartDimension] = useState<'level' | 'category' | 'index'>('level');
  const [isExpanded, setIsExpanded] = useState(false);
  const [hoveredCategory, setHoveredCategory] = useState<string | null>(null);

  // Filter trend data according to timeRange selection
  const trendData = React.useMemo(() => {
    if (timeRange === '7') return mock30DayTrendData.slice(-4);
    if (timeRange === '90') {
      return [...mock30DayTrendData, ...mock30DayTrendData.map(d => ({ ...d, date: `11/${d.date.split('/')[1]}` }))];
    }
    return mock30DayTrendData;
  }, [timeRange]);

  // Color palettes for Light & Dark Mode
  const gridColor = isDarkMode ? '#1e293b' : '#f1f5f9';
  const textColor = isDarkMode ? '#94a3b8' : '#64748b';
  const tooltipBg = isDarkMode ? '#0f172a' : '#ffffff';
  const tooltipBorder = isDarkMode ? '#334155' : '#e2e8f0';

  return (
    <div className={`space-y-6 ${isExpanded ? 'fixed inset-0 z-50 p-6 bg-slate-950 overflow-y-auto' : ''}`}>
      {/* Dashboard Top Control Toolbar */}
      <div className="bg-white/80 dark:bg-slate-900/80 backdrop-blur-md border border-slate-200/80 dark:border-slate-800 rounded-2xl p-4 sm:p-5 flex flex-col xl:flex-row justify-between items-start xl:items-center gap-4 shadow-2xs overflow-hidden">
        <div className="min-w-0 max-w-full">
          <div className="flex flex-wrap items-center gap-2 sm:gap-2.5">
            <Activity className="w-5 h-5 text-[#007aff] shrink-0" />
            <h2 className="text-base sm:text-lg font-black text-slate-900 dark:text-white tracking-tight whitespace-nowrap">
              全网供应链风险数据可视化仪表盘
            </h2>
            <span className="bg-[#007aff]/10 text-[#007aff] dark:text-blue-300 text-[10px] font-extrabold px-2.5 py-0.5 rounded-full border border-[#007aff]/20 whitespace-nowrap shrink-0 inline-flex items-center">
              Live Recharts Engine
            </span>
          </div>
          <p className="text-xs text-slate-500 dark:text-slate-400 mt-1.5 leading-relaxed break-keep">
            基于全网多源异构数据的实时时序演变、事件类型结构、Tier 阶梯画像及 API 链路穿透分析
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2.5 shrink-0 max-w-full">
          {/* Dimension Selector */}
          <div className="flex items-center bg-slate-100 dark:bg-slate-800/80 p-1 rounded-xl border border-slate-200/80 dark:border-slate-700/60 text-xs font-semibold shrink-0 max-w-full overflow-x-auto no-scrollbar">
            <button
              onClick={() => setChartDimension('level')}
              className={`px-3 py-1 rounded-lg transition-all cursor-pointer whitespace-nowrap shrink-0 ${
                chartDimension === 'level'
                  ? 'bg-white dark:bg-slate-700 text-slate-900 dark:text-white shadow-2xs font-bold'
                  : 'text-slate-500 dark:text-slate-400 hover:text-slate-900 dark:hover:text-white'
              }`}
            >
              按预警等级 (P1-P4)
            </button>
            <button
              onClick={() => setChartDimension('category')}
              className={`px-3 py-1 rounded-lg transition-all cursor-pointer whitespace-nowrap shrink-0 ${
                chartDimension === 'category'
                  ? 'bg-white dark:bg-slate-700 text-slate-900 dark:text-white shadow-2xs font-bold'
                  : 'text-slate-500 dark:text-slate-400 hover:text-slate-900 dark:hover:text-white'
              }`}
            >
              按事件类别
            </button>
            <button
              onClick={() => setChartDimension('index')}
              className={`px-3 py-1 rounded-lg transition-all cursor-pointer whitespace-nowrap shrink-0 ${
                chartDimension === 'index'
                  ? 'bg-white dark:bg-slate-700 text-slate-900 dark:text-white shadow-2xs font-bold'
                  : 'text-slate-500 dark:text-slate-400 hover:text-slate-900 dark:hover:text-white'
              }`}
            >
              全网综合风险指数
            </button>
          </div>

          {/* Time Range Selector */}
          <div className="flex items-center bg-slate-100 dark:bg-slate-800/80 p-1 rounded-xl border border-slate-200/80 dark:border-slate-700/60 text-xs font-semibold shrink-0">
            {(['7', '30', '90'] as const).map((r) => (
              <button
                key={r}
                onClick={() => setTimeRange(r)}
                className={`px-3 py-1 rounded-lg transition-all cursor-pointer whitespace-nowrap shrink-0 ${
                  timeRange === r
                    ? 'bg-[#007aff] text-white shadow-2xs font-bold'
                    : 'text-slate-500 dark:text-slate-400 hover:text-slate-900 dark:hover:text-white'
                }`}
              >
                {r}天
              </button>
            ))}
          </div>

          {/* Fullscreen Expand Button */}
          <button
            onClick={() => setIsExpanded(!isExpanded)}
            className="p-2 text-slate-600 dark:text-slate-300 bg-slate-100 dark:bg-slate-800 hover:bg-slate-200 dark:hover:bg-slate-700 rounded-xl transition-colors cursor-pointer shrink-0"
            title={isExpanded ? '退出大屏' : '全屏展示可视化仪表盘'}
          >
            {isExpanded ? <Minimize2 className="w-4 h-4" /> : <Maximize2 className="w-4 h-4" />}
          </button>
        </div>
      </div>

      {/* Main Feature Area 1: High-impact Time Series Trend Area Chart */}
      <motion.div
        layout
        className="bg-white/80 dark:bg-slate-900/80 backdrop-blur-md border border-slate-200/80 dark:border-slate-800 rounded-2xl p-5 shadow-2xs space-y-4"
      >
        <div className="flex justify-between items-center">
          <div>
            <h3 className="font-extrabold text-[15px] text-slate-900 dark:text-white flex items-center gap-2">
              <TrendingUp className="w-4 h-4 text-[#007aff]" />
              <span>全网供应链风险事件动态演变趋势 ({timeRange} 天)</span>
            </h3>
            <p className="text-[12px] text-slate-500 dark:text-slate-400 mt-0.5">
              高频监测点每 10 分钟自动聚合全网司法、制裁、物流及舆情信号
            </p>
          </div>

          <div className="flex items-center gap-2">
            <span className="text-[11px] font-mono text-emerald-600 dark:text-emerald-400 font-bold bg-emerald-50 dark:bg-emerald-950/60 px-2 py-0.5 rounded-md border border-emerald-200 dark:border-emerald-800">
              环比增长 +14.2%
            </span>
          </div>
        </div>

        {/* Recharts Area / Line Container */}
        <div className="w-full h-[300px] sm:h-[340px]">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={trendData} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
              <defs>
                <linearGradient id="colorP1" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#ff3b30" stopOpacity={0.8} />
                  <stop offset="95%" stopColor="#ff3b30" stopOpacity={0.0} />
                </linearGradient>
                <linearGradient id="colorP2" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#ff9500" stopOpacity={0.8} />
                  <stop offset="95%" stopColor="#ff9500" stopOpacity={0.0} />
                </linearGradient>
                <linearGradient id="colorP3" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#007aff" stopOpacity={0.8} />
                  <stop offset="95%" stopColor="#007aff" stopOpacity={0.0} />
                </linearGradient>
                <linearGradient id="colorP4" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#8e8e93" stopOpacity={0.6} />
                  <stop offset="95%" stopColor="#8e8e93" stopOpacity={0.0} />
                </linearGradient>
                <linearGradient id="colorIndex" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#af52de" stopOpacity={0.8} />
                  <stop offset="95%" stopColor="#af52de" stopOpacity={0.0} />
                </linearGradient>
              </defs>

              <CartesianGrid strokeDasharray="3 3" stroke={gridColor} vertical={false} />
              <XAxis dataKey="date" stroke={textColor} tick={{ fontSize: 11 }} tickLine={false} />
              <YAxis stroke={textColor} tick={{ fontSize: 11 }} tickLine={false} />

              <Tooltip
                contentStyle={{
                  backgroundColor: tooltipBg,
                  borderColor: tooltipBorder,
                  borderRadius: '12px',
                  boxShadow: '0 10px 25px -5px rgba(0, 0, 0, 0.3)',
                  color: isDarkMode ? '#f8fafc' : '#0f172a',
                  fontSize: '12px',
                  fontWeight: '600',
                }}
              />

              <Legend verticalAlign="top" height={36} iconType="circle" />

              {chartDimension === 'level' && (
                <>
                  <Area
                    type="monotone"
                    dataKey="P1"
                    name="P1 极高危"
                    stroke="#ff3b30"
                    strokeWidth={2.5}
                    fillOpacity={1}
                    fill="url(#colorP1)"
                  />
                  <Area
                    type="monotone"
                    dataKey="P2"
                    name="P2 高风险"
                    stroke="#ff9500"
                    strokeWidth={2}
                    fillOpacity={1}
                    fill="url(#colorP2)"
                  />
                  <Area
                    type="monotone"
                    dataKey="P3"
                    name="P3 中度"
                    stroke="#007aff"
                    strokeWidth={2}
                    fillOpacity={1}
                    fill="url(#colorP3)"
                  />
                  <Area
                    type="monotone"
                    dataKey="P4"
                    name="P4 低微关注"
                    stroke="#8e8e93"
                    strokeWidth={1.5}
                    fillOpacity={1}
                    fill="url(#colorP4)"
                  />
                </>
              )}

              {chartDimension === 'category' && (
                <>
                  <Area
                    type="monotone"
                    dataKey="lawsuit"
                    name="司法诉讼"
                    stroke="#007aff"
                    strokeWidth={2.5}
                    fillOpacity={1}
                    fill="url(#colorP3)"
                  />
                  <Area
                    type="monotone"
                    dataKey="financial"
                    name="财务异动"
                    stroke="#ff9500"
                    strokeWidth={2}
                    fillOpacity={1}
                    fill="url(#colorP2)"
                  />
                  <Area
                    type="monotone"
                    dataKey="sanction"
                    name="国际制裁"
                    stroke="#ff3b30"
                    strokeWidth={2.5}
                    fillOpacity={1}
                    fill="url(#colorP1)"
                  />
                  <Area
                    type="monotone"
                    dataKey="disruption"
                    name="生产履约"
                    stroke="#af52de"
                    strokeWidth={1.5}
                    fillOpacity={1}
                    fill="url(#colorIndex)"
                  />
                </>
              )}

              {chartDimension === 'index' && (
                <Area
                  type="monotone"
                  dataKey="avgRiskIndex"
                  name="全网风险防御综合指数"
                  stroke="#af52de"
                  strokeWidth={3}
                  fillOpacity={1}
                  fill="url(#colorIndex)"
                />
              )}
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </motion.div>

      {/* Grid of Secondary Visualizations */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-5">
        {/* Pie / Donut Chart: Risk Category Breakdown (5 cols) */}
        <div className="lg:col-span-5 bg-white/80 dark:bg-slate-900/80 backdrop-blur-md border border-slate-200/80 dark:border-slate-800 rounded-2xl p-4 shadow-2xs space-y-3">
          <div className="flex justify-between items-center">
            <h3 className="font-extrabold text-[14px] text-slate-900 dark:text-white flex items-center gap-2">
              <PieIcon className="w-4 h-4 text-[#ff9500]" />
              <span>全网风险类型结构分布</span>
            </h3>
            <span className="text-[11px] font-mono text-slate-400">484 起全网事件</span>
          </div>

          <div className="w-full h-[220px]">
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie
                  data={mockRiskCategoryData}
                  cx="50%"
                  cy="50%"
                  innerRadius={55}
                  outerRadius={85}
                  paddingAngle={4}
                  dataKey="value"
                  onMouseEnter={(_, index) => setHoveredCategory(mockRiskCategoryData[index].name)}
                  onMouseLeave={() => setHoveredCategory(null)}
                >
                  {mockRiskCategoryData.map((entry, index) => (
                    <Cell
                      key={`cell-${index}`}
                      fill={entry.color}
                      stroke={isDarkMode ? '#0f172a' : '#ffffff'}
                      strokeWidth={2}
                    />
                  ))}
                </Pie>
                <Tooltip
                  contentStyle={{
                    backgroundColor: tooltipBg,
                    borderColor: tooltipBorder,
                    borderRadius: '12px',
                    fontSize: '12px',
                    color: isDarkMode ? '#f8fafc' : '#0f172a',
                  }}
                />
              </PieChart>
            </ResponsiveContainer>
          </div>

          {/* Interactive Legend Items */}
          <div className="space-y-1.5 pt-2 border-t border-slate-100 dark:border-slate-800">
            {mockRiskCategoryData.map((cat) => (
              <div
                key={cat.name}
                className={`flex justify-between items-center p-1.5 rounded-lg text-[12px] transition-colors ${
                  hoveredCategory === cat.name ? 'bg-slate-100 dark:bg-slate-800 font-bold' : ''
                }`}
              >
                <div className="flex items-center gap-2">
                  <span
                    className="w-2.5 h-2.5 rounded-full"
                    style={{ backgroundColor: cat.color }}
                  />
                  <span className="text-slate-700 dark:text-slate-300 font-medium">{cat.name}</span>
                </div>
                <div className="flex items-center gap-3 font-mono">
                  <span className="text-slate-400 text-[11px]">{cat.count} 事件</span>
                  <span className="font-extrabold text-slate-900 dark:text-white">{cat.value}%</span>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Bar Chart: Supplier Tier Risk Profile (7 cols) */}
        <div className="lg:col-span-7 bg-white/80 dark:bg-slate-900/80 backdrop-blur-md border border-slate-200/80 dark:border-slate-800 rounded-2xl p-4 shadow-2xs space-y-3">
          <div className="flex justify-between items-center">
            <h3 className="font-extrabold text-[14px] text-slate-900 dark:text-white flex items-center gap-2">
              <Layers className="w-4 h-4 text-[#af52de]" />
              <span>供应商 Tier 阶梯风险指数对比</span>
            </h3>
            <span className="text-[11px] font-mono text-slate-400">按供应商核定层级</span>
          </div>

          <div className="w-full h-[220px]">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={mockSupplierTierData} margin={{ top: 10, right: 10, left: -10, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke={gridColor} vertical={false} />
                <XAxis dataKey="tier" stroke={textColor} tick={{ fontSize: 11 }} tickLine={false} />
                <YAxis stroke={textColor} tick={{ fontSize: 11 }} tickLine={false} />
                <Tooltip
                  contentStyle={{
                    backgroundColor: tooltipBg,
                    borderColor: tooltipBorder,
                    borderRadius: '12px',
                    fontSize: '12px',
                    color: isDarkMode ? '#f8fafc' : '#0f172a',
                  }}
                />
                <Bar dataKey="avgRiskScore" name="平均风险分 (0-100)" fill="#007aff" radius={[6, 6, 0, 0]} />
                <Bar dataKey="highRiskCount" name="P1/P2 高危企业数" fill="#ff3b30" radius={[6, 6, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>

          {/* Tier Detail Summary Cards */}
          <div className="grid grid-cols-3 gap-2 pt-2 border-t border-slate-100 dark:border-slate-800">
            {mockSupplierTierData.map((tier) => (
              <div
                key={tier.tier}
                className="bg-slate-50/80 dark:bg-slate-800/60 p-2.5 rounded-xl border border-slate-200/60 dark:border-slate-700/60 text-center"
              >
                <div className="text-[10px] font-bold text-slate-500 dark:text-slate-400 truncate">
                  {tier.tier.split(' ')[0]}
                </div>
                <div className="text-base font-black font-mono text-slate-900 dark:text-white mt-0.5">
                  {tier.totalCount} <span className="text-[10px] font-normal text-slate-400">家</span>
                </div>
                <div className="text-[10px] font-semibold text-[#ff3b30] mt-0.5">
                  高危 {tier.highRiskCount} 家 ({Math.round((tier.highRiskCount / tier.totalCount) * 100)}%)
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Feature Row 3: Data Source Latency & Throughput Matrix */}
      <div className="bg-white/80 dark:bg-slate-900/80 backdrop-blur-md border border-slate-200/80 dark:border-slate-800 rounded-2xl p-4 shadow-2xs space-y-3">
        <div className="flex justify-between items-center">
          <div>
            <h3 className="font-extrabold text-[14px] text-slate-900 dark:text-white flex items-center gap-2">
              <RefreshCw className="w-4 h-4 text-[#34c759]" />
              <span>全网数据源节点 API 响应时延与吞吐量 (延迟 vs 24h 处理量)</span>
            </h3>
          </div>
          <span className="text-[11px] font-mono text-emerald-600 dark:text-emerald-400 font-bold bg-emerald-50 dark:bg-emerald-950/60 px-2 py-0.5 rounded-md">
            全节点平均 API 延迟: 18ms
          </span>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-3">
          {mockDataSourcePerformance.map((ds) => (
            <div
              key={ds.source}
              className={`p-3 rounded-xl border text-xs space-y-1.5 ${
                ds.status === 'degraded'
                  ? 'bg-amber-500/10 border-amber-300/80 dark:border-amber-800/60 text-amber-900 dark:text-amber-200'
                  : 'bg-slate-50 dark:bg-slate-800/60 border-slate-200 dark:border-slate-700/60 text-slate-900 dark:text-white'
              }`}
            >
              <div className="flex justify-between items-center font-bold">
                <span className="truncate">{ds.source}</span>
                <span
                  className={`w-2 h-2 rounded-full ${
                    ds.status === 'degraded' ? 'bg-[#ff9500] animate-pulse' : 'bg-[#34c759]'
                  }`}
                />
              </div>

              <div className="flex justify-between items-baseline font-mono text-[11px]">
                <span className="text-slate-500 dark:text-slate-400">延迟:</span>
                <span className={`font-bold ${ds.latencyMs > 500 ? 'text-[#ff9500]' : 'text-[#34c759]'}`}>
                  {ds.latencyMs} ms
                </span>
              </div>

              <div className="flex justify-between items-baseline font-mono text-[11px]">
                <span className="text-slate-500 dark:text-slate-400">24h 信号量:</span>
                <span className="font-bold text-[#007aff]">{ds.dailyVolume.toLocaleString()}</span>
              </div>

              <div className="w-full bg-slate-200 dark:bg-slate-700 h-1 rounded-full overflow-hidden mt-1">
                <div
                  className="bg-[#007aff] h-full rounded-full"
                  style={{ width: `${Math.min(100, ds.accuracy)}%` }}
                />
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};
