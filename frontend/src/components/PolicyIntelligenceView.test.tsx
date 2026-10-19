import {cleanup, render, screen, waitFor} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {MemoryRouter} from 'react-router-dom';
import {afterEach, describe, expect, it, vi} from 'vitest';
import {
  api,
  type IntelligenceCategory,
  type IntelligenceReportListResponse,
  type MarketIndicatorListResponse,
} from '../api';
import {PolicyIntelligenceView} from './PolicyIntelligenceView';

const report = (category: IntelligenceCategory, id: number) => ({
  id,
  event_id: id * 10,
  category,
  status: 'ready' as const,
  title: category === 'policy' ? `政策报告 ${id}` : `行业报告 ${id}`,
  event_summary: '事件摘要',
  event_type: 'policy',
  event_subtype: null,
  event_start_at: '2026-10-01T00:00:00Z',
  source_code: 'SRC-1',
  source_name: '示例来源',
  source_url: null,
  published_at: '2026-10-01T00:00:00Z',
  report_zh: '报告正文',
  suggestions: ['建议一'],
  provider: 'fake',
  model: 'fake',
  prompt_version: 'v1',
  error: null,
  generated_at: '2026-10-01T01:00:00Z',
});

const intelligenceResponse = (tab: IntelligenceCategory): IntelligenceReportListResponse => ({
  items: [report(tab, tab === 'policy' ? 1 : 2)],
  total: 1,
  offset: 0,
  limit: 20,
});

const marketResponse: MarketIndicatorListResponse = {
  generated_at: '2026-10-19T08:00:00Z',
  items: [{
    code: 'fx-CNY',
    label: '美元兑人民币汇率',
    source_code: 'fx-rates',
    source_name: '汇率行情',
    unit: '汇率',
    latest: {observed_at: '2026-10-19T08:00:00Z', value: 7.1234, change_pct: 1.2},
    stats: {observations: 12, sustained_change_pct: 3.4, shock_max_day_pct: 1.2},
    thresholds: {
      sustained_change_pct: 3,
      sustained_window: 20,
      shock_change_pct: 1.5,
      shock_window: 5,
      min_observations: 10,
      direction: 'both',
      episode_gap: 5,
      severity_bands: [{threshold_pct: 3, severity: 'medium'}],
    },
    status: 'tripped',
    series: [
      {observed_at: '2026-10-19T07:58:00Z', value: 7.0},
      {observed_at: '2026-10-19T08:00:00Z', value: 7.1234},
    ],
    latest_alert_id: 91,
  }],
};

/** 两个情报面板与行情面板各自取数；三者都常驻挂载（hidden），首载即全部发出请求。 */
const mockAll = () => {
  const intelligence = vi.spyOn(api, 'intelligence').mockImplementation(async (tab) => {
    // 页签值若越过情报分类契约，接口必须被调用出可观测的失败，而不是静默返回情报。
    if (tab !== 'policy' && tab !== 'industry') throw new Error('行情页签不得复用情报接口');
    return intelligenceResponse(tab);
  });
  const market = vi.spyOn(api.market, 'indicators').mockResolvedValue(marketResponse);
  return {intelligence, market};
};

const renderView = () => render(
  <MemoryRouter initialEntries={['/intelligence']}>
    <PolicyIntelligenceView />
  </MemoryRouter>,
);

const tabs = () => screen.getAllByRole('tab');
const tabByName = (name: string) => screen.getByRole('tab', {name});
const panel = (id: string) => screen.getByTestId(id);

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('情报聚合页签结构', () => {
  it('提供三个页签：政策情报、行业情报、行情监控', async () => {
    mockAll();
    renderView();

    await waitFor(() => expect(tabs()).toHaveLength(3));
    expect(tabs().map((tab) => tab.textContent)).toEqual(['政策情报', '行业情报', '行情监控']);
    expect(tabByName('政策情报')).toHaveAttribute('aria-selected', 'true');
  });

  it('行情监控页签的 hint 明确「越阈生成供应商风险提醒，与情报报告不同」', async () => {
    mockAll();
    renderView();

    await waitFor(() => expect(tabs()).toHaveLength(3));
    expect(tabByName('行情监控')).toHaveAttribute('aria-controls', 'intelligence-tabpanel-market');
    expect(tabByName('行情监控')).toHaveAttribute('id', 'intelligence-tab-market');

    await userEvent.click(tabByName('行情监控'));

    await waitFor(() => expect(panel('intelligence-tabpanel-market')).not.toHaveAttribute('hidden'));
    expect(panel('intelligence-tabpanel-market').textContent).toContain('行情越阈会生成供应商风险提醒，与情报报告不同');
  });

  it('三个面板的 tabpanel / tab aria 关联成对，不存在悬空的 aria-controls', async () => {
    mockAll();
    renderView();

    await waitFor(() => expect(tabs()).toHaveLength(3));
    for (const name of ['政策情报', '行业情报', '行情监控']) {
      const tab = tabByName(name);
      const controlled = tab.getAttribute('aria-controls');
      expect(controlled, `${name} 必须声明 aria-controls`).not.toBeNull();
      const target = document.getElementById(controlled!);
      expect(target, `${name} 的 aria-controls 必须指向已渲染面板`).not.toBeNull();
      expect(target).toHaveAttribute('aria-labelledby', tab.id);
      expect(target).toHaveAttribute('role', 'tabpanel');
    }
  });
});

describe('行情监控页签切换', () => {
  it('点击第三签：显示行情面板、隐藏两个情报面板，并只请求一次行情接口', async () => {
    const {market} = mockAll();
    renderView();
    await screen.findByText('政策报告 1');

    await userEvent.click(tabByName('行情监控'));

    await screen.findByText('美元兑人民币汇率');
    expect(panel('intelligence-tabpanel-market')).not.toHaveAttribute('hidden');
    expect(panel('intelligence-tabpanel-policy')).toHaveAttribute('hidden');
    expect(panel('intelligence-tabpanel-industry')).toHaveAttribute('hidden');
    expect(tabByName('行情监控')).toHaveAttribute('aria-selected', 'true');
    expect(tabByName('政策情报')).toHaveAttribute('aria-selected', 'false');
    expect(market).toHaveBeenCalledTimes(1);
  });

  it('行情面板只在切到该页签时渲染内容；切回情报页签后情报面板行为不变', async () => {
    const {intelligence} = mockAll();
    renderView();
    await screen.findByText('政策报告 1');

    await userEvent.click(tabByName('行情监控'));
    await screen.findByText('美元兑人民币汇率');

    await userEvent.click(tabByName('行业情报'));

    await screen.findByText('行业报告 2');
    expect(panel('intelligence-tabpanel-industry')).not.toHaveAttribute('hidden');
    expect(panel('intelligence-tabpanel-policy')).toHaveAttribute('hidden');
    expect(panel('intelligence-tabpanel-market')).toHaveAttribute('hidden');
    expect(tabByName('行业情报')).toHaveAttribute('aria-selected', 'true');
    // 情报接口只按情报分类调用，行情页签值从不进入该接口。
    expect(intelligence.mock.calls.map(([tab]) => tab)).toEqual(expect.arrayContaining(['policy', 'industry']));
    expect(intelligence.mock.calls.map(([tab]) => tab)).not.toContain('market');
  });
});

describe('三页签键盘导航', () => {
  it('ArrowRight 从行业情报推进到行情监控，ArrowLeft 回到行业情报', async () => {
    mockAll();
    renderView();
    await screen.findByText('政策报告 1');

    tabByName('政策情报').focus();
    await userEvent.keyboard('{ArrowRight}');
    expect(tabByName('行业情报')).toHaveFocus();

    await userEvent.keyboard('{ArrowRight}');
    expect(tabByName('行情监控')).toHaveFocus();
    expect(tabByName('行情监控')).toHaveAttribute('aria-selected', 'true');

    await userEvent.keyboard('{ArrowLeft}');
    expect(tabByName('行业情报')).toHaveFocus();
    expect(tabByName('行业情报')).toHaveAttribute('aria-selected', 'true');
  });

  it('End 跳到行情监控、Home 回到政策情报', async () => {
    mockAll();
    renderView();
    await screen.findByText('政策报告 1');

    tabByName('政策情报').focus();
    await userEvent.keyboard('{End}');
    expect(tabByName('行情监控')).toHaveFocus();

    await userEvent.keyboard('{Home}');
    expect(tabByName('政策情报')).toHaveFocus();
    expect(tabByName('政策情报')).toHaveAttribute('aria-selected', 'true');
  });

  it('未选中页签 tabIndex=-1，保持单一 Tab 停靠点', async () => {
    mockAll();
    renderView();
    await screen.findByText('政策报告 1');

    expect(tabByName('政策情报')).toHaveAttribute('tabindex', '0');
    expect(tabByName('行业情报')).toHaveAttribute('tabindex', '-1');
    expect(tabByName('行情监控')).toHaveAttribute('tabindex', '-1');
  });
});
