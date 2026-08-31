import {expect, test, type BrowserContext, type Page} from '@playwright/test';
import {mkdir, writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';

const evidenceDirectory = resolve(process.cwd(), '..', '.omo', 'evidence', 'task-4-frontend-gap-closure');
const permittedUser = {
  user: {id: 1, username: 'route-e2e', email: null, display_name: '路由验证账号', role: 'platform_admin', status: 'active', last_login_at: null, created_at: '2026-08-30T00:00:00Z'},
  permissions: ['risk_view', 'supplier_view', 'source_status_view', 'rule_summary_view', 'risk_query_use', 'user_manage', 'source_manage', 'supplier_manage', 'rule_manage'],
};

const readOnlyUser = {...permittedUser, permissions: ['risk_view']};

const apiResponse = (url: string) => {
  if (url.includes('/auth/me')) return permittedUser;
  if (url.includes('/risk-alerts')) return {items: [], total: 0};
  if (url.includes('/suppliers')) return {items: [], total: 0};
  if (url.includes('/sources')) return [];
  if (url.includes('/collection-runs')) return {items: [], total: 0};
  if (url.includes('/rule-engine/dimensions')) return [];
  if (url.includes('/system/health')) return {status: 'healthy'};
  if (url.includes('/agent/status')) return {enabled: false, configured: false};
  return {detail: 'Unhandled deterministic API fixture'};
};

const mockApi = async (page: Page, permissions = permittedUser.permissions) => {
  await page.route('**/api/**', async (route) => {
    const url = route.request().url();
    const response = url.includes('/auth/me') ? {...permittedUser, permissions} : apiResponse(url);
    await route.fulfill({contentType: 'application/json', body: JSON.stringify(response)});
  });
};

const capture = async (page: Page, name: string) => {
  await expect(page.getByRole('status', {name: '正在初始化供应商风险监控平台'})).toBeHidden({timeout: 5_000});
  await expect(page.getByTestId('route-content')).toHaveCSS('opacity', '1');
  await page.screenshot({path: resolve(evidenceDirectory, `${name}.png`)});
};

const assertNoHorizontalOverflow = async (page: Page) => {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
};

const assertStatusIsAboveMobileNavigation = async (page: Page) => {
  const status = page.getByText('显示 0 条，共 0 条已加载结果').last();
  await expect(status).toBeVisible();
  const [navigationBox, statusBox] = await Promise.all([
    page.locator('nav').last().boundingBox(),
    status.boundingBox(),
  ]);
  expect(navigationBox).not.toBeNull();
  expect(statusBox).not.toBeNull();
  if (navigationBox === null || statusBox === null) throw new Error('移动导航或风险状态不可见');
  expect(statusBox.y + statusBox.height).toBeLessThanOrEqual(navigationBox.y - 12);
};

const writeLogs = async (name: string, consoleMessages: readonly string[], requests: readonly string[]) => {
  await writeFile(resolve(evidenceDirectory, `${name}.json`), JSON.stringify({consoleMessages, requests}, null, 2));
};

const newMockedPage = async (context: BrowserContext, permissions = permittedUser.permissions) => {
  const page = await context.newPage();
  await mockApi(page, permissions);
  return page;
};

test.beforeAll(async () => { await mkdir(evidenceDirectory, {recursive: true}); });

test('uses the production preview router without business-service requests', async ({browser}) => {
  const consoleMessages: string[] = [];
  const requests: string[] = [];
  const desktop = await browser.newContext({viewport: {width: 1280, height: 720}});
  const page = await newMockedPage(desktop);
  page.on('console', (message) => consoleMessages.push(`${message.type()}: ${message.text()}`));
  page.on('request', (request) => requests.push(request.url()));

  await page.goto('/');
  await expect(page).toHaveURL(/\/overview$/);
  await expect(page.getByText('全网供应链风险概览')).toBeVisible();
  await capture(page, 'desktop-overview');
  await page.getByRole('link', {name: /当前风险监控/}).click();
  await expect(page).toHaveURL(/\/risks$/);
  await capture(page, 'desktop-navigation-risks');
  await page.goBack();
  await expect(page).toHaveURL(/\/overview$/);
  await page.goForward();
  await expect(page).toHaveURL(/\/risks$/);
  await assertNoHorizontalOverflow(page);
  expect(consoleMessages.filter((message) => message.startsWith('error:'))).toEqual([]);
  expect(requests.some((url) => url.includes('127.0.0.1:8080'))).toBe(false);
  await writeLogs('desktop-network-console', consoleMessages, requests);
  await desktop.close();
});

test('renders 403 and frozen or unknown routes in desktop and mobile viewports', async ({browser}) => {
  const desktop = await browser.newContext({viewport: {width: 1280, height: 720}});
  const denied = await newMockedPage(desktop, readOnlyUser.permissions);
  await denied.goto('/settings/users');
  await expect(denied.getByRole('alert')).toContainText('无权访问');
  await capture(denied, 'desktop-403-settings-users');
  await denied.goto('/research');
  await expect(denied.getByRole('alert')).toContainText('页面不存在');
  await capture(denied, 'desktop-404-research');
  await denied.goto('/source-agent');
  await expect(denied.getByRole('alert')).toContainText('页面不存在');
  await denied.goto('/unknown-route');
  await expect(denied.getByRole('alert')).toContainText('页面不存在');
  await assertNoHorizontalOverflow(denied);
  await desktop.close();

  const mobile = await browser.newContext({viewport: {width: 390, height: 844}});
  const mobilePage = await newMockedPage(mobile);
  await mobilePage.goto('/overview');
  await expect(mobilePage.getByRole('link', {name: '风险'})).toBeVisible();
  await capture(mobilePage, 'mobile-overview');
  await mobilePage.getByRole('link', {name: '风险'}).click();
  await expect(mobilePage).toHaveURL(/\/risks$/);
  await assertStatusIsAboveMobileNavigation(mobilePage);
  await capture(mobilePage, 'mobile-navigation-risks');
  const mobileDenied = await newMockedPage(mobile, readOnlyUser.permissions);
  await mobileDenied.goto('/settings/users');
  await expect(mobileDenied.getByRole('alert')).toContainText('无权访问');
  await capture(mobileDenied, 'mobile-403-settings-users');
  await mobilePage.goto('/source-agent');
  await expect(mobilePage.getByRole('alert')).toContainText('页面不存在');
  await capture(mobilePage, 'mobile-404-source-agent');
  await assertNoHorizontalOverflow(mobilePage);
  await mobile.close();
});
