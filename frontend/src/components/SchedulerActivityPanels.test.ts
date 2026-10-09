import {CheckCircle2} from 'lucide-react';
import {describe, expect, it} from 'vitest';
import {RUN_STATUS_META, formatDuration, formatRunningDuration} from './SchedulerActivityPanels';

// 只读时间格式化的诚实口径：真实时间戳缺失/非法/倒序时返回占位符，不用 0 冒充；
// 相同时刻或不足 1 秒的真实时长保留为 0 秒，不是占位。

describe('formatRunningDuration（观测时长）', () => {
  it('开始时间缺失返回占位符', () => {
    expect(formatRunningDuration(null, '2026-09-11T06:00:00Z')).toBe('—');
  });

  it('任一端时间非法返回占位符', () => {
    expect(formatRunningDuration('not-a-date', '2026-09-11T06:00:00Z')).toBe('—');
    expect(formatRunningDuration('2026-09-11T05:00:00Z', 'not-a-date')).toBe('—');
  });

  it('快照时刻早于开始时间（倒序）返回占位符，不虚构 0 秒', () => {
    expect(formatRunningDuration('2026-09-11T06:00:00Z', '2026-09-11T05:00:00Z')).toBe('—');
  });

  it('相同时刻的真实 0 秒时长保留', () => {
    expect(formatRunningDuration('2026-09-11T06:00:00Z', '2026-09-11T06:00:00Z')).toBe('0 秒');
  });

  it('正常观测时长按真实差值计算', () => {
    expect(formatRunningDuration('2026-09-11T05:47:00Z', '2026-09-11T06:00:00Z')).toBe('13 分钟');
  });
});

describe('formatDuration（记录时长）', () => {
  it('任一端缺失返回占位符', () => {
    expect(formatDuration(null, '2026-09-11T06:00:00Z')).toBe('—');
    expect(formatDuration('2026-09-11T05:00:00Z', null)).toBe('—');
    expect(formatDuration(null, null)).toBe('—');
  });

  it('任一端时间非法返回占位符', () => {
    expect(formatDuration('invalid', '2026-09-11T06:00:00Z')).toBe('—');
    expect(formatDuration('2026-09-11T05:00:00Z', 'invalid')).toBe('—');
  });

  it('结束时间早于开始时间（倒序）返回占位符，不虚构 0 秒', () => {
    expect(formatDuration('2026-09-11T06:00:00Z', '2026-09-11T05:00:00Z')).toBe('—');
  });

  it('相同时刻或不足 1 秒的真实 0 秒时长保留', () => {
    expect(formatDuration('2026-09-11T06:00:00Z', '2026-09-11T06:00:00Z')).toBe('0 秒');
    expect(formatDuration('2026-09-11T06:00:00.000Z', '2026-09-11T06:00:00.400Z')).toBe('0 秒');
  });

  it('正常耗时按真实差值计算', () => {
    expect(formatDuration('2026-09-11T05:30:02Z', '2026-09-11T05:30:14Z')).toBe('12 秒');
  });
});

describe('RUN_STATUS_META completed 中性语义', () => {
  it('执行完成使用中性色与非成功图标，不冒充业务成功', () => {
    const meta = RUN_STATUS_META.completed;
    expect(meta.label).toBe('执行完成');
    expect(meta.spinning).toBe(false);
    expect(meta.iconClass).toContain('slate');
    expect(meta.chipClass).toContain('slate');
    expect(`${meta.iconClass} ${meta.chipClass}`).not.toContain('34c759');
    expect(meta.icon).not.toBe(CheckCircle2);
  });
});
