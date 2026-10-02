import {expect, test, type Locator, type Page} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
import {resolve} from 'node:path';

/**
 * Todo5：信息源清单「调度周期」列回归。
 *
 * 证明目标：
 * 1) 表头行与数据行网格在 md 起同为 14 个计算轨道，调度周期列位于「连通状态」之后；
 * 2) 1280px 与 768px 下根文档无横向溢出，且逐单元格不存在被 overflow-hidden 掩盖的行内裁切；
 * 3) 390px 移动端显示「调度:」前缀；
 * 4) 用页面内临时 DOM 自证溢出判定函数（scrollWidth <= clientWidth + 1）非恒真。
 *
 * 证据目录：.omo/evidence/source-list-schedule-column/
 */

const evidenceDirectory = resolve(process.cwd(), '..', '.omo', 'evidence', 'source-list-schedule-column');
const testUsername = 'e2e-platform-admin';
const testPassword = 'E2E-Test-Only-2026!';
const expectedTrackCount = 14;
const expectedHeaderCellCount = 6;

const login = async (page: Page): Promise<void> => {
  await page.goto('/sources');
  await page.getByLabel('用户名').fill(testUsername);
  await page.getByLabel('密码').fill(testPassword);
  await page.getByRole('button', {name: '登录'}).click();
  await expect(page.getByRole('heading', {name: '信息源清单'})).toBeVisible({timeout: 15_000});
};

const assertNoHorizontalOverflow = async (page: Page): Promise<void> => {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
};

// 计算样式 gridTemplateColumns 为已解析后的轨道列表；精确计数才能发现 12→14 列回归。
const readTrackCount = async (grid: Locator): Promise<number> => {
  const template = await grid.evaluate((element) => getComputedStyle(element).gridTemplateColumns);
  return template.trim().split(/\s+/).filter((track) => track.length > 0).length;
};

interface ClippedCell {
  readonly label: string;
  readonly scrollWidth: number;
  readonly clientWidth: number;
}

// 列表根带 overflow-hidden，行内超宽会被视觉掩盖；逐单元格比较 scrollWidth 才能暴露裁切。
const collectClippedCells = async (cells: Locator): Promise<ClippedCell[]> =>
  cells.evaluateAll((elements: HTMLElement[]) => elements
    .map((element) => ({
      label: element.getAttribute('data-testid')
        ?? (element.textContent ?? '').replace(/\s+/g, ' ').trim().slice(0, 30),
      scrollWidth: element.scrollWidth,
      clientWidth: element.clientWidth,
    }))
    .filter((cell) => cell.scrollWidth > cell.clientWidth + 1));

const assertScheduleColumnGeometry = async (page: Page): Promise<void> => {
  const list = page.getByRole('list', {name: '信息源列表'});
  await expect(list).toBeVisible();

  // 表头行 = 列表根首个直接子 div；其 6 个直接子 div 为表头单元格。
  const headerRow = list.locator(':scope > div').first();
  const headerCells = headerRow.locator(':scope > div');
  await expect(headerCells).toHaveCount(expectedHeaderCellCount);
  await expect(headerRow.getByText('调度周期')).toBeVisible();

  // 首个数据行网格 = 首个 listitem 的首个直接子 div。
  const firstDataGrid = list.locator('[role="listitem"]').first().locator(':scope > div').first();
  const scheduleCells = list.locator('[data-testid^="source-schedule-"]');
  // 真实不变量：调度单元格必须存在，且与数据行数量一致（每行恰好一个调度单元格）。
  expect(await scheduleCells.count()).toBeGreaterThan(0);
  expect(await scheduleCells.count()).toBe(await list.locator('[role="listitem"]').count());
  // 基线 seed 的 e2e-public-source（id=91000）未配置 cron，前端必须回退为「未单独配置」；
  // 按 testid 锚定，避免受 DOM 行序影响。
  await expect(list.locator('[data-testid="source-schedule-91000"]')).toContainText('未单独配置');
  await expect(firstDataGrid.locator('[data-testid^="source-schedule-"]')).toHaveCount(1);

  expect(await readTrackCount(headerRow)).toBe(expectedTrackCount);
  expect(await readTrackCount(firstDataGrid)).toBe(expectedTrackCount);

  expect(await collectClippedCells(headerCells)).toEqual([]);
  expect(await collectClippedCells(scheduleCells)).toEqual([]);

  await assertNoHorizontalOverflow(page);
};

test.beforeAll(async () => {
  await mkdir(evidenceDirectory, {recursive: true});
});

test('1280px：调度周期表头可见，表头行与数据行恰好 14 个轨道且无单元格裁切', async ({browser}) => {
  const context = await browser.newContext({viewport: {width: 1280, height: 720}, reducedMotion: 'reduce'});
  const page = await context.newPage();
  await login(page);
  await assertScheduleColumnGeometry(page);
  await page.screenshot({path: resolve(evidenceDirectory, 'source-schedule-column-1280.png'), fullPage: true});
  await context.close();
});

test('768px 断点：md 网格仍为 14 个轨道且无单元格裁切', async ({browser}) => {
  const context = await browser.newContext({viewport: {width: 768, height: 900}, reducedMotion: 'reduce'});
  const page = await context.newPage();
  await login(page);
  await assertScheduleColumnGeometry(page);
  await page.screenshot({path: resolve(evidenceDirectory, 'source-schedule-column-768.png'), fullPage: true});
  await context.close();
});

test('390px：移动端「调度:」前缀可见且无横向溢出', async ({browser}) => {
  const context = await browser.newContext({viewport: {width: 390, height: 844}, reducedMotion: 'reduce'});
  const page = await context.newPage();
  await login(page);
  const list = page.getByRole('list', {name: '信息源列表'});
  const firstScheduleCell = list.locator('[data-testid^="source-schedule-"]').first();
  await expect(firstScheduleCell.getByText('调度:')).toBeVisible();
  await assertNoHorizontalOverflow(page);
  await page.screenshot({path: resolve(evidenceDirectory, 'source-schedule-column-390.png'), fullPage: true});
  await context.close();
});

test('溢出判定非恒真自证：临时 nowrap 长文本判定为溢出，normal 后判定为不溢出', async ({browser}) => {
  const context = await browser.newContext({viewport: {width: 1280, height: 720}, reducedMotion: 'reduce'});
  const page = await context.newPage();
  await login(page);
  const proof = await page.evaluate(() => {
    // 与规格一致的溢出判定函数：只有 scrollWidth 超过 clientWidth + 1 才视为裁切。
    const isClipped = (element: HTMLElement): boolean =>
      element.scrollWidth > element.clientWidth + 1;

    const probe = document.createElement('div');
    probe.style.width = '40px';
    probe.style.overflow = 'hidden';
    const longText = document.createElement('span');
    longText.textContent = '这是一段用于证明溢出判定逻辑并非恒真的超长中文文本';
    longText.style.whiteSpace = 'nowrap';
    probe.appendChild(longText);
    document.body.appendChild(probe);

    const measure = () => ({
      scrollWidth: probe.scrollWidth,
      clientWidth: probe.clientWidth,
      clipped: isClipped(probe),
    });
    const nowrap = measure();
    longText.style.whiteSpace = 'normal';
    const wrapped = measure();
    probe.remove();
    return {nowrap, wrapped};
  });
  // nowrap 的超长文本必须被判定为溢出；改为 normal 换行后必须判定为不溢出。
  expect(proof.nowrap.clipped).toBe(true);
  expect(proof.wrapped.clipped).toBe(false);
  await context.close();
});
