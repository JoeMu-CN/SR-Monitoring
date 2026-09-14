/**
 * 完整系统自检的本地时间戳策略：
 * - 登录成功或距上次完整自检 ≥ 30 分钟（含无记录）时执行完整自检；
 * - 30 分钟内刷新只走简单加载动画，避免每次刷新都打扰用户。
 * 所有 localStorage 访问都容错，测试或无 storage 环境（隐私模式）下不抛错。
 */

export const SELF_CHECK_TTL_MS: number = 30 * 60 * 1000;

export const SELF_CHECK_STORAGE_KEY: string = 'sr-selfcheck-at';

export function readLastSelfCheckAt(): number | null {
  try {
    if (typeof window === 'undefined') return null;
    const raw = window.localStorage.getItem(SELF_CHECK_STORAGE_KEY);
    if (raw === null || raw.trim() === '') return null;
    const parsed = Number(raw);
    return Number.isFinite(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

export function writeLastSelfCheckAt(at: number): void {
  try {
    if (typeof window === 'undefined') return;
    window.localStorage.setItem(SELF_CHECK_STORAGE_KEY, String(at));
  } catch {
    // storage 不可用时静默跳过：时间戳只是优化项，失败不应阻塞开屏放行。
  }
}

/** 边界规则：now - lastAt >= ttlMs（含恰好等于 ttlMs）视为需要完整自检；无记录始终需要。 */
export function shouldRunFullSelfCheck(now: number, lastAt: number | null, ttlMs: number = SELF_CHECK_TTL_MS): boolean {
  if (lastAt === null) return true;
  return now - lastAt >= ttlMs;
}
