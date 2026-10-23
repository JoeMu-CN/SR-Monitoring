import {AlertTriangle, CircleHelp, Info, ShieldCheck} from 'lucide-react';
import type {
  MonitoringHealthRead,
  MonitoringOverallStatus,
  MonitoringSourceHealth,
  MonitoringSourceState,
} from '../api';
import type {MonitoringHealthSnapshot} from '../useMonitoringHealth';

const formatHealthTime = (value: string): string => (
  Number.isNaN(new Date(value).getTime()) ? '未知' : new Date(value).toLocaleString('zh-CN', {hour12: false})
);

/**
 * 时长格式化：秒 → 「秒 / 分钟 / 小时 / 天（+ 小时）」。
 * 缺省值（null）只说「未知」，不猜 0 秒；负数按 0 处理，避免出现「-1 秒」。
 */
const formatHealthDuration = (seconds: number | null): string => {
  if (seconds === null || !Number.isFinite(seconds)) return '未知';
  const total = Math.max(0, Math.floor(seconds));
  if (total < 60) return `${total} 秒`;
  if (total < 3600) return `${Math.floor(total / 60)} 分钟`;
  if (total < 86400) return `${Math.floor(total / 3600)} 小时`;
  const days = Math.floor(total / 86400);
  const hours = Math.floor((total % 86400) / 3600);
  return hours > 0 ? `${days} 天 ${hours} 小时` : `${days} 天`;
};

/**
 * 信号处理异常明细（顺序固定：积压 → 上一轮失败 → 分类失败）。
 * 总览横幅只判断「是否存在异常」，调度器实况页横幅展示这些具体原因。
 */
const processingIssues = (processing: MonitoringHealthRead['processing']): string[] => {
  const issues: string[] = [];
  const oldestPending = processing.oldest_pending_age_seconds ?? 0;
  if (processing.total > 0 && oldestPending > 3600) {
    issues.push(`积压 ${processing.total} 条（最旧 ${formatHealthDuration(processing.oldest_pending_age_seconds)}）`);
  }
  if (processing.last_run.status === 'failed') {
    issues.push('上一轮处理失败');
  }
  if (processing.classification_failed > 0) {
    issues.push(`${processing.classification_failed} 条分类失败待复核`);
  }
  return issues;
};

/**
 * 总览 degraded 只报「大类」（顺序固定：调度器 → 信源 → 信号处理），不暴露条数与来源名。
 * 具体细节由对应模块页承担：信源采集细节已合并进信息源页既有的「信息源运行提示」，
 * 不再单独提供横幅；信号处理细节由调度器实况页的 ProcessingHealthDetailBanner 承担。
 * 三类都不成立时兜底说明链路异常，避免出现空原因。
 */
const degradedCategories = (health: MonitoringHealthRead | null): string[] => {
  if (health === null) return ['当前监控链路异常'];
  const categories: string[] = [];

  if (health.scheduler.status === 'stale' || health.scheduler.status === 'unknown') {
    categories.push('当前调度器心跳异常');
  }
  if (health.sources.some((source) => source.state === 'failed' || source.state === 'overdue')) {
    categories.push('当前信源采集有部分异常');
  }
  if (processingIssues(health.processing).length > 0) {
    categories.push('当前信号处理异常');
  }

  return categories.length > 0 ? categories : ['当前监控链路异常'];
};

// 四态文案（计划行213）：空风险时 ok 才允许说「暂无当前风险」，其余状态不得用无风险冒充正常。
interface BannerCopy {
  readonly text: string;
  readonly tone: 'ok' | 'degraded' | 'unknown' | 'inactive';
}

const bannerCopy = (
  overall: MonitoringOverallStatus,
  health: MonitoringHealthRead | null,
  asOfText: string | null,
  hasCurrentRisks: boolean,
): BannerCopy => {
  const freshness = asOfText === null ? '' : ` · 数据截至${asOfText}`;
  switch (overall) {
    case 'ok':
      // ok 且有当前风险：只展示新鲜度，不得用「无风险」句式暗示成功。
      return hasCurrentRisks
        ? {text: `监控正常${freshness}`, tone: 'ok'}
        : {text: `监控正常，截至${asOfText ?? ''}暂无当前风险`, tone: 'ok'};
    case 'degraded':
      // 总览只报异常大类；条数、来源名与积压量等细节下放到对应模块页。
      return {text: `${degradedCategories(health).join('；')}${freshness}`, tone: 'degraded'};
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
  const health = snapshot.status === 'ready' ? snapshot.health : null;
  const overall = health === null ? 'unknown' : health.overall;
  const asOfText = health === null ? null : formatHealthTime(health.as_of);
  const copy = bannerCopy(overall, health, asOfText, hasCurrentRisks);
  const Icon = BANNER_ICONS[copy.tone];
  const styles = BANNER_STYLES[copy.tone];
  return (
    <div
      data-testid="monitoring-health-banner"
      data-state={overall}
      role={copy.tone === 'degraded' ? 'alert' : 'status'}
      className={`flex items-start gap-2 rounded-xl border px-3 py-2 text-[12px] font-medium ${styles.container}`}
    >
      <Icon aria-hidden="true" className="mt-0.5 h-3.5 w-3.5 shrink-0"/>
      {/* min-w-0：多原因长文案在窄屏正常换行，不被 flex 子项撑出容器。 */}
      <span className="min-w-0">{copy.text}</span>
    </div>
  );
};

// 模块页细节横幅与总览 degraded 横幅同款琥珀色容器，保证同一异常的视觉语义一致。
const DEGRADED_DETAIL_CLASSES =
  `flex items-start gap-2 rounded-xl border px-3 py-2 text-[12px] font-medium ${BANNER_STYLES.degraded.container}`;

const DetailBannerIcon = () => (
  <AlertTriangle aria-hidden="true" className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[#D97706]"/>
);

/**
 * 调度器实况页横幅：展示信号处理的具体异常（积压量/上一轮失败/分类失败）。
 * 调度器心跳细节由该页既有心跳区与 StaleNotice 承担，这里不重复。
 */
export const ProcessingHealthDetailBanner = ({snapshot}: {readonly snapshot: MonitoringHealthSnapshot}) => {
  if (snapshot.status !== 'ready') return null;
  const issues = processingIssues(snapshot.health.processing);
  if (issues.length === 0) return null;
  return (
    <div data-testid="processing-health-detail-banner" role="alert" className={DEGRADED_DETAIL_CLASSES}>
      <DetailBannerIcon/>
      <span className="min-w-0">信号处理异常：{issues.join('；')}</span>
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
  on_demand: {label: '核查', tone: 'neutral'},
  invalid_schedule: {label: '调度配置无效', tone: 'warning'},
};

const SOURCE_TONE_CLASSES: Record<(typeof SOURCE_STATE_META)[keyof typeof SOURCE_STATE_META]['tone'], string> = {
  ok: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950/80 dark:text-emerald-300',
  fault: 'bg-red-100 text-[#ba1a1a] dark:bg-red-950/80 dark:text-red-300',
  warning: 'bg-amber-100 text-amber-800 dark:bg-amber-950/80 dark:text-amber-300',
  neutral: 'bg-slate-200 text-slate-700 dark:bg-slate-700 dark:text-slate-300',
};

/**
 * 新鲜度时间行：标签与完整时间戳同属一个 nowrap 组，整行永不折行、时间戳永不拆断。
 * 单行组在信息源名称列的宽度预算内（最宽的「下次预期 + 时间戳」远小于列宽），
 * 因此不需要靠换行或截断来规避溢出——只允许「行」与「行」之间换行。
 */
const FreshnessTimeRow = ({label, value, testId}: {
  readonly label: string;
  readonly value: string;
  readonly testId: string;
}) => (
  <div data-testid={testId} className="min-w-0 font-mono text-slate-500 dark:text-slate-400">
    <span className="whitespace-nowrap">{label} {value}</span>
  </div>
);

interface SourceHealthProps {
  readonly health: MonitoringSourceHealth | undefined;
  readonly onDemand?: boolean;
  readonly labelOverride?: string;
}

/**
 * 展示判定单一来源：徽标（MonitoringSourceStateBadge）与时间行（MonitoringSourceFreshness）
 * 必须共用同一份推导结果，否则 onDemand 覆盖与 labelOverride 会在两处漂移、或重复计算状态。
 * onDemand 把后端 disabled/failed 等状态改判为外部核查语义；labelOverride 只在 on_demand 下生效。
 */
interface SourceHealthPresentation {
  readonly effectiveState: MonitoringSourceState;
  readonly meta: (typeof SOURCE_STATE_META)[MonitoringSourceState];
  readonly label: string;
  readonly lastCheckAt: string | null;
}

const resolveSourceHealthPresentation = (
  health: MonitoringSourceHealth,
  onDemand: boolean,
  labelOverride: string | undefined,
): SourceHealthPresentation => {
  const effectiveState: MonitoringSourceState = onDemand ? 'on_demand' : health.state;
  const meta = SOURCE_STATE_META[effectiveState];
  return {
    effectiveState,
    meta,
    label: effectiveState === 'on_demand' && labelOverride !== undefined ? labelOverride : meta.label,
    lastCheckAt: health.last_attempt_at ?? health.last_success_at,
  };
};

/**
 * 采集状态徽标：信息源页名称块内占「名称正下方」一格，因此盒里只有徽标本身，
 * 故障原因与时间都不并入（并入会把徽标盒撑高，破坏与 LOGO 下缘的对齐）。
 * 无该来源诊断数据时不渲染，绝不伪造采集状态；语义与时间行完全同源。
 * self-start：父级是 column flex，徽标按内容宽度贴左，不被拉成整行宽。
 */
export const MonitoringSourceStateBadge = ({health, onDemand = false, labelOverride}: SourceHealthProps) => {
  if (health === undefined) return null;
  const {meta, label} = resolveSourceHealthPresentation(health, onDemand, labelOverride);
  return (
    <span
      data-testid={`source-health-state-${health.source_id}`}
      className={`self-start shrink-0 rounded-full px-2 py-0.5 text-[10px] font-bold leading-tight ${SOURCE_TONE_CLASSES[meta.tone]}`}
    >
      {label}
    </span>
  );
};

/**
 * 信息源页每来源新鲜度时间行：最后成功、下一预期与失败/超期原因（原因单独成行）。
 * 外部核查来源（onDemand 或服务端 state=on_demand）显示「最近核查真实时间」，不显示下次预期；
 * 外部核查工具停用时后端归为 disabled，但业务语义仍是外部核查来源，不允许展示为「已停用」。
 * 停用与外部核查来源使用中性语义，不出现红色故障；无该来源诊断数据时不渲染。
 *
 * 版面：每行内标签与完整时间不拆行，也不使用「·」分隔符（换行语义已由分行承担）。
 * 唯一调用方是信息源页名称列（徽标已上移到名称块），改动版面不影响总览页横幅语义。
 */
export const MonitoringSourceFreshness = ({health, onDemand = false, labelOverride}: SourceHealthProps) => {
  if (health === undefined) return null;
  const {effectiveState, meta, lastCheckAt} = resolveSourceHealthPresentation(health, onDemand, labelOverride);
  return (
    <div data-testid={`source-health-${health.source_id}`} className="mt-1 space-y-0.5 text-[11px] leading-relaxed">
      {meta.tone === 'fault' && (
        <div className="min-w-0 font-mono text-slate-400 dark:text-slate-400">
          <span className="whitespace-nowrap">原因 {health.reason_code}</span>
        </div>
      )}
      {effectiveState === 'on_demand' ? (
        <FreshnessTimeRow
          testId={`source-health-last-check-${health.source_id}`}
          label="最近核查"
          value={lastCheckAt === null ? '—' : formatHealthTime(lastCheckAt)}
        />
      ) : (
        <>
          <FreshnessTimeRow
            testId={`source-health-last-success-${health.source_id}`}
            label="最近成功"
            value={health.last_success_at === null ? '—' : formatHealthTime(health.last_success_at)}
          />
          <FreshnessTimeRow
            testId={`source-health-next-expected-${health.source_id}`}
            label="下次预期"
            value={health.next_expected_at === null ? '—' : formatHealthTime(health.next_expected_at)}
          />
        </>
      )}
    </div>
  );
};
