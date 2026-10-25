import {expect, test, type BrowserContext, type Page, type Route} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
import {resolve} from 'node:path';

/**
 * Todo 18：情报聚合页第三个页签「行情监控」端到端。
 *
 * 沿用 todo-4-routes.spec.ts 的生产预览 + 全量 `page.route` API 桩模式：
 * 本地栈的 app 镜像不含未合并的行情模块（`/api/v1/market/indicators` 返回 404），
 * 因此行情指标走桩数据，其余接口同样以桩满足登录外壳，保证「不新增路由/导航入口」
 * 与三页签切换、键盘导航、320px 起布局在真实浏览器里可回归。
 *
 * 桩序列刻意覆盖全部对抗面：
 * - tripped：单日/窗口双口径同时越阈 + latest_alert_id（跳 /risks/:id）；
 * - accumulating：观察点 3 < min_observations 10，徽标必须给 3/10；
 * - normal：永不挂载提醒入口；
 * - malformed：空 series（不产生 NaN 折线）、latest 三项全空（不编造现值）；
 * - stale_state：首个请求 500 → 重试按钮 → 第二次成功。
 */

const evidenceDirectory = resolve(process.cwd(), '..', '.omo', 'evidence', 'task-18-market-indicator-monitoring');

const permittedUser = {
  user: {id: 1, username: 'market-e2e', email: null, display_name: '行情验证账号', role: 'platform_admin', status: 'active', last_login_at: null, created_at: '2026-08-30T00:00:00Z'},
  permissions: ['risk_view', 'supplier_view', 'source_status_view', 'rule_summary_view', 'risk_query_use', 'user_manage', 'source_manage', 'supplier_manage', 'rule_manage'],
};

const FX_THRESHOLDS = {
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

/** count 个等间隔观察点（分钟步进），保证点数 ≥ min_observations 且可预测。 */
const series = (count: number, base: number, step: number) => (
  Array.from({length: count}, (_, index) => ({
    observed_at: new Date(Date.UTC(2026, 9, 19, 6, index)).toISOString(),
    value: Number((base + index * step).toFixed(4)),
  }))
);

const indicator = (overrides: Record<string, unknown>) => ({
  source_name: '行情源',
  unit: '汇率',
  latest: {observed_at: '2026-10-19T08:00:00Z', value: 7.1234, change_pct: 0.42},
  stats: {observations: 12, sustained_change_pct: 1.85, shock_max_day_pct: 0.9},
  thresholds: FX_THRESHOLDS,
  status: 'normal',
  series: series(12, 7.0, 0.01),
  latest_alert_id: null,
  ...overrides,
});

/** 三组 × 三态 + 两个畸形样本（空 series、latest 全空）。 */
const marketIndicators = {
  generated_at: '2026-10-19T08:00:00Z',
  items: [
    indicator({
      code: 'fx-CNY', label: '美元兑人民币汇率', source_code: 'fx-rates',
      status: 'tripped',
      latest: {observed_at: '2026-10-19T08:00:00Z', value: 7.1234, change_pct: -1.86},
      stats: {observations: 12, sustained_change_pct: -3.42, shock_max_day_pct: 1.9},
      series: series(30, 7.2, -0.005), latest_alert_id: 4821,
    }),
    indicator({
      code: 'fx-JPY', label: '美元兑日元汇率', source_code: 'fx-rates',
      status: 'accumulating',
      latest: {observed_at: '2026-10-19T08:00:00Z', value: 149.52, change_pct: null},
      stats: {observations: 3, sustained_change_pct: null, shock_max_day_pct: null},
      series: series(3, 149.5, 0.02),
    }),
    indicator({
      code: 'fx-EUR', label: '美元兑欧元汇率', source_code: 'fx-rates',
      latest: {observed_at: '2026-10-19T08:00:00Z', value: 0.9128, change_pct: 0.11},
      stats: {observations: 20, sustained_change_pct: 0.44, shock_max_day_pct: 0.3},
      series: series(30, 0.91, 0.0002),
    }),
    indicator({
      code: 'fut-CU', label: '沪铜主力', source_code: 'commodity-futures', unit: '元/吨',
      status: 'tripped',
      latest: {observed_at: '2026-10-19T08:00:00Z', value: 78450, change_pct: 2.4},
      stats: {observations: 15, sustained_change_pct: 6.1, shock_max_day_pct: 2.4},
      series: series(30, 72000, 210), latest_alert_id: 7,
    }),
    indicator({
      code: 'fut-ZN', label: '沪锌主力', source_code: 'commodity-futures', unit: '元/吨',
      status: 'accumulating',
      latest: {observed_at: '2026-10-19T08:00:00Z', value: 24120, change_pct: null},
      stats: {observations: 4, sustained_change_pct: null, shock_max_day_pct: null},
      series: [], // 畸形样本：序列为空，走势不得产生 NaN 路径。
    }),
    indicator({
      code: 'semi-980017', label: '国证半导体芯片指数', source_code: 'semiconductor-index', unit: '点位',
      status: 'accumulating',
      latest: {observed_at: null, value: null, change_pct: null}, // 畸形样本：latest 三项全空。
      stats: {observations: 0, sustained_change_pct: null, shock_max_day_pct: null},
      series: [],
    }),
  ],
};

const intelligenceReport = (category: 'policy' | 'industry') => ({
  id: category === 'policy' ? 11 : 12,
  event_id: category === 'policy' ? 101 : 102,
  category,
  status: 'ready',
  title: category === 'policy' ? '政策报告' : '行业报告',
  event_summary: '事件摘要',
  event_type: category,
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

const fulfil = async (route: Route, body: unknown) => {
  await route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
};

/**
 * 行情指标桩的失败策略：首请求返回 500 触发重试路径，`failFirst` 用后置闭包翻转。
 * `payload` 可替换为 `{generated_at, items: []}` 以验证空态。
 */
const mockApi = async (page: Page, options: {payload?: unknown; failFirst?: boolean} = {}) => {
  let failed = false;
  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (path.includes('/auth/me')) return fulfil(route, permittedUser);
    if (path.includes('/market/indicators')) {
      if (options.failFirst === true && !failed) {
        failed = true;
        await route.fulfill({status: 500, contentType: 'application/json', body: JSON.stringify({detail: '行情指标服务不可用'})});
        return;
      }
      return fulfil(route, options.payload ?? marketIndicators);
    }
    if (path.includes('/intelligence')) {
      const tab = url.searchParams.get('tab');
      return fulfil(route, {items: tab === 'industry' ? [intelligenceReport('industry')] : [intelligenceReport('policy')], total: 1, offset: 0, limit: 20});
    }
    if (path.includes('/risk-alerts')) return fulfil(route, {items: [], total: 0});
    if (path.includes('/suppliers')) return fulfil(route, {items: [], total: 0});
    if (path.includes('/sources')) return fulfil(route, []);
    if (path.includes('/collection-runs')) return fulfil(route, {items: [], total: 0});
    if (path.includes('/rule-engine/dimensions')) return fulfil(route, []);
    if (path.includes('/system/monitoring-health')) {
      return fulfil(route, {as_of: '2026-10-19T08:00:00Z', overall: 'unknown', scheduler: null, processing: null, sources: []});
    }
    if (path.includes('/system/health')) return fulfil(route, {status: 'healthy'});
    if (path.includes('/agent/status')) return fulfil(route, {enabled: false, configured: false});
    if (path.includes('/dashboard/summary')) {
      return fulfil(route, {level_counts: [{level: 'P1', count: 0}, {level: 'P2', count: 0}, {level: 'P3', count: 0}, {level: 'P4', count: 0}], total_current: 0, today_new: 0, type_distribution: [], recent_alerts: [], sources: [], as_of: '2026-10-19T08:00:00Z', window_start: '2026-09-19T08:00:00Z', window_days: 30, period_new_count: 0, supplier_total: 0, active_supplier_total: 0, source_distribution: [], retention_window_days: 90, history_may_be_partial: false});
    }
    return fulfil(route, {detail: 'Unhandled deterministic API fixture'});
  });
};

const newMockedPage = async (context: BrowserContext, options?: {payload?: unknown; failFirst?: boolean}) => {
  const page = await context.newPage();
  await mockApi(page, options);
  return page;
};

const settleRoute = async (page: Page) => {
  await expect(page.getByRole('status', {name: '正在初始化供应商风险监控平台'})).toBeHidden({timeout: 10_000});
  await expect(page.getByTestId('route-content')).toHaveCSS('opacity', '1');
};

const assertNoHorizontalOverflow = async (page: Page) => {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
};

const tab = (page: Page, name: string) => page.getByRole('tab', {name});

test.beforeAll(async () => {
  await mkdir(evidenceDirectory, {recursive: true});
});

test('桌面1280：第三签可切换，三组三态渲染，提醒入口指向 /risks/:id', async ({browser}) => {
  const context = await browser.newContext({viewport: {width: 1280, height: 720}, reducedMotion: 'reduce'});
  const page = await newMockedPage(context);
  const consoleErrors: string[] = [];
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text());
  });
  const requests: string[] = [];
  page.on('request', (request) => requests.push(request.url()));

  await page.goto('/intelligence');
  await expect(page.getByRole('heading', {name: '情报聚合'})).toBeVisible({timeout: 15_000});
  await settleRoute(page);

  // 三个外层页签齐备；行情监控面板不再承载说明文案，直接展示读数与判定。
  await expect(page.getByTestId('intelligence-tabs').getByRole('tab')).toHaveCount(3);
  await expect(tab(page, '政策情报')).toHaveAttribute('aria-selected', 'true');

  await tab(page, '行情监控').click();
  const panel = page.getByTestId('intelligence-tabpanel-market');
  await expect(panel).toBeVisible();
  await expect(panel).not.toContainText('行情越阈会生成供应商风险提醒');
  await expect(page.getByTestId('intelligence-tabpanel-policy')).toBeHidden();
  await expect(page.getByTestId('intelligence-tabpanel-industry')).toBeHidden();

  // 分类改为面板内页签：顺序固定（汇率 → 大宗商品 → 半导体），默认只显示第一个有数据的分类。
  const groupTabs = page.getByTestId('market-indicator-group-tabs').getByRole('tab');
  await expect(groupTabs).toHaveCount(3);
  await expect(groupTabs).toHaveText([/汇率/, /大宗商品/, /半导体/]);
  await expect(page.getByTestId('market-group-tab-fx-rates')).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByTestId('market-indicator-group-fx-rates')).toBeVisible();
  // 非当前分类保持挂载但隐藏：三组长列表不再同屏。
  await expect(page.getByTestId('market-group-panel-commodity-futures')).toBeHidden();
  await expect(page.getByTestId('market-group-panel-semiconductor-index')).toBeHidden();

  // 汇率分类三态：触发中 / 数据积累中 3/10 / 正常。
  await expect(page.getByTestId('market-indicator-status-fx-CNY')).toHaveText('触发中');
  await expect(page.getByTestId('market-indicator-status-fx-JPY')).toHaveText('数据积累中 3/10');
  await expect(page.getByTestId('market-indicator-status-fx-EUR')).toHaveText('正常');

  // 单日变化与窗口累计是两个口径；未形成统计时不得用 0 冒充。
  await expect(page.getByTestId('market-indicator-day-change-fx-CNY')).toContainText('-1.86%');
  await expect(page.getByTestId('market-indicator-window-change-fx-CNY')).toContainText('-3.42%');
  await expect(page.getByTestId('market-indicator-window-change-fx-JPY')).toHaveText('—');

  // 提醒入口只在 latest_alert_id 存在时出现，且指向风险详情。
  await expect(page.getByTestId('market-indicator-alert-fx-CNY')).toHaveAttribute('href', '/risks/4821');
  await expect(page.getByTestId('market-indicator-alert-fx-EUR')).toHaveCount(0);

  // 走势为内联 SVG：30 点折线有几何。
  const spark = page.getByTestId('market-indicator-spark-fx-CNY');
  await expect(spark).toHaveAttribute('data-points', '30');
  const polyline = await spark.locator('polyline').getAttribute('points');
  expect(polyline).not.toBeNull();
  expect(polyline).not.toContain('NaN');
  expect((polyline ?? '').trim().split(/\s+/)).toHaveLength(30);

  // 切到大宗商品：提醒入口指向 /risks/:id；空序列没有折线也没有 NaN。
  await page.getByTestId('market-group-tab-commodity-futures').click();
  await expect(page.getByTestId('market-group-tab-commodity-futures')).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByTestId('market-group-panel-commodity-futures')).toBeVisible();
  await expect(page.getByTestId('market-indicator-alert-fut-CU')).toHaveAttribute('href', '/risks/7');
  await expect(page.getByTestId('market-indicator-spark-fut-ZN')).toHaveAttribute('data-points', '0');
  expect(await page.getByTestId('market-indicator-spark-fut-ZN').locator('polyline').count()).toBe(0);

  // 切到半导体：latest 全空不得编造现值。
  await page.getByTestId('market-group-tab-semiconductor-index').click();
  await expect(page.getByTestId('market-indicator-value-semi-980017')).toHaveText('—');
  await expect(page.getByTestId('market-indicator-day-change-semi-980017')).toHaveText('—');

  await page.screenshot({path: resolve(evidenceDirectory, 'task18-desktop-1280-market-tab.png'), fullPage: true});
  await assertNoHorizontalOverflow(page);

  // 只请求行情指标接口；页签类型解耦后情报接口不会被 market 取值污染。
  expect(requests.some((url) => url.includes('/api/v1/market/indicators'))).toBe(true);
  expect(requests.some((url) => url.includes('/api/v1/intelligence') && url.includes('tab=market'))).toBe(false);
  expect(consoleErrors).toEqual([]);
  await context.close();
});

test('平板768 / 移动375 / 最小320：三态卡片与走势图在窄屏不横向溢出', async ({browser}) => {
  for (const viewport of [{width: 768, height: 1024, name: 'tablet-768'}, {width: 375, height: 812, name: 'mobile-375'}, {width: 320, height: 640, name: 'mobile-320'}]) {
    const context = await browser.newContext({viewport: {width: viewport.width, height: viewport.height}, reducedMotion: 'reduce'});
    const page = await newMockedPage(context);

    await page.goto('/intelligence');
    await expect(page.getByRole('heading', {name: '情报聚合'})).toBeVisible({timeout: 15_000});
    await settleRoute(page);
    await tab(page, '行情监控').click();
    await expect(page.getByTestId('market-indicator-status-fx-CNY')).toBeVisible();

    // 宽带不变，窄屏下四个（含兜底）分类页签必须能换行/收缩，不撑破容器。
    await expect(page.getByTestId('market-indicator-group-tabs')).toBeVisible();
    await page.getByTestId('market-group-tab-commodity-futures').click();

    // 走势 SVG 的可视宽度必须落在卡片内，不得把布局撑出横向滚动。
    const cardBox = await page.getByTestId('market-indicator-fut-CU').boundingBox();
    const sparkBox = await page.getByTestId('market-indicator-spark-fut-CU').boundingBox();
    expect(cardBox, `${viewport.name} 卡片必须可测量`).not.toBeNull();
    expect(sparkBox, `${viewport.name} 走势必须可测量`).not.toBeNull();
    expect(sparkBox!.x + sparkBox!.width).toBeLessThanOrEqual(cardBox!.x + cardBox!.width + 1);

    await page.screenshot({path: resolve(evidenceDirectory, `task18-${viewport.name}-market-tab.png`), fullPage: true});
    await assertNoHorizontalOverflow(page);
    await context.close();
  }
});

test('键盘导航：ArrowRight / End 在三页签间循环并同步 aria-selected', async ({browser}) => {
  const context = await browser.newContext({viewport: {width: 1280, height: 720}, reducedMotion: 'reduce'});
  const page = await newMockedPage(context);

  await page.goto('/intelligence');
  await expect(page.getByRole('heading', {name: '情报聚合'})).toBeVisible({timeout: 15_000});
  await settleRoute(page);

  await tab(page, '政策情报').focus();
  await page.keyboard.press('ArrowRight');
  await expect(tab(page, '行业情报')).toHaveAttribute('aria-selected', 'true');
  await page.keyboard.press('ArrowRight');
  await expect(tab(page, '行情监控')).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByTestId('intelligence-tabpanel-market')).toBeVisible();
  // 从行情监控继续 ArrowRight 必须回到政策情报（三页签闭环，不得越界）。
  await page.keyboard.press('ArrowRight');
  await expect(tab(page, '政策情报')).toHaveAttribute('aria-selected', 'true');
  await page.keyboard.press('End');
  await expect(tab(page, '行情监控')).toHaveAttribute('aria-selected', 'true');
  await page.keyboard.press('ArrowLeft');
  await expect(tab(page, '行业情报')).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByTestId('intelligence-tabpanel-industry')).toBeVisible();

  await context.close();
});

test('空列表：显示空态而不是错误或空白面板', async ({browser}) => {
  const context = await browser.newContext({viewport: {width: 1280, height: 720}, reducedMotion: 'reduce'});
  const page = await newMockedPage(context, {payload: {generated_at: '2026-10-19T08:00:00Z', items: []}});

  await page.goto('/intelligence');
  await expect(page.getByRole('heading', {name: '情报聚合'})).toBeVisible({timeout: 15_000});
  await settleRoute(page);
  await tab(page, '行情监控').click();

  await expect(page.getByText('暂无行情指标')).toBeVisible();
  await expect(page.getByRole('alert')).toHaveCount(0);
  await expect(page.getByTestId('market-indicator-group-tabs')).toHaveCount(0);
  await expect(page.getByTestId(/^market-indicator-group-(fx-rates|commodity-futures|semiconductor-index|other)$/)).toHaveCount(0);
  await page.screenshot({path: resolve(evidenceDirectory, 'task18-desktop-1280-market-empty.png')});

  await context.close();
});

test('失败后重试：首个请求 500 走错误面板，重试后恢复三态数据', async ({browser}) => {
  const context = await browser.newContext({viewport: {width: 1280, height: 720}, reducedMotion: 'reduce'});
  const page = await newMockedPage(context, {failFirst: true});

  await page.goto('/intelligence');
  await expect(page.getByRole('heading', {name: '情报聚合'})).toBeVisible({timeout: 15_000});
  await settleRoute(page);
  await tab(page, '行情监控').click();

  const alert = page.getByRole('alert');
  await expect(alert).toContainText('行情指标加载失败');
  await expect(alert).toContainText('行情指标服务不可用');
  await page.screenshot({path: resolve(evidenceDirectory, 'task18-desktop-1280-market-error.png')});

  await page.getByRole('button', {name: '重试'}).click();

  await expect(page.getByTestId('market-indicator-status-fx-CNY')).toHaveText('触发中');
  await expect(alert).toHaveCount(0);

  await context.close();
});
