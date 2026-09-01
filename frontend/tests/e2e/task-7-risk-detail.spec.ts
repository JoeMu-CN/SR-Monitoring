import {expect, test, type Page} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
import {resolve} from 'node:path';

const evidenceDirectory = resolve(process.cwd(), '..', '.omo', 'evidence');
const testUsername = 'e2e-platform-admin';
const testPassword = 'E2E-Test-Only-2026!';

const login = async (page: Page) => {
  await page.goto('/risks');
  await page.getByLabel('用户名').fill(testUsername);
  await page.getByLabel('密码').fill(testPassword);
  await page.getByRole('button', {name: '登录'}).click();
  await expect(page.getByRole('button', {name: /E2E Supplier 001/})).toBeVisible({timeout: 15_000});
};

const assertNoHorizontalOverflow = async (page: Page) => {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
};

const waitForSettledRoute = async (page: Page) => {
  await expect(page.getByRole('status', {name: '正在初始化供应商风险监控平台'})).toBeHidden({timeout: 5_000});
  await expect(page.getByTestId('route-content')).toHaveCSS('opacity', '1');
};

test.beforeAll(async () => { await mkdir(evidenceDirectory, {recursive: true}); });

test('从风险列表打开详情、刷新深链并后退返回列表', async ({browser}) => {
  const context = await browser.newContext({viewport: {width: 1280, height: 720}, reducedMotion: 'reduce'});
  const page = await context.newPage();
  const consoleErrors: string[] = [];

  await login(page);
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text());
  });
  await page.getByRole('button', {name: /E2E Supplier 001/}).click();
  await expect(page).toHaveURL(/\/risks\/98001$/);
  await expect(page.getByRole('heading', {name: 'E2E Supplier 001'})).toBeVisible();
  await expect(page.getByRole('heading', {name: '原始信号'})).toBeVisible();
  await expect(page.getByText('Verified evidence for E2E-SUP-001')).toBeVisible();
  await expect(page.getByText('E2E Shanghai Site')).toBeVisible();
  await waitForSettledRoute(page);
  await page.screenshot({path: resolve(evidenceDirectory, 'task-7-frontend-gap-closure.png'), fullPage: true});

  await page.reload();
  await expect(page.getByRole('heading', {name: 'E2E Supplier 001'})).toBeVisible();
  await page.goBack();
  await expect(page).toHaveURL(/\/risks$/);
  await expect(page.getByRole('button', {name: /E2E Supplier 001/})).toBeVisible();
  await assertNoHorizontalOverflow(page);
  expect(consoleErrors).toEqual([]);
  await context.close();
});

test('移动端直接访问不存在的提醒时显示可恢复状态', async ({browser}) => {
  const context = await browser.newContext({viewport: {width: 390, height: 844}, reducedMotion: 'reduce'});
  const page = await context.newPage();

  await login(page);
  await page.goto('/risks/99999');
  await expect(page.getByRole('heading', {name: '风险提醒不存在'})).toBeVisible();
  await waitForSettledRoute(page);
  await page.screenshot({path: resolve(evidenceDirectory, 'task-7-frontend-gap-closure-mobile-404.png'), fullPage: true});
  await page.getByRole('button', {name: '返回风险列表'}).click();
  await expect(page).toHaveURL(/\/risks$/);
  await expect(page.getByRole('button', {name: /E2E Supplier 001/})).toBeVisible();
  await assertNoHorizontalOverflow(page);
  await context.close();
});
