import type {JSX} from 'react';
import {motion} from 'motion/react';
import {AlertTriangle, CheckCircle2, LoaderCircle, MinusCircle, XCircle, type LucideIcon} from 'lucide-react';

export type SelfCheckState = 'pending' | 'ok' | 'warn' | 'error' | 'unavailable';

export interface SelfCheckItem {
  readonly id: string;
  readonly label: string;
  readonly detail: string;
  readonly state: SelfCheckState;
}

export interface SystemSplashScreenProps {
  readonly variant: 'full' | 'simple';
  readonly items?: readonly SelfCheckItem[];
}

const STATE_LABELS: Record<SelfCheckState, string> = {
  pending: '检查中',
  ok: '正常',
  warn: '告警',
  error: '错误',
  unavailable: '不可用',
};

// 状态色沿用既有语义：绿(ok)/琥珀(warn)/红(error)/灰(unavailable)，pending 用主色旋转指示。
const STATE_TEXT_CLASS: Record<SelfCheckState, string> = {
  pending: 'text-[#004782] dark:text-blue-400',
  ok: 'text-emerald-600 dark:text-emerald-400',
  warn: 'text-amber-600 dark:text-amber-400',
  error: 'text-[#C92A2A] dark:text-red-400',
  unavailable: 'text-slate-400 dark:text-slate-500',
};

const STATE_ICONS: Record<SelfCheckState, LucideIcon> = {
  pending: LoaderCircle,
  ok: CheckCircle2,
  warn: AlertTriangle,
  error: XCircle,
  unavailable: MinusCircle,
};

function StateIcon({state}: {readonly state: SelfCheckState}): JSX.Element {
  const Icon = STATE_ICONS[state];
  return (
    <span role="img" aria-label={STATE_LABELS[state]} className="inline-flex shrink-0">
      <Icon aria-hidden="true" className={`h-4 w-4 ${STATE_TEXT_CLASS[state]} ${state === 'pending' ? 'animate-spin' : ''}`} />
    </span>
  );
}

/**
 * 纯展示的开屏层：不持有定时器、不发起请求、不伪造状态。
 * - simple：仅品牌加载动画，用于 30 分钟内刷新或身份校验中；
 * - full：逐项展示来自真实后端接口的自检结果与进度。
 * AnimatePresence 负责退出动画；减少动态效果由全局 prefers-reduced-motion / .reduce-motion 样式处理。
 */
export function SystemSplashScreen({variant, items = []}: SystemSplashScreenProps): JSX.Element {
  if (variant === 'simple') {
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
          <div className="flex items-center gap-2 text-xs font-medium text-[#424751] dark:text-slate-400">
            <span role="img" aria-label="加载中" data-testid="splash-loading-indicator" className="inline-flex">
              <LoaderCircle aria-hidden="true" className="h-4 w-4 animate-spin text-[#004782] dark:text-blue-400" />
            </span>
            <span>正在加载…</span>
          </div>
        </div>
      </motion.div>
    );
  }

  const total = items.length > 0 ? items.length : 1;
  const settledCount = items.filter((item) => item.state !== 'pending').length;
  const percent = Math.round((settledCount / total) * 100);

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
      <div className="w-full max-w-lg">
        <header className="mb-6 flex items-center gap-3.5">
          <img src="/logo.svg" alt="SR Monitoring" className="h-12 w-12 rounded-xl shadow-sm" />
          <div>
            <h1 className="text-lg font-bold tracking-tight">供应商风险智能监控平台</h1>
            <p className="mt-0.5 text-[11px] font-mono font-semibold text-[#004782] dark:text-blue-400">SYSTEM SELF-CHECK</p>
          </div>
        </header>

        <section className="rounded-2xl border border-[#c2c6d2] bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
          <div className="mb-3 flex items-center justify-between text-xs font-medium">
            <span className="font-bold text-[#424751] dark:text-slate-300">系统状态自检</span>
            <span className="font-mono font-bold text-[#004782] dark:text-blue-400" data-testid="self-check-percent">{percent}%</span>
          </div>

          <div
            role="progressbar"
            aria-label="自检进度"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={percent}
            className="h-1.5 w-full overflow-hidden rounded-full bg-[#f0f4fa] dark:bg-slate-800"
          >
            <div
              className="h-full w-full origin-left rounded-full bg-[#004782] transition-transform duration-500 ease-out dark:bg-blue-500"
              style={{transform: `scaleX(${percent / 100})`}}
            />
          </div>

          <ul className="mt-3 divide-y divide-slate-100 dark:divide-slate-800/70">
            {items.map((item) => (
              <li key={item.id} className="flex items-center gap-3 py-2.5">
                <StateIcon state={item.state} />
                <div className="min-w-0 flex-1">
                  <p className="text-[13px] font-semibold text-[#101d28] dark:text-slate-100">{item.label}</p>
                  <p className="mt-0.5 truncate text-[11px] text-[#727782] dark:text-slate-400">{item.detail}</p>
                </div>
                <span className={`text-[11px] font-medium ${STATE_TEXT_CLASS[item.state]}`}>{STATE_LABELS[item.state]}</span>
              </li>
            ))}
          </ul>
        </section>
      </div>
    </motion.div>
  );
}
