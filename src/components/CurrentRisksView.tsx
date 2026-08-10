import React, { useState } from 'react';
import { motion, AnimatePresence } from 'motion/react';
import { Search, RotateCcw, SearchX, MapPin, ChevronRight } from 'lucide-react';
import { RiskItem } from '../types';

interface CurrentRisksViewProps {
  riskItems: RiskItem[];
  onSelectRisk: (item: RiskItem) => void;
}

export const CurrentRisksView: React.FC<CurrentRisksViewProps> = ({
  riskItems,
  onSelectRisk,
}) => {
  const [searchTerm, setSearchTerm] = useState('');
  const [selectedLevel, setSelectedLevel] = useState<string>('all');
  const [selectedCountry, setSelectedCountry] = useState<string>('all');
  const [statusFilter, setStatusFilter] = useState<'valid' | 'invalid'>('valid');
  const [currentPage, setCurrentPage] = useState(1);

  // Filter items
  const filteredRisks = riskItems.filter((item) => {
    const matchesSearch =
      item.companyName.toLowerCase().includes(searchTerm.toLowerCase()) ||
      item.riskType.toLowerCase().includes(searchTerm.toLowerCase()) ||
      item.summary.toLowerCase().includes(searchTerm.toLowerCase());

    const matchesLevel = selectedLevel === 'all' || item.level === selectedLevel;
    const matchesCountry = selectedCountry === 'all' || item.country === selectedCountry;
    const matchesStatus = item.status === statusFilter;

    return matchesSearch && matchesLevel && matchesCountry && matchesStatus;
  });

  return (
    <div className="space-y-5 pb-20 lg:pb-8">
      {/* Page Title */}
      <div>
        <h1 className="text-xl lg:text-2xl font-black text-slate-900 dark:text-white tracking-tight">
          全网风险监控中心
        </h1>
        <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
          实时深度追踪并评估 Tier-1/2 供应链企业的高危预警、合规穿透及异常异动
        </p>
      </div>

      {/* macOS Finder Toolbar & Filter Controls Bar */}
      <div className="bg-white/80 dark:bg-slate-800/60 backdrop-blur-md border border-slate-200/80 dark:border-slate-700/60 rounded-2xl p-4 space-y-3 shadow-2xs">
        {/* Search & Action Controls */}
        <div className="flex flex-col md:flex-row gap-3">
          <div className="relative flex-1">
            <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
            <input
              type="text"
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              placeholder="搜索供应商主体、法律诉讼事件或国家地区 (如: 极海半导体, 破产)..."
              className="w-full bg-slate-100/80 dark:bg-slate-900/60 border border-slate-200/80 dark:border-slate-700/60 rounded-xl pl-9 pr-3 py-1.5 text-[13px] text-slate-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-[#007aff]/30"
            />
          </div>

          <button
            onClick={() => {
              setSearchTerm('');
              setSelectedLevel('all');
              setSelectedCountry('all');
            }}
            className="px-3 py-1.5 bg-slate-100 dark:bg-slate-700/60 hover:bg-slate-200/70 border border-slate-200/80 dark:border-slate-700/80 rounded-xl text-[12px] font-semibold text-slate-700 dark:text-slate-300 transition-colors flex items-center gap-1.5 cursor-pointer"
          >
            <RotateCcw className="w-3.5 h-3.5" />
            <span>重置筛选</span>
          </button>
        </div>

        {/* Dropdowns & Status Segmented Switch */}
        <div className="flex flex-wrap items-center justify-between gap-3 pt-2 border-t border-slate-200/60 dark:border-slate-700/40">
          <div className="flex flex-wrap items-center gap-3 text-[12px]">
            {/* Risk Level */}
            <div className="flex items-center gap-1.5">
              <span className="text-slate-500 font-medium">风险等级:</span>
              <select
                value={selectedLevel}
                onChange={(e) => setSelectedLevel(e.target.value)}
                className="bg-slate-100 dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-lg px-2.5 py-1 font-bold text-[#007aff] dark:text-blue-400 focus:outline-none cursor-pointer"
              >
                <option value="all">全部级别</option>
                <option value="P1">P1 极高危</option>
                <option value="P2">P2 高风险</option>
                <option value="P3">P3 中度</option>
                <option value="P4">P4 低微</option>
              </select>
            </div>

            {/* Country / Region */}
            <div className="flex items-center gap-1.5">
              <span className="text-slate-500 font-medium">国家/地区:</span>
              <select
                value={selectedCountry}
                onChange={(e) => setSelectedCountry(e.target.value)}
                className="bg-slate-100 dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-lg px-2.5 py-1 font-medium focus:outline-none cursor-pointer"
              >
                <option value="all">全部区域</option>
                <option value="中国">中国</option>
                <option value="中国台湾">中国台湾</option>
                <option value="德国">德国</option>
                <option value="埃及">埃及</option>
                <option value="美国">美国</option>
              </select>
            </div>
          </div>

          {/* Status Segmented Toggle */}
          <div className="flex bg-slate-200/70 dark:bg-slate-800/80 p-0.5 rounded-xl border border-black/5 dark:border-white/5 text-[12px] font-semibold">
            <button
              onClick={() => setStatusFilter('valid')}
              className={`px-3 py-1 rounded-lg transition-all cursor-pointer ${
                statusFilter === 'valid'
                  ? 'bg-white dark:bg-slate-700 text-slate-900 dark:text-white shadow-2xs font-bold'
                  : 'text-slate-500 hover:text-slate-900 dark:hover:text-white'
              }`}
            >
              有效预警
            </button>
            <button
              onClick={() => setStatusFilter('invalid')}
              className={`px-3 py-1 rounded-lg transition-all cursor-pointer ${
                statusFilter === 'invalid'
                  ? 'bg-white dark:bg-slate-700 text-slate-900 dark:text-white shadow-2xs font-bold'
                  : 'text-slate-500 hover:text-slate-900 dark:hover:text-white'
              }`}
            >
              历史归档
            </button>
          </div>
        </div>
      </div>

      {/* Global Risk Index Summary macOS Gauge Card */}
      <div className="bg-white/80 dark:bg-slate-800/60 backdrop-blur-md border border-slate-200/80 dark:border-slate-700/60 rounded-2xl p-4 shadow-2xs">
        <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-2 pb-3 border-b border-slate-200/60 dark:border-slate-700/40">
          <div className="text-[13px] font-bold text-slate-700 dark:text-slate-300">
            全网供应链网络防御指数
          </div>
          <div className="flex items-center gap-2">
            <span className="font-mono font-black text-[#ff3b30] uppercase text-[12px]">
              Critical High
            </span>
            <span className="bg-[#ff3b30] text-white font-black font-mono text-[13px] px-2 py-0.5 rounded-md shadow-2xs">
              87 / 100
            </span>
          </div>
        </div>

        {/* Risk Index Bar */}
        <div className="w-full bg-slate-100 dark:bg-slate-700 h-2.5 rounded-full overflow-hidden my-3 flex shadow-inner">
          <div className="bg-[#ff3b30] h-full" style={{ width: '35%' }} />
          <div className="bg-[#ff9500] h-full" style={{ width: '40%' }} />
          <div className="bg-[#007aff] h-full" style={{ width: '25%' }} />
        </div>

        <div className="grid grid-cols-3 text-center divide-x divide-slate-200/60 dark:divide-slate-700/40 pt-1">
          <div>
            <div className="text-lg font-black font-mono text-[#ff3b30]">12</div>
            <div className="text-[11px] font-medium text-slate-400">P1 极高危</div>
          </div>
          <div>
            <div className="text-lg font-black font-mono text-[#ff9500]">34</div>
            <div className="text-[11px] font-medium text-slate-400">P2 高危</div>
          </div>
          <div>
            <div className="text-lg font-black font-mono text-[#007aff]">128</div>
            <div className="text-[11px] font-medium text-slate-400">P3 警告</div>
          </div>
        </div>
      </div>

      {/* Risk Cards macOS Finder Cards */}
      <div className="space-y-3">
        <AnimatePresence mode="popLayout">
          {filteredRisks.length === 0 ? (
            <motion.div
              initial={{ opacity: 0, scale: 0.98 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.98 }}
              className="bg-white/80 dark:bg-slate-800/60 backdrop-blur-md border border-slate-200/80 dark:border-slate-700/60 rounded-2xl p-10 text-center flex flex-col items-center"
            >
              <SearchX className="w-10 h-10 text-slate-300" />
              <h3 className="font-bold text-[15px] text-slate-800 dark:text-slate-200 mt-2">
                未查找到符合条件的风险记录
              </h3>
              <p className="text-xs text-slate-400 mt-1">请尝试更换搜索词或重置筛选维度</p>
            </motion.div>
          ) : (
            filteredRisks.map((item) => {
              let badgeBg = 'bg-slate-500 text-white';
              if (item.level === 'P1') badgeBg = 'bg-[#ff3b30] text-white';
              if (item.level === 'P2') badgeBg = 'bg-[#ff9500] text-white';
              if (item.level === 'P3') badgeBg = 'bg-[#007aff] text-white';

              return (
                <motion.div
                  key={item.id}
                  layout
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, scale: 0.98 }}
                  whileHover={{ y: -2 }}
                  onClick={() => onSelectRisk(item)}
                  className="bg-white/80 dark:bg-slate-800/60 backdrop-blur-md border border-slate-200/80 dark:border-slate-700/60 rounded-2xl p-4 shadow-2xs hover:border-[#007aff] hover:shadow-md transition-all cursor-pointer group"
                >
                  <div className="flex justify-between items-start gap-2">
                    <div className="flex items-center gap-2">
                      <span className={`${badgeBg} font-bold text-[10px] px-2 py-0.5 rounded-md shadow-2xs`}>
                        {item.level} {item.levelName}
                      </span>
                      <h3 className="font-bold text-[15px] text-slate-900 dark:text-white group-hover:text-[#007aff] transition-colors">
                        {item.companyName}
                      </h3>
                    </div>
                    <span className="text-[11px] text-slate-400 font-mono">{item.updatedTime}</span>
                  </div>

                  {item.location && (
                    <div className="flex items-center gap-1 text-[12px] text-slate-500 mt-1">
                      <MapPin className="w-3.5 h-3.5 text-slate-400" />
                      <span>{item.location}</span>
                    </div>
                  )}

                  <p className="text-[13px] text-slate-600 dark:text-slate-300 mt-2 leading-relaxed line-clamp-2">
                    {item.summary}
                  </p>

                  <div className="flex items-center justify-between pt-3 mt-3 border-t border-slate-200/60 dark:border-slate-700/40 text-[12px]">
                    <div className="flex items-center gap-2">
                      {item.tags?.map((tag, idx) => (
                        <span
                          key={idx}
                          className="bg-slate-100 dark:bg-slate-700/60 text-[#007aff] dark:text-blue-300 font-semibold px-2 py-0.5 rounded-md text-[11px] border border-slate-200/50 dark:border-slate-600/50"
                        >
                          {tag}
                        </span>
                      ))}
                      {item.aiConfidence && (
                        <span className="text-slate-400 font-mono text-[11px]">
                          置信度: <strong className="text-[#007aff]">{item.aiConfidence}%</strong>
                        </span>
                      )}
                    </div>

                    <span className="text-[#007aff] font-bold hover:underline flex items-center gap-0.5">
                      <span>调出详单</span>
                      <ChevronRight className="w-4 h-4" />
                    </span>
                  </div>
                </motion.div>
              );
            })
          )}
        </AnimatePresence>
      </div>

      {/* Pagination macOS Footer */}
      <div className="flex flex-col sm:flex-row justify-between items-center gap-3 pt-3 text-[12px] text-slate-500 font-medium">
        <div>显示 1-{filteredRisks.length} 条，共 1,248 条预警数据</div>
        <div className="flex items-center gap-1 font-mono">
          <button
            disabled={currentPage === 1}
            onClick={() => setCurrentPage(currentPage - 1)}
            className="px-2.5 py-1 bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg disabled:opacity-40 hover:bg-slate-100 cursor-pointer"
          >
            &lt;
          </button>
          <button
            onClick={() => setCurrentPage(1)}
            className={`px-3 py-1 rounded-lg font-bold cursor-pointer ${
              currentPage === 1 ? 'bg-[#007aff] text-white' : 'bg-white dark:bg-slate-800 hover:bg-slate-100'
            }`}
          >
            1
          </button>
          <button
            onClick={() => setCurrentPage(2)}
            className={`px-3 py-1 rounded-lg font-bold cursor-pointer ${
              currentPage === 2 ? 'bg-[#007aff] text-white' : 'bg-white dark:bg-slate-800 hover:bg-slate-100'
            }`}
          >
            2
          </button>
          <button
            onClick={() => setCurrentPage(3)}
            className={`px-3 py-1 rounded-lg font-bold cursor-pointer ${
              currentPage === 3 ? 'bg-[#007aff] text-white' : 'bg-white dark:bg-slate-800 hover:bg-slate-100'
            }`}
          >
            3
          </button>
          <span className="px-1 text-slate-400">...</span>
          <button
            onClick={() => setCurrentPage(currentPage + 1)}
            className="px-2.5 py-1 bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg hover:bg-slate-100 cursor-pointer"
          >
            &gt;
          </button>
        </div>
      </div>
    </div>
  );
};

