import {expect, test, type Page} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
import {resolve} from 'node:path';

const evidenceDirectory = resolve(process.cwd(), '..', '.omo', 'evidence');
const testUsername = 'e2e-platform-admin';
const viewerUsername = 'e2e-viewer';
const testPassword = 'E2E-Test-Only-2026!';

const login = async (page: Page, username: string, initialUrl: string) => {
  await page.goto(initialUrl);
  await page.getByLabel('用户名').fill(username);
  await page.getByLabel('密码').fill(testPassword);
  await page.getByRole('button', {name: '登录'}).click();
  // 等待登录完成：admin 会看到用户管理页标题，viewer 会看到风险总览
  if (username === testUsername) {
    await expect(page.getByRole('heading', {name: '用户管理'})).toBeVisible({timeout: 15_000});
  } else {
    await expect(page.getByRole('heading', {name: '全网供应链风险概览'})).toBeVisible({timeout: 15_000});
  }
};

const assertNoHorizontalOverflow = async (page: Page) => {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
};

const waitForSettledRoute = async (page: Page) => {
  await expect(page.getByRole('status', {name: '正在初始化供应商风险监控平台'})).toBeHidden({timeout: 5_000});
  await expect(page.getByTestId('route-content')).toHaveCSS('opacity', '1');
};

test.beforeAll(async () => { await mkdir(evidenceDirectory, {recursive: true}); });

test('管理员主链路：创建→编辑资料→重置他人密码→本人编辑自锁', async ({browser}) => {
  const context = await browser.newContext({viewport: {width: 1280, height: 720}, reducedMotion: 'reduce'});
  const page = await context.newPage();
  const consoleErrors: string[] = [];
  const timestamp = Date.now();
  const newUsername = `e2e-um-${timestamp}`;

  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text());
  });

  // 1. 登录 admin 并导航到用户管理页
  await login(page, testUsername, '/settings/users');

  // 2. 断言列表显示 seed 用户
  await expect(page.getByRole('table', {name: '平台用户列表'})).toBeVisible();
  await expect(page.getByRole('row').filter({hasText: 'e2e-platform-admin'})).toBeVisible();
  await expect(page.getByRole('row').filter({hasText: 'e2e-viewer'})).toBeVisible();

  // 3. 创建用户
  await page.getByRole('button', {name: '创建用户'}).click();
  await expect(page.getByRole('dialog', {name: '创建用户'})).toBeVisible();

  await page.getByRole('textbox', {name: '用户名'}).fill(newUsername);
  await page.getByLabel('初始密码').fill(testPassword);
  await page.getByLabel('显示名称').fill('E2E User Management Test');
  await page.getByLabel('邮箱').fill(`e2e-um-${timestamp}@test.local`);
  await page.getByLabel('角色').selectOption('risk_analyst');
  await page.getByRole('button', {name: '创建用户'}).last().click();

  // 4. 断言列表出现新用户且有保存提示
  await expect(page.getByRole('status')).toContainText(`已保存 ${newUsername}`);
  await expect(page.getByRole('table', {name: '平台用户列表'})).toContainText(newUsername);

  // 5. 编辑新用户：改显示名称与角色
  const newUserRow = page.getByRole('row').filter({hasText: newUsername});
  await newUserRow.getByRole('button', {name: '编辑'}).click();
  await expect(page.getByRole('dialog', {name: '编辑用户'})).toBeVisible();

  await page.getByLabel('显示名称').fill('E2E UM Updated');
  await page.getByLabel('角色').selectOption('risk_admin');
  await page.getByRole('button', {name: '保存更改'}).click();

  // 6. 断言会话撤销提示
  await expect(page.getByRole('status')).toContainText('权限或状态更改');
  await expect(page.getByRole('status')).toContainText('该用户现有会话已撤销');

  // 7. 重置新用户密码
  const newUserRowAfterEdit = page.getByRole('row').filter({hasText: newUsername});
  await newUserRowAfterEdit.getByRole('button', {name: '重置密码'}).click();
  await expect(page.getByRole('dialog', {name: '重置密码'})).toBeVisible();

  await page.getByLabel('新密码').fill('New-Reset-Pass-2026!');
  await page.getByRole('button', {name: '确认重置密码'}).click();

  // 8. 断言重置成功提示
  await expect(page.getByRole('status')).toContainText(`已重置 ${newUsername} 的密码`);

  // 9. 编辑本人（platform-admin）：断言角色/状态 select 为 disabled
  const adminRow = page.getByRole('row').filter({hasText: 'e2e-platform-admin'});
  await adminRow.getByRole('button', {name: '编辑'}).click();
  await expect(page.getByRole('dialog', {name: '编辑用户'})).toBeVisible();

  // 角色 select 应禁用
  const roleSelect = page.getByLabel('角色');
  await expect(roleSelect).toBeDisabled();

  // 状态 select 应禁用
  const statusSelect = page.getByLabel('状态');
  await expect(statusSelect).toBeDisabled();

  // 显示「不能修改本人角色或状态」文案
  await expect(page.getByText('不能修改本人角色或状态')).toBeVisible();

  // 重置按钮位置显示「请使用个人设置修改本人密码」
  // （在操作列中，非弹窗内）
  await page.getByRole('button', {name: '关闭用户编辑弹窗'}).click();

  // 检查 admin 行的操作区域中包含「请使用个人设置修改本人密码」文案
  await expect(adminRow.getByText('请使用个人设置修改本人密码')).toBeVisible();

  // 10. 截图（主链路完成后）
  await waitForSettledRoute(page);
  await page.screenshot({path: resolve(evidenceDirectory, 'task-11-frontend-gap-closure.png'), fullPage: true});
  await assertNoHorizontalOverflow(page);

  // 排除登录前 /auth/me 401，只关注产品错误
  const realErrors = consoleErrors.filter((msg) => !msg.includes('/auth/me') && !msg.includes('401'));
  expect(realErrors).toEqual([]);

  await context.close();
});

test('viewer 拒绝链路：直访 /settings/users 显示无权访问且无管理入口', async ({browser}) => {
  const context = await browser.newContext({viewport: {width: 390, height: 844}, reducedMotion: 'reduce'});
  const page = await context.newPage();
  const consoleErrors: string[] = [];

  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text());
  });

  // 1. 登录 viewer（先到 overview 页面）
  await login(page, viewerUsername, '/overview');

  // 2. 直接访问 /settings/users
  await page.goto('/settings/users');

  // 3. 断言出现「无权访问」
  await expect(page.getByRole('heading', {name: '无权访问'})).toBeVisible();
  await expect(page.getByText('当前账号没有访问此页面所需的权限')).toBeVisible();

  // 4. 断言不渲染用户列表/操作按钮
  await expect(page.getByRole('table', {name: '平台用户列表'})).toHaveCount(0);
  await expect(page.getByRole('button', {name: '创建用户'})).toHaveCount(0);

  // 5. 截图（移动端无权访问）
  await waitForSettledRoute(page);
  await page.screenshot({path: resolve(evidenceDirectory, 'task-11-frontend-gap-closure-mobile.png'), fullPage: true});
  await assertNoHorizontalOverflow(page);

  // 403 是 viewer 访问受限 API 的预期结果，不需要过滤 /auth/me 401
  const realErrors = consoleErrors.filter((msg) => !msg.includes('/auth/me') && !msg.includes('401') && !msg.includes('403'));
  expect(realErrors).toEqual([]);

  await context.close();
});
