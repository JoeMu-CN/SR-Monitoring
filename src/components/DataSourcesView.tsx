import React, { useState } from 'react';
import { motion } from 'motion/react';
import { DataSource } from '../types';

interface DataSourcesViewProps {
  dataSources: DataSource[];
  onTriggerSync: () => void;
}

export const DataSourcesView: React.FC<DataSourcesViewProps> = ({
  dataSources,
  onTriggerSync,
}) => {
  const [isSyncing, setIsSyncing] = useState(false);

  const handleSyncClick = () => {
    setIsSyncing(true);
    onTriggerSync();
    setTimeout(() => {
      setIsSyncing(false);
    }, 2000);
  };

  return (
    <div className="space-y-6 pb-20 lg:pb-8">
      {/* Title */}
      <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4">
        <div>
          <h1 className="text-2xl font-bold text-[#101d28] dark:text-white tracking-tight">
            数据源与同步状态
          </h1>
          <p className="text-xs text-[#424751] dark:text-slate-400 mt-0.5">
            监控多维数据API接口连通度、延迟及缓存节点运行状态。
          </p>
        </div>

        <button
          onClick={handleSyncClick}
          disabled={isSyncing}
          className="bg-[#004782] hover:bg-[#185fa5] text-white font-bold text-[13px] px-4 py-2 rounded-lg shadow-sm transition-all flex items-center gap-2 disabled:opacity-50"
        >
          <span
            className={`material-symbols-outlined text-[18px] ${
              isSyncing ? 'animate-spin' : ''
            }`}
          >
            sync
          </span>
          <span>{isSyncing ? '数据同步中...' : '立即全量数据同步'}</span>
        </button>
      </div>

      {/* Data Source Cards Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {dataSources.map((ds) => {
          const isWarning = ds.status === 'warning';
          return (
            <div
              key={ds.id}
              className={`bg-white dark:bg-slate-900 border rounded-xl p-5 shadow-2xs space-y-3 relative overflow-hidden ${
                isWarning
                  ? 'border-[#ba1a1a] bg-red-50/20'
                  : 'border-[#c2c6d2] dark:border-slate-800'
              }`}
            >
              {/* Status Header */}
              <div className="flex justify-between items-start">
                <div className="space-y-0.5">
                  <span className="text-[11px] font-bold text-[#727782] uppercase tracking-wider">
                    {ds.type}
                  </span>
                  <h3 className="font-bold text-[16px] text-[#101d28] dark:text-white">
                    {ds.name}
                  </h3>
                </div>

                <span
                  className={`text-[11px] font-bold px-2.5 py-0.5 rounded-full flex items-center gap-1.5 ${
                    isWarning
                      ? 'bg-red-100 text-[#ba1a1a]'
                      : 'bg-emerald-100 text-emerald-800'
                  }`}
                >
                  <div className="relative flex items-center justify-center w-2 h-2">
                    <motion.span
                      className={`absolute inline-flex h-full w-full rounded-full ${
                        isWarning ? 'bg-red-500/60' : 'bg-emerald-500/60'
                      }`}
                      animate={{ scale: [1, 2.2, 1], opacity: [0.8, 0, 0.8] }}
                      transition={{ duration: isWarning ? 1.2 : 2.0, repeat: Infinity, ease: 'easeInOut' }}
                    />
                    <span
                      className={`relative inline-flex rounded-full h-1.5 w-1.5 ${
                        isWarning ? 'bg-[#ba1a1a]' : 'bg-emerald-600'
                      }`}
                    />
                  </div>
                  {ds.latency}
                </span>
              </div>

              {/* Details */}
              <div className="pt-2 border-t border-slate-100 dark:border-slate-800 space-y-1.5 text-[12px]">
                <div className="flex justify-between text-slate-500">
                  <span>最近同步时间:</span>
                  <span className="font-mono font-bold text-slate-700 dark:text-slate-300">
                    {ds.lastSyncTime}
                  </span>
                </div>
                <div className="flex justify-between text-slate-500">
                  <span>已拉取监管记录:</span>
                  <span className="font-mono font-bold text-[#004782]">
                    {ds.itemCount.toLocaleString()} 条
                  </span>
                </div>
              </div>

              {/* Quick Health Status */}
              <div className="bg-[#f7f9ff] dark:bg-slate-800/60 p-2.5 rounded-lg text-[11px] flex justify-between items-center font-medium">
                <span className="text-slate-500">API 响应节点:</span>
                <span className="font-mono text-slate-700 dark:text-slate-300">
                  {isWarning ? '备用节点 (CN-EAST-CACHE)' : '主节点 (CN-NORTH-01)'}
                </span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};
