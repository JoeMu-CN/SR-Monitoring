import {AlertTriangle} from 'lucide-react';
import {formatDateTime} from './SchedulerActivityPanels';

// SchedulerLiveView 私有面板：仅保留心跳延迟警示。
// 只消费 monitoring-health 契约中的心跳时间戳与运行中计数，不含信号内容或日志。

interface StaleNoticeProps {
  readonly workCount: number;
  readonly lastHeartbeatAt: string | null;
}

// 心跳延迟警示：明确观测快照可能失真，running 历史不代表当前活跃，排期只是最近快照。
export const StaleNotice = ({workCount, lastHeartbeatAt}: StaleNoticeProps) => (
  <div
    data-testid="scheduler-live-stale-notice"
    role="status"
    className="flex items-start gap-2.5 rounded-xl border border-amber-900 bg-amber-950/40 p-3.5 text-xs leading-relaxed text-amber-200"
  >
    <AlertTriangle aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0" />
    <div className="min-w-0">
      <p className="font-bold">
        调度器心跳延迟
        {lastHeartbeatAt !== null ? `（最后心跳 ${formatDateTime(lastHeartbeatAt)}）` : ''}
        ，当前无法确认调度器是否在线。
      </p>
      <p className="mt-1">
        {workCount > 0 ? `观测快照中有 ${workCount} 项任务在进行中，可能已结束。` : '观测快照中没有进行中的任务。'}
      </p>
      <p className="mt-1">
        以下执行历史为已落库事实，可参考；标记为「执行中（快照）」的记录不代表当前仍在运行，排期均为最近排期快照。
      </p>
    </div>
  </div>
);
