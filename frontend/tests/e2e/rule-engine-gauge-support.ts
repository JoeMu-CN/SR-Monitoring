import {expect, type Page} from '@playwright/test';

/**
 * ⑤ 总分层几何回归的共享支撑：确定性 API fixture + 真实浏览器几何测量/断言。
 *
 * 为什么必须进真实浏览器：缺陷是 CSS 几何重叠，jsdom 不解析 Tailwind 布局。
 * 断言契约（单位 CSS px）：
 * 1. 得分标识底边 ≤ 彩条顶边（完整位于彩条上方，不被覆盖）；
 * 2. 指针底端落在彩条纵向范围内（[barTop, barBottom] ± 0.5）；
 * 3. 指针水平中心 = 彩条左缘 + 分数% × 彩条宽度；
 * 4. P1/P2/P3 刻度与彩条等高且对齐阈值百分比；
 * 5. 0/100 分边界对齐后标识不被横向滚动容器裁剪。
 *
 * fixture 拦截方式同 todo-4-routes.spec.ts，不依赖后端与业务数据库。
 */

export const VIEWPORT_DESKTOP = {width: 1280, height: 900};
export const VIEWPORT_NARROW = {width: 375, height: 812};

export interface Box {
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
}

export interface GaugeGeometry {
  readonly badge: Box;
  readonly pointer: Box;
  readonly bar: Box;
  readonly scroll: Box;
  /** 直接包裹仪表盘的容器：定位根因层（pt-7 必须落在其内层 relative 上）。 */
  readonly wrapper: Box;
}

const DIMENSION_KEY = 'geopolitical';
const MATCH_COLUMNS = ['entity', 'location', 'product', 'country', 'industry'];
const THRESHOLDS = {p1: 85, p2: 65, p3: 40} as const;

const permittedUser = {
  user: {id: 1, username: 'gauge-e2e', email: null, display_name: '仪表盘几何验证账号', role: 'platform_admin', status: 'active', last_login_at: null, created_at: '2026-08-30T00:00:00Z'},
  permissions: ['risk_view', 'supplier_view', 'source_status_view', 'rule_summary_view', 'risk_query_use', 'user_manage', 'source_manage', 'supplier_manage', 'rule_manage'],
};

const dimensionRead = () => ({
  key: DIMENSION_KEY, label: '地缘政治与安全', description: '制裁、出口管制与地缘冲突风险',
  content_items: ['制裁', '出口管制'],
  data_sources: [{code: 'ofac-sdn', name: 'OFAC SDN', declared_status: 'connected', linked: true, enabled: true, adapter_status: 'builtin', last_collected_at: '2026-09-13T10:00:00Z', valid_signal_count: 5}],
  event_types: ['geopolitical'], match_columns: MATCH_COLUMNS,
  enabled: true, has_override: false, active_alerts: 1,
  scoring: {
    rule_version: 'geopolitical-v1',
    severity_scores: {critical: 35, high: 28, medium: 20, low: 10},
    association_scores: {registry_no: 30, legal_name: 25, alias: 25, country: 8},
    p1_min: THRESHOLDS.p1, p2_min: THRESHOLDS.p2, p3_min: THRESHOLDS.p3, forced_rules: [],
  },
});

const inputsRead = () => ({
  declared_total: 1, declared_linked: 1, declared_enabled: 1,
  observed: [{code: 'ofac-sdn', name: 'OFAC SDN', signal_count: 12, latest_at: '2026-09-13T10:00:00Z'}],
  has_input: true,
});

const levelFor = (total: number) => (total >= 85 ? 'P1' : total >= 65 ? 'P2' : total >= 40 ? 'P3' : 'P4');

const traceRead = (total: number) => ({
  available: true,
  event: {event_type: 'geopolitical', event_subtype: 'sanctions', severity: 'high', summary: '某国新增对某行业的制裁清单', confidence: 0.92, published_at: '2026-09-13T10:00:00Z', source_name: 'OFAC SDN'},
  routing: {key: DIMENSION_KEY, label: '地缘政治与安全', match_columns: MATCH_COLUMNS},
  match: {match_type: 'registry_no+country', match_reasons: ['注册编号精确命中供应商'], match_evidence: [{object_type: 'supplier', supplier_id: 1, registry_no: '91310000MA1K1234XX'}]},
  score: {total, level: levelFor(total), detail: {severity: 28, association: 25, source_credibility: 14, timeliness: 5, product_relevance: 0}, level_cap: null, forced_rule: null},
  samples: [],
});

const globalConfigRead = () => ({
  source: 'default', enabled: false, effective: {}, defaults: {}, shadowed_by: {}, forced_rules_shadowed_by: [], dropped_dimension_rules: [],
});

const monitoringHealthRead = () => ({
  as_of: '2026-09-13T10:00:00Z', overall: 'ok', sources: [],
  scheduler: {status: 'ok', last_heartbeat_at: '2026-09-13T10:00:00Z', age_seconds: 12, interval_seconds: 60, stale_after_seconds: 300},
  processing: {total: 0, classification_failed: 0, backlog_over_1h: 0, oldest_pending_age_seconds: null, last_run: {status: 'idle', started_at: null, finished_at: null, processed: 0, filtered: 0, failed: 0}},
});

/** 确定性 API fixture：顺序敏感——具体路径必须先于前缀路径判断。 */
export const apiFixture = (url: string, total: number): unknown => {
  if (url.includes('/auth/me')) return permittedUser;
  if (url.includes('/rule-engine/dimensions/') && url.includes('/inputs')) return inputsRead();
  if (url.includes('/rule-engine/dimensions/') && url.includes('/trace')) return traceRead(total);
  if (url.includes('/rule-engine/dimensions')) return [dimensionRead()];
  if (url.includes('/rule-engine/match-columns')) return {match_columns: MATCH_COLUMNS, event_types: [{value: 'geopolitical', label: '地缘政治'}], event_subtypes: [{value: 'sanctions', label: '制裁'}]};
  if (url.includes('/rule-engine/global-config')) return globalConfigRead();
  // SignalFilterSection 与流水线同时挂载：字段缺失会触发 undefined.map 卸载整棵 React 树。
  if (url.includes('/signals/filter-config')) return {high_impact: [], priority_countries: [], list_sources: [], source: 'default', updated_at: null};
  if (url.includes('/risk-alerts')) return {items: [], total: 0};
  if (url.includes('/suppliers')) return {items: [], total: 0};
  if (url.includes('/sources')) return [];
  if (url.includes('/collection-runs')) return {items: [], total: 0};
  if (url.includes('/system/monitoring-health')) return monitoringHealthRead();
  if (url.includes('/system/health')) return {status: 'ok', database: 'ok'};
  if (url.includes('/agent/status')) return {enabled: false, configured: false, llm_configured: false, model: null};
  return {detail: 'Unhandled deterministic API fixture'};
};

const collectConsoleErrors = (page: Page): string[] => {
  const errors: string[] = [];
  page.on('console', (message) => { if (message.type() === 'error') errors.push(message.text()); });
  return errors;
};

/**
 * 打开 /rules 观察态：mock 全部 API、等待开屏自检遮罩退场与路由内容淡入
 * （不等这两个信号会拿到被遮罩盖住的元素或白图），返回 console error 收集器。
 * 减少动效保证入场动画（y:8）不会把标识临时下移，几何测量必须发生在静止态。
 */
export const openGauge = async (page: Page, total: number): Promise<string[]> => {
  const consoleErrors = collectConsoleErrors(page);
  await page.emulateMedia({reducedMotion: 'reduce'});
  await page.route('**/api/**', (route) => route.fulfill({contentType: 'application/json', body: JSON.stringify(apiFixture(route.request().url(), total))}));
  await page.goto('/rules');
  await expect(page.getByRole('status', {name: '正在初始化供应商风险监控平台'})).toBeHidden({timeout: 20_000});
  await expect(page.getByTestId('route-content')).toHaveCSS('opacity', '1', {timeout: 20_000});
  await expect(page.getByTestId('rule-engine-observation')).toBeVisible({timeout: 20_000});
  await expect(page.getByTestId('rule-engine-pipeline-gauge')).toContainText(`${total} 分`, {timeout: 20_000});
  return consoleErrors;
};

/** 同一页面切换总分后重载（覆盖 0/100 的 translate 边界对齐）。 */
export const reloadGauge = async (page: Page, total: number): Promise<void> => {
  await page.unroute('**/api/**');
  await page.route('**/api/**', (route) => route.fulfill({contentType: 'application/json', body: JSON.stringify(apiFixture(route.request().url(), total))}));
  await page.reload();
  await expect(page.getByTestId('rule-engine-pipeline-gauge')).toContainText(`${total} 分`, {timeout: 20_000});
};

const requireBox = (box: Box | null, name: string): Box => {
  if (box === null) throw new Error(`无法取得 ${name} 的 boundingBox`);
  return box;
};

const describeGeometry = (geometry: GaugeGeometry) =>
  [
    `badge ${geometry.badge.y.toFixed(2)}→${(geometry.badge.y + geometry.badge.height).toFixed(2)}`,
    `pointer ${geometry.pointer.y.toFixed(2)}→${(geometry.pointer.y + geometry.pointer.height).toFixed(2)}`,
    `bar ${geometry.bar.y.toFixed(2)}→${(geometry.bar.y + geometry.bar.height).toFixed(2)}`,
    `scroll top=${geometry.scroll.y.toFixed(2)}`,
    `wrapper top=${geometry.wrapper.y.toFixed(2)}`,
  ].join(' | ');

/** 测量并输出几何证据（输出保留：几何数字是本回归的运行时证据）。 */
export const measureGauge = async (page: Page, label: string): Promise<GaugeGeometry> => {
  const [badge, pointer, bar, scroll] = await Promise.all([
    page.getByTestId('rule-engine-pipeline-gauge').boundingBox(),
    page.getByTestId('rule-engine-pipeline-gauge-pointer').boundingBox(),
    page.getByTestId('rule-engine-pipeline-gauge-bar').boundingBox(),
    page.getByTestId('rule-engine-pipeline-gauge-scroll').boundingBox(),
  ]);
  const wrapper = await page.getByTestId('rule-engine-pipeline-gauge-scroll').locator('> div').first().boundingBox();
  const geometry: GaugeGeometry = {
    badge: requireBox(badge, '得分标识'),
    pointer: requireBox(pointer, '指针'),
    bar: requireBox(bar, '彩条'),
    scroll: requireBox(scroll, '滚动容器'),
    wrapper: requireBox(wrapper, '仪表盘包裹层'),
  };
  console.log(`[gauge-geometry] ${label} ${describeGeometry(geometry)}`);
  return geometry;
};

/** 契约 1–3、5：标识在彩条上方、指针落到彩条并水平对齐分数、标识不被滚动容器裁剪。 */
export const expectGaugeAboveBar = (geometry: GaugeGeometry, total: number): void => {
  const badgeBottom = geometry.badge.y + geometry.badge.height;
  const badgeRight = geometry.badge.x + geometry.badge.width;
  const pointerBottom = geometry.pointer.y + geometry.pointer.height;
  const barTop = geometry.bar.y;
  const barBottom = geometry.bar.y + geometry.bar.height;
  const context = describeGeometry(geometry);

  expect(badgeBottom, `得分标识与彩条纵向重叠（${context}）`).toBeLessThanOrEqual(barTop + 0.5);
  expect(pointerBottom, `指针未能到达彩条（${context}）`).toBeGreaterThanOrEqual(barTop - 0.5);
  expect(pointerBottom, `指针越过彩条（${context}）`).toBeLessThanOrEqual(barBottom + 0.5);
  const expectedPointerCenter = geometry.bar.x + (geometry.bar.width * total) / 100;
  const pointerCenter = geometry.pointer.x + geometry.pointer.width / 2;
  expect(Math.abs(pointerCenter - expectedPointerCenter), `指针未对齐 ${total} 分位置（${context}）`).toBeLessThanOrEqual(1.5);
  expect(geometry.badge.x, `得分标识左侧被裁剪（${context}）`).toBeGreaterThanOrEqual(geometry.scroll.x - 0.5);
  expect(badgeRight, `得分标识右侧被裁剪（${context}）`).toBeLessThanOrEqual(geometry.scroll.x + geometry.scroll.width + 0.5);
};

/** 契约 4：刻度线与彩条等高，横向位置等于阈值百分比。 */
export const expectTicksAligned = async (page: Page, bar: Box): Promise<void> => {
  for (const [key, value] of [['P3', THRESHOLDS.p3], ['P2', THRESHOLDS.p2], ['P1', THRESHOLDS.p1]] as const) {
    const tickBox = requireBox(await page.getByTestId(`rule-engine-pipeline-tick-${key}`).boundingBox(), `刻度 ${key}`);
    expect(tickBox.y, `刻度 ${key} 与彩条纵向错位`).toBeGreaterThanOrEqual(bar.y - 0.5);
    expect(tickBox.y + tickBox.height, `刻度 ${key} 与彩条纵向错位`).toBeLessThanOrEqual(bar.y + bar.height + 0.5);
    const expectedX = bar.x + (bar.width * value) / 100;
    expect(Math.abs(tickBox.x + tickBox.width / 2 - expectedX), `刻度 ${key} 未对齐 ${value}%`).toBeLessThanOrEqual(1.5);
  }
};

/** 深色模式为 class 驱动（index.css 的 @custom-variant）：切换后几何必须与浅色一致。 */
export const enableDarkMode = async (page: Page): Promise<{light: string; dark: string}> => {
  const pipeline = page.getByTestId('rule-engine-pipeline');
  const light = await pipeline.evaluate((element) => getComputedStyle(element).backgroundColor);
  await page.evaluate(() => document.documentElement.classList.add('dark'));
  // 主题切换带颜色过渡：轮询到计算背景色真正变化，避免读到过渡初值。
  await expect.poll(() => pipeline.evaluate((element) => getComputedStyle(element).backgroundColor)).not.toBe(light);
  const dark = await pipeline.evaluate((element) => getComputedStyle(element).backgroundColor);
  return {light, dark};
};
