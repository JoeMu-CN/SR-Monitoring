import {expect, test, type Page} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
import {resolve} from 'node:path';

const evidenceDirectory = resolve(process.cwd(), '..', '.omo', 'evidence');
const testPassword = 'E2E-Test-Only-2026!';

const login = async (page: Page, username: 'e2e-platform-admin' | 'e2e-viewer') => {
  await page.goto('/suppliers');
  await page.getByLabel('用户名').fill(username);
  await page.getByLabel('密码').fill(testPassword);
  await page.getByRole('button', {name: '登录'}).click();
  await expect(page.getByRole('heading', {name: '供应商管理'})).toBeVisible({timeout: 15_000});
};

const assertNoHorizontalOverflow = async (page: Page) => {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
};

const waitForSettledRoute = async (page: Page) => {
  await expect(page.getByRole('status', {name: '正在初始化供应商风险监控平台'})).toBeHidden({timeout: 5_000});
  await expect(page.getByTestId('route-content')).toHaveCSS('opacity', '1');
};

const supplierRow = (page: Page, code: string) => page.getByRole('row').filter({hasText: code});

test.beforeAll(async () => { await mkdir(evidenceDirectory, {recursive: true}); });

test('服务端分页、搜索与状态筛选把清单状态完整写入地址栏', async ({browser}) => {
  const context = await browser.newContext({viewport: {width: 1280, height: 720}, reducedMotion: 'reduce'});
  const page = await context.newPage();
  const consoleErrors: string[] = [];

  await login(page, 'e2e-platform-admin');
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text());
  });

  await expect(page).toHaveURL(/\/suppliers$/);
  await expect(page.getByText('显示 1-20，共 25 条')).toBeVisible();
  await expect(supplierRow(page, 'E2E-SUP-001')).toBeVisible();
  await expect(supplierRow(page, 'E2E-SUP-021')).toHaveCount(0);
  await expect(page.getByRole('button', {name: '上一页'})).toBeDisabled();

  await page.getByRole('button', {name: '下一页'}).click();
  await expect(page).toHaveURL(/\/suppliers\?page=2$/);
  await expect(page.getByText('显示 21-25，共 25 条')).toBeVisible();
  await expect(supplierRow(page, 'E2E-SUP-025')).toBeVisible();
  await expect(page.getByRole('button', {name: '下一页'})).toBeDisabled();

  // 深链刷新必须回到同一页，而不是回落到第一页。
  await page.reload();
  await expect(page.getByText('显示 21-25，共 25 条')).toBeVisible();
  await page.goBack();
  await expect(page).toHaveURL(/\/suppliers$/);
  await expect(page.getByText('显示 1-20，共 25 条')).toBeVisible();

  await page.getByRole('button', {name: '下一页'}).click();
  await expect(page).toHaveURL(/page=2$/);
  await page.getByLabel('搜索供应商').fill('E2E Component 007');
  await expect(page).toHaveURL(/\/suppliers\?q=E2E\+Component\+007$/);
  await expect(page.getByText('显示 1-1，共 1 条')).toBeVisible();
  await expect(supplierRow(page, 'E2E-SUP-007')).toBeVisible();

  await page.getByLabel('搜索供应商').fill('E2E-REG-013');
  await expect(page.getByText('显示 1-1，共 1 条')).toBeVisible();
  await expect(supplierRow(page, 'E2E-SUP-013')).toBeVisible();

  await page.getByLabel('搜索供应商').fill('');
  await expect(page).toHaveURL(/\/suppliers$/);
  await page.getByLabel('监控状态:').selectOption('high_risk');
  await expect(page).toHaveURL(/\/suppliers\?status=high_risk$/);
  await expect(page.getByText('显示 1-1，共 1 条')).toBeVisible();
  await expect(supplierRow(page, 'E2E-SUP-001')).toContainText('当前风险');

  await page.getByLabel('监控状态:').selectOption('paused');
  await expect(page.getByText('显示 1-1，共 1 条')).toBeVisible();
  await expect(supplierRow(page, 'E2E-SUP-025')).toContainText('暂停监控');

  await page.getByLabel('监控状态:').selectOption('normal');
  await expect(page.getByText('显示 1-20，共 23 条')).toBeVisible();
  await waitForSettledRoute(page);
  await page.screenshot({path: resolve(evidenceDirectory, 'task-9-frontend-gap-closure.png'), fullPage: true});
  await assertNoHorizontalOverflow(page);
  expect(consoleErrors).toEqual([]);
  await context.close();
});

test('移动端只读账号看不到写操作入口且非法查询参数被规范化', async ({browser}) => {
  const context = await browser.newContext({viewport: {width: 390, height: 844}, reducedMotion: 'reduce'});
  const page = await context.newPage();

  await login(page, 'e2e-viewer');
  await expect(page.getByRole('button', {name: '导入供应商'})).toBeDisabled();
  await expect(page.getByRole('button', {name: /^暂停监控：/}).first()).toBeDisabled();
  await expect(page.getByRole('button', {name: /^编辑供应商：/})).toHaveCount(0);
  await assertNoHorizontalOverflow(page);

  await page.goto('/suppliers?status=bogus&page=0&unknown=1');
  await expect(page).toHaveURL(/\/suppliers$/);
  await expect(page.getByText('显示 1-20，共 25 条')).toBeVisible();

  await page.goto('/suppliers?status=paused');
  await expect(page.getByText('显示 1-1，共 1 条')).toBeVisible();
  await expect(supplierRow(page, 'E2E-SUP-025')).toBeVisible();
  await waitForSettledRoute(page);
  await page.screenshot({path: resolve(evidenceDirectory, 'task-9-frontend-gap-closure-mobile.png'), fullPage: true});
  await assertNoHorizontalOverflow(page);
  await context.close();
});
