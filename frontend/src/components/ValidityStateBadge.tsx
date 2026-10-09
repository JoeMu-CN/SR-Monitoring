import {VALIDITY_STATE_LABELS, type ValidityState} from '../api';

interface ValidityStateBadgeProps {
  readonly state: ValidityState;
}

export const ValidityStateBadge = ({state}: ValidityStateBadgeProps) => (
  <span className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] font-bold ${state === 'active' ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300' : state === 'expired' ? 'bg-slate-200 text-slate-600 dark:bg-slate-700 dark:text-slate-300' : state === 'revoked' ? 'bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300' : state === 'superseded' ? 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300' : state === 'conflicted' ? 'bg-orange-100 text-orange-700 dark:bg-orange-900/40 dark:text-orange-300' : state === 'legacy' ? 'bg-violet-100 text-violet-700 dark:bg-violet-900/40 dark:text-violet-300' : 'bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400'}`}>
    {VALIDITY_STATE_LABELS[state] ?? state}
  </span>
);
