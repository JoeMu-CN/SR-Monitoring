import {Ban, CircleDot, ClockAlert, Loader2, XCircle, type LucideIcon} from 'lucide-react';
import type {MonitoringScheduledRunStatus} from '../api';

// 调度器实况与原始日志共用的只读时间/状态口径。
// 只消费 monitoring-health 契约中的时间戳与状态码，不包含信号内容、异常文本或日志。

export const formatDateTime = (value: string): string => (
  Number.isNaN(new Date(value).getTime()) ? '未知' : new Date(value).toLocaleString('zh-CN', {hour12: false})
);

const formatSeconds = (totalSeconds: number): string => {
  if (totalSeconds < 60) return `${totalSeconds} 秒`;
  const totalMinutes = Math.floor(totalSeconds / 60);
  if (totalMinutes < 60) return `${totalMinutes} 分钟`;
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  return minutes === 0 ? `${hours} 小时` : `${hours} 小时 ${minutes} 分钟`;
};

// 已运行时长以快照的 as_of 为基准，不引入本地计时器或第二套轮询；
// 开始时间缺失、非法或晚于快照时刻（倒序）时返回占位符，不用 0 冒充观测时长。
export const formatRunningDuration = (startedAt: string | null, asOf: string): string => {
  if (startedAt === null) return '—';
  const startedMs = new Date(startedAt).getTime();
  const asOfMs = new Date(asOf).getTime();
  if (!Number.isFinite(startedMs) || !Number.isFinite(asOfMs) || asOfMs < startedMs) return '—';
  return formatSeconds(Math.floor((asOfMs - startedMs) / 1000));
};

// 实际耗时只由两端真实时间戳计算；任一端缺失（如仍在运行）、非法或倒序（结束早于开始）时返回占位符，不编造。
export const formatDuration = (startedAt: string | null, finishedAt: string | null): string => {
  if (startedAt === null || finishedAt === null) return '—';
  const startedMs = new Date(startedAt).getTime();
  const finishedMs = new Date(finishedAt).getTime();
  if (!Number.isFinite(startedMs) || !Number.isFinite(finishedMs) || finishedMs < startedMs) return '—';
  return formatSeconds(Math.floor((finishedMs - startedMs) / 1000));
};

export interface RunStatusMeta {
  readonly label: string;
  readonly icon: LucideIcon;
  readonly iconClass: string;
  readonly chipClass: string;
  readonly spinning: boolean;
}

// 五种运行状态穷举映射：后端新增状态时编译期强制补齐；文案保持诚实，completed 不写成「成功」，
// 也不使用成功绿与对勾图标（执行完成 ≠ 业务处理成功）。
export const RUN_STATUS_META: Record<MonitoringScheduledRunStatus, RunStatusMeta> = {
  running: {label: '执行中', icon: Loader2, iconClass: 'text-sky-300', chipClass: 'bg-sky-400/10 text-sky-300 ring-sky-400/30', spinning: true},
  completed: {label: '执行完成', icon: CircleDot, iconClass: 'text-slate-300', chipClass: 'bg-slate-400/10 text-slate-300 ring-slate-500/40', spinning: false},
  error: {label: '执行异常', icon: XCircle, iconClass: 'text-[#ff3b30]', chipClass: 'bg-[#ff3b30]/10 text-[#ff3b30] ring-[#ff3b30]/30', spinning: false},
  missed: {label: '错过执行', icon: ClockAlert, iconClass: 'text-amber-300', chipClass: 'bg-amber-400/10 text-amber-300 ring-amber-400/30', spinning: false},
  max_instances: {label: '并发上限跳过', icon: Ban, iconClass: 'text-slate-400', chipClass: 'bg-slate-400/10 text-slate-300 ring-slate-500/40', spinning: false},
};
