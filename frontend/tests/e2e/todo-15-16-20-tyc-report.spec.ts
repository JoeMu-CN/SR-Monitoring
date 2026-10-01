import {expect, test, type BrowserContext, type Page} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
import {resolve} from 'node:path';

/**
 * Todo 15/16/20：天眼查多维度核查前端端到端。
 *
 * 全程真实登录 + 真实后端 API（隔离测试栈注入 compose.test.yaml + seed_e2e），
 * 天眼查在用例内通过管理员 API 指向容器内确定性 MCP stub（零真实外网）。
 * 信息源页已不再提供天眼查单供应商选择与手动核查入口，只保留两个「按需核查」胶囊；
 * 采集记录/报告 UI 验证所需的真实信号由已登录会话直接调用
 * POST /api/v1/sources/{id}/run-tyc-batch?supplier_id=... 生成，再走采集记录页摘要 →
 * 报告弹窗（ESC 关闭 / 焦点还原 / Tab 焦点陷阱）→ 移动端 no-report 安全回落。
 * 视觉修复回归：报告弹窗遮罩 z-[60] 高于 MobileNav z-50（点击不穿透），
 * 列表摘要按业务分隔符保护短标签（司法解析/失信被执行在 1280/768/375 均不跨行）。
 *
 * Todo 19：单供应商天眼查核查「中途额度耗尽」后端契约——锚定调用成功后维度在锁内被拒，
 * 真实后端仍返回 200 汇总并标记 quota_exhausted，逐工具计数含 quota_exhausted。
 * 该额度契约已由后端测试 tests/test_tyc_batch_multidim.py 充分覆盖，E2E 只验证
 * 真实 API 契约，并断言信息源页已移除的手动入口不存在。
 *
 * 运行：pwsh -File scripts/test-current-version-hardening.ps1 -Suite e2e
 *       -Tests tests/e2e/todo-15-16-20-tyc-report.spec.ts
 */

const evidenceDirectory = resolve(process.cwd(), '..', '.omo', 'evidence', 'tyc-multidim-risk-llm');
const testUsername = 'e2e-platform-admin';
const testPassword = 'E2E-Test-Only-2026!';
// 隔离栈专用：compose.test.yaml 的 tyc-stub 服务 + 测试密钥（仅测试环境，无真实密钥）。
const tycStubUrl = 'http://tyc-stub:8081/v1';
const tycStubKey = 'tyc-e2e-stub-key';

interface TycBatchToolOutcomeCounts {
  readonly success_with_records: number;
  readonly empty: number;
  readonly error: number;
  readonly quota_exhausted: number;
  readonly busy: number;
}

/** 后端 POST /sources/{id}/run-tyc-batch 的 200 稳定汇总（TycBatchRunRead）。 */
interface TycBatchRunSummary {
  readonly source_id: number;
  readonly shard_index: number;
  readonly shard_count: number;
  readonly supplier_id: number | null;
  readonly targeted_count: number;
  readonly attempted_count: number;
  readonly created_count: number;
  readonly duplicate_count: number;
  readonly empty_count: number;
  readonly failed_count: number;
  readonly quota_exhausted: boolean;
  readonly per_tool_counts: Record<string, TycBatchToolOutcomeCounts>;
}

/** 409 结构化 detail（含剩余额度上下文）。 */
interface TycBatchUnavailableDetail {
  readonly code: string;
  readonly reason: string;
  readonly daily_used: number;
}

const login = async (page: Page) => {
  await page.goto('/sources');
  await page.getByLabel('用户名').fill(testUsername);
  await page.getByLabel('密码').fill(testPassword);
  await page.getByRole('button', {name: '登录'}).click();
  await expect(page.getByRole('heading', {name: '信息源清单'})).toBeVisible({timeout: 15_000});
};

const writeHeaders = async (context: BrowserContext) => {
  const csrfCookie = (await context.cookies()).find((cookie) => cookie.name.endsWith('_csrf'));
  if (csrfCookie === undefined) throw new Error('当前会话缺少 CSRF Cookie，无法执行真实写请求。');
  return {Origin: 'http://127.0.0.1:18080', 'X-CSRF-Token': csrfCookie.value};
};

/** 把 tianyancha 提交态指向本地 stub 并启用；返回源 ID。幂等，可重复调用。 */
const configureTycSource = async (page: Page): Promise<number> => {
  const response = await page.request.get('/api/v1/sources/admin');
  expect(response.status()).toBe(200);
  const sources = await response.json() as Array<{id: number; code: string}>;
  const tycSource = sources.find((source) => source.code === 'tianyancha');
  expect(tycSource, '隔离栈必须包含 tianyancha 信息源').toBeTruthy();
  const headers = await writeHeaders(page.context());
  const updated = await page.request.put(`/api/v1/sources/${tycSource!.id}`, {
    headers,
    data: {
      enabled: true,
      endpoint_url: tycStubUrl,
      api_key: tycStubKey,
      login_config: {mode: 'on_demand', secret_source: 'console', daily_limit: 1000, monthly_limit: 10000},
    },
  });
  expect(updated.status()).toBe(200);
  const body = await updated.json() as {enabled: boolean; api_key_configured: boolean};
  expect(body.enabled).toBe(true);
  expect(body.api_key_configured).toBe(true);
  return tycSource!.id;
};

/** 把天眼查日额度改写为指定值（stub 端点、密钥与月度额度保持不变）；测试结束恢复 1000。 */
const setTycDailyLimit = async (page: Page, tycSourceId: number, dailyLimit: number): Promise<void> => {
  const headers = await writeHeaders(page.context());
  const updated = await page.request.put(`/api/v1/sources/${tycSourceId}`, {
    headers,
    data: {
      enabled: true,
      endpoint_url: tycStubUrl,
      api_key: tycStubKey,
      login_config: {mode: 'on_demand', secret_source: 'console', daily_limit: dailyLimit, monthly_limit: 10000},
    },
  });
  expect(updated.status(), '天眼查日额度切换必须成功').toBe(200);
};

/** 按供应商编码经真实接口取 ID：不硬编码 seed 主键，保证额度探测与 UI 场景指向同一供应商。 */
const supplierIdByCode = async (page: Page, supplierCode: string): Promise<number> => {
  const response = await page.request.get('/api/v1/suppliers?limit=100');
  expect(response.status()).toBe(200);
  const body = await response.json() as {items: Array<{id: number; supplier_code: string}>};
  const supplier = body.items.find((item) => item.supplier_code === supplierCode);
  expect(supplier, `隔离栈必须包含供应商 ${supplierCode}`).toBeTruthy();
  return supplier!.id;
};

const assertNoHorizontalOverflow = async (page: Page) => {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
};

/** 移动端底部导航（MobileNav 是唯一的 fixed bottom-3 容器）。 */
const mobileNav = (page: Page) => page.locator('div.fixed[class*="bottom-3"]');

/**
 * 报告弹窗遮罩必须高于移动端导航（z-[60] > z-50）：
 * 导航区域命中的是遮罩而不是导航，且在该坐标点击不会穿透导航造成路由跳转。
 */
const assertOverlayCoversMobileNav = async (page: Page) => {
  const overlay = page.getByTestId('source-signal-report-overlay');
  await expect(overlay).toBeVisible();
  const nav = mobileNav(page);
  await expect(nav).toBeVisible();
  const navBox = await nav.boundingBox();
  expect(navBox, '移动端导航必须有可测量区域').not.toBeNull();

  const geometry = await page.evaluate(({x, y}) => {
    const overlayElement = document.querySelector('[data-testid="source-signal-report-overlay"]');
    const navElement = document.querySelector('div.fixed[class*="bottom-3"]');
    const topMost = document.elementFromPoint(x, y);
    return {
      overlayZ: overlayElement === null ? null : getComputedStyle(overlayElement).zIndex,
      navZ: navElement === null ? null : getComputedStyle(navElement).zIndex,
      hitsOverlay: overlayElement !== null && topMost !== null && overlayElement.contains(topMost),
      hitsNav: navElement !== null && topMost !== null && navElement.contains(topMost),
    };
  }, {x: navBox!.x + navBox!.width / 2, y: navBox!.y + navBox!.height / 2});

  expect(Number(geometry.overlayZ), '弹窗遮罩 z-index 必须高于导航').toBeGreaterThan(Number(geometry.navZ));
  expect(geometry.hitsNav, '导航不得位于弹窗遮罩之上').toBe(false);
  expect(geometry.hitsOverlay, '导航区域应命中的是弹窗遮罩').toBe(true);

  const urlBeforeClick = page.url();
  await page.mouse.click(navBox!.x + navBox!.width / 2, navBox!.y + navBox!.height / 2);
  await expect(page.getByRole('dialog')).toBeVisible();
  expect(page.url(), '点击导航区域不得穿透触发路由跳转').toBe(urlBeforeClick);
};

/** 逐字符量取末行字数：含「确定性记录」的摘要不得出现单字孤行（如“确定性记 / 录”）。 */
const assertNoSingleCharacterLastLine = async (page: Page, selector: string, requiredFragment: string) => {
  const results = await page.evaluate((targetSelector) => {
    const paragraphs = Array.from(document.querySelectorAll(targetSelector));
    return paragraphs.map((paragraph) => {
      const text = paragraph.textContent ?? '';
      const node = paragraph.firstChild;
      const style = getComputedStyle(paragraph);
      if (node === null) {
        return {text, lastLineChars: 0, textWrapStyle: style.textWrapStyle, whiteSpace: style.whiteSpace, overflowWrap: style.overflowWrap};
      }
      const range = document.createRange();
      const tops: number[] = [];
      for (let index = 0; index < text.length; index += 1) {
        range.setStart(node, index);
        range.setEnd(node, index + 1);
        tops.push(range.getBoundingClientRect().top);
      }
      const lastTop = tops[tops.length - 1]!;
      let start = tops.length - 1;
      for (let index = tops.length - 1; index >= 0; index -= 1) {
        if (Math.abs(tops[index]! - lastTop) < 1) start = index;
        else break;
      }
      return {
        text,
        lastLineChars: text.length - start,
        textWrapStyle: style.textWrapStyle,
        whiteSpace: style.whiteSpace,
        overflowWrap: style.overflowWrap,
      };
    });
  }, selector);

  expect(results.length, `选择器 ${selector} 下应存在摘要段落`).toBeGreaterThan(0);
  expect(
    results.some((item) => item.text.includes(requiredFragment)),
    `至少一段摘要应包含「${requiredFragment}」`,
  ).toBe(true);
  for (const item of results) {
    expect(item.lastLineChars, `摘要末行不得是单字孤行：${item.text}`).toBeGreaterThanOrEqual(2);
    expect(item.textWrapStyle, '摘要必须启用平衡换行 text-wrap: balance').toBe('balance');
    expect(item.whiteSpace.startsWith('pre-wrap'), '摘要必须保留 pre-wrap').toBe(true);
    expect(item.overflowWrap, '摘要必须保留 break-words 防止长标识符溢出').toBe('break-word');
  }
};

/**
 * 逐字符坐标断言短语未被换行拆开（不依赖末行长度）：
 * 摘要段落内目标短语的每个字符取 client rect，全部字符 top 相同才算同一行；
 * 同时校验摘要段落仍保留 pre-wrap + break-words（长正文可断、不溢出）。
 */
const assertPhraseStaysOnOneLine = async (page: Page, selector: string, phrase: string) => {
  const results = await page.evaluate(({targetSelector, targetPhrase}) => {
    const locate = (root: Node, index: number) => {
      let remaining = index;
      const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
      let node = walker.nextNode();
      while (node !== null) {
        const textNode = node as Text;
        if (remaining < textNode.data.length) return {textNode, offset: remaining};
        remaining -= textNode.data.length;
        node = walker.nextNode();
      }
      return null;
    };
    const paragraphs = Array.from(document.querySelectorAll(targetSelector));
    return paragraphs.map((paragraph) => {
      const text = paragraph.textContent ?? '';
      const start = text.indexOf(targetPhrase);
      if (start < 0) {
        return {containsPhrase: false, sameLine: false, lineTops: [] as number[], overflowWrap: '', whiteSpace: ''};
      }
      const range = document.createRange();
      const tops: number[] = [];
      for (let index = start; index < start + targetPhrase.length; index += 1) {
        const located = locate(paragraph, index);
        if (located === null) {
          return {containsPhrase: true, sameLine: false, lineTops: [] as number[], overflowWrap: '', whiteSpace: ''};
        }
        range.setStart(located.textNode, located.offset);
        range.setEnd(located.textNode, located.offset + 1);
        tops.push(range.getBoundingClientRect().top);
      }
      const style = getComputedStyle(paragraph);
      return {
        containsPhrase: true,
        sameLine: new Set(tops.map((top) => Math.round(top))).size === 1,
        lineTops: tops.map((top) => Math.round(top)),
        overflowWrap: style.overflowWrap,
        whiteSpace: style.whiteSpace,
      };
    });
  }, {targetSelector: selector, targetPhrase: phrase});

  const containing = results.filter((item) => item.containsPhrase);
  expect(containing.length, `摘要应包含短语「${phrase}」`).toBeGreaterThan(0);
  for (const item of containing) {
    expect(item.sameLine, `短语「${phrase}」不得被换行拆开（字符 top=${JSON.stringify(item.lineTops)}）`).toBe(true);
    expect(item.overflowWrap, '长正文必须保留 break-words').toBe('break-word');
    expect(item.whiteSpace.startsWith('pre-wrap'), '摘要必须保留 pre-wrap').toBe(true);
  }
};

const waitForSettledRoute = async (page: Page) => {
  await expect(page.getByRole('status', {name: '正在初始化供应商风险监控平台'})).toBeHidden({timeout: 5_000});
  await expect(page.getByTestId('route-content')).toHaveCSS('opacity', '1');
};

/**
 * 新契约：信息源页对天眼查只展示两个「按需核查」胶囊（连通胶囊 + 新鲜度胶囊），
 * 不再提供单供应商选择器、手动核查按钮或通用刷新按钮；单供应商核查由供应商查询助手承担。
 */
const assertTycOnDemandConsoles = async (page: Page, tycSourceId: number) => {
  await expect(page.getByTestId(`source-connectivity-${tycSourceId}`)).toContainText('按需核查');
  await expect(page.getByTestId(`source-health-${tycSourceId}`)).toContainText('按需核查');
  await expect(page.getByLabel('选择核查供应商')).toHaveCount(0);
  await expect(page.getByRole('button', {name: '核查本供应商：天眼查企业核查'})).toHaveCount(0);
  await expect(page.getByRole('button', {name: '刷新天眼查企业核查'})).toHaveCount(0);
};

/** 断言 200 汇总的必要返回契约（源/供应商对齐、单供应商计数与逐工具五态聚合存在）。 */
const assertTycBatchSummaryContract = (
  summary: TycBatchRunSummary,
  tycSourceId: number,
  supplierId: number,
) => {
  expect(summary.source_id, '汇总必须回指同一信息源').toBe(tycSourceId);
  expect(summary.supplier_id, '汇总必须回指同一供应商').toBe(supplierId);
  expect(summary.shard_count, '天眼查固定 2 片').toBe(2);
  expect(summary.targeted_count, '单供应商核查目标数恒为 1').toBe(1);
  expect(summary.attempted_count, '至少尝试 1 家').toBeGreaterThanOrEqual(1);
  expect(
    summary.created_count + summary.duplicate_count,
    '成功汇总必须产生或复用 ≥1 条采集记录',
  ).toBeGreaterThanOrEqual(1);
  expect(Object.keys(summary.per_tool_counts).length, '必须返回逐工具五态聚合').toBeGreaterThan(0);
};

/**
 * 信息源页已移除单供应商核查入口：登录会话内直接调用真实隔离后端的 run-tyc-batch，
 * 断言 200 与必要汇总契约并返回汇总；采集记录与逐工具计数一律以该 API 返回为准。
 */
const runTycBatchViaApi = async (
  page: Page,
  tycSourceId: number,
  supplierId: number,
): Promise<TycBatchRunSummary> => {
  const headers = await writeHeaders(page.context());
  const response = await page.request.post(
    `/api/v1/sources/${tycSourceId}/run-tyc-batch?supplier_id=${supplierId}`,
    {headers},
  );
  expect(response.status(), '天眼查单供应商核查必须返回 200 汇总').toBe(200);
  const summary = await response.json() as TycBatchRunSummary;
  assertTycBatchSummaryContract(summary, tycSourceId, supplierId);
  return summary;
};

test.beforeAll(async () => {
  await mkdir(evidenceDirectory, {recursive: true});
});

test('桌面1280：信息源页按需核查胶囊与手动入口缺席，采集记录摘要经弹窗查看完整报告', async ({browser}) => {
  const context = await browser.newContext({viewport: {width: 1280, height: 720}, reducedMotion: 'reduce'});
  const page = await context.newPage();

  await login(page);
  // 登录后再收集控制台错误：登录前的会话探测 401 属预期引导行为，不计入回归。
  const consoleErrors: string[] = [];
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text());
  });
  const tycSourceId = await configureTycSource(page);

  // 新契约：信息源页不再提供天眼查单供应商选择/手动核查入口，只保留两个「按需核查」胶囊。
  await page.goto('/sources');
  await expect(page.getByTestId(`source-name-${tycSourceId}`)).toBeVisible({timeout: 15_000});
  await waitForSettledRoute(page);
  await assertTycOnDemandConsoles(page, tycSourceId);
  await page.screenshot({path: resolve(evidenceDirectory, 'todo20-desktop-1280-tyc-on-demand.png'), fullPage: true});
  await assertNoHorizontalOverflow(page);

  // 采集记录/报告 UI 验证所需的真实信号由已登录会话直接调用后端 API 生成（零真实外网）。
  const supplierId001 = await supplierIdByCode(page, 'E2E-SUP-001');
  await runTycBatchViaApi(page, tycSourceId, supplierId001);

  // Todo15：采集记录主列表只渲染受控摘要（≤240 字符）。
  await page.goto(`/sources/${tycSourceId}/signals?scope=valid&page=1`);
  await expect(page.getByRole('heading', {name: /天眼查企业核查 · 已采集记录/})).toBeVisible({timeout: 15_000});
  await waitForSettledRoute(page);
  const summaryCell = page.getByTestId(/^source-signal-summary-/).first();
  await expect(summaryCell).toBeVisible();
  await expect(summaryCell).toContainText('重点命中');
  expect((await summaryCell.textContent())?.length ?? 0).toBeLessThanOrEqual(240);
  await expect(page.getByRole('button', {name: '展开正文'})).toHaveCount(0);

  // Todo15 修复回归：短标签短语在 1280 不得跨行（曾被拆「司/法解析」）。
  await assertPhraseStaysOnOneLine(page, '[data-testid^="source-signal-summary-"]', '司法解析');
  await assertPhraseStaysOnOneLine(page, '[data-testid^="source-signal-summary-"]', '失信/被执行');
  await assertNoHorizontalOverflow(page);
  await page.locator('article[role="listitem"]').first().screenshot({
    path: resolve(evidenceDirectory, 'todo15-desktop-1280-signal-summary.png'),
  });

  // Todo16：查看完整报告 → 弹窗按维度分组，命中/未命中状态均以文字呈现 + 来源署名。
  const reportEntry = page.getByRole('button', {name: /^查看完整报告：/}).first();
  await reportEntry.click();
  const dialog = page.getByRole('dialog');
  await expect(dialog).toBeVisible();
  await expect(dialog).toHaveAttribute('aria-modal', 'true');
  await expect(dialog).toContainText('E2E Supplier 001');
  await expect(page.getByTestId('source-signal-report-group-hit')).toContainText('命中');
  await expect(page.getByTestId('source-signal-report-group-hit')).toContainText('风险总览');
  await expect(page.getByTestId('source-signal-report-group-normal')).toContainText('未发现');
  await expect(page.getByTestId('source-signal-report-attribution')).toContainText('数据来源：天眼查');
  await page.screenshot({path: resolve(evidenceDirectory, 'todo16-desktop-1280-report-modal.png'), fullPage: true});
  await assertNoHorizontalOverflow(page);

  // Tab 焦点陷阱：连续 Tab 后焦点仍在弹窗内。
  for (let index = 0; index < 6; index += 1) await page.keyboard.press('Tab');
  expect(await page.evaluate(() => {
    const dialogElement = document.querySelector('[role="dialog"]');
    return dialogElement !== null && dialogElement.contains(document.activeElement);
  })).toBe(true);

  // ESC 关闭并把焦点还原到触发按钮。
  await page.keyboard.press('Escape');
  await expect(dialog).toBeHidden();
  await expect(reportEntry).toBeFocused();

  expect(consoleErrors).toEqual([]);
  await context.close();
});

test('平板768：信息源页按需核查胶囊无横向溢出，报告弹窗遮罩覆盖移动端导航', async ({browser}) => {
  const context = await browser.newContext({viewport: {width: 768, height: 1024}, reducedMotion: 'reduce'});
  const page = await context.newPage();

  await login(page);
  const tycSourceId = await configureTycSource(page);

  // 信息源页新契约：天眼查仅保留「按需核查」胶囊，无手动核查控件；768 宽度不得横向溢出。
  await page.goto('/sources');
  await expect(page.getByTestId(`source-name-${tycSourceId}`)).toBeVisible({timeout: 15_000});
  await waitForSettledRoute(page);
  await assertTycOnDemandConsoles(page, tycSourceId);
  await page.screenshot({path: resolve(evidenceDirectory, 'todo20-tablet-768-tyc-on-demand.png'), fullPage: true});
  await assertNoHorizontalOverflow(page);

  // 采集记录/报告 UI 验证所需的真实信号由已登录会话直接调用后端 API 生成。
  const supplierId002 = await supplierIdByCode(page, 'E2E-SUP-002');
  await runTycBatchViaApi(page, tycSourceId, supplierId002);

  // Todo16 修复回归：768 仍处于移动端导航可见区间，弹窗遮罩必须完整覆盖导航且点击不穿透。
  await page.goto(`/sources/${tycSourceId}/signals?scope=valid&page=1`);
  await expect(page.getByRole('heading', {name: /天眼查企业核查 · 已采集记录/})).toBeVisible({timeout: 15_000});
  await waitForSettledRoute(page);

  // Todo15 修复回归：列表摘要短标签短语在 768 不得跨行（曾被拆「司/法解析」）。
  await assertPhraseStaysOnOneLine(page, '[data-testid^="source-signal-summary-"]', '司法解析');
  await assertPhraseStaysOnOneLine(page, '[data-testid^="source-signal-summary-"]', '失信/被执行');
  await assertNoHorizontalOverflow(page);
  await page.locator('article[role="listitem"]').first().screenshot({
    path: resolve(evidenceDirectory, 'todo15-tablet-768-signal-summary.png'),
  });

  await page.getByRole('button', {name: /^查看完整报告：/}).first().click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await assertOverlayCoversMobileNav(page);
  await assertNoSingleCharacterLastLine(page, '[data-testid="source-signal-dimension-summary"]', '确定性记录');
  await page.screenshot({path: resolve(evidenceDirectory, 'todo16-tablet-768-report-modal.png')});
  await assertNoHorizontalOverflow(page);
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).toBeHidden();

  await context.close();
});

test('移动375：报告摘要短语不跨行、弹窗遮罩覆盖导航，非报告记录展示安全 no-report 状态', async ({browser}) => {
  const context = await browser.newContext({viewport: {width: 375, height: 812}, reducedMotion: 'reduce'});
  const page = await context.newPage();

  await login(page);
  const tycSourceId = await configureTycSource(page);
  // 采集记录/报告 UI 验证所需的真实信号由已登录会话直接调用后端 API 生成。
  const supplierId003 = await supplierIdByCode(page, 'E2E-SUP-003');
  await runTycBatchViaApi(page, tycSourceId, supplierId003);
  await assertNoHorizontalOverflow(page);

  // 天眼查报告记录：列表摘要短标签短语不得跨行（375 曾被拆「失信/被执行」），弹窗摘要不得单字末行。
  await page.goto(`/sources/${tycSourceId}/signals?scope=valid&page=1`);
  await expect(page.getByRole('heading', {name: /天眼查企业核查 · 已采集记录/})).toBeVisible({timeout: 15_000});
  await waitForSettledRoute(page);
  await assertPhraseStaysOnOneLine(page, '[data-testid^="source-signal-summary-"]', '司法解析');
  await assertPhraseStaysOnOneLine(page, '[data-testid^="source-signal-summary-"]', '失信/被执行');
  await assertNoHorizontalOverflow(page);
  await page.locator('article[role="listitem"]').first().screenshot({
    path: resolve(evidenceDirectory, 'todo15-mobile-375-signal-summary.png'),
  });
  await page.getByRole('button', {name: /^查看完整报告：/}).first().click();
  const dialog = page.getByRole('dialog');
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText('数据来源：天眼查');
  await assertOverlayCoversMobileNav(page);
  await assertNoSingleCharacterLastLine(page, '[data-testid="source-signal-dimension-summary"]', '确定性记录');
  // 弹窗为 fixed 覆盖层：截取视口，避免全页截图把弹窗拼接在页面顶部。
  await page.screenshot({path: resolve(evidenceDirectory, 'todo16-mobile-375-report-modal.png')});
  await assertNoHorizontalOverflow(page);
  await page.keyboard.press('Escape');
  await expect(dialog).toBeHidden();

  // 非报告记录：弹窗给出 no-report 说明与完整正文，不伪造天眼查署名。
  const sourcesResponse = await page.request.get('/api/v1/sources/admin');
  expect(sourcesResponse.status()).toBe(200);
  const sources = await sourcesResponse.json() as Array<{id: number; code: string}>;
  const publicSource = sources.find((source) => source.code === 'e2e-public-source');
  expect(publicSource, '隔离栈必须包含 e2e-public-source').toBeTruthy();
  await page.goto(`/sources/${publicSource!.id}/signals?scope=valid&page=1`);
  await expect(page.getByRole('heading', {name: /E2E Public Source · 已采集记录/})).toBeVisible({timeout: 15_000});
  await page.getByRole('button', {name: /^查看完整报告：/}).first().click();
  await expect(page.getByTestId('source-signal-report-no-report')).toBeVisible();
  await expect(page.getByTestId('source-signal-report-no-report')).toContainText('没有结构化多维度报告');
  await expect(page.getByTestId('source-signal-report-attribution')).toHaveCount(0);
  await page.screenshot({path: resolve(evidenceDirectory, 'todo16-mobile-375-no-report.png')});
  await assertNoHorizontalOverflow(page);
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).toBeHidden();

  await context.close();
});

test('桌面1280：信息源页不再提供天眼查手动核查入口，单供应商额度耗尽由真实 API 契约保证', async ({browser}) => {
  const context = await browser.newContext({viewport: {width: 1280, height: 720}, reducedMotion: 'reduce'});
  const page = await context.newPage();
  let tycSourceId: number | null = null;

  try {
    await login(page);
    tycSourceId = await configureTycSource(page);
    const resolvedSourceId: number = tycSourceId;
    const supplierId = await supplierIdByCode(page, 'E2E-SUP-004');

    // 页面新契约：天眼查不再提供单供应商选择/手动核查/通用刷新入口，只保留「按需核查」胶囊。
    await page.goto('/sources');
    await expect(page.getByTestId(`source-name-${resolvedSourceId}`)).toBeVisible({timeout: 15_000});
    await waitForSettledRoute(page);
    await assertTycOnDemandConsoles(page, resolvedSourceId);
    await page.screenshot({
      path: resolve(evidenceDirectory, 'todo19-desktop-1280-tyc-no-manual-entry.png'),
      fullPage: true,
    });
    await assertNoHorizontalOverflow(page);

    // Todo 19 后端额度契约（已由 tests/test_tyc_batch_multidim.py 充分覆盖）：
    // 把日额度压到 1，锚定调用一旦成功，后续每个维度都会在锁内被真实拒绝，
    // 真实后端仍返回 200 汇总并标记 quota_exhausted，逐工具计数含 quota_exhausted。
    // 前置用例共享同一账户当日消耗，起始额度可能已被用尽：此时按结构化 409 detail 的
    // daily_used 精确回设为 daily_used + 1，保证「锚定成功 → 中途耗尽」确定性成立。
    await setTycDailyLimit(page, resolvedSourceId, 1);
    const headers = await writeHeaders(page.context());
    const firstRun = await page.request.post(
      `/api/v1/sources/${resolvedSourceId}/run-tyc-batch?supplier_id=${supplierId}`,
      {headers},
    );

    const assertQuotaExhaustedContract = (summary: TycBatchRunSummary) => {
      assertTycBatchSummaryContract(summary, resolvedSourceId, supplierId);
      expect(summary.quota_exhausted, '锚定成功后中途额度耗尽必须标记 quota_exhausted').toBe(true);
      expect(
        summary.per_tool_counts['search_companies']?.success_with_records,
        '锚定工具 search_companies 必须有记录 1',
      ).toBe(1);
      const dimensionExhausted = Object.values(summary.per_tool_counts)
        .reduce((total, counts) => total + counts.quota_exhausted, 0);
      expect(dimensionExhausted, '维度应在锁内被拒并累计 quota_exhausted').toBeGreaterThanOrEqual(1);
    };

    if (firstRun.status() === 409) {
      const detail = (await firstRun.json() as {detail: TycBatchUnavailableDetail}).detail;
      expect(detail, '起始额度不足时返回结构化 quota_exhausted').toMatchObject({
        code: 'unavailable',
        reason: 'quota_exhausted',
      });
      await setTycDailyLimit(page, resolvedSourceId, detail.daily_used + 1);
      const summary = await runTycBatchViaApi(page, resolvedSourceId, supplierId);
      assertQuotaExhaustedContract(summary);
    } else {
      expect(firstRun.status(), '首跑必须返回 200 汇总或结构化 409').toBe(200);
      const summary = await firstRun.json() as TycBatchRunSummary;
      assertQuotaExhaustedContract(summary);
    }
  } finally {
    // 恢复隔离栈默认提交态（daily_limit=1000），避免同栈后续用例继承低额度。
    if (tycSourceId !== null) await configureTycSource(page);
    await context.close();
  }
});
