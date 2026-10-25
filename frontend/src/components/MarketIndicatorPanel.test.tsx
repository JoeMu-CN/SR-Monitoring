import {cleanup, render, screen, waitFor, within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {MemoryRouter} from 'react-router-dom';
import {afterEach, describe, expect, it, vi} from 'vitest';
import {
  api,
  ApiError,
  type IntelligenceReportListResponse,
  type MarketIndicatorItem,
  type MarketIndicatorListResponse,
  type MarketIndicatorPoint,
  type MarketIndicatorSeriesResponse,
  type MarketIndicatorThresholds,
} from '../api';
import {MarketIndicatorPanel} from './MarketIndicatorPanel';

const GENERATED_AT = '2026-10-19T08:00:00Z';

/** 阈值仍随接口下发，但前端已不再据此展示任何判定语义；只用于构造合法响应。 */
const FX_THRESHOLDS: MarketIndicatorThresholds = {
  sustained_change_pct: 3,
  sustained_window: 20,
  shock_change_pct: 1.5,
  shock_window: 5,
  min_observations: 10,
  direction: 'both',
  episode_gap: 5,
  severity_bands: [
    {threshold_pct: 8, severity: 'critical'},
    {threshold_pct: 5, severity: 'high'},
    {threshold_pct: 3, severity: 'medium'},
  ],
};

/** 生成 `count` 个等间隔观察点，base + i*step；跨度固定为分钟级以便时间戳可预测。 */
const buildSeries = (count: number, base: number, step: number) => (
  Array.from({length: count}, (_, index) => ({
    observed_at: new Date(Date.UTC(2026, 9, 19, 8, index)).toISOString(),
    value: base + index * step,
  }))
);

const item = (overrides: Partial<MarketIndicatorItem> & Pick<MarketIndicatorItem, 'code' | 'label' | 'source_code'>): MarketIndicatorItem => ({
  unit: '汇率',
  source_name: '示例行情源',
  latest: {observed_at: '2026-10-19T08:00:00Z', value: 7.1234, change_pct: 0.42},
  stats: {observations: 12, sustained_change_pct: 1.85, shock_max_day_pct: 0.9},
  thresholds: FX_THRESHOLDS,
  status: 'normal',
  series: buildSeries(12, 7.0, 0.01),
  latest_alert_id: null,
  ...overrides,
});

const listResponse = (items: MarketIndicatorItem[]): MarketIndicatorListResponse => ({generated_at: GENERATED_AT, items});

const seriesResponse = (
  indicator: Pick<MarketIndicatorItem, 'code' | 'label' | 'unit'>,
  points: MarketIndicatorPoint[],
): MarketIndicatorSeriesResponse => ({
  code: indicator.code,
  label: indicator.label,
  unit: indicator.unit,
  days: 90,
  points,
});

const emptyIntelligenceResponse: IntelligenceReportListResponse = {items: [], total: 0, offset: 0, limit: 20};

const fxTripped = item({
  code: 'fx-CNY',
  label: '美元兑人民币汇率',
  source_code: 'fx-rates',
  status: 'tripped',
  latest: {observed_at: '2026-10-19T08:00:00Z', value: 7.1234, change_pct: -1.86},
  stats: {observations: 12, sustained_change_pct: -3.42, shock_max_day_pct: 1.9},
  series: buildSeries(30, 7.2, -0.005),
  latest_alert_id: 4821,
});

const fxNormal = item({
  code: 'fx-EUR',
  label: '美元兑欧元汇率',
  source_code: 'fx-rates',
  latest: {observed_at: '2026-10-19T08:00:00Z', value: 0.9128, change_pct: 0.11},
  series: buildSeries(30, 0.91, 0.0002),
});

const copper = item({
  code: 'fut-CU',
  label: '沪铜主力',
  source_code: 'commodity-futures',
  unit: '元/吨',
  latest: {observed_at: '2026-10-19T08:00:00Z', value: 78_450, change_pct: 2.4},
  series: buildSeries(30, 72_000, 210),
  latest_alert_id: 7,
});

const semiconductor = item({
  code: 'semi-980017',
  label: '国证半导体芯片指数',
  source_code: 'semiconductor-index',
  unit: '点位',
  latest: {observed_at: '2026-10-19T08:00:00Z', value: 3120.55, change_pct: -0.8},
  series: buildSeries(30, 3_200, -2.5),
});

/** 与组件同源的数值格式化：断言数据流，而不是硬编码某台机器的 locale 输出。 */
const expectedValue = (value: number) => (
  value.toLocaleString('zh-CN', {minimumFractionDigits: Math.abs(value) < 10 ? 4 : 2, maximumFractionDigits: Math.abs(value) < 10 ? 4 : 2})
);

const renderPanel = (active = true) => render(
  <MemoryRouter initialEntries={['/intelligence']}>
    <MarketIndicatorPanel active={active} />
  </MemoryRouter>,
);

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('行情监控面板：只做记录', () => {
  it('卡片只给读数、单位与读数时间，不再出现状态徽标、涨跌与提醒入口', async () => {
    vi.spyOn(api.market, 'indicators').mockResolvedValue(listResponse([fxTripped, copper]));

    renderPanel();

    await screen.findByText('美元兑人民币汇率');
    expect(screen.getByTestId('market-indicator-value-fx-CNY')).toHaveTextContent(expectedValue(7.1234));
    expect(screen.getByTestId('market-indicator-unit-fx-CNY')).toHaveTextContent('汇率');
    expect(screen.getByTestId('market-indicator-observed-fx-CNY')).toHaveTextContent('2026');

    // 判定与提醒语义全部移除：不再有徽标、单日变化、窗口累计、最近提醒。
    expect(screen.queryByTestId('market-indicator-status-fx-CNY')).not.toBeInTheDocument();
    expect(screen.queryByTestId('market-indicator-day-change-fx-CNY')).not.toBeInTheDocument();
    expect(screen.queryByTestId('market-indicator-window-change-fx-CNY')).not.toBeInTheDocument();
    expect(screen.queryByTestId('market-indicator-alert-fx-CNY')).not.toBeInTheDocument();
    expect(screen.queryByText('触发中')).not.toBeInTheDocument();
    expect(screen.queryByText('数据积累中 3/10')).not.toBeInTheDocument();
    expect(screen.queryByText('正常')).not.toBeInTheDocument();
    expect(screen.queryByText(/最近提醒/)).not.toBeInTheDocument();
  });

  it('无观察点的指标：读数时间显示「暂无读数」，不用当前时间冒充', async () => {
    vi.spyOn(api.market, 'indicators').mockResolvedValue(listResponse([
      item({code: 'fx-GBP', label: '美元兑英镑汇率', source_code: 'fx-rates', latest: {observed_at: null, value: null, change_pct: null}, series: []}),
    ]));

    renderPanel();

    await screen.findByText('美元兑英镑汇率');
    expect(screen.getByTestId('market-indicator-observed-fx-GBP')).toHaveTextContent('暂无读数');
    expect(screen.getByTestId('market-indicator-value-fx-GBP')).toHaveTextContent('—');
  });

  it('页脚说明只声明记录口径，并给出指标数与生成时间', async () => {
    vi.spyOn(api.market, 'indicators').mockResolvedValue(listResponse([fxNormal, copper]));

    renderPanel();

    await screen.findByText('美元兑欧元汇率');
    const footer = screen.getByTestId('market-indicator-footer-note');
    expect(footer).toHaveTextContent('共 2 个指标');
    expect(footer).toHaveTextContent('读数生成于');
    expect(footer).toHaveTextContent('行情数据仅记录走势，暂不参与风险判定');
  });
});

describe('行情监控面板：分组与走势', () => {
  it('分类以页签呈现，顺序固定，默认只显示第一个有数据的分类', async () => {
    vi.spyOn(api.market, 'indicators').mockResolvedValue(listResponse([semiconductor, copper, fxNormal]));

    renderPanel();

    await screen.findByText('美元兑欧元汇率');
    const groupTabs = within(screen.getByTestId('market-indicator-group-tabs')).getAllByRole('tab');
    expect(groupTabs.map((groupTab) => groupTab.getAttribute('aria-label'))).toEqual(['汇率', '大宗商品', '半导体']);
    expect(screen.getByTestId('market-group-tab-fx-rates')).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByTestId('market-group-count-fx-rates')).toHaveTextContent('1');
    expect(screen.getByTestId('market-indicator-group-fx-rates')).toContainElement(screen.getByText('美元兑欧元汇率'));
    // 非当前分类保持挂载但隐藏：一次只展示一类，同时 aria-controls 始终指向存在的面板。
    expect(screen.getByTestId('market-group-panel-commodity-futures')).not.toBeVisible();
    expect(screen.getByTestId('market-group-panel-semiconductor-index')).not.toBeVisible();
    expect(screen.getByTestId('market-group-panel-fx-rates')).toBeVisible();
  });

  it('点击分类页签切换：只替换内容区，aria-selected 与计数同步', async () => {
    const user = userEvent.setup();
    vi.spyOn(api.market, 'indicators').mockResolvedValue(listResponse([semiconductor, copper, fxNormal]));

    renderPanel();

    await screen.findByText('美元兑欧元汇率');
    await user.click(screen.getByTestId('market-group-tab-commodity-futures'));

    expect(screen.getByTestId('market-group-tab-commodity-futures')).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByTestId('market-group-tab-fx-rates')).toHaveAttribute('aria-selected', 'false');
    expect(screen.getByTestId('market-group-panel-commodity-futures')).toContainElement(screen.getByText('沪铜主力'));
    expect(screen.getByTestId('market-group-panel-commodity-futures')).toBeVisible();
    expect(screen.getByTestId('market-group-panel-fx-rates')).not.toBeVisible();

    await user.click(screen.getByTestId('market-group-tab-semiconductor-index'));
    expect(screen.getByTestId('market-group-panel-semiconductor-index')).toContainElement(screen.getByText('国证半导体芯片指数'));
    expect(screen.getByTestId('market-group-panel-semiconductor-index')).toBeVisible();
    expect(screen.getByTestId('market-group-panel-commodity-futures')).not.toBeVisible();
  });

  it('键盘：ArrowRight / ArrowLeft 在分类间循环，Home / End 跳到首尾并移动焦点', async () => {
    const user = userEvent.setup();
    vi.spyOn(api.market, 'indicators').mockResolvedValue(listResponse([semiconductor, copper, fxNormal]));

    renderPanel();

    await screen.findByText('美元兑欧元汇率');
    screen.getByTestId('market-group-tab-fx-rates').focus();

    await user.keyboard('{ArrowRight}');
    expect(screen.getByTestId('market-group-tab-commodity-futures')).toHaveFocus();
    expect(screen.getByTestId('market-group-tab-commodity-futures')).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByTestId('market-group-tab-fx-rates')).toHaveAttribute('tabindex', '-1');

    await user.keyboard('{ArrowLeft}');
    expect(screen.getByTestId('market-group-tab-fx-rates')).toHaveFocus();

    await user.keyboard('{End}');
    expect(screen.getByTestId('market-group-tab-semiconductor-index')).toHaveFocus();
    expect(screen.getByTestId('market-group-tab-semiconductor-index')).toHaveAttribute('aria-selected', 'true');

    // 末个页签继续右移必须循环回首项，不得越界。
    await user.keyboard('{ArrowRight}');
    expect(screen.getByTestId('market-group-tab-fx-rates')).toHaveAttribute('aria-selected', 'true');

    await user.keyboard('{ArrowLeft}');
    expect(screen.getByTestId('market-group-tab-semiconductor-index')).toHaveAttribute('aria-selected', 'true');
  });

  it('默认选中第一个有数据的分类：汇率缺失时落到大宗商品', async () => {
    vi.spyOn(api.market, 'indicators').mockResolvedValue(listResponse([semiconductor, copper]));

    renderPanel();

    await screen.findByText('沪铜主力');
    // 无数据的分类不出现空页签，也不留空白面板。
    expect(screen.queryByTestId('market-group-tab-fx-rates')).not.toBeInTheDocument();
    expect(screen.getByTestId('market-group-tab-commodity-futures')).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByTestId('market-group-panel-semiconductor-index')).not.toBeVisible();
  });

  it('未登记信源的指标进入「其他指标」分类，不静默丢弃', async () => {
    vi.spyOn(api.market, 'indicators').mockResolvedValue(listResponse([
      item({code: 'misc-1', label: '未登记指标', source_code: 'unknown-source'}),
    ]));

    renderPanel();

    await screen.findByText('未登记指标');
    expect(screen.getByTestId('market-group-tab-other')).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByTestId('market-indicator-group-other')).toContainElement(screen.getByText('未登记指标'));
  });

  it('走势为内联 SVG：点数最多取近 30 个观察点，路径不含 NaN', async () => {
    vi.spyOn(api.market, 'indicators').mockResolvedValue(listResponse([
      item({...fxTripped, series: [...fxTripped.series, ...buildSeries(20, 7.0, 0.01)]}),
    ]));

    renderPanel();

    await screen.findByText('美元兑人民币汇率');
    const spark = screen.getByTestId('market-indicator-spark-fx-CNY');
    expect(spark.tagName.toLowerCase()).toBe('svg');
    expect(spark).toHaveAttribute('data-points', '30');
    const points = spark.querySelector('polyline')?.getAttribute('points') ?? '';
    expect(points).not.toBe('');
    expect(points).not.toContain('NaN');
    expect(points.split(' ')).toHaveLength(30);
  });

  it('空序列：仍渲染走势容器但没有折线，不产生 NaN 路径', async () => {
    vi.spyOn(api.market, 'indicators').mockResolvedValue(listResponse([
      item({code: 'fx-GBP', label: '美元兑英镑汇率', source_code: 'fx-rates', series: [], latest: {observed_at: null, value: null, change_pct: null}}),
    ]));

    renderPanel();

    await screen.findByText('美元兑英镑汇率');
    const spark = screen.getByTestId('market-indicator-spark-fx-GBP');
    expect(spark.tagName.toLowerCase()).toBe('svg');
    expect(spark).toHaveAttribute('data-points', '0');
    expect(spark.querySelector('polyline')).toBeNull();
    expect(spark.innerHTML).not.toContain('NaN');
  });

  it('单点序列：退化为一个点标记，不抛除零错误', async () => {
    vi.spyOn(api.market, 'indicators').mockResolvedValue(listResponse([
      item({code: 'fx-AUD', label: '美元兑澳元汇率', source_code: 'fx-rates', series: buildSeries(1, 0.66, 0)}),
    ]));

    renderPanel();

    await screen.findByText('美元兑澳元汇率');
    const spark = screen.getByTestId('market-indicator-spark-fx-AUD');
    expect(spark).toHaveAttribute('data-points', '1');
    expect(spark.querySelector('polyline')).toBeNull();
    expect(spark.querySelector('circle')).not.toBeNull();
    expect(spark.innerHTML).not.toContain('NaN');
  });

  it('全部等值的序列：折线保持水平且仍可见', async () => {
    vi.spyOn(api.market, 'indicators').mockResolvedValue(listResponse([
      item({code: 'fx-CHF', label: '美元兑瑞郎汇率', source_code: 'fx-rates', series: buildSeries(5, 0.88, 0)}),
    ]));

    renderPanel();

    await screen.findByText('美元兑瑞郎汇率');
    const points = screen.getByTestId('market-indicator-spark-fx-CHF').querySelector('polyline')?.getAttribute('points') ?? '';
    const ys = points.split(' ').map((pair) => pair.split(',')[1]);
    expect(new Set(ys).size).toBe(1);
  });
});

describe('行情监控面板：放大走势弹窗', () => {
  const openDialog = async () => {
    const user = userEvent.setup();
    vi.spyOn(api.market, 'indicators').mockResolvedValue(listResponse([fxNormal]));
    const series = vi.spyOn(api.market, 'series').mockResolvedValue(
      seriesResponse(fxNormal, buildSeries(12, 0.9, 0.001)),
    );

    renderPanel();
    await screen.findByText('美元兑欧元汇率');
    const trigger = screen.getByRole('button', {name: '查看 美元兑欧元汇率 完整走势'});
    await user.click(trigger);
    return {user, trigger, series};
  };

  it('点击折线打开弹窗，并按需请求 90 天序列', async () => {
    const {series} = await openDialog();

    const dialog = await screen.findByRole('dialog');
    expect(dialog).toHaveAttribute('aria-modal', 'true');
    expect(dialog).toHaveAttribute('aria-labelledby', 'market-indicator-dialog-title');
    expect(within(dialog).getByText('美元兑欧元汇率')).toBeInTheDocument();
    expect(within(dialog).getByText('汇率')).toBeInTheDocument();
    expect(screen.getByTestId('market-indicator-dialog')).toBe(dialog);

    expect(series).toHaveBeenCalledTimes(1);
    expect(series).toHaveBeenCalledWith('fx-EUR', 90);
    await waitFor(() => expect(screen.getByTestId('market-indicator-dialog-chart')).toBeInTheDocument());
    expect(screen.getByTestId('market-indicator-dialog-subtitle')).toHaveTextContent('共 12 个观察点');
  });

  it('打开期间必须请求序列；加载中有独立占位，不误报空数据', async () => {
    let settle!: (value: MarketIndicatorSeriesResponse) => void;
    vi.spyOn(api.market, 'indicators').mockResolvedValue(listResponse([fxNormal]));
    vi.spyOn(api.market, 'series').mockReturnValue(new Promise<MarketIndicatorSeriesResponse>((resolve) => {
      settle = resolve;
    }));

    const user = userEvent.setup();
    renderPanel();
    await screen.findByText('美元兑欧元汇率');
    await user.click(screen.getByRole('button', {name: '查看 美元兑欧元汇率 完整走势'}));

    expect(await screen.findByTestId('market-indicator-series-loading')).toHaveTextContent('正在加载近 90 天走势');
    expect(screen.queryByText('暂无观察点')).not.toBeInTheDocument();

    settle(seriesResponse(fxNormal, buildSeries(5, 0.9, 0.001)));
    await screen.findByTestId('market-indicator-dialog-chart');
    expect(screen.queryByTestId('market-indicator-series-loading')).not.toBeInTheDocument();
  });

  it('序列请求失败：显示错误与重试按钮，重试后回到图表', async () => {
    vi.spyOn(api.market, 'indicators').mockResolvedValue(listResponse([fxNormal]));
    const series = vi.spyOn(api.market, 'series')
      .mockRejectedValueOnce(new ApiError(500, '走势服务不可用'))
      .mockResolvedValueOnce(seriesResponse(fxNormal, buildSeries(4, 0.9, 0.001)));

    const user = userEvent.setup();
    renderPanel();
    await screen.findByText('美元兑欧元汇率');
    await user.click(screen.getByRole('button', {name: '查看 美元兑欧元汇率 完整走势'}));

    const error = await screen.findByTestId('market-indicator-series-error');
    expect(error).toHaveTextContent('走势序列加载失败');
    expect(error).toHaveTextContent('走势服务不可用');
    expect(screen.queryByTestId('market-indicator-dialog-chart')).not.toBeInTheDocument();

    await user.click(within(error).getByRole('button', {name: '重试'}));
    await screen.findByTestId('market-indicator-dialog-chart');
    expect(series).toHaveBeenCalledTimes(2);
    expect(screen.queryByTestId('market-indicator-series-error')).not.toBeInTheDocument();
  });

  it('空序列：退化为虚线基线并给出「暂无观察点」', async () => {
    vi.spyOn(api.market, 'indicators').mockResolvedValue(listResponse([fxNormal]));
    vi.spyOn(api.market, 'series').mockResolvedValue(seriesResponse(fxNormal, []));

    const user = userEvent.setup();
    renderPanel();
    await screen.findByText('美元兑欧元汇率');
    await user.click(screen.getByRole('button', {name: '查看 美元兑欧元汇率 完整走势'}));

    await screen.findByTestId('market-indicator-dialog-chart');
    expect(screen.getByTestId('market-indicator-dialog-subtitle')).toHaveTextContent('0 个观察点');
    expect(within(screen.getByTestId('market-indicator-dialog-chart')).getByText('暂无观察点')).toBeInTheDocument();
    expect(screen.getByTestId('market-indicator-dialog').querySelectorAll('polyline')).toHaveLength(0);
  });

  it('悬停观察点显示 tooltip：只有时间与数值，不含涨跌幅', async () => {
    const {user} = await openDialog();
    await screen.findByTestId('market-indicator-dialog-chart');

    const point0 = screen.getByTestId('market-indicator-dialog-point-0');
    await user.hover(point0);

    const tooltip = await screen.findByTestId('market-indicator-dialog-tooltip');
    expect(tooltip).toHaveTextContent(expectedValue(0.9));
    expect(tooltip).toHaveTextContent('2026');
    // 只记录不分析：tooltip 不得出现涨跌幅。
    expect(tooltip).not.toHaveTextContent('%');
  });

  it('Esc 关闭弹窗并把焦点交还给触发折线', async () => {
    const {user, trigger} = await openDialog();
    await screen.findByTestId('market-indicator-dialog-chart');

    await user.keyboard('{Escape}');

    await waitFor(() => expect(screen.queryByTestId('market-indicator-dialog')).not.toBeInTheDocument());
    await waitFor(() => expect(trigger).toHaveFocus());
  });

  it('右上关闭按钮关闭弹窗，且焦点回归折线', async () => {
    const {user, trigger} = await openDialog();
    await screen.findByTestId('market-indicator-dialog-chart');

    await user.click(screen.getByTestId('market-indicator-dialog-close'));

    await waitFor(() => expect(screen.queryByTestId('market-indicator-dialog')).not.toBeInTheDocument());
    await waitFor(() => expect(trigger).toHaveFocus());
  });

  it('点击遮罩关闭；点击弹窗内部不关闭', async () => {
    const {user} = await openDialog();
    await screen.findByTestId('market-indicator-dialog-chart');

    const dialog = screen.getByTestId('market-indicator-dialog');
    await user.click(dialog);
    expect(screen.getByTestId('market-indicator-dialog')).toBeInTheDocument();

    // 遮罩是 dialog 的父节点：直接点它的空白区域。
    const backdrop = dialog.parentElement;
    expect(backdrop).not.toBeNull();
    await user.click(backdrop as HTMLElement);
    await waitFor(() => expect(screen.queryByTestId('market-indicator-dialog')).not.toBeInTheDocument());
  });

  it('打开时锁定 body 滚动，关闭后恢复', async () => {
    const {user} = await openDialog();
    await screen.findByTestId('market-indicator-dialog-chart');
    expect(document.body.style.overflow).toBe('hidden');

    await user.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByTestId('market-indicator-dialog')).not.toBeInTheDocument());
    expect(document.body.style.overflow).not.toBe('hidden');
  });
});

describe('行情监控面板：加载 / 错误 / 空态', () => {
  it('加载中：先给状态占位，不闪现空态', async () => {
    // 手工控制 resolve：断言「请求在飞」期间不出现空态/错误态。
    let settle!: (value: MarketIndicatorListResponse) => void;
    const inFlight = new Promise<MarketIndicatorListResponse>((resolve) => {
      settle = resolve;
    });
    vi.spyOn(api.market, 'indicators').mockReturnValue(inFlight);

    renderPanel();

    expect(screen.getByTestId('market-indicator-loading')).toHaveTextContent('正在加载行情指标…');
    expect(screen.queryByText('暂无行情指标')).not.toBeInTheDocument();

    settle(listResponse([fxNormal]));
    await screen.findByText('美元兑欧元汇率');
    expect(screen.queryByTestId('market-indicator-loading')).not.toBeInTheDocument();
  });

  it('接口失败：显示错误面板与重试按钮，重试后恢复数据', async () => {
    const user = userEvent.setup();
    const request = vi.spyOn(api.market, 'indicators')
      .mockRejectedValueOnce(new ApiError(500, '行情指标服务不可用'))
      .mockResolvedValueOnce(listResponse([fxTripped]));

    renderPanel();

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('行情指标加载失败');
    expect(alert).toHaveTextContent('行情指标服务不可用');
    // 失败态不得残留上一次的数据视图。
    expect(screen.queryByText('美元兑人民币汇率')).not.toBeInTheDocument();

    await user.click(within(alert).getByRole('button', {name: '重试'}));

    await screen.findByText('美元兑人民币汇率');
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(request).toHaveBeenCalledTimes(2);
  });

  it('403：给出无权限文案而不是透出后端消息', async () => {
    vi.spyOn(api.market, 'indicators').mockRejectedValue(new ApiError(403, 'forbidden detail'));

    renderPanel();

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('无权查看行情指标');
    expect(alert).not.toHaveTextContent('forbidden detail');
  });

  it('空列表：显示空态而不是错误', async () => {
    vi.spyOn(api.market, 'indicators').mockResolvedValue(listResponse([]));

    renderPanel();

    await screen.findByText('暂无行情指标');
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(screen.queryByTestId('market-indicator-group-tabs')).not.toBeInTheDocument();
    expect(screen.queryByTestId(/^market-indicator-group-(fx-rates|commodity-futures|semiconductor-index|other)$/)).not.toBeInTheDocument();
  });
});

describe('行情监控面板：数据源约束', () => {
  it('只调用行情指标接口，不复用情报接口', async () => {
    const market = vi.spyOn(api.market, 'indicators').mockResolvedValue(listResponse([fxNormal]));
    const series = vi.spyOn(api.market, 'series');
    const intelligence = vi.spyOn(api, 'intelligence').mockResolvedValue(emptyIntelligenceResponse);

    renderPanel();

    await screen.findByText('美元兑欧元汇率');
    expect(market).toHaveBeenCalledTimes(1);
    expect(market).toHaveBeenCalledWith();
    // 序列只在点击折线后按需请求，面板挂载时不预取。
    expect(series).not.toHaveBeenCalled();
    expect(intelligence).not.toHaveBeenCalled();
  });

  it('未选中时面板保持挂载但隐藏：请求已发出、内容仍在 DOM，切页签不重放请求', async () => {
    const request = vi.spyOn(api.market, 'indicators').mockResolvedValue(listResponse([fxNormal]));

    renderPanel(false);

    const panel = screen.getByTestId('intelligence-tabpanel-market');
    expect(panel).toHaveAttribute('hidden');
    expect(panel).toHaveAttribute('role', 'tabpanel');
    await waitFor(() => expect(screen.getByTestId('market-indicator-group-fx-rates')).toBeInTheDocument());
    expect(request).toHaveBeenCalledTimes(1);
  });
});
