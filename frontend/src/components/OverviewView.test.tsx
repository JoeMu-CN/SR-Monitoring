import {cleanup, render, screen, waitFor, within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {MemoryRouter, Route, Routes, useLocation, useNavigate} from 'react-router-dom';
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {api, ApiError, type DashboardSummary, type MonitoringHealthRead, type RiskAlertRead} from '../api';
import type {RiskItem} from '../types';
import type {MonitoringHealthSnapshot} from '../useMonitoringHealth';
import {OverviewView} from './OverviewView';

// 仅把 dashboardSummary 网络方法替换为可控 mock，保留真实 mapRiskAlert / ApiError。
vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>();
  return {
    ...actual,
    api: {...actual.api, dashboardSummary: vi.fn()},
  };
});

const alertRead: RiskAlertRead = {
  id: 501,
  level: 'P1',
  score: 88,
  score_detail: {severity: 30, rule_version: 'rule-v1'},
  status: 'current',
  supplier_id: 9,
  supplier_name: '示例风险供应商',
  event_id: 77,
  event_type: 'compliance',
  event_subtype: 'sanction',
  event_summary: '被列入制裁清单',
  event_start_at: '2026-09-01T00:00:00Z',
  event_end_at: null,
  confidence: 0.9,
  match_type: 'registry_no',
  match_reasons: ['注册号精确匹配'],
  match_evidence: [],
  source_title: '合规公告',
  source_url: null,
  published_at: '2026-09-01T00:00:00Z',
  updated_at: '2026-09-07T00:00:00Z',
  expires_at: null,
  expiry_kind: 'none',
  validity_state: 'active',
  valid_until: null,
  review_due_at: null,
  validity_policy_version: 'rule-v1',
  validity_reason: {code: 'active', anchor_source: 'published_at', details: {}},
};

const baseSummary: DashboardSummary = {
  level_counts: [
    {level: 'P1', count: 3},
    {level: 'P2', count: 5},
    {level: 'P3', count: 7},
    {level: 'P4', count: 2},
  ],
  total_current: 17,
  today_new: 1,
  type_distribution: [{event_type: 'compliance', count: 9}],
  recent_alerts: [],
  sources: [],
  as_of: '2026-09-08T02:00:00Z',
  window_start: '2026-08-09T02:00:00Z',
  window_days: 30,
  period_new_count: 12,
  supplier_total: 40,
  active_supplier_total: 33,
  source_distribution: [
    {source_id: 1, code: 'ofac-sdn', name: 'OFAC SDN', count: 6},
    {source_id: 2, code: 'nmc-weather', name: '中央气象台预警', count: 4},
  ],
  retention_window_days: 90,
  history_may_be_partial: false,
};

const makeSummary = (overrides: Partial<DashboardSummary> = {}): DashboardSummary => ({...baseSummary, ...overrides});

const monitoringHealthOk: MonitoringHealthRead = {
  as_of: '2026-09-11T06:00:00Z',
  overall: 'ok',
  scheduler: {
    status: 'ok',
    last_heartbeat_at: '2026-09-11T05:59:30Z',
    age_seconds: 30,
    interval_seconds: 60,
    stale_after_seconds: 180,
    current_work: [],
    scheduled_jobs: [],
    recent_runs: [],
  },
  processing: {
    total: 0,
    classification_failed: 0,
    backlog_over_1h: 0,
    oldest_pending_age_seconds: null,
    last_run: {status: 'succeeded', started_at: '2026-09-11T05:58:00Z', finished_at: '2026-09-11T05:58:20Z', processed: 5, filtered: 1, failed: 0},
  },
  sources: [],
};

const readyHealth = (health: MonitoringHealthRead): MonitoringHealthSnapshot => ({status: 'ready', health});

const Probe = () => {
  const location = useLocation();
  return <output data-testid="ov-search">{location.search}</output>;
};

const BackButton = () => {
  const navigate = useNavigate();
  return <button type="button" onClick={() => navigate(-1)}>历史后退</button>;
};

interface RenderOptions {
  readonly entries?: readonly string[];
  readonly index?: number;
  readonly onSelectRisk?: (item: RiskItem) => void;
  readonly onViewAllRisks?: () => void;
  readonly onRequestError?: (error: ApiError) => void;
  readonly monitoringHealth?: MonitoringHealthSnapshot;
}

const renderOverview = (options: RenderOptions = {}) => {
  const onSelectRisk = options.onSelectRisk ?? vi.fn();
  const onViewAllRisks = options.onViewAllRisks ?? vi.fn();
  const onRequestError = options.onRequestError ?? vi.fn();
  const result = render(
    <MemoryRouter initialEntries={[...(options.entries ?? ['/overview'])]} initialIndex={options.index}>
      <Routes>
        <Route
          path="/overview"
          element={(
            <>
              <OverviewView
                onSelectRisk={onSelectRisk}
                onViewAllRisks={onViewAllRisks}
                onRequestError={onRequestError}
                monitoringHealth={options.monitoringHealth ?? readyHealth(monitoringHealthOk)}
              />
              <Probe />
              <BackButton />
            </>
          )}
        />
      </Routes>
    </MemoryRouter>,
  );
  return {onSelectRisk, onViewAllRisks, onRequestError, ...result};
};

const totalCurrentText = (value: number) => (
  screen.getByText((_content, element) => element?.tagName === 'SPAN' && element.textContent === `当前风险提醒：${value} 条`)
);

beforeEach(() => {
  vi.mocked(api.dashboardSummary).mockResolvedValue(makeSummary());
});

afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

describe('OverviewView 服务端汇总统计', () => {
  it('列表最多 100 条时总计仍来自 summary.total_current（135）而非任何前端数组', async () => {
    vi.mocked(api.dashboardSummary).mockResolvedValue(makeSummary({
      total_current: 135,
      level_counts: [
        {level: 'P1', count: 40},
        {level: 'P2', count: 35},
        {level: 'P3', count: 30},
        {level: 'P4', count: 30},
      ],
    }));
    renderOverview();

    expect(await screen.findByText('全网供应链风险概览')).toBeInTheDocument();
    expect(totalCurrentText(135)).toBeInTheDocument();
    expect(screen.getByTestId('overview-metric-total')).toHaveTextContent('135');
    // 深色模式下总计必须可读：slate 数值语义色不得丢失 dark:text-white（回归过一次）。
    expect(screen.getByTestId('overview-metric-total')).toHaveClass('dark:text-white');
    expect(screen.getByTestId('overview-metric-p1')).toHaveTextContent('40');
  });

  it('recent_alerts 提供最近风险提醒，独立于任何列表 props', async () => {
    vi.mocked(api.dashboardSummary).mockResolvedValue(makeSummary({recent_alerts: [alertRead]}));
    renderOverview();

    expect((await screen.findAllByText('示例风险供应商')).length).toBeGreaterThan(0);
  });

  it('as_of 作为数据截至时间展示，不使用"最后更新/最后一条风险时间"字样', async () => {
    vi.mocked(api.dashboardSummary).mockResolvedValue(makeSummary({as_of: '2026-09-08T02:00:00Z'}));
    renderOverview();

    // 页头与健康横幅都可携带「数据截至」，只需保证语义存在且不出现旧文案
    expect((await screen.findAllByText(/数据截至/)).length).toBeGreaterThan(0);
    expect(screen.queryByText(/最后更新/)).not.toBeInTheDocument();
  });
});

describe('OverviewView 时间窗口 URL 白名单', () => {
  it('缺省无 days 参数时按 30 请求且仅 30 天为选中态', async () => {
    renderOverview();

    await screen.findByText('全网供应链风险概览');
    expect(api.dashboardSummary).toHaveBeenCalledWith(30);
    expect(screen.getByRole('button', {name: '30 天'})).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', {name: '7 天'})).toHaveAttribute('aria-pressed', 'false');
    expect(screen.getByRole('button', {name: '90 天'})).toHaveAttribute('aria-pressed', 'false');
    expect(screen.getByTestId('ov-search')).toHaveTextContent('');
  });

  it('?days=7 刷新即恢复为 7 天并按 7 请求', async () => {
    renderOverview({entries: ['/overview?days=7']});

    await screen.findByText('全网供应链风险概览');
    expect(api.dashboardSummary).toHaveBeenCalledWith(7);
    expect(screen.getByRole('button', {name: '7 天'})).toHaveAttribute('aria-pressed', 'true');
  });

  it('点击 90 天写入 ?days=90（push），点击 30 天清空 query', async () => {
    const user = userEvent.setup();
    renderOverview();
    await screen.findByText('全网供应链风险概览');

    await user.click(screen.getByRole('button', {name: '90 天'}));
    await waitFor(() => expect(screen.getByTestId('ov-search')).toHaveTextContent('?days=90'));
    await waitFor(() => expect(api.dashboardSummary).toHaveBeenCalledWith(90));
    // 选中态互斥：仅 90 天 aria-pressed
    expect(screen.getByRole('button', {name: '90 天'})).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', {name: '30 天'})).toHaveAttribute('aria-pressed', 'false');
    expect(screen.getByRole('button', {name: '7 天'})).toHaveAttribute('aria-pressed', 'false');

    await user.click(screen.getByRole('button', {name: '30 天'}));
    await waitFor(() => expect(screen.getByTestId('ov-search').textContent).toBe(''));
  });

  it('浏览器后退恢复上一 days（90 → 缺省 30）', async () => {
    const user = userEvent.setup();
    renderOverview({entries: ['/overview', '/overview?days=90'], index: 1});
    await screen.findByText('全网供应链风险概览');
    await waitFor(() => expect(api.dashboardSummary).toHaveBeenCalledWith(90));

    await user.click(screen.getByRole('button', {name: '历史后退'}));

    await waitFor(() => expect(screen.getByTestId('ov-search').textContent).toBe(''));
    await waitFor(() => expect(api.dashboardSummary).toHaveBeenLastCalledWith(30));
  });

  it('非法 days（?days=5）以 replace 规范化为缺省 30，不以 5 发起请求', async () => {
    renderOverview({entries: ['/overview?days=5']});

    await screen.findByText('全网供应链风险概览');
    await waitFor(() => expect(screen.getByTestId('ov-search').textContent).toBe(''));
    expect(api.dashboardSummary).toHaveBeenCalledWith(30);
    expect(api.dashboardSummary).not.toHaveBeenCalledWith(5);
    expect(screen.getByRole('button', {name: '30 天'})).toHaveAttribute('aria-pressed', 'true');
  });

  it('显式 ?days=30 也规范化为无 query', async () => {
    renderOverview({entries: ['/overview?days=30']});

    await screen.findByText('全网供应链风险概览');
    await waitFor(() => expect(screen.getByTestId('ov-search').textContent).toBe(''));
    expect(api.dashboardSummary).toHaveBeenCalledWith(30);
  });
});

describe('OverviewView 当前统计与期间新增分离', () => {
  it('7/90 切换时 period_new_count 变化而 total_current 不变', async () => {
    vi.mocked(api.dashboardSummary).mockImplementation((days) => Promise.resolve(makeSummary({
      window_days: days,
      total_current: 120,
      period_new_count: days === 7 ? 5 : days === 90 ? 88 : 30,
    })));
    const user = userEvent.setup();
    renderOverview();

    await screen.findByText('全网供应链风险概览');
    expect(totalCurrentText(120)).toBeInTheDocument();
    expect(screen.getByTestId('overview-period-new')).toHaveTextContent('最近 30 天新增提醒（含已失效）：30 条');

    await user.click(screen.getByRole('button', {name: '7 天'}));
    await waitFor(() => expect(screen.getByTestId('overview-period-new')).toHaveTextContent('最近 7 天新增提醒（含已失效）：5 条'));
    expect(totalCurrentText(120)).toBeInTheDocument();

    await user.click(screen.getByRole('button', {name: '90 天'}));
    await waitFor(() => expect(screen.getByTestId('overview-period-new')).toHaveTextContent('最近 90 天新增提醒（含已失效）：88 条'));
    expect(totalCurrentText(120)).toBeInTheDocument();
  });

  it('history_may_be_partial=true 时提示历史可能不完整；false 时无提示', async () => {
    vi.mocked(api.dashboardSummary).mockResolvedValue(makeSummary({
      window_days: 90,
      history_may_be_partial: true,
      retention_window_days: 90,
    }));
    const {unmount} = renderOverview({entries: ['/overview?days=90']});
    expect(await screen.findByText(/历史可能不完整/)).toBeInTheDocument();
    unmount();

    vi.mocked(api.dashboardSummary).mockResolvedValue(makeSummary({history_may_be_partial: false}));
    renderOverview();
    await screen.findByText('全网供应链风险概览');
    expect(screen.queryByText(/历史可能不完整/)).not.toBeInTheDocument();
  });
});

describe('OverviewView 来源分布', () => {
  it('标注非互斥来源计数，不伪造百分比总和 100%', async () => {
    vi.mocked(api.dashboardSummary).mockResolvedValue(makeSummary({
      total_current: 10,
      source_distribution: [
        {source_id: 1, code: 'a', name: '来源甲', count: 8},
        {source_id: 2, code: 'b', name: '来源乙', count: 6},
      ],
    }));
    renderOverview();

    const section = await screen.findByTestId('overview-source-distribution');
    expect(within(section).getByText(/非互斥|可重复归属|之和可(能)?大于/)).toBeInTheDocument();
    expect(within(section).getByText('来源甲')).toBeInTheDocument();
    expect(within(section).getByText('8 条')).toBeInTheDocument();
  });

  it('来源分布为空（zero distribution）时展示占位而不画非零比例', async () => {
    vi.mocked(api.dashboardSummary).mockResolvedValue(makeSummary({source_distribution: []}));
    renderOverview();

    const section = await screen.findByTestId('overview-source-distribution');
    expect(within(section).getByText('暂无信源分布数据')).toBeInTheDocument();
  });
});

describe('OverviewView 加载失败与陈旧数据', () => {
  it('首次失败展示组件内错误与独立重试，不显示 0，不触发 onRequestError', async () => {
    const onRequestError = vi.fn();
    vi.mocked(api.dashboardSummary)
      .mockRejectedValueOnce(new ApiError(503, '总览暂时不可用'))
      .mockResolvedValue(makeSummary({total_current: 77}));
    const user = userEvent.setup();
    renderOverview({onRequestError});

    expect(await screen.findByText('总览汇总加载失败')).toBeInTheDocument();
    expect(screen.getByText('总览暂时不可用')).toBeInTheDocument();
    expect(screen.queryByText('全网供应链风险概览')).not.toBeInTheDocument();
    expect(onRequestError).not.toHaveBeenCalled();

    await user.click(screen.getByRole('button', {name: '重试'}));
    expect(await screen.findByText('全网供应链风险概览')).toBeInTheDocument();
    expect(totalCurrentText(77)).toBeInTheDocument();
  });

  it('已有 summary 后刷新失败保留旧值并标注"旧数据/刷新失败"，重试成功恢复', async () => {
    const onRequestError = vi.fn();
    vi.mocked(api.dashboardSummary)
      .mockResolvedValueOnce(makeSummary({total_current: 120, period_new_count: 30}))
      .mockRejectedValueOnce(new ApiError(500, '汇总服务异常'))
      .mockResolvedValue(makeSummary({total_current: 120, window_days: 7, period_new_count: 5}));
    const user = userEvent.setup();
    renderOverview({onRequestError});

    await screen.findByText('全网供应链风险概览');
    expect(totalCurrentText(120)).toBeInTheDocument();

    await user.click(screen.getByRole('button', {name: '7 天'}));

    await screen.findByText(/旧数据/);
    expect(screen.getByText(/刷新失败/)).toBeInTheDocument();
    // 旧值保留，未被清零或替换为占位
    expect(totalCurrentText(120)).toBeInTheDocument();
    expect(screen.getByTestId('overview-period-new')).toHaveTextContent('30 条');
    expect(onRequestError).not.toHaveBeenCalled();

    await user.click(screen.getByRole('button', {name: '重试'}));
    await waitFor(() => expect(screen.getByTestId('overview-period-new')).toHaveTextContent('最近 7 天新增提醒（含已失效）：5 条'));
    expect(screen.queryByText(/刷新失败/)).not.toBeInTheDocument();
  });

  it('summary 返回 401 时调用 App 统一错误处理，不显示组件内错误', async () => {
    const onRequestError = vi.fn();
    vi.mocked(api.dashboardSummary).mockRejectedValue(new ApiError(401, '登录已失效'));
    renderOverview({onRequestError});

    await waitFor(() => expect(onRequestError).toHaveBeenCalledTimes(1));
    const [caught] = onRequestError.mock.calls[0] as [ApiError];
    expect(caught).toBeInstanceOf(ApiError);
    expect(caught.status).toBe(401);
    expect(screen.queryByText('总览汇总加载失败')).not.toBeInTheDocument();
  });

  it('summary 返回 403（非 401）时组件内报错且不进入全局错误门', async () => {
    const onRequestError = vi.fn();
    vi.mocked(api.dashboardSummary).mockRejectedValue(new ApiError(403, '无权访问总览'));
    renderOverview({onRequestError});

    expect(await screen.findByText('总览汇总加载失败')).toBeInTheDocument();
    expect(onRequestError).not.toHaveBeenCalled();
  });
});

describe('OverviewView 请求竞态', () => {
  it('快速 7→90 时更早的 7 响应晚到也不覆盖 90（deferred promise 真实区分）', async () => {
    let resolveSeven!: (summary: DashboardSummary) => void;
    vi.mocked(api.dashboardSummary).mockImplementation((days) => {
      if (days === 7) {
        return new Promise<DashboardSummary>((resolve) => { resolveSeven = resolve; });
      }
      return Promise.resolve(makeSummary({
        window_days: days,
        total_current: 120,
        period_new_count: days === 90 ? 88 : 30,
      }));
    });
    const user = userEvent.setup();
    renderOverview();
    await screen.findByText('全网供应链风险概览');

    await user.click(screen.getByRole('button', {name: '7 天'}));
    await waitFor(() => expect(api.dashboardSummary).toHaveBeenCalledWith(7));

    await user.click(screen.getByRole('button', {name: '90 天'}));
    await waitFor(() => expect(screen.getByTestId('overview-period-new')).toHaveTextContent('最近 90 天新增提醒（含已失效）：88 条'));

    // 迟到的 7 响应必须被请求序号丢弃
    resolveSeven(makeSummary({window_days: 7, total_current: 120, period_new_count: 5}));
    await new Promise((resolve) => { setTimeout(resolve, 50); });

    expect(screen.getByTestId('overview-period-new')).toHaveTextContent('最近 90 天新增提醒（含已失效）：88 条');
    expect(screen.getByRole('button', {name: '90 天'})).toHaveAttribute('aria-pressed', 'true');
  });
});

describe('OverviewView 严重度零值', () => {
  it('count=0 的级别不渲染可见 segment，非零级别按真实 count 比例', async () => {
    vi.mocked(api.dashboardSummary).mockResolvedValue(makeSummary({
      total_current: 10,
      level_counts: [
        {level: 'P1', count: 3},
        {level: 'P2', count: 5},
        {level: 'P3', count: 0},
        {level: 'P4', count: 2},
      ],
    }));
    renderOverview();

    await screen.findByText('全网供应链风险概览');
    // 零值级别不得渲染任何可见色块（旧实现 Math.max(1, 0) 会给它可见宽度）
    expect(screen.queryByTestId('severity-segment-P3')).not.toBeInTheDocument();
    // 非零级别保留真实比例（flex-grow === count），不归一化到最小 1
    expect(screen.getByTestId('severity-segment-P1')).toHaveStyle({flexGrow: '3'});
    expect(screen.getByTestId('severity-segment-P2')).toHaveStyle({flexGrow: '5'});
    expect(screen.getByTestId('severity-segment-P4')).toHaveStyle({flexGrow: '2'});
    expect(screen.getAllByTestId(/^severity-segment-/)).toHaveLength(3);
  });

  it('全部级别为 0 时显示占位文案而不渲染任何 segment', async () => {
    vi.mocked(api.dashboardSummary).mockResolvedValue(makeSummary({
      total_current: 0,
      level_counts: [
        {level: 'P1', count: 0},
        {level: 'P2', count: 0},
        {level: 'P3', count: 0},
        {level: 'P4', count: 0},
      ],
    }));
    renderOverview();

    expect(await screen.findByText('当前无有效风险提醒')).toBeInTheDocument();
    expect(screen.queryByTestId(/^severity-segment-/)).not.toBeInTheDocument();
  });
});

describe('OverviewView 导航回调', () => {
  it('点击"查看完整风险中心"触发 onViewAllRisks', async () => {
    const onViewAllRisks = vi.fn();
    vi.mocked(api.dashboardSummary).mockResolvedValue(makeSummary({recent_alerts: [alertRead]}));
    const user = userEvent.setup();
    renderOverview({onViewAllRisks});

    await user.click(await screen.findByRole('button', {name: /查看完整风险中心/}));
    expect(onViewAllRisks).toHaveBeenCalledTimes(1);
  });

  it('点击最近风险行触发 onSelectRisk 并携带映射后的风险项', async () => {
    const onSelectRisk = vi.fn();
    vi.mocked(api.dashboardSummary).mockResolvedValue(makeSummary({recent_alerts: [alertRead]}));
    const user = userEvent.setup();
    renderOverview({onSelectRisk});

    await user.click((await screen.findAllByText('示例风险供应商'))[0]);
    expect(onSelectRisk).toHaveBeenCalledTimes(1);
    const [item] = onSelectRisk.mock.calls[0] as [RiskItem];
    expect(item.id).toBe('501');
    expect(item.companyName).toBe('示例风险供应商');
  });
});

describe('OverviewView 监控健康横幅', () => {
  it('空风险且诊断 ok：显示「监控正常，截至…暂无当前风险」，空态不能被误读为监控成功', async () => {
    vi.mocked(api.dashboardSummary).mockResolvedValue(makeSummary({
      total_current: 0,
      level_counts: [
        {level: 'P1', count: 0},
        {level: 'P2', count: 0},
        {level: 'P3', count: 0},
        {level: 'P4', count: 0},
      ],
    }));
    renderOverview();

    await screen.findByText('当前无有效风险提醒');
    const banner = screen.getByTestId('monitoring-health-banner');
    expect(banner).toHaveAttribute('data-state', 'ok');
    expect(banner.textContent).toMatch(/监控正常，截至.*暂无当前风险/);
  });

  it('空风险且诊断降级：显示降级文案而非「监控正常」', async () => {
    vi.mocked(api.dashboardSummary).mockResolvedValue(makeSummary({total_current: 0}));
    renderOverview({monitoringHealth: readyHealth({...monitoringHealthOk, overall: 'degraded'})});

    const banner = await screen.findByTestId('monitoring-health-banner');
    expect(banner).toHaveAttribute('data-state', 'degraded');
    expect(banner.textContent).toContain('部分链路异常，结果可能不完整');
    expect(banner.textContent).not.toContain('监控正常，截至');
  });

  it('非空风险且诊断 inactive：仍展示横幅（含数据新鲜度），风险数据不被清空', async () => {
    vi.mocked(api.dashboardSummary).mockResolvedValue(makeSummary({total_current: 17}));
    renderOverview({monitoringHealth: readyHealth({...monitoringHealthOk, overall: 'inactive'})});

    const banner = await screen.findByTestId('monitoring-health-banner');
    expect(banner).toHaveAttribute('data-state', 'inactive');
    expect(banner.textContent).toContain('未开启自动监控');
    expect(totalCurrentText(17)).toBeInTheDocument();
  });

  it('诊断 503（snapshot=unknown）：显示无法确认，业务统计保持可见', async () => {
    vi.mocked(api.dashboardSummary).mockResolvedValue(makeSummary({total_current: 17}));
    renderOverview({monitoringHealth: {status: 'unknown'}});

    const banner = await screen.findByTestId('monitoring-health-banner');
    expect(banner).toHaveAttribute('data-state', 'unknown');
    expect(banner.textContent).toContain('尚无法确认监控状态');
    expect(banner.textContent).not.toContain('监控正常');
    expect(totalCurrentText(17)).toBeInTheDocument();
  });

  it('无诊断权限（hidden）：不渲染横幅，页面其余内容不受影响', async () => {
    vi.mocked(api.dashboardSummary).mockResolvedValue(makeSummary({total_current: 17}));
    renderOverview({monitoringHealth: {status: 'hidden'}});

    await screen.findByText('全网供应链风险概览');
    expect(screen.queryByTestId('monitoring-health-banner')).not.toBeInTheDocument();
    expect(totalCurrentText(17)).toBeInTheDocument();
  });
});
