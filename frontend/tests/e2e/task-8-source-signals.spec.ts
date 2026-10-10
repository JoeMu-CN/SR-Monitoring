import {expect, test, type Page} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
import {resolve} from 'node:path';

/**
 * Task 8：信息源采集记录页的表格化与 100 条/页分页回归。
 *
 * 契约要点（对应 backend list_source_signals 的 limit=1..100、默认 20）：
 * 1) 列表由卡片列表改为语义表格（table[aria-label=采集记录]），非天眼查来源不渲染报告列；
 * 2) 页面固定按 100 条/页请求（offset=(page-1)*100、limit=100），隔离栈 seed 让该来源
 *    有效 125 条 / 全部 130 条，因此第二页是真实的 offset=100（有效 25 条、全部 30 条）；
 * 3) 每一页的表格行都必须与同一请求参数下的真实后端响应逐条一致，才算「不跳条、不重复」；
 * 4) 摘要仍由后端受控截断（≤240 字符），页面不再提供「展开正文」。
 */

const evidenceDirectory = resolve(process.cwd(), '..', '.omo', 'evidence');
const testPassword = 'E2E-Test-Only-2026!';
const sourceId = 91_000;
const pageSize = 100;
/** 隔离栈 seed_e2e 的样本契约：e2e-public-source 有效 125 条、全部 130 条。 */
const validTotal = 125;
const allTotal = 130;

const login = async (page: Page, username: 'e2e-platform-admin' | 'e2e-viewer') => {
  await page.goto('/sources');
  await page.getByLabel('用户名').fill(username);
  await page.getByLabel('密码').fill(testPassword);
  await page.getByRole('button', {name: '登录'}).click();
  await expect(page.getByRole('heading', {name: '信息源清单'})).toBeVisible({timeout: 15_000});
};

const assertNoHorizontalOverflow = async (page: Page) => {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
};

const waitForSettledRoute = async (page: Page) => {
  await expect(page.getByRole('status', {name: '正在初始化供应商风险监控平台'})).toBeHidden({timeout: 5_000});
  await expect(page.getByTestId('route-content')).toHaveCSS('opacity', '1');
};

/** 采集记录表格本体：锚定本页表格，避免命中信息源清单等其它表格。 */
const signalTable = (page: Page) => page.getByRole('table', {name: '采集记录'});

/** 表格数据行（非表头行）。 */
const signalRows = (page: Page) => signalTable(page).locator('tbody tr');

/** 分页导航：页码指示与上/下一页按钮都只在这里断言。 */
const pagination = (page: Page) => page.getByRole('navigation', {name: '采集记录分页'});

/** 标题单元格（行内第一个按钮）渲染文本序列，顺序即后端返回顺序；序号列是纯文本单元格，不参与。 */
const domTitles = async (page: Page): Promise<readonly string[]> =>
  signalRows(page).evaluateAll((rows) => rows.map((row) => (row.querySelector('td button')?.textContent ?? '').trim()));

interface SignalListResponse {
  readonly total: number;
  readonly limit: number;
  readonly offset: number;
  readonly items: readonly {readonly id: number; readonly title: string}[];
}

/** 用同一组查询参数打真实后端：页面渲染必须与它逐条一致。 */
const fetchSignalPage = async (page: Page, scope: 'valid' | 'all', offset: number): Promise<SignalListResponse> => {
  const response = await page.request.get(
    `/api/v1/sources/${sourceId}/signals?scope=${scope}&offset=${offset}&limit=${pageSize}`,
  );
  expect(response.status(), `scope=${scope}&offset=${offset} 必须返回 200`).toBe(200);
  const body = await response.json() as SignalListResponse;
  expect(body.offset, '响应必须回显请求 offset').toBe(offset);
  expect(body.limit, '响应必须回显请求 limit（单页上限 100）').toBe(pageSize);
  return body;
};

interface ExpectedSignalPage {
  readonly scope: 'valid' | 'all';
  readonly pageNumber: number;
  readonly offset: number;
  readonly rows: number;
  readonly total: number;
}

/** 断言一页：URL 页码、区间文案、表格行数，以及与真实 API 的逐条一致性。 */
const assertSignalPage = async (page: Page, expected: ExpectedSignalPage): Promise<readonly string[]> => {
  await expect(page).toHaveURL(
    new RegExp(`/sources/${sourceId}/signals\\?scope=${expected.scope}&page=${expected.pageNumber}$`),
  );
  await expect(signalTable(page)).toBeVisible();
  await expect(signalRows(page)).toHaveCount(expected.rows);
  await expect(
    page.getByText(`显示 ${expected.offset + 1}-${expected.offset + expected.rows}，共 ${expected.total.toLocaleString()} 条`),
  ).toBeVisible();
  await expect(pagination(page).getByText(`第 ${expected.pageNumber} 页`)).toBeVisible();

  const api = await fetchSignalPage(page, expected.scope, expected.offset);
  expect(api.total, `${expected.scope} 口径总条数`).toBe(expected.total);
  expect(api.items.length, `${expected.scope} 第 ${expected.pageNumber} 页返回条数`).toBe(expected.rows);
  const rendered = await domTitles(page);
  expect(rendered, '表格行必须与真实 API 逐条一致：不跳条、不重复、不越界')
    .toEqual(api.items.map((item) => item.title));
  return rendered;
};

/** 两页拼起来必须正好覆盖该口径全部条目，且互不重复。 */
const expectFullCoverage = (pages: readonly (readonly string[])[], total: number) => {
  const titles = pages.flat();
  expect(titles.length, '两页渲染行数之和必须等于该口径总条数').toBe(total);
  expect(new Set(titles).size, '两页之间不得出现重复条目').toBe(titles.length);
};

test.beforeAll(async () => { await mkdir(evidenceDirectory, {recursive: true}); });

test('从有效数入口进入 100 条表格，第二页是真实 offset=100 且当前有效与全部历史都不跳条', async ({browser}) => {
  const context = await browser.newContext({viewport: {width: 1280, height: 720}, reducedMotion: 'reduce'});
  const page = await context.newPage();
  const consoleErrors: string[] = [];

  await login(page, 'e2e-platform-admin');
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text());
  });
  await page.getByRole('link', {name: `E2E Public Source 有效记录 ${validTotal} 条`}).click();

  await expect(page.getByRole('heading', {name: 'E2E Public Source · 已采集记录'})).toBeVisible();
  await expect(page.getByRole('tab', {name: '当前有效'})).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByRole('tab', {name: '全部历史'})).toHaveAttribute('aria-selected', 'false');
  await waitForSettledRoute(page);

  // 非天眼查来源只有 11 个列（序号 + 10 个基础列）：状态、原文与报告列都不存在，报告入口整体缺席。
  await expect(signalTable(page).locator('thead th')).toHaveCount(11);
  await expect(page.getByRole('button', {name: /^查看完整报告：/})).toHaveCount(0);

  const validPage1 = await assertSignalPage(page, {scope: 'valid', pageNumber: 1, offset: 0, rows: pageSize, total: validTotal});
  await expect(pagination(page).getByRole('button', {name: '上一页'})).toBeDisabled();

  // Todo15：主列表只渲染后端受控摘要（≤240 字符），不再提供「展开正文」入口。
  const longRow = signalRows(page).filter({hasText: 'E2E Source Signal 01'});
  await expect(longRow).toHaveCount(1);
  const summaryCell = longRow.locator('[data-testid^="source-signal-summary-"]');
  await expect(summaryCell).toBeVisible();
  expect((await summaryCell.textContent())?.length ?? 0).toBeLessThanOrEqual(240);
  await expect(summaryCell).toContainText('E2E long source signal content.');
  await expect(page.getByRole('button', {name: '展开正文'})).toHaveCount(0);

  // 原文列保留真实来源链接（安全外链）。
  await expect(longRow.getByRole('link', {name: '原文'})).toHaveAttribute('href', 'https://example.test/source-signals/1');

  // 第二页必须是真实的 offset=100，而不是被后端上限截断后重复第一页。
  await pagination(page).getByRole('button', {name: '下一页'}).click();
  const validPage2 = await assertSignalPage(page, {scope: 'valid', pageNumber: 2, offset: pageSize, rows: 25, total: validTotal});
  await expect(pagination(page).getByRole('button', {name: '上一页'})).toBeEnabled();
  await expect(pagination(page).getByRole('button', {name: '下一页'})).toBeDisabled();
  expectFullCoverage([validPage1, validPage2], validTotal);

  // 全部历史：同一页大小下总条数变为 130，第二页承载余下 30 条。
  await page.getByRole('tab', {name: '全部历史'}).click();
  await expect(page.getByRole('tab', {name: '全部历史'})).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByRole('tab', {name: '当前有效'})).toHaveAttribute('aria-selected', 'false');
  const allPage1 = await assertSignalPage(page, {scope: 'all', pageNumber: 1, offset: 0, rows: pageSize, total: allTotal});

  await pagination(page).getByRole('button', {name: '下一页'}).click();
  const allPage2 = await assertSignalPage(page, {scope: 'all', pageNumber: 2, offset: pageSize, rows: 30, total: allTotal});
  await expect(pagination(page).getByRole('button', {name: '下一页'})).toBeDisabled();
  expectFullCoverage([allPage1, allPage2], allTotal);

  // 已过期记录只出现在全部历史里。
  expect(validPage1).not.toContain('E2E Source Signal 128');
  expect(allPage2).toContain('E2E Source Signal 128');

  await page.screenshot({path: resolve(evidenceDirectory, 'task-8-frontend-gap-closure.png'), fullPage: true});
  await assertNoHorizontalOverflow(page);
  expect(consoleErrors).toEqual([]);
  await context.close();
});

test('移动端只读账号可访问深链并从不存在状态返回清单', async ({browser}) => {
  const context = await browser.newContext({viewport: {width: 390, height: 844}, reducedMotion: 'reduce'});
  const page = await context.newPage();

  await login(page, 'e2e-viewer');
  await page.goto(`/sources/${sourceId}/signals?scope=valid&page=1`);
  await expect(page.getByRole('heading', {name: 'E2E Public Source · 已采集记录'})).toBeVisible();
  await expect(signalTable(page)).toBeVisible();
  await assertNoHorizontalOverflow(page);

  await page.goto('/sources/99999/signals?scope=valid&page=1');
  await expect(page.getByRole('heading', {name: '信息源不存在'})).toBeVisible();
  await waitForSettledRoute(page);
  await page.screenshot({path: resolve(evidenceDirectory, 'task-8-frontend-gap-closure-mobile.png'), fullPage: true});
  await page.getByRole('link', {name: '返回信息源清单'}).click();
  await expect(page).toHaveURL(/\/sources$/);
  await expect(page.getByRole('heading', {name: '信息源清单'})).toBeVisible();
  await assertNoHorizontalOverflow(page);
  await context.close();
});