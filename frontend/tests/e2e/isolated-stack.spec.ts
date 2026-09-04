import {expect, test, type Locator, type Page} from '@playwright/test';
import {mkdir, writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';

const productionBaseUrl = 'http://127.0.0.1:18080';
const evidenceDirectory = resolve(process.cwd(), '..', '.omo', 'evidence', 'task-12-frontend-gap-closure', 'browser');
const testUsername = 'e2e-platform-admin';
const testPassword = 'E2E-Test-Only-2026!';
const desktopViewport = {width: 1280, height: 720};
const mobileViewport = {width: 390, height: 844};
const spaPaths = ['/overview', '/risks', '/research', '/source-agent', '/task-12-unknown-route'] as const;

type ConsoleError = {
  readonly source: string;
  readonly line: number;
  readonly column: number;
};

type FailedRequest = {
  readonly method: string;
  readonly path: string;
  readonly error: string;
};

type HttpError = {
  readonly method: string;
  readonly path: string;
  readonly status: number;
};

type BrowserEvidence = {
  readonly consoleErrors: ConsoleError[];
  readonly failedRequests: FailedRequest[];
  readonly httpErrors: HttpError[];
};

const login = async (page: Page) => {
  await page.goto('/overview');
  await page.getByLabel('用户名').fill(testUsername);
  await page.getByLabel('密码').fill(testPassword);
  await page.getByRole('button', {name: '登录'}).click();
  await expect(page.getByRole('heading', {name: '全网供应链风险概览'})).toBeVisible({timeout: 15_000});
};

const waitForSettledRoute = async (page: Page) => {
  await expect(page.getByRole('status', {name: '正在初始化供应商风险监控平台'})).toBeHidden({timeout: 5_000});
  await expect(page.getByTestId('route-content')).toHaveCSS('opacity', '1');
};

const waitForSplashToFinish = async (page: Page) => {
  const splash = page.getByRole('status', {name: '正在初始化供应商风险监控平台'});
  await expect(splash).toBeVisible();
  await expect(splash).toBeHidden({timeout: 5_000});
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
    return bounds.top >= 0
      && bounds.left >= 0
      && bounds.right <= window.innerWidth
      && bounds.bottom <= window.innerHeight;
  })).toBe(true);
};

const focusWithTab = async (page: Page, target: Locator) => {
  for (let tabIndex = 0; tabIndex < 60; tabIndex += 1) {
    await page.keyboard.press('Tab');
    if (await target.evaluate((element) => document.activeElement === element)) return;
  }
  await expect(target).toBeFocused();
};

const pathFromUrl = (url: string) => decodeURIComponent(new URL(url).pathname);

const collectBrowserEvidence = (page: Page): BrowserEvidence => {
  const evidence: BrowserEvidence = {consoleErrors: [], failedRequests: [], httpErrors: []};
  page.on('console', (message) => {
    if (message.type() === 'error') {
      const location = message.location();
      evidence.consoleErrors.push({
        source: location.url ? pathFromUrl(location.url) : 'browser',
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

const isExpectedHttpError = (error: HttpError) => (
  error.method === 'POST' && error.path === '/api/v1/auth/logout' && error.status === 403
);

const assertBrowserEvidence = async (name: string, evidence: BrowserEvidence) => {
  const unexpectedHttpErrors = evidence.httpErrors.filter((error) => !isExpectedHttpError(error));
  await writeFile(
    resolve(evidenceDirectory, `${name}-console-network.json`),
    `${JSON.stringify({...evidence, unexpectedHttpErrors}, null, 2)}\n`,
    'utf8',
  );
  expect(evidence.consoleErrors).toEqual([]);
  expect(evidence.failedRequests).toEqual([]);
  expect(unexpectedHttpErrors).toEqual([]);
};

test.beforeAll(async () => {
  expect(process.env.PLAYWRIGHT_BASE_URL).toBe(productionBaseUrl);
  await mkdir(evidenceDirectory, {recursive: true});
});

test('真实 FastAPI static fallback 加载 SPA 路径，且未知 API 保持 HTTP 404', async ({browser}) => {
  const context = await browser.newContext({viewport: desktopViewport, reducedMotion: 'reduce'});
  const page = await context.newPage();

  try {
    await login(page);
    const evidence = collectBrowserEvidence(page);
    for (const routePath of spaPaths) {
      const response = await page.goto(routePath);
      expect(response?.status()).toBe(200);
      expect(response?.headers()['content-type'] ?? '').toContain('text/html');
    }

    await page.goto('/overview');
    await expect(page.getByRole('heading', {name: '全网供应链风险概览'})).toBeVisible();
    await page.goto('/risks');
    await expect(page.getByRole('button', {name: /E2E Supplier 001/})).toBeVisible();

    for (const frozenOrUnknownPath of ['/research', '/source-agent', '/task-12-unknown-route']) {
      await page.goto(frozenOrUnknownPath);
      await expect(page.getByRole('heading', {name: '页面不存在'})).toBeVisible();
    }
    await expect(page.getByRole('heading', {name: '把公开信息变成可回看的研究草稿'})).toHaveCount(0);

    const apiResponse = await page.request.get('/api/不存在');
    expect(apiResponse.status()).toBe(404);
    expect(apiResponse.headers()['content-type'] ?? '').toContain('application/json');
    await assertNoHorizontalOverflow(page);
    await assertBrowserEvidence('fallback', evidence);
  } finally {
    await context.close();
  }
});

test('键盘 Tab 顺序、focus-visible 与主题设置在桌面生产容器中持久化', async ({browser}) => {
  const context = await browser.newContext({viewport: desktopViewport, reducedMotion: 'reduce'});
  const page = await context.newPage();
  const username = page.getByLabel('用户名');
  const password = page.getByLabel('密码');
  const loginButton = page.getByRole('button', {name: '登录'});
  const settingsButton = page.getByRole('button', {name: '设置'});
  const themeControl = page.locator('select').filter({hasText: '浅色模式'});
  const reduceMotionControl = page.getByLabel('减少页面过渡与脉冲动效');

  try {
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
    const evidence = collectBrowserEvidence(page);

    await focusWithTab(page, settingsButton);
    await assertKeyboardFocus(settingsButton);
    await page.keyboard.press('Enter');
    await expect(page.getByText('系统设置')).toBeVisible();
    await themeControl.selectOption('light');
    await reduceMotionControl.check();
    await page.getByRole('button', {name: '保存设置'}).click();
    await expect(page.locator('html')).toHaveClass(/light/);
    await expect(page.locator('html')).not.toHaveClass(/dark/);
    await expect(page.locator('html')).toHaveClass(/reduce-motion/);
    expect(await page.evaluate(() => ({
      theme: localStorage.getItem('sr-theme'),
      reduceMotion: localStorage.getItem('sr-reduce-motion'),
    }))).toEqual({theme: 'light', reduceMotion: 'true'});

    await page.reload();
    await expect(page.getByRole('heading', {name: '全网供应链风险概览'})).toBeVisible();
    await expect(page.locator('html')).toHaveClass(/light/);
    await expect(page.locator('html')).toHaveClass(/reduce-motion/);
    await settingsButton.click();
    await expect(themeControl).toHaveValue('light');
    await expect(reduceMotionControl).toBeChecked();
    await themeControl.selectOption('dark');
    await page.getByRole('button', {name: '保存设置'}).click();
    await expect(page.locator('html')).toHaveClass(/dark/);
    await expect(page.locator('html')).not.toHaveClass(/light/);
    await expect(page.locator('html')).toHaveClass(/reduce-motion/);

    await page.reload();
    const overviewHeading = page.getByRole('heading', {name: '全网供应链风险概览'});
    await waitForSplashToFinish(page);
    await waitForSettledRoute(page);
    await expect(page.locator('html')).toHaveClass(/dark/);
    await expect(page.locator('html')).toHaveClass(/reduce-motion/);
    expect(await page.evaluate(() => ({
      theme: localStorage.getItem('sr-theme'),
      reduceMotion: localStorage.getItem('sr-reduce-motion'),
    }))).toEqual({theme: 'dark', reduceMotion: 'true'});
    await assertNoHorizontalOverflow(page);
    await assertVisibleWithinViewport(overviewHeading);
    await page.screenshot({path: resolve(evidenceDirectory, 'desktop-settings-dark.png')});
    await assertBrowserEvidence('desktop', evidence);
  } finally {
    await context.close();
  }
});

test('已登录真实会话拒绝缺少 CSRF header 的写请求，并保留移动端可用性', async ({browser}) => {
  const context = await browser.newContext({viewport: mobileViewport, reducedMotion: 'reduce'});
  const page = await context.newPage();

  try {
    await login(page);
    const evidence = collectBrowserEvidence(page);
    const csrfRejectedResponse = await page.request.post('/api/v1/auth/logout', {
      headers: {Origin: productionBaseUrl},
    });
    expect(csrfRejectedResponse.status()).toBe(403);

    await page.goto('/overview');
    const overviewHeading = page.getByRole('heading', {name: '全网供应链风险概览'});
    await waitForSplashToFinish(page);
    await waitForSettledRoute(page);
    await assertNoHorizontalOverflow(page);
    await assertVisibleWithinViewport(overviewHeading);
    await page.screenshot({path: resolve(evidenceDirectory, 'mobile-overview-light.png')});
    await assertBrowserEvidence('mobile', evidence);
  } finally {
    await context.close();
  }
});
