import {useEffect, useState} from 'react';
import {motion} from 'motion/react';

interface SystemSplashScreenProps {
  onComplete: () => void;
}

const steps = [
  {label: '系统架构与环境配置校验', detail: 'CN-NORTH-01 节点就绪'},
  {label: 'Gemini AI 风险决策引擎连通', detail: '模型推理 API 正常'},
  {label: '天眼查 API & 司法库直连', detail: '全网监管日志同步中'},
  {label: '构建供应商三级风险图谱', detail: '实时规则校验引擎就绪'},
];

export const SystemSplashScreen = ({onComplete}: SystemSplashScreenProps) => {
  const [progress, setProgress] = useState(15);
  const [currentStepIndex, setCurrentStepIndex] = useState(0);

  useEffect(() => {
    const timers = [
      window.setTimeout(() => { setProgress(38); setCurrentStepIndex(1); }, 600),
      window.setTimeout(() => { setProgress(68); setCurrentStepIndex(2); }, 1300),
      window.setTimeout(() => { setProgress(92); setCurrentStepIndex(3); }, 2100),
      window.setTimeout(() => setProgress(100), 2600),
      window.setTimeout(onComplete, 3100),
    ];
    return () => timers.forEach((timer) => window.clearTimeout(timer));
  }, [onComplete]);

  const step = steps[currentStepIndex];

  return (
    <motion.div
      initial={{opacity: 1}}
      exit={{opacity: 0, scale: 0.98}}
      transition={{duration: 0.4, ease: 'easeInOut'}}
      className="fixed inset-0 z-50 flex flex-col items-center justify-center bg-[#f7f9ff] dark:bg-[#0b131e] text-[#101d28] dark:text-slate-100 select-none p-6"
      role="status"
      aria-live="polite"
      aria-label="正在初始化供应商风险监控平台"
    >
      <div className="w-full max-w-md space-y-8 text-center">
        <motion.div
          initial={{opacity: 0, y: -10}}
          animate={{opacity: 1, y: 0}}
          transition={{duration: 0.5}}
          className="flex flex-col items-center space-y-3"
        >
          <div className="relative flex items-center justify-center w-16 h-16 rounded-2xl bg-[#004782] shadow-lg shadow-[#004782]/25">
            <img src="/logo.svg" alt="SR Monitoring" className="w-16 h-16 rounded-2xl" />
            <motion.div
              className="absolute -top-1 -right-1 w-4 h-4 rounded-full bg-emerald-500 border-2 border-[#f7f9ff] dark:border-[#0b131e]"
              animate={{scale: [1, 1.3, 1], opacity: [0.8, 1, 0.8]}}
              transition={{duration: 1.5, repeat: Infinity, ease: 'easeInOut'}}
            />
          </div>

          <div>
            <h1 className="text-xl font-bold tracking-tight text-[#101d28] dark:text-white">供应商风险智能监控平台</h1>
            <p className="text-xs font-mono text-[#004782] dark:text-blue-400 mt-1 font-semibold">SUPPLIER RISK INTELLIGENCE PLATFORM v2.4</p>
          </div>
        </motion.div>

        <div className="space-y-3 bg-white dark:bg-slate-900 border border-[#c2c6d2] dark:border-slate-800 p-5 rounded-2xl shadow-sm text-left">
          <div className="flex justify-between items-center text-xs font-medium">
            <span className="text-[#424751] dark:text-slate-300 font-bold flex items-center gap-2">
              <span className="relative flex h-2 w-2">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-blue-400 opacity-75" />
                <span className="relative inline-flex rounded-full h-2 w-2 bg-[#004782]" />
              </span>
              {step.label}
            </span>
            <span className="font-mono font-bold text-[#004782] dark:text-blue-400">{progress}%</span>
          </div>

          <div className="w-full bg-[#f0f4fa] dark:bg-slate-800 h-2 rounded-full overflow-hidden p-0.5 border border-slate-200/60 dark:border-slate-700">
            <motion.div
              className="bg-[#004782] dark:bg-blue-500 h-full rounded-full shadow-sm"
              initial={{width: '10%'}}
              animate={{width: `${progress}%`}}
              transition={{duration: 0.5, ease: 'easeOut'}}
            />
          </div>

          <div className="flex justify-between items-center text-[11px] text-slate-500 dark:text-slate-400 pt-1 font-mono">
            <span>状态: {step.detail}</span>
            <span className="text-emerald-600 dark:text-emerald-400 font-bold">节点在线</span>
          </div>
        </div>

        <div className="flex items-center justify-center gap-6 text-[11px] text-[#727782] dark:text-slate-400 font-medium">
          <span className="flex items-center gap-1.5"><span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />Gemini AI 在线</span>
          <span className="flex items-center gap-1.5"><span className="w-1.5 h-1.5 rounded-full bg-blue-500" />天眼查 API 直连</span>
          <span className="flex items-center gap-1.5"><span className="w-1.5 h-1.5 rounded-full bg-cyan-500" />加密通信中</span>
        </div>
      </div>
    </motion.div>
  );
};
