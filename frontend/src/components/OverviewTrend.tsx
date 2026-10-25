import type {DashboardTrendPoint} from '../api';

/** 总览大屏趋势：按日新增提醒的连续折线，空数据时给占位而不画假曲线。 */

const WIDTH = 600;
const HEIGHT = 160;
const PADDING = 10;

const formatDay = (value: string): string => value.length >= 10 ? value.slice(5) : value;

interface OverviewTrendProps {
  readonly points: readonly DashboardTrendPoint[];
  readonly loading?: boolean;
}

export const OverviewTrend = ({points, loading = false}: OverviewTrendProps) => {
  const total = points.reduce((sum, point) => sum + point.count, 0);
  const max = Math.max(1, ...points.map((point) => point.count));
  const step = points.length > 1 ? (WIDTH - PADDING * 2) / (points.length - 1) : 0;
  const x = (index: number): number => PADDING + index * step;
  const y = (value: number): number => HEIGHT - 22 - (value / max) * (HEIGHT - 46);

  const line = points.map((point, index) => `${index === 0 ? 'M' : 'L'}${x(index).toFixed(1)} ${y(point.count).toFixed(1)}`).join(' ');
  const area = points.length > 0
    ? `${line} L${x(points.length - 1).toFixed(1)} ${HEIGHT - 22} L${x(0).toFixed(1)} ${HEIGHT - 22} Z`
    : '';

  const axisIndexes = points.length > 3
    ? [0, Math.floor((points.length - 1) / 3), Math.floor(((points.length - 1) * 2) / 3), points.length - 1]
    : points.map((_point, index) => index);

  return (
    <section className="flex h-full min-h-0 flex-col rounded-xl border border-sky-400/15 bg-[linear-gradient(180deg,rgba(12,30,54,0.8),rgba(6,14,27,0.6))]">
      <div className="flex items-center justify-between border-b border-sky-400/10 px-3.5 py-2">
        <div className="flex items-baseline gap-2">
          <h2 className="text-[13px] font-bold tracking-[0.1em] text-sky-100">风险新增趋势</h2>
          <span className="text-[9px] tracking-[0.16em] text-sky-500/70">DAILY NEW ALERTS</span>
        </div>
        <span className="font-mono text-[10px] text-sky-300/80">
          {loading ? '同步中…' : `合计 ${total.toLocaleString('zh-CN')} 条`}
        </span>
      </div>

      <div className="relative min-h-[100px] flex-1 px-2 pb-6 pt-2">
        {points.length === 0 ? (
          <div className="grid h-full place-items-center text-[12px] text-sky-300/60">
            {loading ? '正在加载趋势…' : '暂无按日趋势数据'}
          </div>
        ) : (
          <>
            <svg className="h-full w-full" viewBox={`0 0 ${WIDTH} ${HEIGHT}`} preserveAspectRatio="none" role="img" aria-label={`近 ${points.length} 天新增提醒趋势，合计 ${total} 条`}>
              <defs>
                <linearGradient id="overview-trend-fill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#38bdf8" stopOpacity="0.36" />
                  <stop offset="100%" stopColor="#38bdf8" stopOpacity="0" />
                </linearGradient>
              </defs>
              {[0, 1, 2].map((index) => (
                <line
                  key={index}
                  x1={PADDING}
                  y1={34 + index * 34}
                  x2={WIDTH - PADDING}
                  y2={34 + index * 34}
                  stroke="rgba(56,189,248,0.1)"
                  strokeDasharray="2 4"
                />
              ))}
              <path d={area} fill="url(#overview-trend-fill)" />
              <path d={line} fill="none" stroke="#38bdf8" strokeWidth={1.8} strokeLinejoin="round" />
              <circle cx={x(points.length - 1)} cy={y(points[points.length - 1].count)} r={3} fill="#7dd3fc" />
              <circle
                cx={x(points.length - 1)}
                cy={y(points[points.length - 1].count)}
                r={7}
                fill="none"
                stroke="#38bdf8"
                strokeOpacity={0.6}
                className="overview-pulse"
              />
            </svg>
            <div className="absolute inset-x-3 bottom-1 flex justify-between font-mono text-[9px] text-sky-500/70">
              {axisIndexes.map((index) => (
                <span key={index}>{formatDay(points[index]?.date ?? '')}</span>
              ))}
            </div>
          </>
        )}
      </div>
    </section>
  );
};
