import {formatProcessingState, formatValidityState} from '../api';

interface ValidityStateBadgeProps {
  /** 生命周期状态；null/undefined 表示生命周期尚未确定，不渲染徽标。 */
  readonly validityState?: string | null;
  /** 处理状态；analyzed 不单独展示，避免与生命周期徽标重复表达。 */
  readonly processingState?: string | null;
}

const BADGE_BASE = 'inline-block shrink-0 rounded-full px-2 py-0.5 align-middle text-[11px] font-bold';

const LIFECYCLE_CLASS: Record<string, string> = {
  active: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300',
  expired: 'bg-slate-200 text-slate-600 dark:bg-slate-700 dark:text-slate-300',
  revoked: 'bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300',
  superseded: 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300',
  conflicted: 'bg-orange-100 text-orange-700 dark:bg-orange-900/40 dark:text-orange-300',
  legacy: 'bg-violet-100 text-violet-700 dark:bg-violet-900/40 dark:text-violet-300',
  pending_classification: 'bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400',
  irrelevant: 'bg-zinc-100 text-zinc-600 dark:bg-zinc-900/40 dark:text-zinc-300',
};

const PROCESSING_CLASS: Record<string, string> = {
  pending_classification: 'bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400',
  analyzed: 'bg-emerald-50 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300',
  filtered: 'bg-zinc-100 text-zinc-600 dark:bg-zinc-900/40 dark:text-zinc-300',
  no_trigger: 'bg-sky-100 text-sky-700 dark:bg-sky-900/40 dark:text-sky-300',
  classification_failed: 'bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300',
};

const FALLBACK_CLASS = 'bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400';

/** 生命周期徽标与处理状态徽标可同时出现；任一为空时该枚徽标不渲染。 */
export const ValidityStateBadge = ({validityState, processingState}: ValidityStateBadgeProps) => {
  const lifecycle = validityState !== null && validityState !== undefined && validityState !== ''
    ? validityState
    : null;
  // 每个处理阶段都单独展示：analyzed 表示已进入事件链，与生命周期（是否在
  // 有效期内）是两个维度，不能互相替代。
  const processing = processingState !== null
    && processingState !== undefined
    && processingState !== ''
    ? processingState
    : null;
  if (lifecycle === null && processing === null) return null;
  return (
    <>
      {lifecycle !== null && (
        <span className={`${BADGE_BASE} mr-1 ${LIFECYCLE_CLASS[lifecycle] ?? FALLBACK_CLASS}`}>
          {formatValidityState(lifecycle)}
        </span>
      )}
      {processing !== null && (
        <span className={`${BADGE_BASE} ${PROCESSING_CLASS[processing] ?? FALLBACK_CLASS}`}>
          {formatProcessingState(processing)}
        </span>
      )}
    </>
  );
};
