import React, { useState } from 'react';
import { motion } from 'motion/react';
import { RefreshCw, Database, Network, Radio, Activity } from 'lucide-react';
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
  const [testingId, setTestingId] = useState<string | null>(null);

  const handleSyncClick = () => {
    setIsSyncing(true);
    onTriggerSync();
    setTimeout(() => {
      setIsSyncing(false);
    }, 2000);
  };

  const handleTestConnection = (id: string) => {
    setTestingId(id);
    setTimeout(() => {
      setTestingId(null);
    }, 1200);
  };

  const totalRecords = dataSources.reduce((acc, ds) => acc + ds.itemCount, 0);
  const normalCount = dataSources.filter((ds) => ds.status === 'normal').length;
  const warningCount = dataSources.filter((ds) => ds.status === 'warning').length;

  return (
    <div className="space-y-6 pb-20 lg:pb-8">
      {/* Title Header */}
      <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4">
        <div>
          <h1 className="text-xl lg:text-2xl font-black text-slate-900 dark:text-white tracking-tight">
            数据源与同步状态
          </h1>
          <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
            监控多维数据 API 接口连通度、网络延迟、已拉取监管日志及全网缓存节点状态。
          </p>
        </div>

        <button
          onClick={handleSyncClick}
          disabled={isSyncing}
          className="bg-[#007aff] hover:bg-[#0062cc] text-white font-bold text-[13px] px-4 py-2 rounded-xl shadow-xs transition-all flex items-center gap-2 disabled:opacity-50 cursor-pointer"
        >
          <RefreshCw className={`w-4 h-4 ${isSyncing ? 'animate-spin' : ''}`} />
          <span>{isSyncing ? '全量数据同步中...' : '立即全量数据同步'}</span>
        </button>
      </div>

      {/* Summary KPI Bar */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 bg-white/80 dark:bg-slate-800/60 backdrop-blur-md border border-slate-200/80 dark:border-slate-700/60 rounded-2xl p-4 shadow-2xs">
        <div className="space-y-0.5">
          <div className="text-[11px] text-slate-500 font-medium">数据源接入数</div>
          <div className="text-xl font-black font-mono text-slate-900 dark:text-white">{dataSources.length} <span className="text-xs font-normal text-slate-500">个管道</span></div>
        </div>
        <div className="space-y-0.5">
          <div className="text-[11px] text-slate-500 font-medium">正常运行 (Normal)</div>
          <div className="text-xl font-black font-mono text-[#34c759]">{normalCount} <span className="text-xs font-normal text-slate-500">个</span></div>
        </div>
        <div className="space-y-0.5">
          <div className="text-[11px] text-slate-500 font-medium">异常/延迟节点</div>
          <div className="text-xl font-black font-mono text-[#ff3b30]">{warningCount} <span className="text-xs font-normal text-slate-500">个</span></div>
        </div>
        <div className="space-y-0.5">
          <div className="text-[11px] text-slate-500 font-medium">全网拉取监管记录</div>
          <div className="text-xl font-black font-mono text-[#007aff]">{totalRecords.toLocaleString()} <span className="text-xs font-normal text-slate-500">条</span></div>
        </div>
      </div>

      {/* Vertical List View */}
      <div className="bg-white/80 dark:bg-slate-800/60 backdrop-blur-md border border-slate-200/80 dark:border-slate-700/60 rounded-2xl shadow-2xs overflow-hidden">
        {/* Table Header Row */}
        <div className="hidden md:grid grid-cols-12 gap-4 px-5 py-3 bg-slate-100/70 dark:bg-slate-800/80 border-b border-slate-200/80 text-[12px] font-bold text-slate-500 dark:text-slate-400">
          <div className="col-span-4">数据源名称与类型</div>
          <div className="col-span-2">连通状态 / 延迟</div>
          <div className="col-span-2">最近同步时间</div>
          <div className="col-span-2">已拉取监管记录</div>
          <div className="col-span-2 text-right">节点与操作</div>
        </div>

        {/* Vertical List Items */}
        <div className="divide-y divide-slate-100 dark:divide-slate-800/60">
          {dataSources.map((ds) => {
            const isWarning = ds.status === 'warning';
            const isTesting = testingId === ds.id;

            return (
              <motion.div
                key={ds.id}
                whileHover={{ backgroundColor: 'rgba(0, 122, 255, 0.03)' }}
                className={`p-4 sm:px-5 transition-colors ${
                  isWarning ? 'bg-red-50/20 dark:bg-red-950/10' : ''
                }`}
              >
                <div className="grid grid-cols-1 md:grid-cols-12 gap-3 md:gap-4 items-center">
                  {/* Name & Type */}
                  <div className="col-span-12 md:col-span-4 flex items-center gap-3">
                    <div className={`p-2.5 rounded-xl flex items-center justify-center shrink-0 ${
                      isWarning
                        ? 'bg-red-100 text-[#ff3b30] dark:bg-red-950'
                        : 'bg-blue-50 text-[#007aff] dark:bg-slate-800'
                    }`}>
                      {ds.type.includes('API') || ds.type.includes('接口') ? (
                        <Radio className="w-5 h-5" />
                      ) : (
                        <Database className="w-5 h-5" />
                      )}
                    </div>
                    <div>
                      <div className="flex items-center gap-2">
                        <h3 className="font-bold text-[14px] text-slate-900 dark:text-white">
                          {ds.name}
                        </h3>
                        <span className="text-[10px] font-bold px-1.5 py-0.2 rounded bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-400 border border-slate-200 dark:border-slate-700">
                          {ds.type}
                        </span>
                      </div>
                      <p className="text-[11px] text-slate-500 dark:text-slate-400 mt-0.5">
                        标识 ID: <span className="font-mono">{ds.id}</span>
                      </p>
                    </div>
                  </div>

                  {/* Status & Latency with Pulse Light */}
                  <div className="col-span-6 md:col-span-2 flex items-center gap-2">
                    <span
                      className={`text-[11px] font-bold px-2.5 py-1 rounded-full inline-flex items-center gap-1.5 ${
                        isWarning
                          ? 'bg-red-100 text-[#ff3b30] dark:bg-red-950/80 dark:text-red-300'
                          : 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950/80 dark:text-emerald-300'
                      }`}
                    >
                      <div className="relative flex items-center justify-center w-2 h-2">
                        <motion.span
                          className={`absolute inline-flex h-full w-full rounded-full ${
                            isWarning ? 'bg-red-500/60' : 'bg-emerald-500/60'
                          }`}
                          animate={{ scale: [1, 2.2, 1], opacity: [0.8, 0, 0.8] }}
                          transition={{
                            duration: isWarning ? 1.2 : 2.0,
                            repeat: Infinity,
                            ease: 'easeInOut',
                          }}
                        />
                        <span
                          className={`relative inline-flex rounded-full h-1.5 w-1.5 ${
                            isWarning ? 'bg-[#ff3b30]' : 'bg-emerald-600'
                          }`}
                        />
                      </div>
                      <span>{ds.latency}</span>
                    </span>
                  </div>

                  {/* Last Sync Time */}
                  <div className="col-span-6 md:col-span-2 text-[12px] font-mono text-slate-700 dark:text-slate-300">
                    <span className="md:hidden text-slate-400 text-[11px] font-sans mr-1">同步:</span>
                    {ds.lastSyncTime}
                  </div>

                  {/* Fetched Items */}
                  <div className="col-span-6 md:col-span-2 text-[13px] font-mono font-bold text-[#007aff] dark:text-blue-400">
                    <span className="md:hidden text-slate-400 text-[11px] font-sans font-normal mr-1">记录:</span>
                    {ds.itemCount.toLocaleString()} <span className="text-[11px] font-normal text-slate-500">条</span>
                  </div>

                  {/* Node & Action */}
                  <div className="col-span-6 md:col-span-2 flex items-center justify-end gap-2 text-right">
                    <span className="text-[11px] font-mono text-slate-500 hidden xl:inline-block">
                      {isWarning ? 'CN-EAST-CACHE' : 'CN-NORTH-01'}
                    </span>

                    <button
                      onClick={() => handleTestConnection(ds.id)}
                      disabled={isTesting}
                      className="px-2.5 py-1 text-[11px] font-bold rounded-lg border border-slate-200 dark:border-slate-700 hover:bg-slate-100 dark:hover:bg-slate-800 text-[#007aff] dark:text-blue-300 transition-colors flex items-center gap-1 cursor-pointer"
                      title="检测接口连通性"
                    >
                      <Activity className={`w-3.5 h-3.5 ${isTesting ? 'animate-spin' : ''}`} />
                      <span>{isTesting ? '测速中...' : '测试连通'}</span>
                    </button>
                  </div>
                </div>
              </motion.div>
            );
          })}
        </div>
      </div>
    </div>
  );
};


