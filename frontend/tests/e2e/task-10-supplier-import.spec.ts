import {expect, test, type Page} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
import {resolve} from 'node:path';

const evidenceDirectory = resolve(process.cwd(), '..', '.omo', 'evidence');
const validWorkbook = resolve(process.cwd(), '..', 'backend', 'tests', 'fixtures', 'task-10-valid.xlsx');
const testPassword = 'E2E-Test-Only-2026!';

const login = async (page: Page) => {
  await page.goto('/suppliers');
  await page.getByLabel('用户名').fill('e2e-platform-admin');
  await page.getByLabel('密码').fill(testPassword);
  await page.getByRole('button', {name: '登录'}).click();
  await expect(page.getByRole('heading', {name: '供应商管理'})).toBeVisible({timeout: 15_000});
};

const assertNoHorizontalOverflow = async (page: Page) => {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
};

test.beforeAll(async () => { await mkdir(evidenceDirectory, {recursive: true}); });

test('下载真实模板、导入有效工作簿并以结构化表格展示无效文件错误', async ({browser}) => {
  const context = await browser.newContext({viewport: {width: 1280, height: 720}, reducedMotion: 'reduce'});
  const page = await context.newPage();
  await login(page);

  await expect(page.getByRole('button', {name: '新增供应商'})).toBeVisible();
  await page.getByRole('button', {name: '导入供应商'}).click();
  await expect(page.getByRole('dialog', {name: 'Excel 导入供应商'})).toBeVisible();

  const downloadPromise = page.waitForEvent('download');
  await page.getByRole('button', {name: '下载标准模板'}).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe('供应商导入模板.xlsx');
  await download.saveAs(resolve(evidenceDirectory, 'task-10-supplier-import-template.xlsx'));

  await page.getByLabel('选择 Excel 文件').setInputFiles(validWorkbook);
  await expect(page.getByText('task-10-valid.xlsx')).toBeVisible();
  await page.getByRole('button', {name: '开始导入'}).click();
  const importSummary = page.getByRole('region', {name: '导入完成'});
  await expect(importSummary).toBeVisible();
  await expect(importSummary.getByText('新增供应商')).toBeVisible();
  await expect(importSummary.getByText('更新供应商')).toBeVisible();
  await page.getByRole('button', {name: '完成'}).click();

  await page.getByLabel('搜索供应商').fill('TASK10-IMPORT-001');
  await expect(page.getByRole('row').filter({hasText: 'TASK10-IMPORT-001'})).toContainText('Task 10 Excel 导入供应商');
  await page.getByLabel('搜索供应商').fill('');
  await expect(page.getByText('显示 1-20，共 26 条')).toBeVisible();

  await page.getByRole('button', {name: '导入供应商'}).click();
  await page.getByLabel('选择 Excel 文件').setInputFiles({
    name: 'invalid.xlsx',
    mimeType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    buffer: Buffer.from('not a valid workbook'),
  });
  await page.getByRole('button', {name: '开始导入'}).click();
  const errorTable = page.getByRole('table', {name: '导入错误明细'});
  await expect(errorTable).toContainText('工作簿');
  await expect(errorTable).toContainText('不是有效的 .xlsx 文件');
  await page.screenshot({path: resolve(evidenceDirectory, 'task-10-frontend-gap-closure.png'), fullPage: true});
  await assertNoHorizontalOverflow(page);
  await context.close();
});

test('移动端导入弹窗保持单列、可滚动且没有页面横向溢出', async ({browser}) => {
  const context = await browser.newContext({viewport: {width: 390, height: 844}, reducedMotion: 'reduce'});
  const page = await context.newPage();
  await login(page);

  await page.getByRole('button', {name: '导入供应商'}).click();
  const dialog = page.getByRole('dialog', {name: 'Excel 导入供应商'});
  await expect(dialog).toBeVisible();
  await page.getByLabel('选择 Excel 文件').setInputFiles(validWorkbook);
  await expect(page.getByText('task-10-valid.xlsx')).toBeVisible();
  await page.screenshot({path: resolve(evidenceDirectory, 'task-10-frontend-gap-closure-mobile.png'), fullPage: true});
  await assertNoHorizontalOverflow(page);
  await context.close();
});
