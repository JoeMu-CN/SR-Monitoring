import React, { useState } from 'react';
import { motion, AnimatePresence } from 'motion/react';
import { RiskItem, RiskLevel } from '../types';

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
    <div className="space-y-6 pb-20 lg:pb-8">
      {/* Page Title */}
      <div>
        <h1 className="text-2xl font-bold text-[#101d28] dark:text-white tracking-tight">
          当前风险监控
        </h1>
        <p className="text-xs text-[#424751] dark:text-slate-400 mt-0.5">
          实时追踪和评估供应链网络中的高危预警及异常信号。
        </p>
      </div>

      {/* Filter Controls Row */}
      <div className="bg-white dark:bg-slate-900 border border-[#c2c6d2] dark:border-slate-800 rounded-xl p-4 space-y-3 shadow-2xs">
        {/* Search & Main Controls */}
        <div className="flex flex-col md:flex-row gap-3">
          <div className="relative flex-1">
            <span className="material-symbols-outlined absolute left-3 top-1/2 -translate-y-1/2 text-[#727782] text-[20px]">
              search
            </span>
            <input
              type="text"
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              placeholder="搜索供应商或事件 (如: 火灾, 拥堵, 失信)..."
              className="w-full bg-[#f7f9ff] dark:bg-slate-800 border border-[#c2c6d2] dark:border-slate-700 rounded-lg pl-9 pr-3 py-2 text-[13px] text-[#101d28] dark:text-white focus:outline-none focus:ring-2 focus:ring-[#004782]"
            />
          </div>

          <div className="flex items-center gap-2">
            <button
              onClick={() => {
                setSearchTerm('');
                setSelectedLevel('all');
                setSelectedCountry('all');
              }}
              className="px-3 py-2 border border-[#c2c6d2] dark:border-slate-700 rounded-lg text-[13px] font-medium text-[#424751] dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-800 transition-colors flex items-center gap-1"
            >
              <span className="material-symbols-outlined text-[18px]">restart_alt</span>
              <span>重置筛选</span>
            </button>
          </div>
        </div>

        {/* Dropdowns & Status Toggle */}
        <div className="flex flex-wrap items-center justify-between gap-3 pt-2 border-t border-slate-100 dark:border-slate-800">
          <div className="flex flex-wrap items-center gap-3 text-[12px]">
            {/* Risk Level */}
            <div className="flex items-center gap-1.5">
              <span className="text-[#727782]">风险等级:</span>
              <select
                value={selectedLevel}
                onChange={(e) => setSelectedLevel(e.target.value)}
                className="bg-[#f7f9ff] dark:bg-slate-800 border border-[#c2c6d2] dark:border-slate-700 rounded-lg px-2.5 py-1 font-bold text-[#004782] focus:outline-none"
              >
                <option value="all">全部</option>
                <option value="P1">P1 严重</option>
                <option value="P2">P2 高风险</option>
                <option value="P3">P3 中度</option>
                <option value="P4">P4 轻微</option>
              </select>
            </div>

            {/* Country / Region */}
            <div className="flex items-center gap-1.5">
              <span className="text-[#727782]">国家/地区:</span>
              <select
                value={selectedCountry}
                onChange={(e) => setSelectedCountry(e.target.value)}
                className="bg-[#f7f9ff] dark:bg-slate-800 border border-[#c2c6d2] dark:border-slate-700 rounded-lg px-2.5 py-1 font-medium focus:outline-none"
              >
                <option value="all">全部</option>
                <option value="中国">中国</option>
                <option value="中国台湾">中国台湾</option>
                <option value="德国">德国</option>
                <option value="埃及">埃及</option>
                <option value="美国">美国</option>
              </select>
            </div>
          </div>

          {/* Status Segmented Toggle: 有效 vs 失效 */}
          <div className="flex bg-[#f7f9ff] dark:bg-slate-800 border border-[#c2c6d2] dark:border-slate-700 rounded-lg p-0.5 text-[12px] font-bold">
            <button
              onClick={() => setStatusFilter('valid')}
              className={`px-3 py-1 rounded-md transition-all ${
                statusFilter === 'valid'
                  ? 'bg-[#004782] text-white shadow-2xs'
                  : 'text-[#727782] hover:text-[#101d28]'
              }`}
            >
              有效预警
            </button>
            <button
              onClick={() => setStatusFilter('invalid')}
              className={`px-3 py-1 rounded-md transition-all ${
                statusFilter === 'invalid'
                  ? 'bg-[#004782] text-white shadow-2xs'
                  : 'text-[#727782] hover:text-[#101d28]'
              }`}
            >
              已归档/失效
            </button>
          </div>
        </div>
      </div>

      {/* Global Risk Index Summary Box */}
      <div className="bg-white dark:bg-slate-900 border border-[#c2c6d2] dark:border-slate-800 rounded-xl p-4 shadow-2xs">
        <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-2 pb-3 border-b border-slate-100 dark:border-slate-800">
          <div className="text-[13px] font-medium text-[#424751] dark:text-slate-400">
            全球供应链网络风险指数
          </div>
          <div className="flex items-center gap-2">
            <span className="font-mono font-bold text-red-600 uppercase text-[13px]">
              Critical High
            </span>
            <span className="bg-red-100 text-red-700 font-bold font-mono text-[14px] px-2 py-0.5 rounded-md">
              87/100
            </span>
          </div>
        </div>

        {/* Risk Index Multi-Segment Bar */}
        <div className="w-full bg-slate-100 dark:bg-slate-800 h-2.5 rounded-full overflow-hidden my-3 flex">
          <div className="bg-[#C92A2A] h-full" style={{ width: '35%' }}></div>
          <div className="bg-[#D97706] h-full" style={{ width: '40%' }}></div>
          <div className="bg-[#2563EB] h-full" style={{ width: '25%' }}></div>
        </div>

        <div className="grid grid-cols-3 text-center divide-x divide-slate-100 dark:divide-slate-800 pt-1">
          <div>
            <div className="text-xl font-bold font-mono text-[#C92A2A]">12</div>
            <div className="text-[11px] text-slate-500">P1 严重</div>
          </div>
          <div>
            <div className="text-xl font-bold font-mono text-[#D97706]">34</div>
            <div className="text-[11px] text-slate-500">P2 高危</div>
          </div>
          <div>
            <div className="text-xl font-bold font-mono text-[#2563EB]">128</div>
            <div className="text-[11px] text-slate-500">P3 警告</div>
          </div>
        </div>
      </div>

      {/* Risk Cards List */}
      <div className="space-y-4">
        <AnimatePresence mode="popLayout">
          {filteredRisks.length === 0 ? (
            <motion.div
              initial={{ opacity: 0, scale: 0.95 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.95 }}
              className="bg-white dark:bg-slate-900 border border-[#c2c6d2] dark:border-slate-800 rounded-xl p-12 text-center"
            >
              <span className="material-symbols-outlined text-[48px] text-slate-300">search_off</span>
              <h3 className="font-bold text-[16px] text-slate-700 dark:text-slate-300 mt-2">
                未找到匹配的风险预警记录
              </h3>
              <p className="text-xs text-slate-400 mt-1">请尝试调整搜索关键词或重置筛选条件。</p>
            </motion.div>
          ) : (
            filteredRisks.map((item) => {
              let badgeBg = 'bg-[#64748B]';
              if (item.level === 'P1') badgeBg = 'bg-[#C92A2A]';
              if (item.level === 'P2') badgeBg = 'bg-[#D97706]';
              if (item.level === 'P3') badgeBg = 'bg-[#2563EB]';

              return (
                <motion.div
                  key={item.id}
                  layout
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, scale: 0.95 }}
                  whileHover={{ y: -3, transition: { duration: 0.15 } }}
                  onClick={() => onSelectRisk(item)}
                  className="bg-white dark:bg-slate-900 border border-[#c2c6d2] dark:border-slate-800 rounded-xl p-4 shadow-2xs hover:border-[#004782] dark:hover:border-blue-500 hover:shadow-md transition-colors cursor-pointer group"
                >
                  <div className="flex justify-between items-start gap-2">
                  <div className="flex items-center gap-2">
                    <span className={`${badgeBg} text-white font-bold text-[11px] px-2 py-0.5 rounded shadow-2xs`}>
                      {item.level} {item.levelName}
                    </span>
                    <h3 className="font-bold text-[16px] text-[#101d28] dark:text-white group-hover:text-[#004782] dark:group-hover:text-blue-400 transition-colors">
                      {item.companyName}
                    </h3>
                  </div>
                  <span className="text-[12px] text-[#727782] font-mono">{item.updatedTime}</span>
                </div>

                {item.location && (
                  <div className="flex items-center gap-1 text-[12px] text-[#424751] dark:text-slate-400 mt-1">
                    <span className="material-symbols-outlined text-[16px]">location_on</span>
                    <span>{item.location}</span>
                  </div>
                )}

                <p className="text-[13px] text-[#424751] dark:text-slate-300 mt-2 leading-relaxed line-clamp-2">
                  {item.summary}
                </p>

                <div className="flex items-center justify-between pt-3 mt-3 border-t border-slate-100 dark:border-slate-800 text-[12px]">
                  <div className="flex items-center gap-2">
                    {item.tags?.map((tag, idx) => (
                      <span
                        key={idx}
                        className="bg-[#f7f9ff] dark:bg-slate-800 text-[#004782] dark:text-blue-300 font-medium px-2 py-0.5 rounded text-[11px] border border-[#c2c6d2]/50"
                      >
                        {tag}
                      </span>
                    ))}
                    {item.aiConfidence && (
                      <span className="text-[#727782] font-mono text-[11px]">
                        AI置信度: <strong>{item.aiConfidence}%</strong>
                      </span>
                    )}
                  </div>

                  <button className="text-[#004782] dark:text-blue-400 font-bold hover:underline flex items-center gap-1">
                    <span>查看详情</span>
                    <span className="material-symbols-outlined text-[16px]">arrow_forward</span>
                  </button>
                </div>
              </motion.div>
            );
          })
        )}
        </AnimatePresence>
      </div>

      {/* Pagination Footer */}
      <div className="flex flex-col sm:flex-row justify-between items-center gap-3 pt-4 border-t border-[#c2c6d2] text-[13px] text-[#424751] dark:text-slate-400">
        <div>显示 1-{filteredRisks.length} 条，共 1,248 条结果</div>
        <div className="flex items-center gap-1 font-mono">
          <button
            disabled={currentPage === 1}
            onClick={() => setCurrentPage(currentPage - 1)}
            className="px-2.5 py-1 border border-[#c2c6d2] rounded-lg disabled:opacity-40 hover:bg-slate-100 dark:hover:bg-slate-800"
          >
            &lt;
          </button>
          <button
            onClick={() => setCurrentPage(1)}
            className={`px-3 py-1 rounded-lg font-bold ${
              currentPage === 1 ? 'bg-[#004782] text-white' : 'hover:bg-slate-100 dark:hover:bg-slate-800'
            }`}
          >
            1
          </button>
          <button
            onClick={() => setCurrentPage(2)}
            className={`px-3 py-1 rounded-lg font-bold ${
              currentPage === 2 ? 'bg-[#004782] text-white' : 'hover:bg-slate-100 dark:hover:bg-slate-800'
            }`}
          >
            2
          </button>
          <button
            onClick={() => setCurrentPage(3)}
            className={`px-3 py-1 rounded-lg font-bold ${
              currentPage === 3 ? 'bg-[#004782] text-white' : 'hover:bg-slate-100 dark:hover:bg-slate-800'
            }`}
          >
            3
          </button>
          <span>...</span>
          <button
            onClick={() => setCurrentPage(125)}
            className="px-3 py-1 rounded-lg hover:bg-slate-100 dark:hover:bg-slate-800"
          >
            125
          </button>
          <button
            onClick={() => setCurrentPage(currentPage + 1)}
            className="px-2.5 py-1 border border-[#c2c6d2] rounded-lg hover:bg-slate-100 dark:hover:bg-slate-800"
          >
            &gt;
          </button>
        </div>
      </div>
    </div>
  );
};
