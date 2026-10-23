import type {JSX} from 'react';
import {motion} from 'motion/react';

/**
 * 启动开屏：只展示品牌标识与一条不确定态加载进度条。
 * - 不持有定时器、不发起任何请求、不展示任何自检/健康状态项；
 * - AnimatePresence 负责退出动画；减少动态效果由全局 prefers-reduced-motion / .reduce-motion 处理。
 */
export function SystemSplashScreen(): JSX.Element {
  return (
    <motion.div
      initial={{opacity: 0}}
      animate={{opacity: 1}}
      exit={{opacity: 0, scale: 0.98}}
      transition={{duration: 0.3, ease: 'easeInOut'}}
      className="fixed inset-0 z-50 flex flex-col items-center justify-center bg-[#f7f9ff] dark:bg-[#0b131e] text-[#101d28] dark:text-slate-100 select-none p-6"
      role="status"
      aria-live="polite"
      aria-label="正在初始化供应商风险监控平台"
    >
      <div className="flex flex-col items-center gap-5">
        <img src="/logo.svg" alt="SR Monitoring" className="h-16 w-16 rounded-2xl" />
        <div className="text-center">
          <h1 className="text-lg font-bold tracking-tight">供应商风险智能监控平台</h1>
          <p className="mt-1 text-[11px] font-mono font-semibold text-[#004782] dark:text-blue-400">SUPPLIER RISK INTELLIGENCE PLATFORM</p>
        </div>
        <div
          role="progressbar"
          aria-label="正在加载"
          className="mt-1 h-1 w-40 overflow-hidden rounded-full bg-[#dbe4f3] dark:bg-slate-800"
        >
          <motion.div
            className="h-full w-1/3 rounded-full bg-[#007aff]"
            initial={{x: '-100%'}}
            animate={{x: '350%'}}
            transition={{duration: 1.4, repeat: Infinity, ease: 'easeInOut'}}
          />
        </div>
      </div>
    </motion.div>
  );
}
