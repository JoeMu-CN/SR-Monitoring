import {expect, test, type Page} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
import {resolve} from 'node:path';

const evidenceDirectory = resolve(process.cwd(), '..', '.omo', 'evidence');
const testPassword = 'E2E-Test-Only-2026!';

const login = async (page: Page, username: 'e2e-platform-admin' | 'e2e-viewer') => {
  await page.goto('/sources');
  await page.getByLabel('用户名').fill(username);
  await page.getByLabel('密码').fill(testPassword);
  await page.getByRole('button', {name: '登录'}).click();
  await expect(page.getByRole('heading', {name: '数据源清单'})).toBeVisible({timeout: 15_000});
};

const assertNoHorizontalOverflow = async (page: Page) => {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
};

const waitForSettledRoute = async (page: Page) => {
  await expect(page.getByRole('status', {name: '正在初始化供应商风险监控平台'})).toBeHidden({timeout: 5_000});
  await expect(page.getByTestId('route-content')).toHaveCSS('opacity', '1');
};

test.beforeAll(async () => { await mkdir(evidenceDirectory, {recursive: true}); });

test('从有效数入口查看、展开并分页浏览全部历史记录', async ({browser}) => {
  const context = await browser.newContext({viewport: {width: 1280, height: 720}, reducedMotion: 'reduce'});
  const page = await context.newPage();
  const consoleErrors: string[] = [];

  await login(page, 'e2e-platform-admin');
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text());
  });
  await page.getByRole('link', {name: 'E2E Public Source 有效记录 20 条'}).click();

  await expect(page).toHaveURL(/\/sources\/91000\/signals\?scope=valid&page=1$/);
  await expect(page.getByRole('heading', {name: 'E2E Public Source · 已采集记录'})).toBeVisible();
  await expect(page.getByRole('tab', {name: '当前有效'})).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByText('显示 1-20，共 20 条')).toBeVisible();
  await expect(page.getByRole('listitem')).toHaveCount(20);
  await waitForSettledRoute(page);

  await page.getByRole('button', {name: '展开正文'}).click();
  await expect(page.getByRole('button', {name: '收起正文'})).toHaveAttribute('aria-expanded', 'true');
  await page.getByRole('tab', {name: '全部历史'}).click();
  await expect(page).toHaveURL(/scope=all&page=1$/);
  await expect(page.getByText('显示 1-20，共 25 条')).toBeVisible();
  await page.getByRole('button', {name: '下一页'}).click();

  await expect(page).toHaveURL(/scope=all&page=2$/);
  await expect(page.getByText('显示 21-25，共 25 条')).toBeVisible();
  await expect(page.getByRole('listitem')).toHaveCount(5);
  await expect(page.getByText('E2E Source Signal 23')).toBeVisible();
  await waitForSettledRoute(page);
  await page.screenshot({path: resolve(evidenceDirectory, 'task-8-frontend-gap-closure.png'), fullPage: true});
  await assertNoHorizontalOverflow(page);
  expect(consoleErrors).toEqual([]);
  await context.close();
});

test('移动端只读账号可访问深链并从不存在状态返回清单', async ({browser}) => {
  const context = await browser.newContext({viewport: {width: 390, height: 844}, reducedMotion: 'reduce'});
  const page = await context.newPage();

  await login(page, 'e2e-viewer');
  await page.goto('/sources/91000/signals?scope=valid&page=1');
  await expect(page.getByRole('heading', {name: 'E2E Public Source · 已采集记录'})).toBeVisible();
  await assertNoHorizontalOverflow(page);

  await page.goto('/sources/99999/signals?scope=valid&page=1');
  await expect(page.getByRole('heading', {name: '数据源不存在'})).toBeVisible();
  await waitForSettledRoute(page);
  await page.screenshot({path: resolve(evidenceDirectory, 'task-8-frontend-gap-closure-mobile.png'), fullPage: true});
  await page.getByRole('link', {name: '返回数据源清单'}).click();
  await expect(page).toHaveURL(/\/sources$/);
  await expect(page.getByRole('heading', {name: '数据源清单'})).toBeVisible();
  await assertNoHorizontalOverflow(page);
  await context.close();
});
