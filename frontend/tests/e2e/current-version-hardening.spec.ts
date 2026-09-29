import {expect, test, type APIResponse, type BrowserContext, type Locator, type Page} from '@playwright/test';
import {mkdir, writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';

/**
 * 任务10：当前版本真实 API 端到端回归与视觉验收。
 *
 * 全部断言走真实登录、真实后端 API 与真实浏览器交互，不 mock 任何 API；数据由
 * backend/tests/seed_hardening_e2e.py 在隔离栈启动后显式写入，本文件在运行时动态
 * 发现供应商、提醒与投递记录，不依赖固定 alert id。
 */
test.use({trace: 'on'});

const productionBaseUrl = 'http://127.0.0.1:18080';
const testUsername = 'e2e-platform-admin';
const viewerUsername = 'e2e-viewer';
const testPassword = 'E2E-Test-Only-2026!';
const desktopViewport = {width: 1280, height: 720};
const mobileViewport = {width: 390, height: 844};
const narrowViewport = {width: 320, height: 690};
const longChineseName = '苏州泓睿精密制造与供应链合规管理研究所有限公司华东区域运营分公司第二生产基地';
const evidenceDirectory = process.env.HARDENING_EVIDENCE_DIR
  ? resolve(process.env.HARDENING_EVIDENCE_DIR)
  : resolve(process.cwd(), '..', '.omo', 'evidence', 'task-10-current-version-hardening');

interface ConsoleError {
  readonly source: string;
  readonly text: string;
  readonly line: number;
  readonly column: number;
}

interface FailedRequest {
  readonly method: string;
  readonly path: string;
  readonly error: string;
}

interface HttpError {
  readonly method: string;
  readonly path: string;
  readonly status: number;
}

interface BrowserEvidence {
  readonly consoleErrors: ConsoleError[];
  readonly failedRequests: FailedRequest[];
  readonly httpErrors: HttpError[];
}

interface SupplierDetail {
  id: number;
  supplier_code: string;
  legal_name: string;
  country_code: string;
  registry_no: string | null;
  registration_address: string | null;
  industry: string | null;
  raw_materials: string[];
  enabled: boolean;
  updated_at: string;
  aliases: Array<{id: number; alias: string; language: string | null}>;
  sites: Array<{
    id: number;
    site_name: string;
    country_code: string;
    region: string | null;
    city: string | null;
    district: string | null;
    address: string;
    latitude: number | null;
    longitude: number | null;
  }>;
  products: Array<{id: number; name: string; keywords: string[]}>;
}

interface SupplierListResponse {
  items: Array<SupplierDetail & {current_risk_level: string | null; current_risk_score: number | null}>;
  total: number;
  limit: number;
  offset: number;
}

interface DashboardSummaryRead {
  total_current: number;
  window_days: number;
}

interface DeliveryRead {
  id: number;
  alert_id: number | null;
  channel: string;
  status: string;
  title: string | null;
}

const pathFromUrl = (url: string) => decodeURIComponent(new URL(url).pathname);

const readJson = async <T>(response: APIResponse): Promise<T> => (await response.json()) as T;

const collectBrowserEvidence = (page: Page): BrowserEvidence => {
  const evidence: BrowserEvidence = {consoleErrors: [], failedRequests: [], httpErrors: []};
  page.on('console', (message) => {
    if (message.type() === 'error') {
      const location = message.location();
      evidence.consoleErrors.push({
        source: location.url ? pathFromUrl(location.url) : 'browser',
        text: message.text(),
        line: location.lineNumber,
        column: location.columnNumber,
      });
    }
  });
  page.on('requestfailed', (request) => {
    evidence.failedRequests.push({
      method: request.method(),
      path: pathFromUrl(request.url()),
      error: request.failure()?.errorText ?? '未知网络错误',
    });
  });
  page.on('response', (response) => {
    if (response.status() >= 400) {
      evidence.httpErrors.push({
        method: response.request().method(),
        path: pathFromUrl(response.url()),
        status: response.status(),
      });
    }
  });
  return evidence;
};

// 本期用例主动触发的失败路径必须逐一登记，其余任何 4xx/5xx 仍视为回归失败。
// - GET /auth/me 401：未登录页首次会话探测，属设计内；
// - GET /agent/status 403：只读角色无权使用风险查询助手，App 已按设计降级；
// - 供应商 PUT/DELETE 409：并发版本冲突与有风险历史的删除保护，本用例主动触发。
const isExpectedHttpError = (error: HttpError) => (
  (error.method === 'GET' && error.path === '/api/v1/auth/me' && error.status === 401) ||
  (error.method === 'GET' && error.path === '/api/v1/agent/status' && error.status === 403) ||
  (error.method === 'POST' && error.path === '/api/v1/auth/logout' && error.status === 403) ||
  (error.method === 'PUT' && error.path.startsWith('/api/v1/suppliers/') && error.status === 409) ||
  (error.method === 'DELETE' && error.path.startsWith('/api/v1/suppliers/') && error.status === 409)
);

// 浏览器会把预期的 4xx 资源响应记为控制台错误；仅放行本用例明确触发的路径与状态码。
const expectedConsoleStatuses = (source: string): readonly number[] => {
  if (source === '/api/v1/auth/me') return [401];
  if (source === '/api/v1/agent/status') return [403];
  if (source.startsWith('/api/v1/suppliers/')) return [409];
  return [];
};

const isExpectedConsoleError = (error: ConsoleError) => {
  const statusMatch = /status of (\d+)/.exec(error.text);
  return statusMatch !== null && expectedConsoleStatuses(error.source).includes(Number(statusMatch[1]));
};

const assertBrowserEvidence = async (name: string, evidence: BrowserEvidence) => {
  const unexpectedHttpErrors = evidence.httpErrors.filter((error) => !isExpectedHttpError(error));
  const unexpectedConsoleErrors = evidence.consoleErrors.filter((error) => !isExpectedConsoleError(error));
  await writeFile(
    resolve(evidenceDirectory, `${name}-console-network.json`),
    `${JSON.stringify({...evidence, unexpectedHttpErrors, unexpectedConsoleErrors}, null, 2)}\n`,
    'utf8',
  );
  expect(unexpectedConsoleErrors).toEqual([]);
  expect(evidence.failedRequests).toEqual([]);
  expect(unexpectedHttpErrors).toEqual([]);
};

const loginAs = async (page: Page, username: string, landingPath: string, heading: string) => {
  await page.goto(landingPath);
  await page.getByLabel('用户名').fill(username);
  await page.getByLabel('密码').fill(testPassword);
  await page.getByRole('button', {name: '登录'}).click();
  await expect(page.getByRole('heading', {name: heading})).toBeVisible({timeout: 15_000});
};

const waitForSettledRoute = async (page: Page) => {
  await expect(page.getByRole('status', {name: '正在初始化供应商风险监控平台'})).toBeHidden({timeout: 5_000});
  await expect(page.getByTestId('route-content')).toHaveCSS('opacity', '1');
};

const assertNoHorizontalOverflow = async (page: Page) => {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
};

const assertKeyboardFocus = async (locator: Locator) => {
  await expect(locator).toBeFocused();
  expect(await locator.evaluate((element) => element.matches(':focus-visible'))).toBe(true);
};

const assertVisibleWithinViewport = async (locator: Locator) => {
  await expect(locator).toBeVisible();
  expect(await locator.evaluate((element) => {
    const bounds = element.getBoundingClientRect();
    return bounds.top >= 0 && bounds.left >= 0 && bounds.right <= window.innerWidth && bounds.bottom <= window.innerHeight;
  })).toBe(true);
};

// 写请求需要同源 Origin 与会话派生的双提交 CSRF Token；token 来自登录后下发的 <session>_csrf Cookie。
const writeHeaders = async (context: BrowserContext) => {
  const csrfCookie = (await context.cookies()).find((cookie) => cookie.name.endsWith('_csrf'));
  if (csrfCookie === undefined) throw new Error('当前会话缺少 CSRF Cookie，无法执行真实写请求。');
  return {Origin: productionBaseUrl, 'X-CSRF-Token': csrfCookie.value};
};

const fetchSuppliers = async (page: Page, query: string): Promise<SupplierListResponse> => {
  const response = await page.request.get(`/api/v1/suppliers?${query}`);
  expect(response.status()).toBe(200);
  return readJson<SupplierListResponse>(response);
};

const getSupplierDetail = async (page: Page, id: number): Promise<SupplierDetail> => {
  const response = await page.request.get(`/api/v1/suppliers/${id}`);
  expect(response.status()).toBe(200);
  return readJson<SupplierDetail>(response);
};

const supplierUpdatePayload = (
  detail: SupplierDetail,
  overrides: {legalName?: string; enabled?: boolean} = {},
) => ({
  legal_name: overrides.legalName ?? detail.legal_name,
  country_code: detail.country_code,
  registry_no: detail.registry_no,
  registration_address: detail.registration_address,
  industry: detail.industry,
  raw_materials: detail.raw_materials,
  enabled: overrides.enabled ?? detail.enabled,
  aliases: detail.aliases.map((item) => ({id: item.id, alias: item.alias, language: item.language})),
  sites: detail.sites.map((item) => ({...item})),
  products: detail.products.map((item) => ({id: item.id, name: item.name, keywords: item.keywords})),
  expected_updated_at: detail.updated_at,
});

// 当前有效提醒可能超过单页上限（seed 预置 100+），按页发现指定供应商的提醒，避免依赖第一页。
const findCurrentAlertForSupplier = async (
  page: Page,
  supplierId: number,
): Promise<{id: number; supplier_id: number; supplier_name: string} | null> => {
  for (let offset = 0; offset < 1000; offset += 100) {
    const response = await page.request.get(`/api/v1/risk-alerts?status=current&limit=100&offset=${offset}`);
    expect(response.status()).toBe(200);
    const payload = await readJson<{items: Array<{id: number; supplier_id: number; supplier_name: string}>}>(response);
    const found = payload.items.find((item) => item.supplier_id === supplierId);
    if (found !== undefined) return found;
    if (payload.items.length < 100) break;
  }
  return null;
};

test.beforeAll(async () => {
  expect(process.env.PLAYWRIGHT_BASE_URL).toBe(productionBaseUrl);
  await mkdir(evidenceDirectory, {recursive: true});
});

test.describe('桌面端真实 API 场景', () => {
  test.use({viewport: desktopViewport});

  test('未登录深链与会话化后低权限写操作准确报告', async ({page}) => {
    const evidence = collectBrowserEvidence(page);

    // 未登录直接访问受保护深链只显示登录页，不泄露供应商管理内容。
    await page.goto('/suppliers');
    await expect(page.getByLabel('用户名')).toBeVisible();
    await expect(page.getByRole('heading', {name: '供应商管理'})).toHaveCount(0);

    // 登录后保留原深链并落到供应商管理页。
    await page.getByLabel('用户名').fill(viewerUsername);
    await page.getByLabel('密码').fill(testPassword);
    await page.getByRole('button', {name: '登录'}).click();
    await expect(page.getByRole('heading', {name: '供应商管理'})).toBeVisible({timeout: 15_000});
    await expect(page).toHaveURL(/\/suppliers$/);

    // 只读角色看不到写操作入口；启停按钮存在但被禁用，编辑入口完全不渲染。
    await expect(page.getByRole('button', {name: '导入供应商'})).toBeDisabled();
    await expect(page.getByRole('button', {name: /^编辑供应商：/})).toHaveCount(0);
    const toggleButtons = page.getByRole('button', {name: /^(暂停监控|恢复监控)：/});
    await expect(toggleButtons.first()).toBeVisible({timeout: 15_000});
    await expect(toggleButtons.first()).toBeDisabled();

    // 低权限调用真实管理 API 必须得到 403，而不是被前端按钮隐藏掩盖。
    const headers = await writeHeaders(page.context());
    const list = await fetchSuppliers(page, 'limit=1');
    const targetId = list.items[0].id;
    expect((await page.request.delete(`/api/v1/suppliers/${targetId}`, {headers})).status()).toBe(403);
    expect((await page.request.get('/api/v1/notifications/deliveries')).status()).toBe(403);
    expect((await page.request.get('/api/v1/sources/admin')).status()).toBe(403);

    await assertNoHorizontalOverflow(page);
    await assertBrowserEvidence('task10-low-privilege', evidence);
  });

  test('无损编辑保留暂停状态且长中文不溢出', async ({page}) => {
    const evidence = collectBrowserEvidence(page);
    await loginAs(page, testUsername, '/suppliers', '供应商管理');
    const headers = await writeHeaders(page.context());

    // 选一个当前启用、无当前风险且仅有单地点单产品的供应商，避免多子项拼接干扰无损断言。
    const candidates = await fetchSuppliers(page, 'limit=100&enabled=true&has_current_alert=false');
    const target = candidates.items.find((item) => item.sites.length === 1 && item.products.length === 1);
    expect(target).toBeDefined();
    const supplierId = target!.id;
    const before = await getSupplierDetail(page, supplierId);

    try {
      // 先真实暂停，再通过网页编辑；监控启停不是编辑表单能覆盖的字段。
      expect((await page.request.patch(`/api/v1/suppliers/${supplierId}/enabled`, {headers, data: {enabled: false}})).status()).toBe(200);
      await page.goto('/suppliers');
      await waitForSettledRoute(page);

      const row = page.getByRole('row').filter({hasText: before.supplier_code});
      await expect(row).toBeVisible();
      await expect(row.getByText('暂停监控')).toBeVisible();
      await row.getByRole('button', {name: `编辑供应商：${before.legal_name}`}).click();
      await expect(page.getByText(`编辑供应商 · ${before.supplier_code}`)).toBeVisible();
      await page.getByPlaceholder('例如: 杭州智芯半导体有限公司').fill(longChineseName);
      await page.getByRole('button', {name: '保存修改'}).click();
      await expect(page.getByText(`编辑供应商 · ${before.supplier_code}`)).toBeHidden({timeout: 15_000});

      const updatedRow = page.getByRole('row').filter({hasText: longChineseName});
      await expect(updatedRow).toBeVisible();
      await expect(updatedRow.getByText('暂停监控')).toBeVisible();

      // 无损往返：长中文名生效，暂停状态、原材料、别名、地点与产品 id 全部保留。
      const after = await getSupplierDetail(page, supplierId);
      expect(after.legal_name).toBe(longChineseName);
      expect(after.enabled).toBe(false);
      expect(after.raw_materials).toEqual(before.raw_materials);
      expect(after.aliases.map((item) => item.id)).toEqual(before.aliases.map((item) => item.id));
      expect(after.aliases.map((item) => item.alias)).toEqual(before.aliases.map((item) => item.alias));
      expect(after.sites.map((item) => item.id)).toEqual(before.sites.map((item) => item.id));
      expect(after.products.map((item) => item.id)).toEqual(before.products.map((item) => item.id));

      await assertNoHorizontalOverflow(page);
      await page.screenshot({path: resolve(evidenceDirectory, 'desktop.png'), fullPage: true});
      await assertBrowserEvidence('task10-desktop-lossless-edit', evidence);
    } finally {
      // 恢复供应商原始名称与启用状态，保证既有 E2E 回归看到与 seed 一致的基线。
      const latest = await getSupplierDetail(page, supplierId);
      const restore = await page.request.put(`/api/v1/suppliers/${supplierId}`, {
        headers,
        data: supplierUpdatePayload(latest, {legalName: before.legal_name, enabled: before.enabled}),
      });
      expect(restore.status()).toBe(200);
    }
  });

  test('有风险的供应商删除被拒绝且详情保留', async ({page}) => {
    const evidence = collectBrowserEvidence(page);
    await loginAs(page, testUsername, '/suppliers', '供应商管理');

    // 带当前风险的供应商必然存在事件匹配历史，服务端必须原子拒绝硬删除。
    const candidates = await fetchSuppliers(page, 'limit=100&enabled=true&has_current_alert=true');
    const target = candidates.items.find((item) => item.current_risk_level !== null);
    expect(target).toBeDefined();

    const row = page.getByRole('row').filter({hasText: target!.supplier_code});
    await expect(row).toBeVisible();
    await row.getByRole('button', {name: `编辑供应商：${target!.legal_name}`}).click();
    await expect(page.getByText(`编辑供应商 · ${target!.supplier_code}`)).toBeVisible();
    await expect(page.getByText(/删除将被服务端阻止/)).toBeVisible();

    page.on('dialog', (dialog) => void dialog.accept());
    await page.getByRole('button', {name: '删除供应商'}).click();
    // 弹窗内错误提示与 App 全局错误横幅都会出现同一文案，取首个可见元素即可。
    await expect(page.getByText(/供应商存在风险关联历史，已阻止删除/).first()).toBeVisible({timeout: 15_000});

    // 删除被拒绝后供应商与风险详情都必须仍可访问。
    expect((await page.request.get(`/api/v1/suppliers/${target!.id}`)).status()).toBe(200);
    const relatedAlert = await findCurrentAlertForSupplier(page, target!.id);
    expect(relatedAlert).not.toBeNull();

    await page.getByRole('button', {name: '取消'}).click();
    await page.goto(`/risks/${relatedAlert!.id}`);
    await expect(page.getByRole('heading', {name: relatedAlert!.supplier_name})).toBeVisible({timeout: 15_000});
    await expect(page.getByRole('heading', {name: '原始信号'})).toBeVisible();
    await page.screenshot({path: resolve(evidenceDirectory, 'desktop-delete-blocked.png'), fullPage: true});

    await assertNoHorizontalOverflow(page);
    await assertBrowserEvidence('task10-delete-blocked', evidence);
  });

  test('总览全量统计超过100条且7/30/90窗口刷新后退一致', async ({page}) => {
    const evidence = collectBrowserEvidence(page);
    await loginAs(page, testUsername, '/overview', '全网供应链风险概览');

    // 服务端汇总必须返回超过 100 条当前有效提醒，且窗口天数可真实切换。
    const summaryResponse = await page.request.get('/api/v1/dashboard/summary?days=30');
    expect(summaryResponse.status()).toBe(200);
    const summary = await readJson<DashboardSummaryRead>(summaryResponse);
    expect(summary.window_days).toBe(30);
    expect(summary.total_current).toBeGreaterThan(100);

    await waitForSettledRoute(page);
    const totalCurrentText = await page.locator('span', {hasText: '当前风险提醒：'}).locator('strong').first().textContent();
    expect(Number((totalCurrentText ?? '').trim())).toBe(summary.total_current);
    await expect(page.getByTestId('overview-period-new')).toContainText('最近 30 天新增提醒');

    await page.getByRole('button', {name: '7 天'}).click();
    await expect(page).toHaveURL(/\/overview\?days=7$/);
    await expect(page.getByTestId('overview-period-new')).toContainText('最近 7 天新增提醒');
    const sevenDay = await readJson<DashboardSummaryRead>(await page.request.get('/api/v1/dashboard/summary?days=7'));
    expect(sevenDay.window_days).toBe(7);

    await page.reload();
    await expect(page).toHaveURL(/\/overview\?days=7$/);
    await expect(page.getByTestId('overview-period-new')).toContainText('最近 7 天新增提醒');

    await page.getByRole('button', {name: '90 天'}).click();
    await expect(page).toHaveURL(/\/overview\?days=90$/);
    await expect(page.getByTestId('overview-period-new')).toContainText('最近 90 天新增提醒');

    await page.goBack();
    await expect(page).toHaveURL(/\/overview\?days=7$/);
    await expect(page.getByTestId('overview-period-new')).toContainText('最近 7 天新增提醒');
    await page.goBack();
    await expect(page).toHaveURL(/\/overview$/);
    await expect(page.getByTestId('overview-period-new')).toContainText('最近 30 天新增提醒');

    // 风险列表按入口 limit=100 如实标注“已加载结果”，与总览全量统计不混淆。
    await page.goto('/risks');
    await expect(page.getByText('当前风险概览')).toBeVisible({timeout: 15_000});
    await expect(page.getByText(/共 100 条已加载结果/)).toBeVisible();
    await assertNoHorizontalOverflow(page);
    await page.screenshot({path: resolve(evidenceDirectory, 'desktop-overview-100-plus.png'), fullPage: true});
    await assertBrowserEvidence('task10-overview-100-plus', evidence);
  });

  test('心跳过期时监控降级且不阻塞风险列表', async ({page}) => {
    const evidence = collectBrowserEvidence(page);
    await loginAs(page, testUsername, '/overview', '全网供应链风险概览');

    // seed 写入过期调度心跳：健康聚合必须判定 degraded，而不是用“无风险”冒充正常。
    const healthResponse = await page.request.get('/api/v1/system/monitoring-health');
    expect(healthResponse.status()).toBe(200);
    const health = await readJson<{overall: string; scheduler: {status: string}}>(healthResponse);
    expect(health.overall).toBe('degraded');
    expect(health.scheduler.status).toBe('stale');

    const banner = page.getByTestId('monitoring-health-banner');
    await expect(banner).toBeVisible({timeout: 15_000});
    await expect(banner).toHaveAttribute('data-state', 'degraded');
    await expect(banner).toHaveAttribute('role', 'alert');
    await expect(banner).toContainText('部分链路异常，结果可能不完整');
    await page.screenshot({path: resolve(evidenceDirectory, 'desktop-degraded.png'), fullPage: true});

    // 降级只影响监控结论，不清空也不阻塞风险列表。
    await page.goto('/risks');
    await expect(page.getByText('当前风险概览')).toBeVisible({timeout: 15_000});
    await assertNoHorizontalOverflow(page);
    await assertBrowserEvidence('task10-degraded', evidence);
  });

  test('从通知投递记录提取真实风险详情链接并打开', async ({page}) => {
    const evidence = collectBrowserEvidence(page);
    await loginAs(page, testUsername, '/overview', '全网供应链风险概览');

    // 真实调用通知投递 API，从投递内容里提取 /risks/{id}，不依赖固定 alert id。
    const deliveriesResponse = await page.request.get('/api/v1/notifications/deliveries?limit=200');
    expect(deliveriesResponse.status()).toBe(200);
    const deliveries = await readJson<{items: DeliveryRead[]; total: number}>(deliveriesResponse);
    expect(deliveries.items.length).toBeGreaterThan(0);

    const linkPattern = /\/risks\/(\d+)/;
    const titleLinked = deliveries.items.find(
      (item) => item.alert_id !== null && linkPattern.test(item.title ?? ''),
    );
    const alertLinked = deliveries.items.find((item) => item.alert_id !== null);
    const riskId = titleLinked !== undefined
      ? Number(linkPattern.exec(titleLinked.title ?? '')?.[1])
      : alertLinked?.alert_id ?? null;
    expect(riskId).not.toBeNull();
    const discoveredFrom = titleLinked !== undefined ? 'delivery-title' : 'delivery-alert-id';

    const alertResponse = await page.request.get(`/api/v1/risk-alerts/${riskId}`);
    expect(alertResponse.status()).toBe(200);
    const alert = await readJson<{id: number; supplier_name: string}>(alertResponse);

    await page.goto(`/risks/${riskId}`);
    await expect(page).toHaveURL(new RegExp(`/risks/${riskId}$`));
    await expect(page.getByRole('heading', {name: alert.supplier_name})).toBeVisible({timeout: 15_000});
    await expect(page.getByRole('heading', {name: '原始信号'})).toBeVisible();

    await page.reload();
    await expect(page.getByRole('heading', {name: alert.supplier_name})).toBeVisible();
    await page.goBack();
    await expect(page).toHaveURL(/\/overview$/);

    await writeFile(
      resolve(evidenceDirectory, 'task10-notification-link.json'),
      `${JSON.stringify({risk_id: riskId, discovered_from: discoveredFrom, delivery_total: deliveries.total}, null, 2)}\n`,
      'utf8',
    );
    await page.goto(`/risks/${riskId}`);
    await page.screenshot({path: resolve(evidenceDirectory, 'desktop-notification-detail.png'), fullPage: true});
    await assertNoHorizontalOverflow(page);
    await assertBrowserEvidence('task10-notification-link', evidence);
  });

  test('并发修改触发版本冲突且详情保留旧输入提示', async ({page}) => {
    const evidence = collectBrowserEvidence(page);
    await loginAs(page, testUsername, '/suppliers', '供应商管理');
    const headers = await writeHeaders(page.context());

    const candidates = await fetchSuppliers(page, 'limit=100&enabled=true&has_current_alert=false');
    const target = candidates.items.find((item) => item.sites.length === 1 && item.products.length === 1);
    expect(target).toBeDefined();
    const supplierId = target!.id;
    const before = await getSupplierDetail(page, supplierId);
    const competingName = `${before.legal_name}（并发改写）`;

    const row = page.getByRole('row').filter({hasText: before.supplier_code});
    await expect(row).toBeVisible();
    await row.getByRole('button', {name: `编辑供应商：${before.legal_name}`}).click();
    await expect(page.getByText(`编辑供应商 · ${before.supplier_code}`)).toBeVisible();

    try {
      // 另一个会话基于同一版本抢先提交，网页持有的 expected_updated_at 随即过期。
      const competingResponse = await page.request.put(`/api/v1/suppliers/${supplierId}`, {
        headers,
        data: supplierUpdatePayload(before, {legalName: competingName}),
      });
      expect(competingResponse.status()).toBe(200);

      await page.getByRole('button', {name: '保存修改'}).click();
      await expect(page.getByText(/供应商资料已被其他用户修改/)).toBeVisible({timeout: 15_000});
      // 409 后弹窗保留用户输入，不静默覆盖最新版本。
      await expect(page.getByPlaceholder('例如: 杭州智芯半导体有限公司')).not.toHaveValue('');
      await page.screenshot({path: resolve(evidenceDirectory, 'desktop-version-conflict.png'), fullPage: true});
    } finally {
      await page.getByRole('button', {name: '取消'}).click();
      const latest = await getSupplierDetail(page, supplierId);
      const restore = await page.request.put(`/api/v1/suppliers/${supplierId}`, {
        headers,
        data: supplierUpdatePayload(latest, {legalName: before.legal_name, enabled: before.enabled}),
      });
      expect(restore.status()).toBe(200);
    }

    await assertBrowserEvidence('task10-version-conflict', evidence);
  });

  test('手工 JSON 导入能力不可用时返回真实503', async ({page}) => {
    const evidence = collectBrowserEvidence(page);
    await loginAs(page, testUsername, '/overview', '全网供应链风险概览');
    const headers = await writeHeaders(page.context());

    const sourcesResponse = await page.request.get('/api/v1/sources/admin');
    expect(sourcesResponse.status()).toBe(200);
    const sources = await readJson<Array<{id: number; code: string; enabled: boolean}>>(sourcesResponse);
    const manualSource = sources.find((source) => source.code === 'manual-json');
    const originallyEnabled = manualSource?.enabled ?? false;
    let disabledForProbe = false;

    try {
      // 手工导入依赖启用中的 manual-json 信息源；先停用再探测真实 503，随后恢复。
      if (manualSource !== undefined && manualSource.enabled) {
        expect((await page.request.put(`/api/v1/sources/${manualSource.id}`, {headers, data: {enabled: false}})).status()).toBe(200);
        disabledForProbe = true;
      }

      const importResponse = await page.request.post('/api/v1/signals/import', {
        headers,
        multipart: {
          file: {
            name: 'e2e-hardening-503-probe.json',
            mimeType: 'application/json',
            buffer: Buffer.from(JSON.stringify({
              version: '1.0',
              signals: [{
                external_id: 'E2E-HARDENING-503-PROBE',
                title: '手工导入能力探测',
                content: '用于验证手工 JSON 信息源不可用时的真实 503 报告。',
                url: null,
                published_at: null,
              }],
            }), 'utf8'),
          },
        },
      });
      expect(importResponse.status()).toBe(503);
      expect(await readJson<{detail: string}>(importResponse)).toEqual({detail: '手工 JSON 信息源不可用'});
    } finally {
      if (manualSource !== undefined && disabledForProbe) {
        const restoreResponse = await page.request.put(`/api/v1/sources/${manualSource.id}`, {
          headers,
          data: {enabled: originallyEnabled},
        });
        expect(restoreResponse.status()).toBe(200);
      }
    }

    await assertBrowserEvidence('task10-signal-import-503', evidence);
  });
  test('1280宽度通过设置弹窗切换深色并跨刷新持久化', async ({page}) => {
    const evidence = collectBrowserEvidence(page);
    await loginAs(page, testUsername, '/overview', '全网供应链风险概览');
    await page.getByRole('button', {name: '设置'}).click();
    await expect(page.getByText('系统设置')).toBeVisible();
    await page.locator('select').filter({hasText: '浅色模式'}).selectOption('dark');
    await page.getByRole('button', {name: '保存设置'}).click();
    await expect(page.locator('html')).toHaveClass(/dark/);
    await expect(page.locator('html')).not.toHaveClass(/light/);
    await page.reload();
    await expect(page.locator('html')).toHaveClass(/dark/);
    await assertNoHorizontalOverflow(page);
    await page.screenshot({path: resolve(evidenceDirectory, 'desktop-dark.png'), fullPage: true});
    await assertBrowserEvidence('task10-desktop-dark', evidence);
  });
});

test.describe('移动端390宽度视觉验收', () => {
  test.use({viewport: mobileViewport});

  test('390宽度风险列表无横向溢出且深色主题持久化', async ({page}) => {
    const evidence = collectBrowserEvidence(page);
    await loginAs(page, testUsername, '/risks', '全网风险监控中心');
    await waitForSettledRoute(page);
    await expect(page.getByText('当前风险概览')).toBeVisible();
    await assertNoHorizontalOverflow(page);
    await page.screenshot({path: resolve(evidenceDirectory, 'mobile.png'), fullPage: true});

    // 移动端按设计隐藏侧边栏设置入口，深色主题经浏览器持久化偏好生效并跨刷新保持。
    await page.evaluate(() => window.localStorage.setItem('sr-theme', 'dark'));
    await page.reload();
    await expect(page.locator('html')).toHaveClass(/dark/);
    await assertNoHorizontalOverflow(page);
    await page.screenshot({path: resolve(evidenceDirectory, 'mobile-dark.png'), fullPage: true});
    await assertBrowserEvidence('task10-mobile-390', evidence);
  });
});

test.describe('窄屏320宽度与键盘可达性验收', () => {
  test.use({viewport: narrowViewport});

  test('320宽度登录键盘顺序、总览无溢出与浅深色切换', async ({page}) => {
    const evidence = collectBrowserEvidence(page);
    const username = page.getByLabel('用户名');
    const password = page.getByLabel('密码');
    const loginButton = page.getByRole('button', {name: '登录'});

    // 键盘可达性：Tab 顺序与 focus-visible 在窄屏下仍成立。
    await page.goto('/overview');
    await expect(username).toBeVisible();
    await page.keyboard.press('Tab');
    await assertKeyboardFocus(username);
    await page.keyboard.type(testUsername);
    await page.keyboard.press('Tab');
    await assertKeyboardFocus(password);
    await page.keyboard.type(testPassword);
    await page.keyboard.press('Tab');
    await assertKeyboardFocus(loginButton);
    await page.keyboard.press('Enter');
    await expect(page.getByRole('heading', {name: '全网供应链风险概览'})).toBeVisible({timeout: 15_000});
    await waitForSettledRoute(page);

    const overviewHeading = page.getByRole('heading', {name: '全网供应链风险概览'});
    await assertVisibleWithinViewport(overviewHeading);
    await assertNoHorizontalOverflow(page);
    await page.screenshot({path: resolve(evidenceDirectory, 'narrow-320-light.png'), fullPage: true});

    // 320 宽度同样按设计隐藏设置入口，深色主题经持久化偏好生效并跨刷新保持。
    await page.evaluate(() => window.localStorage.setItem('sr-theme', 'dark'));
    await page.reload();
    await expect(page.locator('html')).toHaveClass(/dark/);
    await expect(page.getByRole('heading', {name: '全网供应链风险概览'})).toBeVisible({timeout: 15_000});
    await assertNoHorizontalOverflow(page);
    await page.screenshot({path: resolve(evidenceDirectory, 'narrow-320-dark.png'), fullPage: true});
    await assertBrowserEvidence('task10-narrow-320', evidence);
  });
});
