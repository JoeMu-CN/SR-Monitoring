import {useEffect, useRef, useState} from 'react';
import {api, ApiError, type MonitoringHealthRead} from './api';

// 计划行212：页面可见时每 60 秒刷新；隐藏暂停、重新可见立即刷新、销毁取消。
export const MONITORING_HEALTH_REFRESH_MS = 60_000;

const OVERALL_STATUSES: readonly string[] = ['ok', 'degraded', 'unknown', 'inactive'];

// 非 200 契约载荷的边界解析：HTTP 可能 200 但 body 不是 MonitoringHealthRead（如 {detail:...}）。
// 最小契约校验（对象非空、overall 合法枚举、sources 为数组）通过才进入 ready；
// 非核心诊断不得阻塞核心渲染，载荷非法时与诊断失败同权降级 unknown。
const isMonitoringHealthRead = (value: unknown): value is MonitoringHealthRead => {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return false;
  if (!('overall' in value) || !('sources' in value)) return false;
  const {overall, sources} = value;
  return (
    typeof overall === 'string'
    && OVERALL_STATUSES.includes(overall)
    && Array.isArray(sources)
  );
};

// 诊断快照状态机：
// - loading：首次请求在途（未取得任何结论，不渲染横幅）；
// - hidden：无权限或服务端 403（只读诊断整体隐藏）；
// - unknown：诊断失败（含 503），展示「尚无法确认监控状态」，绝不沿用旧 ok 结论；
// - ready：最后一次成功聚合。
export type MonitoringHealthSnapshot =
  | {readonly status: 'loading'}
  | {readonly status: 'hidden'}
  | {readonly status: 'unknown'}
  | {readonly status: 'ready'; readonly health: MonitoringHealthRead};

interface UseMonitoringHealthOptions {
  /** 当前账号是否具备 source_status_view 权限；false 时不发任何请求。 */
  readonly enabled: boolean;
  /** 当前路由是否展示诊断（总览/数据源）；false 时暂停轮询。 */
  readonly active: boolean;
  /**
   * 外部刷新版本号（默认 0）：递增时若 enabled+active 且页面可见，
   * 立即发起新请求并重置 60 秒周期；旧响应由 AbortController/序号抑制。
   */
  readonly refreshVersion?: number;
  /** 401 统一入口：交给 App 会话失效处理（403 不走这里）。 */
  readonly onRequestError: (error: ApiError) => void;
}

export function useMonitoringHealth({
  enabled,
  active,
  refreshVersion = 0,
  onRequestError,
}: UseMonitoringHealthOptions): MonitoringHealthSnapshot {
  const [snapshot, setSnapshot] = useState<MonitoringHealthSnapshot>({status: 'loading'});
  // 请求序号：慢的旧响应不得覆盖新一轮状态；清理时自增使在途响应全部失效。
  const sequenceRef = useRef(0);
  const controllerRef = useRef<AbortController | null>(null);
  // 回调放进 ref，避免调用方传入不稳定函数导致轮询被反复重建。
  const onRequestErrorRef = useRef(onRequestError);
  useEffect(() => {
    onRequestErrorRef.current = onRequestError;
  });

  useEffect(() => {
    if (!enabled || !active) return undefined;

    let disposed = false;
    let timer: number | undefined;

    const fetchHealth = () => {
      controllerRef.current?.abort();
      const controller = new AbortController();
      controllerRef.current = controller;
      const sequence = sequenceRef.current + 1;
      sequenceRef.current = sequence;

      void api.monitoringHealth(controller.signal)
        .then((health) => {
          if (disposed || sequenceRef.current !== sequence) return;
          if (!isMonitoringHealthRead(health)) {
            // 200 但载荷非契约：按诊断失败处理，展示「尚无法确认监控状态」。
            setSnapshot({status: 'unknown'});
            return;
          }
          setSnapshot({status: 'ready', health});
        })
        .catch((caught: unknown) => {
          if (disposed || sequenceRef.current !== sequence) return;
          if (caught instanceof DOMException && caught.name === 'AbortError') return;
          if (caught instanceof ApiError) {
            if (caught.status === 401) {
              // 登录失效统一走 App 处理；快照保持 loading，等待登出后的重挂载。
              onRequestErrorRef.current(caught);
              return;
            }
            if (caught.status === 403) {
              setSnapshot({status: 'hidden'});
              return;
            }
          }
          // 其余失败（含 503）：展示「无法确认」，不沿用任何旧结论。
          setSnapshot({status: 'unknown'});
        });
    };

    const stopTimer = () => {
      if (timer !== undefined) {
        window.clearInterval(timer);
        timer = undefined;
      }
    };
    const startCycle = () => {
      fetchHealth();
      stopTimer();
      timer = window.setInterval(fetchHealth, MONITORING_HEALTH_REFRESH_MS);
    };
    const handleVisibility = () => {
      if (document.visibilityState === 'visible') {
        // 重新可见：立即刷新并重建 60 秒周期。
        startCycle();
      } else {
        // 隐藏页暂停：仅停表，不打断在途请求（其结果仍受序号保护）。
        stopTimer();
      }
    };

    if (document.visibilityState === 'visible') startCycle();
    document.addEventListener('visibilitychange', handleVisibility);

    return () => {
      disposed = true;
      stopTimer();
      document.removeEventListener('visibilitychange', handleVisibility);
      controllerRef.current?.abort();
      sequenceRef.current += 1;
    };
    // refreshVersion 递增触发重建：清理会 abort 在途请求并推进序号，
    // 新周期立即刷新并重置 60 秒表；隐藏或停用时仅重建、不发起请求。
  }, [enabled, active, refreshVersion]);

  return snapshot;
}
