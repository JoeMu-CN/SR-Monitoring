import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type PointerEvent as ReactPointerEvent,
} from 'react';
import {createPortal} from 'react-dom';
import {AlertTriangle, BarChart3, X} from 'lucide-react';
import {
  api,
  ApiError,
  type MarketIndicatorItem,
  type MarketIndicatorListResponse,
  type MarketIndicatorPoint,
} from '../api';

/**
 * 行情监控面板（情报聚合页第三个页签）。
 *
 * 产品口径：**只做记录，不做分析**。后端已默认停用阈值判定与风险提醒，前端同步移除
 * 全部判定语义（状态徽标、涨跌、窗口累计、提醒入口），只保留「读数 + 读数时间 + 走势」。
 * 走势仍为内联 SVG，点击后在居中弹窗中按需拉取 90 天序列看完整形态。
 *
 * 视觉上沿用既有面板的组件语言：同款 2xl 圆角卡片、slate 描边/表面、深色等价值、
 * Tailwind 固定色阶（无自定义 token、无图表库），主色 #004782。
 */

/** 卡片内走势只取最近 30 个观察点，避免长时间窗把近期形态压成一条直线。 */
const SPARK_POINTS = 30;
const SPARK_WIDTH = 150;
const SPARK_HEIGHT = 64;
const SPARK_PADDING = 4;

/** 弹窗序列默认回看天数。 */
const SERIES_DAYS = 90;

/** 放大图几何：观察点间距固定，点数多时横向滚动而不是压缩。 */
const CHART_HEIGHT = 300;
const CHART_POINT_SPACING = 28;
const CHART_PADDING = {top: 20, right: 28, bottom: 34, left: 72} as const;
const CHART_MIN_PLOT_WIDTH = 260;
/** 拖动超过该像素才算平移，避免「点击观察点」被识别成拖动。 */
const DRAG_THRESHOLD = 3;

/** 分组顺序固定：先汇率（结算币种口径），再大宗商品，最后半导体行业指数。 */
const GROUPS: ReadonlyArray<{readonly key: string; readonly label: string}> = [
  {key: 'fx-rates', label: '汇率'},
  {key: 'commodity-futures', label: '大宗商品'},
  {key: 'semiconductor-index', label: '半导体'},
];

/** 未登记信源的指标不静默丢弃：单列「其他指标」，避免运营误以为指标不存在。 */
const OTHER_GROUP = {key: 'other', label: '其他指标'} as const;

const formatDateTime = (value: string | null) => (
  value === null ? '—' : new Date(value).toLocaleString('zh-CN', {hour12: false})
);

/** 坐标轴时间更紧凑：只到分钟，避免 90 天窗口的刻度互相挤压。 */
const formatAxisTime = (value: string) => new Date(value).toLocaleString('zh-CN', {
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
});

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

/** 取整到 1/2/5 的十次幂倍，让 Y 轴刻度落在「人读得懂」的数上。 */
const niceStep = (raw: number) => {
  if (!Number.isFinite(raw) || raw <= 0) return 1;
  const exponent = Math.floor(Math.log10(raw));
  const base = 10 ** exponent;
  const normalized = raw / base;
  const factor = normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 5 ? 5 : 10;
  return factor * base;
};

/** Y 轴 3~4 条刻度：以 3 等分取整步长，最多输出 4 条，避免小图被网格压满。 */
const buildTicks = (min: number, max: number): number[] => {
  if (!Number.isFinite(min) || !Number.isFinite(max)) return [];
  if (min === max) return [min];
  const step = niceStep((max - min) / 3);
  const start = Math.floor(min / step) * step;
  const ticks: number[] = [];
  for (let tick = start; tick <= max + step / 2 && ticks.length < 4; tick += step) {
    if (tick >= min - step / 2) ticks.push(Number(tick.toFixed(10)));
  }
  return ticks;
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
      className="h-16 w-[150px] shrink-0 text-[#004782] dark:text-blue-300"
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

interface MarketIndicatorCardProps {
  readonly indicator: MarketIndicatorItem;
  readonly onOpen: (indicator: MarketIndicatorItem, trigger: HTMLButtonElement | null) => void;
}

/** 单指标卡片：只记录「最近读数 + 读数时间 + 走势」，不出现任何判定/涨跌语义。 */
const MarketIndicatorCard = ({indicator, onOpen}: MarketIndicatorCardProps) => {
  const triggerRef = useRef<HTMLButtonElement>(null);
  return (
    <article
      data-testid={`market-indicator-${indicator.code}`}
      className="space-y-2 rounded-2xl border border-slate-200/80 bg-white/80 p-4 shadow-sm backdrop-blur-md dark:border-slate-700/60 dark:bg-slate-800/60"
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0 flex-1">
          <h4 className="min-w-0 text-[14px] font-bold text-slate-900 dark:text-white">{indicator.label}</h4>
          <p className="mt-1 min-w-0 font-mono text-[16px] font-bold text-slate-900 dark:text-white">
            <span data-testid={`market-indicator-value-${indicator.code}`}>{formatValue(indicator.latest.value)}</span>
            <span data-testid={`market-indicator-unit-${indicator.code}`} className="ml-1 text-[11px] font-semibold text-slate-500 dark:text-slate-400">
              {indicator.unit}
            </span>
          </p>
          <p className="mt-0.5 text-[11px] text-slate-500 dark:text-slate-400">
            读数时间
            <span data-testid={`market-indicator-observed-${indicator.code}`} className="ml-1 font-mono">
              {indicator.latest.observed_at === null ? '暂无读数' : formatDateTime(indicator.latest.observed_at)}
            </span>
          </p>
        </div>

        <button
          ref={triggerRef}
          type="button"
          aria-label={`查看 ${indicator.label} 完整走势`}
          aria-haspopup="dialog"
          onClick={() => onOpen(indicator, triggerRef.current)}
          className="group -m-1 inline-flex shrink-0 cursor-pointer items-center rounded-xl p-1 text-[#004782] transition-colors hover:bg-slate-100/80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#004782] focus-visible:ring-offset-1 focus-visible:ring-offset-white motion-reduce:transition-none dark:text-blue-300 dark:hover:bg-slate-700/50 dark:focus-visible:ring-blue-400 dark:focus-visible:ring-offset-slate-800"
        >
          <Sparkline indicator={indicator} />
        </button>
      </div>
    </article>
  );
};

interface MarketIndicatorDialogProps {
  readonly indicator: MarketIndicatorItem;
  readonly onClose: () => void;
}

interface SeriesState {
  readonly points: MarketIndicatorPoint[];
  readonly days: number;
}

/**
 * 放大走势弹窗：打开时按需请求 90 天序列，只记录形态，不给任何涨跌/判定结论。
 * 观察点间距固定（点数多则横向滚动），鼠标可拖拽平移，触屏走原生横向滚动。
 */
const MarketIndicatorDialog = ({indicator, onClose}: MarketIndicatorDialogProps) => {
  const [series, setSeries] = useState<SeriesState | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(true);
  const [retryKey, setRetryKey] = useState(0);
  const [activeIndex, setActiveIndex] = useState<number | null>(null);
  const [viewportWidth, setViewportWidth] = useState(0);
  const [scrollLeft, setScrollLeft] = useState(0);
  const [dragging, setDragging] = useState(false);

  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const requestSequence = useRef(0);
  const dragState = useRef<{startX: number; startScroll: number; moved: boolean} | null>(null);
  /** 遮罩关闭要求「按下与释放都落在遮罩上」，拖拽在遮罩上结束不能误关闭。 */
  const backdropPress = useRef(false);
  const onCloseRef = useRef(onClose);

  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  /** 打开时锁 body 滚动，关闭时恢复原值。 */
  useEffect(() => {
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, []);

  useEffect(() => {
    closeButtonRef.current?.focus();
  }, []);

  useEffect(() => {
    const handleKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape') onCloseRef.current();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, []);

  useEffect(() => {
    const sequence = requestSequence.current + 1;
    requestSequence.current = sequence;
    setLoading(true);
    setError(null);
    setSeries(null);
    setActiveIndex(null);

    void api.market.series(indicator.code, SERIES_DAYS)
      .then((response) => {
        if (requestSequence.current !== sequence) return;
        setSeries({points: response.points, days: response.days});
        setLoading(false);
      })
      .catch((caught: unknown) => {
        if (requestSequence.current !== sequence) return;
        setError(caught instanceof Error ? caught : new Error('走势序列加载失败'));
        setLoading(false);
      });

    return () => {
      if (requestSequence.current === sequence) requestSequence.current += 1;
    };
  }, [indicator.code, retryKey]);

  /** 容器宽度决定放大图是「铺满」还是「按点数撑开 + 横向滚动」。图表在序列到达后才挂载，用回调 ref 测量。 */
  const [chartNode, setChartNode] = useState<HTMLDivElement | null>(null);
  const setChartRef = useCallback((node: HTMLDivElement | null) => {
    scrollRef.current = node;
    setChartNode(node);
  }, []);

  useLayoutEffect(() => {
    if (chartNode === null) return;
    const measure = () => setViewportWidth(chartNode.clientWidth);
    measure();
    window.addEventListener('resize', measure);
    let observer: ResizeObserver | null = null;
    if (typeof ResizeObserver !== 'undefined') {
      observer = new ResizeObserver(measure);
      observer.observe(chartNode);
    }
    return () => {
      window.removeEventListener('resize', measure);
      observer?.disconnect();
    };
  }, [chartNode]);

  const points = series?.points ?? [];
  const plotWidth = Math.max(
    CHART_PADDING.left + CHART_PADDING.right + Math.max(points.length - 1, 1) * CHART_POINT_SPACING,
    CHART_MIN_PLOT_WIDTH,
    viewportWidth,
  );
  const svgWidth = Math.max(plotWidth, 1);
  const innerWidth = Math.max(svgWidth - CHART_PADDING.left - CHART_PADDING.right, 1);
  const innerHeight = CHART_HEIGHT - CHART_PADDING.top - CHART_PADDING.bottom;

  const values = points.map((point) => point.value);
  const rawMin = values.length === 0 ? 0 : Math.min(...values);
  const rawMax = values.length === 0 ? 0 : Math.max(...values);
  const equalPadding = rawMin === rawMax ? (Math.abs(rawMin) || 1) * 0.01 : 0;
  const domainMin = rawMin - equalPadding;
  const domainMax = rawMax + equalPadding;
  const domainSpan = domainMax - domainMin || 1;

  const toX = (index: number) => {
    if (points.length <= 1) return CHART_PADDING.left + innerWidth / 2;
    const spacing = innerWidth / Math.max(points.length - 1, 1);
    return CHART_PADDING.left + index * spacing;
  };
  const toY = (value: number) => (
    CHART_PADDING.top + innerHeight - ((value - domainMin) / domainSpan) * innerHeight
  );

  const polyline = points
    .map((point, index) => `${toX(index).toFixed(2)},${toY(point.value).toFixed(2)}`)
    .join(' ');
  const ticks = buildTicks(domainMin, domainMax);
  const xTickIndexes = points.length <= 1
    ? (points.length === 1 ? [0] : [])
    : [0, Math.floor((points.length - 1) / 2), points.length - 1];

  const handlePointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    // 触屏交给原生横向滚动，避免自定义平移和浏览器手势互相抢。
    if (event.pointerType !== 'mouse') return;
    const node = scrollRef.current;
    if (node === null) return;
    dragState.current = {startX: event.clientX, startScroll: node.scrollLeft, moved: false};
  };

  const handlePointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.pointerType !== 'mouse') return;
    const node = scrollRef.current;
    const state = dragState.current;
    if (node === null || state === null) return;
    const delta = event.clientX - state.startX;
    if (!state.moved && Math.abs(delta) > DRAG_THRESHOLD) {
      state.moved = true;
      setDragging(true);
    }
    if (state.moved) node.scrollLeft = state.startScroll - delta;
  };

  const endDrag = () => {
    dragState.current = null;
    setDragging(false);
  };

  const firstPoint = points[0];
  const lastPoint = points[points.length - 1];
  const activePoint = activeIndex === null ? null : points[activeIndex] ?? null;
  /** tooltip 渲染在滚动容器之外（否则会被 overflow-y-hidden 裁掉），坐标需减去 scrollLeft。 */
  const tooltipLeft = activeIndex === null
    ? 0
    : Math.min(Math.max(toX(activeIndex) - scrollLeft, 72), Math.max(viewportWidth - 72, 72));
  const tooltipTop = activePoint === null ? 0 : Math.max(toY(activePoint.value) - 10, 44);

  return createPortal(
    <div
      className="fixed inset-0 z-[90] flex items-center justify-center overflow-y-auto bg-slate-900/50 p-2 backdrop-blur-sm sm:p-4"
      onPointerDown={(event) => {
        backdropPress.current = event.target === event.currentTarget;
      }}
      onClick={(event) => {
        // 只有「按下与点击」都发生在遮罩本身时才关闭：拖拽结束不应误触关闭。
        const pressedOnBackdrop = backdropPress.current;
        backdropPress.current = false;
        if (event.target !== event.currentTarget) return;
        if (!pressedOnBackdrop) return;
        onClose();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="market-indicator-dialog-title"
        data-testid="market-indicator-dialog"
        className="flex w-[min(880px,calc(100vw-0.5rem))] max-h-[calc(100dvh-1rem)] flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-2xl dark:border-slate-700 dark:bg-slate-900 sm:max-h-[calc(100dvh-2rem)]"
      >
        <div className="flex items-start justify-between gap-3 border-b border-slate-200/80 px-4 py-3 dark:border-slate-700/60">
          <div className="min-w-0">
            <h3
              id="market-indicator-dialog-title"
              className="flex flex-wrap items-baseline gap-1.5 text-[15px] font-bold text-slate-900 dark:text-white"
            >
              <span className="min-w-0 break-words">{indicator.label}</span>
              <span className="text-[11px] font-semibold text-slate-500 dark:text-slate-400">{indicator.unit}</span>
            </h3>
            <p data-testid="market-indicator-dialog-subtitle" className="mt-1 text-[11px] text-slate-500 dark:text-slate-400">
              {loading
                ? `近 ${SERIES_DAYS} 天走势`
                : points.length === 0
                  ? `近 ${SERIES_DAYS} 天 · 0 个观察点`
                  : `${formatDateTime(firstPoint?.observed_at ?? null)} — ${formatDateTime(lastPoint?.observed_at ?? null)} · 共 ${points.length} 个观察点`}
            </p>
          </div>
          <button
            ref={closeButtonRef}
            type="button"
            aria-label="关闭走势弹窗"
            data-testid="market-indicator-dialog-close"
            onClick={onClose}
            className="-mr-1 -mt-1 flex min-h-9 min-w-9 shrink-0 items-center justify-center rounded-xl text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#004782] motion-reduce:transition-none dark:text-slate-400 dark:hover:bg-slate-800 dark:hover:text-slate-100 dark:focus-visible:ring-blue-400"
          >
            <X aria-hidden="true" className="h-4 w-4" />
          </button>
        </div>

        <div className="min-h-0 flex-1 px-3 py-3 sm:px-4">
          {loading ? (
            <div
              role="status"
              data-testid="market-indicator-series-loading"
              className="flex h-[280px] flex-col justify-end gap-2 rounded-xl bg-slate-50 p-4 dark:bg-slate-800/40 sm:h-[320px]"
            >
              <span className="text-[12px] text-slate-500 dark:text-slate-400">正在加载近 {SERIES_DAYS} 天走势…</span>
              <div className="space-y-2" aria-hidden="true">
                <div className="h-2 w-full animate-pulse rounded bg-slate-200 motion-reduce:animate-none dark:bg-slate-700" />
                <div className="h-2 w-4/5 animate-pulse rounded bg-slate-200 motion-reduce:animate-none dark:bg-slate-700" />
                <div className="h-2 w-3/5 animate-pulse rounded bg-slate-200 motion-reduce:animate-none dark:bg-slate-700" />
              </div>
            </div>
          ) : error !== null ? (
            <div
              role="alert"
              data-testid="market-indicator-series-error"
              className="flex h-[280px] flex-col items-center justify-center rounded-xl border border-red-200 bg-red-50 p-6 text-center dark:border-red-900 dark:bg-red-950/30 sm:h-[320px]"
            >
              <AlertTriangle className="h-7 w-7 text-red-500" aria-hidden="true" />
              <p className="mt-2 text-[13px] font-bold text-red-800 dark:text-red-300">走势序列加载失败</p>
              <p className="mt-1 max-w-full break-words text-[11px] text-red-600 dark:text-red-400">
                {error instanceof ApiError && error.status === 403 ? '无权查看行情走势' : error.message}
              </p>
              <button
                type="button"
                onClick={() => setRetryKey((value) => value + 1)}
                className="mt-3 rounded-xl bg-red-600 px-4 py-2 text-[12px] font-bold text-white hover:bg-red-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500 focus-visible:ring-offset-1"
              >
                重试
              </button>
            </div>
          ) : (
            <div className="relative">
              <div
                ref={setChartRef}
                data-testid="market-indicator-dialog-chart"
                onPointerDown={handlePointerDown}
                onPointerMove={handlePointerMove}
                onPointerUp={endDrag}
                onPointerLeave={endDrag}
                onScroll={(event) => setScrollLeft(event.currentTarget.scrollLeft)}
                className={`overflow-x-auto overflow-y-hidden overscroll-x-contain rounded-xl bg-slate-50/70 dark:bg-slate-800/40 ${
                  dragging ? 'cursor-grabbing select-none' : 'cursor-grab'
                }`}
              >
                <div className="relative" style={{width: svgWidth, height: CHART_HEIGHT}}>
                <svg
                  width={svgWidth}
                  height={CHART_HEIGHT}
                  viewBox={`0 0 ${svgWidth} ${CHART_HEIGHT}`}
                  role="img"
                  aria-label={`${indicator.label} 近 ${SERIES_DAYS} 天走势，共 ${points.length} 个观察点`}
                  className="block text-[#004782] dark:text-blue-300"
                >
                  {ticks.map((tick) => {
                    const y = toY(tick);
                    return (
                      <g key={`tick-${tick}`}>
                        <line
                          x1={CHART_PADDING.left}
                          y1={y}
                          x2={svgWidth - CHART_PADDING.right}
                          y2={y}
                          stroke="currentColor"
                          strokeOpacity="0.14"
                          strokeWidth="1"
                        />
                        <text
                          x={CHART_PADDING.left - 8}
                          y={y + 3}
                          textAnchor="end"
                          className="fill-slate-500 text-[10px] dark:fill-slate-400"
                        >
                          {formatValue(tick)}
                        </text>
                      </g>
                    );
                  })}

                  {/* 基线：空数据时同样画一条虚线，给「暂无观察点」一个视觉锚。 */}
                  {points.length === 0 ? (
                    <line
                      x1={CHART_PADDING.left}
                      y1={CHART_PADDING.top + innerHeight / 2}
                      x2={svgWidth - CHART_PADDING.right}
                      y2={CHART_PADDING.top + innerHeight / 2}
                      stroke="currentColor"
                      strokeOpacity="0.35"
                      strokeWidth="1"
                      strokeDasharray="4 4"
                    />
                  ) : null}

                  {points.length >= 2 ? (
                    <polyline
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="1.75"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      points={polyline}
                    />
                  ) : null}

                  {/* 每个观察点一个可见圆点，单点序列同样复用该渲染，不再单独分支。 */}
                  {points.map((point, index) => (
                    <circle
                      key={`dot-${point.observed_at}-${index}`}
                      cx={toX(index)}
                      cy={toY(point.value)}
                      r="2.5"
                      fill="currentColor"
                      strokeWidth="1"
                      className="stroke-slate-50 dark:stroke-slate-800"
                    />
                  ))}

                  {activePoint !== null && activeIndex !== null ? (
                    <>
                      <line
                        x1={toX(activeIndex)}
                        y1={CHART_PADDING.top}
                        x2={toX(activeIndex)}
                        y2={CHART_PADDING.top + innerHeight}
                        stroke="currentColor"
                        strokeOpacity="0.35"
                        strokeWidth="1"
                        strokeDasharray="3 3"
                      />
                      <circle
                        cx={toX(activeIndex)}
                        cy={toY(activePoint.value)}
                        r="4"
                        fill="currentColor"
                        stroke="white"
                        strokeWidth="1.5"
                        className="dark:stroke-slate-900"
                      />
                    </>
                  ) : null}

                  {/* 每个点一个透明命中区：鼠标悬停/触屏点按都可读出该点的时间和数值。 */}
                  {points.map((point, index) => (
                    <circle
                      key={`hit-${point.observed_at}-${index}`}
                      data-testid={`market-indicator-dialog-point-${index}`}
                      cx={toX(index)}
                      cy={toY(point.value)}
                      r="9"
                      fill="transparent"
                      onMouseEnter={() => setActiveIndex(index)}
                      onMouseLeave={() => setActiveIndex((current) => (current === index ? null : current))}
                      onPointerDown={() => setActiveIndex(index)}
                      onFocus={() => setActiveIndex(index)}
                    />
                  ))}

                  {xTickIndexes.map((index) => {
                    const point = points[index];
                    if (point === undefined) return null;
                    const anchor = index === 0 ? 'start' : index === points.length - 1 ? 'end' : 'middle';
                    return (
                      <text
                        key={`x-${point.observed_at}-${index}`}
                        x={toX(index)}
                        y={CHART_HEIGHT - 12}
                        textAnchor={anchor}
                        className="fill-slate-500 text-[10px] dark:fill-slate-400"
                      >
                        {formatAxisTime(point.observed_at)}
                      </text>
                    );
                  })}
                </svg>

                {points.length === 0 ? (
                  <p className="pointer-events-none absolute inset-0 flex items-center justify-center text-[12px] text-slate-500 dark:text-slate-400">
                    暂无观察点
                  </p>
                ) : null}
                </div>
              </div>

              {activeIndex !== null && activePoint !== null ? (
                <div
                  data-testid="market-indicator-dialog-tooltip"
                  className="pointer-events-none absolute z-10 -translate-x-1/2 -translate-y-full whitespace-nowrap rounded-lg bg-slate-900/90 px-2 py-1 text-[11px] leading-tight text-white shadow-lg dark:bg-slate-700"
                  style={{left: tooltipLeft, top: tooltipTop}}
                >
                  <div className="font-mono">{formatDateTime(activePoint.observed_at)}</div>
                  <div className="font-mono font-bold">
                    {formatValue(activePoint.value)}
                    <span className="ml-0.5 font-normal text-slate-300">{indicator.unit}</span>
                  </div>
                </div>
              ) : null}
            </div>
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
};

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
  /** 当前放大查看的指标；为空表示弹窗关闭。 */
  const [openIndicator, setOpenIndicator] = useState<MarketIndicatorItem | null>(null);
  /** 触发折线按钮：弹窗关闭后把焦点交还给它，键盘用户不丢位置。 */
  const triggerRef = useRef<HTMLButtonElement | null>(null);
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

  /** 指标被轮询移除时，弹窗不能指向一个已不存在的数据。 */
  useEffect(() => {
    if (openIndicator === null) return;
    const stillExists = (data?.items ?? []).some((item) => item.code === openIndicator.code);
    if (!stillExists) setOpenIndicator(null);
  }, [data, openIndicator]);

  const activeKey = groups.some((group) => group.key === selectedKey)
    ? selectedKey
    : groups[0]?.key ?? null;

  const handleOpen = useCallback((indicator: MarketIndicatorItem, trigger: HTMLButtonElement | null) => {
    triggerRef.current = trigger;
    setOpenIndicator(indicator);
  }, []);

  const handleClose = useCallback(() => {
    setOpenIndicator(null);
    const trigger = triggerRef.current;
    // 等弹窗卸载后再交还焦点，否则焦点会被随后移除的弹窗元素吃掉。
    window.setTimeout(() => trigger?.focus(), 0);
  }, []);

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
          <p data-testid="market-indicator-footer-note" className="text-[12px] text-slate-500 dark:text-slate-400">
            共 {data?.items.length ?? 0} 个指标，读数生成于 {formatDateTime(data?.generated_at ?? null)}；行情数据仅记录走势，暂不参与风险判定
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
                    <MarketIndicatorCard key={indicator.code} indicator={indicator} onOpen={handleOpen} />
                  ))}
                </div>
              </section>
            );
          })}
        </div>
      )}

      {openIndicator !== null ? (
        <MarketIndicatorDialog indicator={openIndicator} onClose={handleClose} />
      ) : null}
    </div>
  );
};
