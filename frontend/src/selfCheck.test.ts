import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {
  SELF_CHECK_STORAGE_KEY,
  SELF_CHECK_TTL_MS,
  readLastSelfCheckAt,
  shouldRunFullSelfCheck,
  writeLastSelfCheckAt,
} from './selfCheck';

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('selfCheck 时间戳存储', () => {
  it('TTL 为 30 分钟、storage key 固定', () => {
    expect(SELF_CHECK_TTL_MS).toBe(30 * 60 * 1000);
    expect(SELF_CHECK_STORAGE_KEY).toBe('sr-selfcheck-at');
  });

  it('write → read 往返得到同一时间戳', () => {
    const at = 1_757_000_000_000;
    writeLastSelfCheckAt(at);
    expect(readLastSelfCheckAt()).toBe(at);
  });

  it('无记录时返回 null', () => {
    expect(readLastSelfCheckAt()).toBeNull();
  });

  it('非法值返回 null（非数字、空串）', () => {
    localStorage.setItem(SELF_CHECK_STORAGE_KEY, 'not-a-number');
    expect(readLastSelfCheckAt()).toBeNull();
    localStorage.setItem(SELF_CHECK_STORAGE_KEY, '');
    expect(readLastSelfCheckAt()).toBeNull();
  });

  it('storage 抛错时 read 返回 null、write 不抛出', () => {
    const getItem = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('storage denied');
    });
    expect(readLastSelfCheckAt()).toBeNull();
    getItem.mockRestore();

    const setItem = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('storage denied');
    });
    expect(() => writeLastSelfCheckAt(Date.now())).not.toThrow();
    setItem.mockRestore();
  });
});

describe('shouldRunFullSelfCheck 判定', () => {
  it('无记录（null）→ 需要完整自检', () => {
    expect(shouldRunFullSelfCheck(1_000_000, null)).toBe(true);
  });

  it('恰好等于 TTL → 需要完整自检（边界含等号）', () => {
    expect(shouldRunFullSelfCheck(10_000_000 + SELF_CHECK_TTL_MS, 10_000_000, SELF_CHECK_TTL_MS)).toBe(true);
  });

  it('小于 TTL → 跳过完整自检', () => {
    expect(shouldRunFullSelfCheck(10_000_000 + SELF_CHECK_TTL_MS - 1, 10_000_000, SELF_CHECK_TTL_MS)).toBe(false);
  });

  it('大于 TTL → 需要完整自检', () => {
    expect(shouldRunFullSelfCheck(10_000_000 + SELF_CHECK_TTL_MS + 1, 10_000_000, SELF_CHECK_TTL_MS)).toBe(true);
  });

  it('默认 TTL 不传时同样按 30 分钟判定', () => {
    const now = 10_000_000;
    expect(shouldRunFullSelfCheck(now, now - SELF_CHECK_TTL_MS + 1)).toBe(false);
    expect(shouldRunFullSelfCheck(now, now - SELF_CHECK_TTL_MS)).toBe(true);
  });
});
