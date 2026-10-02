import {describe, expect, it} from 'vitest';
import type {DataSource} from './types';
import {describeSourceSchedule} from './sourceSchedule';

// 最小快照工厂：只覆盖调度展示需要的 schedule/type/code 三个字段，其余字段不属于契约。
const sourceOf = (
  overrides: Partial<Pick<DataSource, 'schedule' | 'type' | 'code'>>,
): Pick<DataSource, 'schedule' | 'type' | 'code'> => ({
  schedule: null,
  type: 'official',
  code: 'official-source',
  ...overrides,
});

describe('describeSourceSchedule cron 识别正例', () => {
  it.each([
    ['* * * * *', '每分钟'],
    ['*/5 * * * *', '每 5 分钟'],
    ['*/30 * * * *', '每 30 分钟'],
    ['0 */6 * * *', '每 6 小时'],
    ['15 * * * *', '每小时 15 分'],
    ['0 3 * * *', '每天 03:00'],
    ['30 8 * * mon', '每周一 08:30'],
    ['0 6 * * sun', '每周日 06:00'],
    ['0 0 1 * *', '每月 1 日 00:00'],
    ['0 8 * * 0', '每周一 08:00'],
    ['0 8 * * 6', '每周日 08:00'],
  ])('cron「%s」展示为「%s」且带北京时间标注', (cron, label) => {
    // Given 一个使用该 cron 的官方来源
    const source = sourceOf({schedule: cron, type: 'official', code: 'nmc-weather'});

    // When 生成调度展示信息
    const display = describeSourceSchedule(source);

    // Then label 为中文自然语言，title 与 ariaLabel 带规范化 cron 与北京时间
    expect(display.label).toBe(label);
    expect(display.title).toBe(`${cron}（北京时间）`);
    expect(display.title).toContain('北京时间');
    expect(display.ariaLabel).toBe(`调度周期：${label}，原始 cron ${cron}（北京时间）`);
    expect(display.ariaLabel.startsWith('调度周期：')).toBe(true);
  });

  it('数字星期按 APScheduler 语义：0 为周一、6 为周日（不得用标准 crontab 的 0=周日）', () => {
    // Given 两份仅星期数字不同的 cron
    const monday = describeSourceSchedule(sourceOf({schedule: '0 8 * * 0'}));
    const sunday = describeSourceSchedule(sourceOf({schedule: '0 8 * * 6'}));

    // When/Then 数字语义与 APScheduler 对齐
    expect(monday.label).toBe('每周一 08:00');
    expect(sunday.label).toBe('每周日 08:00');
  });
});

describe('describeSourceSchedule cron 识别负例', () => {
  it.each([
    ['0 8 * * 7'], // 数字越界（APScheduler 仅接受 0…6）
    ['*/60 * * * *'], // 非法步长 N=60
    ['0 */24 * * *'], // 非法步长 N=24
    ['0 3 * *'], // 段数错误（4 段）
    ['61 3 * * *'], // 分钟越界
    ['0 3 * 1 *'], // 月份字段不支持识别
    ['*/0 * * * *'], // 非法步长 N=0
  ])('畸形 cron「%s」不崩溃并回退为自定义周期', (cron) => {
    // Given 使用畸形 cron 的官方来源
    const source = sourceOf({schedule: cron});

    // When 生成调度展示信息
    const display = describeSourceSchedule(source);

    // Then 不误识别为常用周期，label 固定为自定义周期，title/ariaLabel 保留规范化表达式
    expect(display.label).toBe('自定义周期');
    expect(display.label).not.toBe(cron);
    expect(display.title).toContain('未能识别为常用周期');
    expect(display.title).toContain(cron);
    expect(display.ariaLabel).toContain('未能识别为常用周期');
    expect(display.ariaLabel).toContain(cron);
  });
});

describe('describeSourceSchedule 空白归一化', () => {
  it('首尾与连续空白归一化后仍按同一 cron 识别', () => {
    // Given cron 文本带多余空白
    const source = sourceOf({schedule: '  */30   * * * *  '});

    // When 生成调度展示信息
    const display = describeSourceSchedule(source);

    // Then 归一化为标准表达式，label/title 与无空白输入一致
    expect(display.label).toBe('每 30 分钟');
    expect(display.title).toBe('*/30 * * * *（北京时间）');
    expect(display.ariaLabel).toBe('调度周期：每 30 分钟，原始 cron */30 * * * *（北京时间）');
  });
});

describe('describeSourceSchedule 类别分支优先于 cron 识别', () => {
  it('天眼查 schedule 为 null 时输出周度分片文案', () => {
    // Given 未配置 cron 的天眼查外部核查工具
    const source = sourceOf({type: 'external_tool', code: 'tianyancha', schedule: null});

    // When 生成调度展示信息
    const display = describeSourceSchedule(source);

    // Then 固定分片文案，说明自动分片与按需核查
    expect(display.label).toBe('每周日、周一 06:00（分片）');
    expect(display.title).toContain('自动核查');
    expect(display.title).toContain('每周日 06:00');
    expect(display.title).toContain('每周一 06:00');
    expect(display.title).toContain('分片执行');
    expect(display.title).toContain('北京时间');
    expect(display.title).toContain('按需核查');
    expect(display.ariaLabel).toContain('分片执行');
    expect(display.ariaLabel).toContain('按需核查');
  });

  it('天眼查即使配置了 cron 也不做 cron 识别', () => {
    // Given 天眼查配置了与固定分片计划冲突的 cron
    const source = sourceOf({type: 'external_tool', code: 'tianyancha', schedule: '*/5 * * * *'});

    // When 生成调度展示信息
    const display = describeSourceSchedule(source);

    // Then 仍输出分片文案，不把 schedule 当通用采集周期
    expect(display.label).toBe('每周日、周一 06:00（分片）');
    expect(display.label).not.toBe('每 5 分钟');
    expect(display.title).not.toContain('*/5 * * * *');
    expect(display.ariaLabel).not.toContain('*/5 * * * *');
  });

  it('其它 external_tool 展示为按需调用', () => {
    // Given 非天眼查的外部核查工具
    const source = sourceOf({
      type: 'external_tool',
      code: 'other-tool',
      schedule: '0 6 * * *',
    });

    // When 生成调度展示信息
    const display = describeSourceSchedule(source);

    // Then 按需调用，不按信源 cron 定时采集
    expect(display.label).toBe('按需调用');
    expect(display.title).toContain('外部核查工具按需调用');
    expect(display.title).toContain('不按信源 cron 定时采集');
    expect(display.ariaLabel).toContain('不按信源 cron 定时采集');
  });

  it('manual-json 展示为人工录入', () => {
    // Given 手工 JSON 导入来源
    const source = sourceOf({type: 'official', code: 'manual-json', schedule: null});

    // When 生成调度展示信息
    const display = describeSourceSchedule(source);

    // Then 人工录入文案，不联网采集
    expect(display.label).toBe('人工录入');
    expect(display.title).toContain('人工上传文件录入');
    expect(display.title).toContain('不联网采集');
    expect(display.ariaLabel).toContain('不联网采集');
  });

  it('普通官方来源 schedule 为 null 时展示为未单独配置', () => {
    // Given 未单独配置 cron 的官方拉取来源
    const source = sourceOf({type: 'official', code: 'nmc-weather', schedule: null});

    // When 生成调度展示信息
    const display = describeSourceSchedule(source);

    // Then 说明跟随系统默认周期（每 30 分钟，可被部署配置覆盖）
    expect(display.label).toBe('未单独配置');
    expect(display.title).toContain('未单独配置 cron');
    expect(display.title).toContain('系统默认周期');
    expect(display.title).toContain('默认每 30 分钟');
    expect(display.title).toContain('部署配置覆盖');
    expect(display.title).toContain('北京时间');
    expect(display.ariaLabel).toContain('默认每 30 分钟');
  });

  it('纯空白 schedule 与 null 等价，展示为未单独配置', () => {
    // Given schedule 只有空白字符的官方来源
    const source = sourceOf({type: 'official', code: 'nmc-weather', schedule: '   '});

    // When 生成调度展示信息
    const display = describeSourceSchedule(source);

    // Then 视为未配置，不进入 cron 识别
    expect(display.label).toBe('未单独配置');
    expect(display.title).toContain('未单独配置 cron');
  });
});
