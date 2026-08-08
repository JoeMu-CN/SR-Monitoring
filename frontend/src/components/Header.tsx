import {useState} from 'react';
import {motion} from 'motion/react';
import type {AgentStatusRead, SystemHealth} from '../api';
import type {ActiveTab, RiskItem} from '../types';

interface HeaderProps {
  activeTab: ActiveTab;
  unreadCount: number;
  riskItems: RiskItem[];
  health: SystemHealth | null;
  agentStatus: AgentStatusRead | null;
  onSearch?: (term: string) => void;
}

export const Header = ({activeTab, unreadCount, riskItems, health, agentStatus, onSearch}: HeaderProps) => {
  const [searchTerm, setSearchTerm] = useState('');
  const [showNotifications, setShowNotifications] = useState(false);
  const [showProfile, setShowProfile] = useState(false);
  const currentRisks = riskItems.filter((item) => item.level === 'P1' || item.level === 'P2').slice(0, 5);
  const systemHealthy = health?.status === 'ok' && health.database === 'ok';

  const tabTitles: Record<ActiveTab, string> = {
    overview: '风险总览',
    'current-risks': '当前风险监控',
    'risk-assistant': '风险查询助手',
    suppliers: '供应商列表',
    'data-sources': '数据源与同步状态',
    rules: '规则引擎与沙箱',
  };

  const statusLight = (active: boolean, color: 'emerald' | 'blue' | 'cyan') => {
    const activeClasses = {
      emerald: ['bg-emerald-500/60', 'bg-emerald-500'],
      blue: ['bg-blue-500/60', 'bg-blue-500'],
      cyan: ['bg-cyan-500/60', 'bg-cyan-500'],
    }[color];
    return <div className="relative flex items-center justify-center w-2.5 h-2.5">
      {active && <motion.span className={`absolute inline-flex h-full w-full rounded-full ${activeClasses[0]}`}
        animate={{scale: [1, 2.2, 1], opacity: [0.8, 0, 0.8]}}
        transition={{duration: 1.8, repeat: Infinity, ease: 'easeInOut'}} />}
      <span className={`relative inline-flex rounded-full h-2 w-2 ${active ? activeClasses[1] : 'bg-slate-400'}`} />
    </div>;
  };

  return (
    <header className="bg-white dark:bg-[#101d28] border-b border-[#c2c6d2] dark:border-slate-800 sticky top-0 z-20 px-4 lg:px-8 py-3 transition-all shadow-xs">
      <div className="max-w-[1440px] mx-auto flex items-center justify-between gap-4">
        <div className="flex items-center gap-2">
          <div className="lg:hidden flex items-center gap-2 mr-2">
            <div className="w-8 h-8 rounded-lg bg-[#004782] text-white flex items-center justify-center font-black">
              <span className="material-symbols-outlined text-[18px]">shield_with_house</span>
            </div>
          </div>
          <div>
            <div className="text-[12px] font-bold text-[#185fa5] dark:text-blue-400">SR / 供应商风险监控</div>
            <h1 className="font-bold text-[18px] lg:text-[20px] text-[#101d28] dark:text-white leading-tight">{tabTitles[activeTab]}</h1>
          </div>
        </div>

        <div className="hidden xl:flex items-center gap-3 bg-[#f7f9ff] dark:bg-slate-900 border border-[#c2c6d2] dark:border-slate-800 rounded-xl px-3 py-1.5 text-[11px] shadow-2xs">
          <div className="flex items-center gap-2 pr-2.5 border-r border-[#c2c6d2]/60 dark:border-slate-800">
            {statusLight(Boolean(agentStatus?.llm_configured), 'emerald')}
            <div className="flex flex-col leading-tight">
              <span className="font-bold text-[#101d28] dark:text-slate-200">模型状态</span>
              <span className="text-emerald-600 dark:text-emerald-400 font-medium">{agentStatus?.llm_configured ? agentStatus.model : '演示模型'}</span>
            </div>
          </div>
          <div className="flex items-center gap-2 pr-2.5 border-r border-[#c2c6d2]/60 dark:border-slate-800">
            {statusLight(Boolean(agentStatus?.tyc_enabled), 'blue')}
            <div className="flex flex-col leading-tight">
              <span className="font-bold text-[#101d28] dark:text-slate-200">外部核查状态</span>
              <span className="text-blue-600 dark:text-blue-400 font-medium">{agentStatus?.tyc_enabled ? '天眼查已启用' : '天眼查未启用'}</span>
            </div>
          </div>
          <div className="flex items-center gap-2">
            {statusLight(systemHealthy, 'cyan')}
            <div className="flex flex-col leading-tight">
              <span className="font-bold text-[#101d28] dark:text-slate-200">数据流与监控</span>
              <span className="text-cyan-600 dark:text-cyan-400 font-medium">{systemHealthy ? '本机服务正常' : '状态待确认'}</span>
            </div>
          </div>
        </div>

        <div className="flex items-center gap-3">
          <div className="relative hidden md:block w-64 lg:w-80">
            <span className="material-symbols-outlined absolute left-3 top-1/2 -translate-y-1/2 text-[#727782] text-[20px]">search</span>
            <input type="text" value={searchTerm} onChange={(event) => setSearchTerm(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && searchTerm.trim()) {
                  onSearch?.(`查询与“${searchTerm.trim()}”相关的当前风险和重点供应商。`);
                  setSearchTerm('');
                }
              }}
              placeholder="搜索后按回车询问风险助手..."
              className="w-full bg-[#f7f9ff] dark:bg-slate-800 border border-[#c2c6d2] dark:border-slate-700 rounded-lg pl-9 pr-3 py-1.5 text-[13px] text-[#101d28] dark:text-white focus:outline-none focus:ring-2 focus:ring-[#004782] transition-all" />
          </div>

          <div className="hidden sm:flex items-center gap-2 px-3 py-1.5 rounded-lg text-[12px] font-medium border bg-[#ecf4ff] text-[#004782] border-[#c2c6d2] dark:bg-slate-800 dark:text-blue-300">
            {statusLight(systemHealthy, 'emerald')}
            <span>本地实时模式</span>
          </div>

          <div className="relative">
            <button aria-label="查看风险提醒" onClick={() => {setShowNotifications(!showNotifications); setShowProfile(false);}}
              className="relative p-2 text-[#424751] dark:text-slate-300 hover:bg-[#f7f9ff] dark:hover:bg-slate-800 rounded-lg transition-colors">
              <span className="material-symbols-outlined text-[22px]">notifications</span>
              {unreadCount > 0 && <span className="absolute top-1.5 right-1.5 w-2.5 h-2.5 rounded-full bg-[#C92A2A] ring-2 ring-white" />}
            </button>
            {showNotifications && (
              <div className="absolute right-0 mt-2 w-80 bg-white dark:bg-slate-900 border border-[#c2c6d2] dark:border-slate-700 rounded-xl shadow-xl p-3 z-50 animate-in fade-in zoom-in-95 duration-150">
                <div className="flex justify-between items-center pb-2 border-b border-slate-100 dark:border-slate-800 mb-2">
                  <span className="font-bold text-[14px]">当前高等级风险</span>
                  <span className="text-[11px] bg-[#C92A2A] text-white px-2 py-0.5 rounded-full font-bold">{unreadCount} 条 P1</span>
                </div>
                <div className="space-y-2 max-h-64 overflow-y-auto pr-1">
                  {currentRisks.length === 0 ? <div className="p-4 text-center text-[12px] text-slate-400">暂无 P1/P2 当前风险</div>
                    : currentRisks.map((risk) => <div key={risk.id} className={`${risk.level === 'P1' ? 'bg-red-50 dark:bg-red-950/40' : 'bg-amber-50 dark:bg-amber-950/40'} p-2 rounded-lg text-[12px]`}>
                      <div className={`${risk.level === 'P1' ? 'text-[#C92A2A]' : 'text-[#D97706]'} font-bold flex justify-between gap-2`}>
                        <span>{risk.level} · {risk.companyName}</span><span className="text-[10px] text-slate-500">{risk.updatedTime}</span>
                      </div>
                      <p className="text-slate-600 dark:text-slate-300 mt-1 line-clamp-2">{risk.summary}</p>
                    </div>)}
                </div>
              </div>
            )}
          </div>

          <div className="relative">
            <button aria-label="打开本地系统菜单" onClick={() => {setShowProfile(!showProfile); setShowNotifications(false);}}
              className="w-10 h-10 rounded-full border border-[#c2c6d2] bg-[#004782] text-white font-black hover:ring-2 hover:ring-[#004782] transition-all">SR</button>
            {showProfile && (
              <div className="absolute right-0 mt-2 w-56 bg-white dark:bg-slate-900 border border-[#c2c6d2] dark:border-slate-700 rounded-xl shadow-xl p-3 z-50">
                <div className="pb-2 border-b border-slate-100 dark:border-slate-800 mb-2">
                  <div className="font-bold text-[14px]">本地单用户模式</div>
                  <div className="text-[12px] text-slate-500">仅绑定 127.0.0.1</div>
                </div>
                <a href="/api/docs" target="_blank" rel="noreferrer"
                  className="block w-full text-left px-2 py-1.5 hover:bg-slate-100 dark:hover:bg-slate-800 rounded-lg text-[13px]">打开接口文档</a>
              </div>
            )}
          </div>
        </div>
      </div>
    </header>
  );
};
