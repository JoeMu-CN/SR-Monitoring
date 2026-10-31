import {cleanup, render, screen, within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {useState} from 'react';
import {afterEach, describe, expect, it, vi} from 'vitest';
import type {RiskEventCluster, RiskEventClusterMember, RiskEventClusterSummary, RiskEventState} from '../api';
import {RiskEventsView, RiskViewModeTabs, type RiskViewMode} from './RiskEventsView';

afterEach(cleanup);

const member = (overrides: Partial<RiskEventClusterMember> & {event_id: number}): RiskEventClusterMember => ({
  summary: `成员事件 ${overrides.event_id}`,
  source_code: 'nmc-weather-bulletin',
  source_title: '中央气象台',
  published_at: '2026-10-22T00:00:00Z',
  valid_from: '2026-10-22T00:00:00Z',
  expires_at: '2026-10-29T00:00:00Z',
  validity_state: 'active',
  supplier_count: 33,
  max_level: 'P2',
  expired_reason: null,
  ...overrides,
});

const cluster = (overrides: Partial<RiskEventCluster> = {}): RiskEventCluster => ({
  cluster_id: 'weather:浙江省宁波市:2026-10-22',
  event_type: 'weather',
  title: '台风“蔷琵”影响浙江沿海',
  region_label: '浙江省宁波市',
  event_count: 11,
  supplier_count: 33,
  max_level: 'P2',
  level_counts: [{level: 'P2', count: 1}, {level: 'P3', count: 10}],
  expires_min: '2026-10-29T00:00:00Z',
  expires_max: '2026-11-01T02:00:00Z',
  status: 'active',
  members: [member({event_id: 6514})],
  ...overrides,
});

const summary: RiskEventClusterSummary = {
  current_events: 22,
  current_clusters: 12,
  current_groups: 44,
  current_alerts: 327,
  current_suppliers: 34,
  as_of: '2026-10-27T00:00:00Z',
};

const renderView = (options: {
  clusters?: RiskEventCluster[];
  total?: number;
  summary?: RiskEventClusterSummary | null;
  loading?: boolean;
  error?: string | null;
  state?: 'current' | 'expired';
} = {}) => {
  const onStateChange = vi.fn();
  const onRetry = vi.fn();
  const onSelectEvent = vi.fn();
  render(
    <RiskEventsView
      state={options.state ?? 'current'}
      onStateChange={onStateChange}
      clusters={options.clusters ?? [cluster()]}
      total={options.total ?? 12}
      summary={options.summary === undefined ? summary : options.summary}
      loading={options.loading ?? false}
      error={options.error ?? null}
      onRetry={onRetry}
      onSelectEvent={onSelectEvent}
    />,
  );
  return {onStateChange, onRetry, onSelectEvent};
};

const renderTabs = (mode: RiskViewMode = 'events') => {
  const onModeChange = vi.fn();
  render(<RiskViewModeTabs mode={mode} onModeChange={onModeChange} />);
  return {onModeChange};
};

/** 受控页签的状态化外壳：键盘导航用例需要真实回流，才能验证焦点与激活态一起移动。 */
const StatefulTabs = () => {
  const [mode, setMode] = useState<RiskViewMode>('events');
  return <RiskViewModeTabs mode={mode} onModeChange={setMode} />;
};

/** 三个不同等级、类型、地区与信源的簇，用于筛选组合用例。 */
const filterClusters = (): RiskEventCluster[] => [
  cluster({
    cluster_id: 'weather:浙江省宁波市:2026-10-22',
    title: '台风“蔷琵”影响浙江沿海',
    event_type: 'weather',
    region_label: '浙江省宁波市',
    max_level: 'P2',
    members: [member({event_id: 6514, summary: '台风蓝色预警信号', source_title: '中央气象台'})],
  }),
  cluster({
    cluster_id: 'trade_policy:上海市:2026-10-20',
    title: '出口管制清单更新',
    event_type: 'trade_policy',
    region_label: '上海市',
    max_level: 'P1',
    members: [member({event_id: 7001, summary: '商务部公布新一批出口管制清单', source_title: '商务部'})],
  }),
  cluster({
    cluster_id: 'logistics:江苏省苏州市:2026-10-18',
    title: '苏州港拥堵加剧',
    event_type: 'logistics',
    region_label: '江苏省苏州市',
    max_level: 'P3',
    members: [member({event_id: 7100, summary: '港口拥堵待泊时间延长', source_title: '交通运输部'})],
  }),
];

/** 受控口径的状态化外壳：重置用例需要真实回流，才能验证按钮复位到「当前有效」。 */
const StatefulEventsView = ({onStateChange}: {readonly onStateChange?: (state: RiskEventState) => void}) => {
  const [state, setState] = useState<RiskEventState>('current');
  return (
    <RiskEventsView
      state={state}
      onStateChange={(next) => {
        onStateChange?.(next);
        setState(next);
      }}
      clusters={filterClusters()}
      total={12}
      summary={summary}
      loading={false}
      error={null}
      onRetry={() => {}}
      onSelectEvent={() => {}}
    />
  );
};

const searchBox = () => screen.getByLabelText('搜索风险事件');

describe('RiskEventsView', () => {
  it('只保留筛选栏、统计条与列表，不再自带标题和视图切换控件', () => {
    renderView();

    // 标题与页签由 App 在 /risks 层统一渲染，视图内不得重复。
    expect(screen.queryByRole('heading', {name: '风险事件'})).not.toBeInTheDocument();
    expect(screen.queryByRole('tab')).not.toBeInTheDocument();

    const stats = screen.getByText('风险事件统计').closest('section');
    expect(stats).not.toBeNull();
    for (const [label, value] of [['当前事件', '22'], ['事件簇', '12'], ['供应商', '34'], ['风险组', '44']] as const) {
      expect(within(stats as HTMLElement).getByText(label)).toBeInTheDocument();
      expect(within(stats as HTMLElement).getByText(value)).toBeInTheDocument();
    }
    expect(screen.getByText('台风“蔷琵”影响浙江沿海')).toBeInTheDocument();
  });

  it('当前与已失效切换由外部状态决定，点击只回调口径', async () => {
    const {onStateChange} = renderView();
    const stateGroup = screen.getByRole('group', {name: '有效期口径'});
    expect(stateGroup).toBeInTheDocument();
    expect(screen.getByRole('button', {name: '当前有效'})).toHaveAttribute('aria-pressed', 'true');

    await userEvent.setup().click(screen.getByRole('button', {name: '已失效'}));
    expect(onStateChange).toHaveBeenCalledWith('expired');
  });

  it('部分失效的事件簇显示「部分失效」徽标', () => {
    renderView({clusters: [cluster({status: 'partially_expired'})]});
    expect(screen.getByText('部分失效')).toBeInTheDocument();
  });

  it('全部失效的事件簇显示「已失效」徽标', () => {
    renderView({clusters: [cluster({status: 'expired'})], state: 'expired'});
    // 口径切换按钮也叫「已失效」，这里只断言卡片内的徽标。
    expect(within(screen.getByRole('article')).getByText('已失效')).toBeInTheDocument();
  });

  it('展开成员后按信源分组展示，点击成员进入事件详情', async () => {
    const {onSelectEvent} = renderView({
      clusters: [cluster({
        members: [
          member({event_id: 6514, summary: '台风蓝色预警信号'}),
          member({event_id: 6515, summary: '沿海大风预报', source_code: 'cma-news', source_title: '中国天气网'}),
        ],
      })],
    });

    // 折叠态不渲染成员内容
    expect(screen.queryByText('台风蓝色预警信号')).not.toBeInTheDocument();

    await userEvent.setup().click(screen.getByRole('button', {name: '展开成员（2）'}));

    expect(screen.getByText('台风蓝色预警信号')).toBeInTheDocument();
    expect(screen.getByText('中央气象台（1）')).toBeInTheDocument();
    expect(screen.getByText('中国天气网（1）')).toBeInTheDocument();
    expect(screen.getByRole('button', {name: '收起成员'})).toBeInTheDocument();

    await userEvent.setup().click(screen.getByRole('button', {name: /沿海大风预报/}));
    expect(onSelectEvent).toHaveBeenCalledWith(6515);
  });

  it('已失效口径无数据时显示空态而不是错误态', () => {
    renderView({clusters: [], total: 0, state: 'expired', summary: null});
    expect(screen.getByText('暂无已失效的风险事件')).toBeInTheDocument();
    expect(screen.queryByText('风险事件加载失败')).not.toBeInTheDocument();
  });

  it('加载失败时显示错误信息，并提供重试入口', async () => {
    const {onRetry} = renderView({clusters: [], total: 0, error: '事件簇服务不可用'});
    expect(screen.getByText('风险事件加载失败')).toBeInTheDocument();
    expect(screen.getByText('事件簇服务不可用')).toBeInTheDocument();

    await userEvent.setup().click(screen.getByRole('button', {name: /重新加载/}));
    expect(onRetry).toHaveBeenCalled();
  });

  it('筛选栏包含搜索、重置、等级、类型与状态切换，统计卡不再承载状态切换', () => {
    renderView({clusters: filterClusters(), total: 12});

    const filterBar = screen.getByRole('region', {name: '风险事件筛选'});
    expect(within(filterBar).getByPlaceholderText('搜索事件、地区、信源或供应商...')).toBeInTheDocument();
    expect(within(filterBar).getByRole('button', {name: /重置筛选/})).toBeInTheDocument();
    expect(within(filterBar).getByLabelText('风险等级')).toBeInTheDocument();
    expect(within(filterBar).getByLabelText('事件类型')).toBeInTheDocument();
    expect(within(filterBar).getByRole('group', {name: '有效期口径'})).toBeInTheDocument();

    const stats = screen.getByText('风险事件统计').closest('section') as HTMLElement;
    expect(within(stats).queryByRole('group', {name: '有效期口径'})).not.toBeInTheDocument();
  });

  it('搜索按事件簇标题、地区、信源与成员摘要过滤', async () => {
    const user = userEvent.setup();
    renderView({clusters: filterClusters(), total: 12});

    await user.type(searchBox(), '浙江');
    expect(screen.getByText('台风“蔷琵”影响浙江沿海')).toBeInTheDocument();
    expect(screen.queryByText('出口管制清单更新')).not.toBeInTheDocument();
    expect(screen.queryByText('苏州港拥堵加剧')).not.toBeInTheDocument();

    // 信源名称命中：商务部是贸易政策簇成员的信源
    await user.clear(searchBox());
    await user.type(searchBox(), '商务部');
    expect(screen.getByText('出口管制清单更新')).toBeInTheDocument();
    expect(screen.queryByText('台风“蔷琵”影响浙江沿海')).not.toBeInTheDocument();

    // 成员事件摘要命中：摘要折叠时也能参与搜索
    await user.clear(searchBox());
    await user.type(searchBox(), '待泊时间');
    expect(screen.getByText('苏州港拥堵加剧')).toBeInTheDocument();
    expect(screen.queryByText('出口管制清单更新')).not.toBeInTheDocument();
  });

  it('风险等级与事件类型可单独过滤，也可组合过滤', async () => {
    const user = userEvent.setup();
    renderView({clusters: filterClusters(), total: 12});

    await user.selectOptions(screen.getByLabelText('风险等级'), 'P1');
    expect(screen.getByText('出口管制清单更新')).toBeInTheDocument();
    expect(screen.queryByText('台风“蔷琵”影响浙江沿海')).not.toBeInTheDocument();
    expect(screen.getByText(/本页显示 1 条（已加载 3 条），全库共 12 条（已筛选）/)).toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText('风险等级'), 'all');
    await user.selectOptions(screen.getByLabelText('事件类型'), 'weather');
    expect(screen.getByText('台风“蔷琵”影响浙江沿海')).toBeInTheDocument();
    expect(screen.queryByText('出口管制清单更新')).not.toBeInTheDocument();

    // 组合：等级 P1 + 类型 天气 无交集
    await user.selectOptions(screen.getByLabelText('风险等级'), 'P1');
    expect(screen.getByText('未找到匹配的风险事件，请调整筛选条件')).toBeInTheDocument();
  });

  it('筛选无结果时显示「未找到匹配的风险事件」，而不是空口径文案', async () => {
    const user = userEvent.setup();
    renderView({clusters: filterClusters(), total: 12});

    await user.type(searchBox(), '不存在的关键词');

    expect(screen.getByText('未找到匹配的风险事件，请调整筛选条件')).toBeInTheDocument();
    expect(screen.queryByText('当前暂无有效风险事件')).not.toBeInTheDocument();
    expect(screen.getByText(/本页显示 0 条（已加载 3 条），全库共 12 条（已筛选）/)).toBeInTheDocument();
  });

  it('统计卡保持全库口径，不随筛选变化', async () => {
    const user = userEvent.setup();
    renderView({clusters: filterClusters(), total: 12});

    // P4 在已加载簇里没有匹配项，列表为空但统计数字仍是汇总值
    await user.selectOptions(screen.getByLabelText('风险等级'), 'P4');
    expect(screen.getByText('未找到匹配的风险事件，请调整筛选条件')).toBeInTheDocument();

    const stats = screen.getByText('风险事件统计').closest('section') as HTMLElement;
    for (const [label, value] of [['当前事件', '22'], ['事件簇', '12'], ['供应商', '34'], ['风险组', '44']] as const) {
      expect(within(stats).getByText(label)).toBeInTheDocument();
      expect(within(stats).getByText(value)).toBeInTheDocument();
    }
  });

  it('重置清空搜索与筛选，并把口径复位为「当前有效」', async () => {
    const onStateChange = vi.fn();
    const user = userEvent.setup();
    render(<StatefulEventsView onStateChange={onStateChange} />);

    await user.click(screen.getByRole('button', {name: '已失效'}));
    expect(screen.getByRole('button', {name: '已失效'})).toHaveAttribute('aria-pressed', 'true');

    await user.type(searchBox(), '浙江');
    await user.selectOptions(screen.getByLabelText('风险等级'), 'P2');
    await user.selectOptions(screen.getByLabelText('事件类型'), 'weather');
    expect(screen.queryByText('出口管制清单更新')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', {name: /重置筛选/}));

    expect(onStateChange).toHaveBeenLastCalledWith('current');
    expect(screen.getByRole('button', {name: '当前有效'})).toHaveAttribute('aria-pressed', 'true');
    expect(searchBox()).toHaveValue('');
    expect(screen.getByLabelText('风险等级')).toHaveValue('all');
    expect(screen.getByLabelText('事件类型')).toHaveValue('all');
    expect(screen.getByText('台风“蔷琵”影响浙江沿海')).toBeInTheDocument();
    expect(screen.getByText('出口管制清单更新')).toBeInTheDocument();
    expect(screen.getByText('苏州港拥堵加剧')).toBeInTheDocument();
  });

  it('无已加载数据时不渲染事件类型下拉', () => {
    renderView({clusters: [], total: 0, summary: null});
    expect(screen.queryByLabelText('事件类型')).not.toBeInTheDocument();
    expect(screen.getByLabelText('风险等级')).toBeInTheDocument();
  });
});

describe('RiskViewModeTabs', () => {
  it('页签标签为「事件」「供应商」，激活态与 tabIndex 由外部 mode 决定', async () => {
    const {onModeChange} = renderTabs();

    expect(screen.getAllByRole('tab').map((tab) => tab.textContent)).toEqual(['事件', '供应商']);
    expect(screen.getByRole('tab', {name: '事件'})).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tab', {name: '事件'})).toHaveAttribute('tabindex', '0');
    expect(screen.getByRole('tab', {name: '事件'})).toHaveAttribute('aria-controls', 'risk-view-panel-events');
    expect(screen.getByRole('tab', {name: '供应商'})).toHaveAttribute('aria-selected', 'false');
    expect(screen.getByRole('tab', {name: '供应商'})).toHaveAttribute('tabindex', '-1');
    expect(screen.getByRole('tab', {name: '供应商'})).toHaveAttribute('aria-controls', 'risk-view-panel-suppliers');

    await userEvent.setup().click(screen.getByRole('tab', {name: '供应商'}));
    expect(onModeChange).toHaveBeenCalledWith('suppliers');
  });

  it('左右方向键在页签间切换并聚焦目标页签', async () => {
    render(<StatefulTabs />);
    const user = userEvent.setup();

    screen.getByRole('tab', {name: '事件'}).focus();
    await user.keyboard('{ArrowRight}');
    expect(screen.getByRole('tab', {name: '供应商'})).toHaveFocus();
    expect(screen.getByRole('tab', {name: '供应商'})).toHaveAttribute('aria-selected', 'true');

    await user.keyboard('{ArrowLeft}');
    expect(screen.getByRole('tab', {name: '事件'})).toHaveFocus();
    expect(screen.getByRole('tab', {name: '事件'})).toHaveAttribute('aria-selected', 'true');
  });

  it('mode 为供应商时激活态跟随切换，事件页签退化为 tabIndex=-1', () => {
    renderTabs('suppliers');
    expect(screen.getByRole('tab', {name: '事件'})).toHaveAttribute('aria-selected', 'false');
    expect(screen.getByRole('tab', {name: '供应商'})).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tab', {name: '供应商'})).toHaveAttribute('tabindex', '0');
  });
});
