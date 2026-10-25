import {useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent} from 'react';
import {AlertTriangle, ArrowDownRight, ArrowUpRight, BarChart3, Bell, Minus} from 'lucide-react';
import {Link} from 'react-router-dom';
import {
  api,
  ApiError,
  type MarketIndicatorItem,
  type MarketIndicatorListResponse,
  type MarketIndicatorPoint,
  type MarketIndicatorStatus,
} from '../api';
import {riskDetailPath} from '../routes';

/**
 * 行情监控面板（情报聚合页第三个页签）。
 *
 * 与情报报告的关键差异：情报面板读的是 `GET /api/v1/intelligence`，本面板只读
 * `GET /api/v1/market/indicators` 的判定摘要。指标越阈由后端生成**供应商风险提醒**，
 * 因此面板只做「读数 + 阈值状态 + 走势 + 最近提醒跳转」，不展示任何情报报告内容，
 * 也不复用 `api.intelligence`（两个数据源的语义、字段与更新节奏都不同）。
 *
 * 视觉上沿用既有情报面板的组件语言：同款 2xl 圆角卡片、slate 描边/表面、深色等价值、
 * Tailwind 固定色阶（无自定义 token、无论图表库）；走势使用内联 SVG 折线。
 */

/** 走势只取最近 30 个观察点，避免长时间窗把近期形态压成一条直线。 */
const SPARK_POINTS = 30;
const SPARK_WIDTH = 120;
const SPARK_HEIGHT = 32;
const SPARK_PADDING = 2;

/** 分组顺序固定：先汇率（结算币种口径），再大宗商品，最后半导体行业指数。 */const GROUPS: ReadonlyArray<{readonly key: string; readonly label: string}> = [
  {key: 'fx-rates', label: '汇率'},
  {key: 'commodity-futures', label: '大宗商品'},
  {key: 'semiconductor-index', label: '半导体'},
];

/** 未登记信源的指标不静默丢弃：单列「其他指标」，避免运营误以为指标不存在。 */
const OTHER_GROUP = {key: 'other', label: '其他指标'} as const;

const formatDateTime = (value: string | null) => (
  value === null ? '—' : new Date(value).toLocaleString('zh-CN', {hour12: false})
);

const groupKeyFor = (sourceCode: string) => (
  GROUPS.some((group) => group.key === sourceCode) ? sourceCode : OTHER_GROUP.key
);

const groupLabelFor = (key: string) => (
  GROUPS.find((group) => group.key === key)?.label ?? OTHER_GROUP.label
);

const groupIndicators = (items: readonly MarketIndicatorItem[]) => {
  const buckets = new Map<string, MarketIndicatorItem[]>();
  for (const item of items) {
    const key = groupKeyFor(item.source_code);
    const bucket = buckets.get(key);
    if (bucket === undefined) buckets.set(key, [item]);
    else bucket.push(item);
  }
  const orderedKeys = [...GROUPS.map((group) => group.key), OTHER_GROUP.key].filter((key) => buckets.has(key));
  return orderedKeys.map((key) => ({key, label: groupLabelFor(key), items: buckets.get(key) ?? []}));
};

/** 汇率是小数量级（0.6~150），大宗商品与指数是大数量级：按量级选小数位，避免出现「7.00」。 */
const formatValue = (value: number | null) => {
  if (value === null) return '—';
  const digits = Math.abs(value) < 10 ? 4 : 2;
  return value.toLocaleString('zh-CN', {minimumFractionDigits: digits, maximumFractionDigits: digits});
};

/** 百分比带显式正负号（`-0.00` 归一为 `0.00`），让方向在文本层也可读。 */
const formatPercent = (value: number | null) => {
  if (value === null) return '—';
  const rounded = Number(value.toFixed(2));
  const normalized = rounded === 0 ? 0 : rounded;
  return `${normalized > 0 ? '+' : ''}${normalized.toFixed(2)}%`;
};

const STATUS_STYLES: Record<MarketIndicatorStatus, string> = {
  tripped: 'bg-[#ffdad6] text-[#93000a] dark:bg-red-950/40 dark:text-red-300',
  normal: 'bg-emerald-50 text-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-200',
  accumulating: 'bg-slate-100 text-slate-600 dark:bg-slate-800/70 dark:text-slate-300',
};

/** 触发中不带数字（已判定）；数据积累中必须带上 x/min，否则运营无法知道还差几个观察点。 */
const statusText = (status: MarketIndicatorStatus, observations: number, minObservations: number) => {
  switch (status) {
    case 'tripped':
      return '触发中';
    case 'normal':
      return '正常';
    case 'accumulating':
      return `数据积累中 ${observations}/${minObservations}`;
  }
};

const StatusBadge = ({indicator}: {readonly indicator: MarketIndicatorItem}) => (
  <span
    data-testid={`market-indicator-status-${indicator.code}`}
    className={`inline-flex shrink-0 items-center rounded-full px-2 py-0.5 text-[10px] font-bold ${STATUS_STYLES[indicator.status]}`}
  >
    {statusText(indicator.status, indicator.stats.observations, indicator.thresholds.min_observations)}
  </span>
);

/**
 * 趋势箭头：按涨跌方向选图标，颜色统一用中性墨色——按 DESIGN.md，风险色只表达风险等级，
 * 涨/跌本身不是风险判定（只有 `status=tripped` 才允许红色语义）。
 */
const ChangeIcon = ({value}: {readonly value: number | null}) => {
  if (value === null) return <Minus className="h-3 w-3" aria-hidden="true" />;
  if (value > 0) return <ArrowUpRight className="h-3 w-3" aria-hidden="true" />;
  if (value < 0) return <ArrowDownRight className="h-3 w-3" aria-hidden="true" />;
  return <Minus className="h-3 w-3" aria-hidden="true" />;
};

/**
 * 内联 SVG 折线（不引入图表库）。退化为三种情形并全部给出确定性几何：
 * 0 点=虚线基线（无 polyline）、1 点=单个圆点（不产生除零）、等值=水平线居中。
 */
const Sparkline = ({indicator}: {readonly indicator: MarketIndicatorItem}) => {
  const points: MarketIndicatorPoint[] = indicator.series.slice(-SPARK_POINTS);
  const values = points.map((point) => point.value);
  const min = values.length === 0 ? 0 : Math.min(...values);
  const max = values.length === 0 ? 0 : Math.max(...values);
  const span = max - min;
  const innerWidth = SPARK_WIDTH - SPARK_PADDING * 2;
  const innerHeight = SPARK_HEIGHT - SPARK_PADDING * 2;
  const toX = (index: number) => (
    values.length <= 1 ? SPARK_WIDTH / 2 : SPARK_PADDING + (index * innerWidth) / (values.length - 1)
  );
  const toY = (value: number) => (
    span === 0 ? SPARK_HEIGHT / 2 : SPARK_PADDING + innerHeight - ((value - min) / span) * innerHeight
  );
  const polyline = values
    .map((value, index) => `${toX(index).toFixed(2)},${toY(value).toFixed(2)}`)
    .join(' ');

  return (
    <svg
      viewBox={`0 0 ${SPARK_WIDTH} ${SPARK_HEIGHT}`}
      preserveAspectRatio="none"
      role="img"
      aria-label={`${indicator.label} 近 ${points.length} 个观察点走势`}
      data-testid={`market-indicator-spark-${indicator.code}`}
      data-points={points.length}
      className="h-8 w-[120px] shrink-0 text-[#004782] dark:text-blue-300"
    >
      {points.length === 0 ? (
        <line
          x1={SPARK_PADDING}
          y1={SPARK_HEIGHT / 2}
          x2={SPARK_WIDTH - SPARK_PADDING}
          y2={SPARK_HEIGHT / 2}
          stroke="currentColor"
          strokeOpacity="0.35"
          strokeWidth="1"
          strokeDasharray="3 3"
        />
      ) : null}
      {points.length >= 2 ? (
        <polyline
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
          points={polyline}
        />
      ) : null}
      {points.length === 1 ? (
        <circle
          cx={toX(0)}
          cy={toY(values[0] ?? 0)}
          r="2"
          fill="currentColor"
        />
      ) : null}
    </svg>
  );
};

const MarketIndicatorCard = ({indicator}: {readonly indicator: MarketIndicatorItem}) => (
  <article
    data-testid={`market-indicator-${indicator.code}`}
    className="space-y-2 rounded-2xl border border-slate-200/80 bg-white/80 p-4 shadow-sm backdrop-blur-md dark:border-slate-700/60 dark:bg-slate-800/60"
  >
    <div className="flex flex-wrap items-center gap-2">
      <h4 className="min-w-0 flex-1 text-[14px] font-bold text-slate-900 dark:text-white">{indicator.label}</h4>
      <StatusBadge indicator={indicator} />
    </div>

    <div className="flex flex-wrap items-end justify-between gap-3">
      <p className="min-w-0 font-mono text-[16px] font-bold text-slate-900 dark:text-white">
        <span data-testid={`market-indicator-value-${indicator.code}`}>{formatValue(indicator.latest.value)}</span>
        <span data-testid={`market-indicator-unit-${indicator.code}`} className="ml-1 text-[11px] font-semibold text-slate-500 dark:text-slate-400">
          {indicator.unit}
        </span>
      </p>
      <Sparkline indicator={indicator} />
    </div>

    <dl className="flex flex-wrap gap-x-4 gap-y-1 text-[12px] text-slate-500 dark:text-slate-400">
      <div className="inline-flex items-center gap-1">
        <dt>单日变化</dt>
        <dd
          data-testid={`market-indicator-day-change-${indicator.code}`}
          className="inline-flex items-center gap-0.5 font-mono font-bold text-slate-700 dark:text-slate-200"
        >
          <ChangeIcon value={indicator.latest.change_pct} />
          {formatPercent(indicator.latest.change_pct)}
        </dd>
      </div>
      <div className="inline-flex items-center gap-1">
        <dt>窗口累计</dt>
        <dd
          data-testid={`market-indicator-window-change-${indicator.code}`}
          className="inline-flex items-center gap-0.5 font-mono font-bold text-slate-700 dark:text-slate-200"
        >
          <ChangeIcon value={indicator.stats.sustained_change_pct} />
          {formatPercent(indicator.stats.sustained_change_pct)}
        </dd>
      </div>
      <div>
        <dt className="inline font-semibold">观察点：</dt>
        <dd className="inline font-mono">{indicator.stats.observations}</dd>
      </div>
    </dl>

    {indicator.latest_alert_id !== null ? (
      <Link
        to={riskDetailPath(String(indicator.latest_alert_id))}
        data-testid={`market-indicator-alert-${indicator.code}`}
        className="inline-flex min-h-11 items-center gap-1 rounded-xl px-1 text-[12px] font-bold text-[#185fa5] underline-offset-2 hover:underline dark:text-blue-300"
      >
        <Bell className="h-3.5 w-3.5" aria-hidden="true" />
        最近提醒（#{indicator.latest_alert_id}）
      </Link>
    ) : null}
  </article>
);

/** 单次行情指标读数加载：与情报面板同样的「序号 + 卸载失效」模式，避免重试竞态回写旧响应。 */
const useMarketIndicators = () => {
  const [data, setData] = useState<MarketIndicatorListResponse | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(true);
  const [retryKey, setRetryKey] = useState(0);
  const requestSequence = useRef(0);

  useEffect(() => {
    const sequence = requestSequence.current + 1;
    requestSequence.current = sequence;
    setLoading(true);
    setError(null);

    void api.market.indicators()
      .then((response) => {
        if (requestSequence.current !== sequence) return;
        setData(response);
        setLoading(false);
      })
      .catch((caught: unknown) => {
        if (requestSequence.current !== sequence) return;
        setError(caught instanceof Error ? caught : new Error('行情指标加载失败'));
        setLoading(false);
      });

    return () => {
      if (requestSequence.current === sequence) requestSequence.current += 1;
    };
  }, [retryKey]);

  const retry = useCallback(() => setRetryKey((value) => value + 1), []);
  return {data, error, loading, retry};
};

/**
 * 行情监控页签面板。`active=false` 时仍挂载但 `hidden`，与情报面板保持同一套
 * tabpanel/aria 关联和「切页签不重放请求」的常驻策略。
 */
export const MarketIndicatorPanel = ({active}: {readonly active: boolean}) => {
  const {data, error, loading, retry} = useMarketIndicators();
  const groups = useMemo(() => groupIndicators(data?.items ?? []), [data]);
  /** 当前选中的分类（分组 key）；为空表示尚未选出第一个有数据的分类。 */
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  /** 每个页签按钮的 DOM 引用，键盘导航时把焦点移到目标页签。 */
  const tabRefs = useRef<Record<string, HTMLButtonElement | null>>({});

  /**
   * 数据会随轮询变化：选中的分类若消失（该组指标清空），回退到第一个可用分类。
   * 派生值 `activeKey` 与 state 同步计算，避免回退时出现一帧空白面板。
   */
  useEffect(() => {
    if (selectedKey !== null && groups.some((group) => group.key === selectedKey)) return;
    setSelectedKey(groups[0]?.key ?? null);
  }, [groups, selectedKey]);

  const activeKey = groups.some((group) => group.key === selectedKey)
    ? selectedKey
    : groups[0]?.key ?? null;

  /** 页签键盘导航：左右方向键循环、Home/End 跳到首尾，切换后焦点跟随目标页签。 */
  const handleGroupKeyDown = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    if (groups.length === 0) return;
    let nextIndex: number | null = null;
    if (event.key === 'ArrowRight') nextIndex = (index + 1) % groups.length;
    else if (event.key === 'ArrowLeft') nextIndex = (index - 1 + groups.length) % groups.length;
    else if (event.key === 'Home') nextIndex = 0;
    else if (event.key === 'End') nextIndex = groups.length - 1;
    if (nextIndex === null) return;
    event.preventDefault();
    const nextGroup = groups[nextIndex];
    if (nextGroup === undefined) return;
    setSelectedKey(nextGroup.key);
    tabRefs.current[nextGroup.key]?.focus();
  };

  return (
    <div
      role="tabpanel"
      id="intelligence-tabpanel-market"
      aria-labelledby="intelligence-tab-market"
      data-testid="intelligence-tabpanel-market"
      hidden={!active}
    >
      {loading ? (
        <div
          role="status"
          data-testid="market-indicator-loading"
          className="rounded-2xl border border-slate-200/80 bg-white/80 p-12 text-center shadow-sm dark:border-slate-700/60 dark:bg-slate-800/60"
        >
          <span className="text-[13px] text-slate-500 dark:text-slate-400">正在加载行情指标…</span>
        </div>
      ) : error !== null ? (
        <div
          role="alert"
          className="rounded-2xl border border-red-200 bg-red-50 p-6 text-center shadow-sm dark:border-red-900 dark:bg-red-950/30"
        >
          <AlertTriangle className="mx-auto h-8 w-8 text-red-500" />
          <h3 className="mt-2 text-[15px] font-bold text-red-800 dark:text-red-300">行情指标加载失败</h3>
          <p className="mt-1 text-xs text-red-600 dark:text-red-400">
            {error instanceof ApiError && error.status === 403 ? '无权查看行情指标' : error.message}
          </p>
          <button type="button" onClick={retry} className="mt-3 rounded-xl bg-red-600 px-4 py-2 text-sm font-bold text-white hover:bg-red-700">
            重试
          </button>
        </div>
      ) : groups.length === 0 ? (
        <div className="rounded-2xl border border-slate-200/80 bg-white/80 p-12 text-center shadow-sm dark:border-slate-700/60 dark:bg-slate-800/60">
          <BarChart3 className="mx-auto h-10 w-10 text-slate-300" />
          <h3 className="mt-3 text-[15px] font-bold text-slate-700 dark:text-slate-300">暂无行情指标</h3>
          <p className="mt-1 text-xs text-slate-400">尚无启用中的行情指标，请先在信息源中启用行情采集。</p>
        </div>
      ) : (
        <div className="space-y-4">
          <p className="text-[12px] text-slate-500 dark:text-slate-400">
            共 {data?.items.length ?? 0} 个指标，读数生成于 {formatDateTime(data?.generated_at ?? null)}
          </p>

          {/*
            内层分类切换（分段控件）：外层是「政策/行业/行情」下划线页签，这里改为分组页签，
            一次只渲染当前分类，避免三组长列表把关键读数淹没。
          */}
          <div
            role="tablist"
            aria-label="指标分类"
            data-testid="market-indicator-group-tabs"
            className="flex flex-wrap gap-1 rounded-2xl border border-slate-200/80 bg-slate-50/80 p-1 dark:border-slate-700/60 dark:bg-slate-800/60"
          >
            {groups.map((group, index) => {
              const selected = group.key === activeKey;
              return (
                <button
                  key={group.key}
                  id={`market-group-tab-${group.key}`}
                  type="button"
                  role="tab"
                  aria-selected={selected}
                  aria-label={group.label}
                  aria-controls={`market-group-panel-${group.key}`}
                  tabIndex={selected ? 0 : -1}
                  data-testid={`market-group-tab-${group.key}`}
                  ref={(node) => {
                    tabRefs.current[group.key] = node;
                  }}
                  onClick={() => setSelectedKey(group.key)}
                  onKeyDown={(event) => handleGroupKeyDown(event, index)}
                  className={`inline-flex min-h-9 items-center whitespace-nowrap rounded-xl px-3 py-1.5 text-[12px] font-bold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#004782] focus-visible:ring-offset-1 focus-visible:ring-offset-slate-50 dark:focus-visible:ring-blue-400 dark:focus-visible:ring-offset-slate-800 ${
                    selected
                      ? 'bg-white text-[#004782] shadow-sm ring-1 ring-slate-200/70 dark:bg-slate-700 dark:text-blue-300 dark:ring-slate-600'
                      : 'text-slate-600 hover:bg-white/70 hover:text-slate-900 dark:text-slate-300 dark:hover:bg-slate-700/60 dark:hover:text-white'
                  }`}
                >
                  <span>{group.label}</span>
                  <span
                    data-testid={`market-group-count-${group.key}`}
                    aria-hidden="true"
                    className="ml-1 font-mono text-[11px] font-semibold text-slate-400 dark:text-slate-500"
                  >
                    {group.items.length}
                  </span>
                </button>
              );
            })}
          </div>

          {/* 三个分类面板都常驻挂载、非当前项 hidden：与外层页签同一套 aria 关联，`aria-controls` 始终指向存在的元素。 */}
          {groups.map((group) => {
            const selected = group.key === activeKey;
            return (
              <section
                key={group.key}
                role="tabpanel"
                id={`market-group-panel-${group.key}`}
                aria-labelledby={`market-group-tab-${group.key}`}
                tabIndex={0}
                hidden={!selected}
                data-testid={`market-group-panel-${group.key}`}
                className="space-y-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#004782]/40 rounded-2xl"
              >
                <div data-testid={`market-indicator-group-${group.key}`} className="space-y-2">
                  {group.items.map((indicator) => (
                    <MarketIndicatorCard key={indicator.code} indicator={indicator} />
                  ))}
                </div>
              </section>
            );
          })}
        </div>
      )}
    </div>
  );
};
