import {expect, test} from '@playwright/test';
import {
  VIEWPORT_DESKTOP,
  VIEWPORT_NARROW,
  expectGaugeAboveBar,
  expectTicksAligned,
  measureGauge,
  openGauge,
  reloadGauge,
  enableDarkMode,
} from './rule-engine-gauge-support';

/**
 * ⑤ 总分层几何回归：实际得分标识必须完整位于彩条上方，指针仍落在真实分数位置。
 * fixture 与几何契约见 rule-engine-gauge-support.ts；jsdom 不解析 Tailwind 几何，
 * 该重叠缺陷只能在真实浏览器用 boundingBox 证明。
 */
test.describe('规则引擎观察态 ⑤ 总分层：得分标识与彩条几何', () => {
  test('72 分（1280px）：标识位于彩条上方，指针落在真实分数位置，刻度对齐', async ({browser}) => {
    // Given 1280px 桌面视口与 mock 总分 72
    const context = await browser.newContext({viewport: VIEWPORT_DESKTOP});
    const page = await context.newPage();
    const consoleErrors = await openGauge(page, 72);
    // When 测量静止态几何
    const geometry = await measureGauge(page, 'desktop-72');
    // Then 标识在彩条上方、指针落到 72% 位置、刻度对齐、无 console error
    expectGaugeAboveBar(geometry, 72);
    await expectTicksAligned(page, geometry.bar);
    expect(consoleErrors).toEqual([]);
    await context.close();
  });

  test('边界 0 分与 100 分（1280px）：标识仍在彩条上方且不被滚动容器裁剪', async ({browser}) => {
    // Given 先以 0 分开局，再切换为 100 分（覆盖两侧 translate 边界）
    const context = await browser.newContext({viewport: VIEWPORT_DESKTOP});
    const page = await context.newPage();
    const consoleErrors = await openGauge(page, 0);
    // Then 0 分（左边界对齐）满足全部几何契约
    expectGaugeAboveBar(await measureGauge(page, 'desktop-0'), 0);
    // When 切换为 100 分
    await reloadGauge(page, 100);
    // Then 100 分（右边界对齐）同样不被裁剪
    expectGaugeAboveBar(await measureGauge(page, 'desktop-100'), 100);
    expect(consoleErrors).toEqual([]);
    await context.close();
  });

  test('72 分（375px）：窄屏同样不重叠、指针正确、标识不被裁剪', async ({browser}) => {
    // Given 375px 窄屏视口
    const context = await browser.newContext({viewport: VIEWPORT_NARROW});
    const page = await context.newPage();
    const consoleErrors = await openGauge(page, 72);
    // When 测量静止态几何
    const geometry = await measureGauge(page, 'narrow-72');
    // Then 窄屏下契约与桌面一致
    expectGaugeAboveBar(geometry, 72);
    await expectTicksAligned(page, geometry.bar);
    expect(consoleErrors).toEqual([]);
    await context.close();
  });

  test('深色模式（1280px）：计算样式证明 dark 生效且几何与浅色一致', async ({browser}) => {
    // Given 观察态渲染完成
    const context = await browser.newContext({viewport: VIEWPORT_DESKTOP});
    const page = await context.newPage();
    await openGauge(page, 72);
    // When 挂上 .dark（index.css 的 class 驱动深色模式）
    const {light, dark} = await enableDarkMode(page);
    // Then 计算背景色确实切换，且几何契约不变
    expect(dark, '深色模式下容器背景色应与浅色不同').not.toBe(light);
    expectGaugeAboveBar(await measureGauge(page, 'desktop-72-dark'), 72);
    await context.close();
  });
});
