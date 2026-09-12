import {AlertTriangle, CircleHelp, Info, ShieldCheck} from 'lucide-react';
import type {MonitoringOverallStatus, MonitoringSourceHealth, MonitoringSourceState} from '../api';
import type {MonitoringHealthSnapshot} from '../useMonitoringHealth';

const formatHealthTime = (value: string): string => (
  Number.isNaN(new Date(value).getTime()) ? '未知' : new Date(value).toLocaleString('zh-CN', {hour12: false})
);

// 四态文案（计划行213）：空风险时 ok 才允许说「暂无当前风险」，其余状态不得用无风险冒充正常。
interface BannerCopy {
  readonly text: string;
  readonly tone: 'ok' | 'degraded' | 'unknown' | 'inactive';
}

const bannerCopy = (overall: MonitoringOverallStatus, asOfText: string | null, hasCurrentRisks: boolean): BannerCopy => {
  const freshness = asOfText === null ? '' : ` · 数据截至${asOfText}`;
  switch (overall) {
    case 'ok':
      // ok 且有当前风险：只展示新鲜度，不得用「无风险」句式暗示成功。
      return hasCurrentRisks
        ? {text: `监控正常${freshness}`, tone: 'ok'}
        : {text: `监控正常，截至${asOfText ?? ''}暂无当前风险`, tone: 'ok'};
    case 'degraded':
      return {text: `部分链路异常，结果可能不完整${freshness}`, tone: 'degraded'};
    case 'unknown':
      // 无论后端返回 unknown 还是诊断请求失败，都只说「无法确认」，不带任何新鲜度断言。
      return {text: '尚无法确认监控状态', tone: 'unknown'};
    case 'inactive':
      return {text: `未开启自动监控${freshness}`, tone: 'inactive'};
  }
};

const BANNER_STYLES: Record<BannerCopy['tone'], {container: string; icon: string}> = {
  ok: {
    container: 'border-emerald-200/80 bg-emerald-50/80 text-emerald-900 dark:border-emerald-800/60 dark:bg-emerald-950/40 dark:text-emerald-200',
    icon: 'text-emerald-600 dark:text-emerald-400',
  },
  degraded: {
    container: 'border-[#D97706]/40 bg-[#fff7ed] text-[#9a3412] dark:border-[#D97706]/30 dark:bg-[#D97706]/10 dark:text-[#fdba74]',
    icon: 'text-[#D97706]',
  },
  unknown: {
    container: 'border-slate-200/80 bg-slate-100/80 text-slate-700 dark:border-slate-700/60 dark:bg-slate-800/60 dark:text-slate-300',
    icon: 'text-slate-500 dark:text-slate-400',
  },
  inactive: {
    container: 'border-slate-200/80 bg-slate-100/80 text-slate-700 dark:border-slate-700/60 dark:bg-slate-800/60 dark:text-slate-300',
    icon: 'text-slate-500 dark:text-slate-400',
  },
};

const BANNER_ICONS: Record<BannerCopy['tone'], typeof ShieldCheck> = {
  ok: ShieldCheck,
  degraded: AlertTriangle,
  unknown: CircleHelp,
  inactive: Info,
};

/**
 * 总览页监控健康横幅：展示 ok / degraded / unknown / inactive 四态与数据新鲜度。
 * 诊断隐藏（403/无权限）与首载中不渲染任何内容。
 */
export const MonitoringHealthBanner = ({snapshot, hasCurrentRisks}: {
  readonly snapshot: MonitoringHealthSnapshot;
  readonly hasCurrentRisks: boolean;
}) => {
  // hidden（403/无权限）与首载中不渲染；诊断失败（unknown）必须渲染「尚无法确认监控状态」。
  if (snapshot.status === 'hidden' || snapshot.status === 'loading') return null;
  const overall = snapshot.status === 'unknown' ? 'unknown' : snapshot.health.overall;
  const asOfText = snapshot.status === 'unknown' ? null : formatHealthTime(snapshot.health.as_of);
  const copy = bannerCopy(overall, asOfText, hasCurrentRisks);
  const Icon = BANNER_ICONS[copy.tone];
  const styles = BANNER_STYLES[copy.tone];
  return (
    <div
      data-testid="monitoring-health-banner"
      data-state={overall}
      role={copy.tone === 'degraded' ? 'alert' : 'status'}
      className={`flex items-center gap-2 rounded-xl border px-3 py-2 text-[12px] font-medium ${styles.container}`}
    >
      <Icon aria-hidden="true" className="h-3.5 w-3.5 shrink-0"/>
      <span>{copy.text}</span>
    </div>
  );
};

// 来源状态展示元数据：Record 全键映射，新增后端枚举会在编译期强制补齐。
const SOURCE_STATE_META: Record<MonitoringSourceState, {label: string; tone: 'ok' | 'fault' | 'warning' | 'neutral'}> = {
  ok: {label: '采集正常', tone: 'ok'},
  failed: {label: '采集失败', tone: 'fault'},
  overdue: {label: '已超期', tone: 'fault'},
  never_run: {label: '尚未运行', tone: 'warning'},
  disabled: {label: '已停用', tone: 'neutral'},
  on_demand: {label: '按需核查', tone: 'neutral'},
  invalid_schedule: {label: '调度配置无效', tone: 'warning'},
};

const SOURCE_TONE_CLASSES: Record<(typeof SOURCE_STATE_META)[keyof typeof SOURCE_STATE_META]['tone'], string> = {
  ok: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950/80 dark:text-emerald-300',
  fault: 'bg-red-100 text-[#ba1a1a] dark:bg-red-950/80 dark:text-red-300',
  warning: 'bg-amber-100 text-amber-800 dark:bg-amber-950/80 dark:text-amber-300',
  neutral: 'bg-slate-200 text-slate-700 dark:bg-slate-700 dark:text-slate-300',
};

/**
 * 数据源页每来源新鲜度：最后成功、下一预期与失败/超期原因。
 * 停用与按需来源使用中性语义，不出现红色故障；无该来源诊断数据时不渲染。
 */
export const MonitoringSourceFreshness = ({health}: {readonly health: MonitoringSourceHealth | undefined}) => {
  if (health === undefined) return null;
  const meta = SOURCE_STATE_META[health.state];
  return (
    <p
      data-testid={`source-health-${health.source_id}`}
      className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px] leading-relaxed"
    >
      <span className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-bold ${SOURCE_TONE_CLASSES[meta.tone]}`}>{meta.label}</span>
      <span className="flex flex-wrap items-center gap-x-2 font-mono text-slate-500 dark:text-slate-400">
        <span className="whitespace-nowrap">最近成功 {health.last_success_at === null ? '—' : formatHealthTime(health.last_success_at)}</span>
        {' · '}
        <span className="whitespace-nowrap">下次预期 {health.next_expected_at === null ? '—' : formatHealthTime(health.next_expected_at)}</span>
      </span>
      {meta.tone === 'fault' && <span className="font-mono text-slate-400">原因 {health.reason_code}</span>}
    </p>
  );
};
