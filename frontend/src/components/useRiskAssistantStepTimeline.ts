import {useCallback, useEffect, useMemo, useRef, useState} from 'react';
import {api, type AgentStepRead} from '../api';

/** 真实步骤事件轮询周期（毫秒）；与后端 run_token 快照语义一致，不新增第二套轮询。 */
export const STEP_POLL_INTERVAL_MS = 700;

/**
 * 同一快照内多事件的逐行展开间隔（毫秒）。
 * 这只是一次「快照落地后把行依次铺开」的短暂视觉展开，不是对历史执行仍在运行的声明：
 * 行的完成状态完全由真实 tool_done 证据决定。
 */
export const REVEAL_INTERVAL_MS = 180;

/** 步骤展示行：一条真实事件合并后的单行展示单元。 */
export interface StepRow {
  /** 稳定标识：由真实事件 index 派生，保证重复快照不会产生重复 React key。 */
  readonly key: string;
  readonly label: string;
  /** true=该步骤已结束（末尾显示 ✓）；false=它是当前正在执行的行。 */
  readonly done: boolean;
}

// 运行期步骤时间线的工具文案；未知工具回退通用只读表述。
const STEP_TOOL_LABEL: Record<string, string> = {
  query_suppliers: '检索重点供应商、地点与产品',
  query_current_alerts: '检索当前有效 P1–P4 风险提醒',
  verify_company: '执行清单外企业工商核查',
  get_budget: '查询天眼查调用额度',
};

const toolLabel = (tool: string | null): string =>
  (tool !== null ? STEP_TOOL_LABEL[tool] ?? null : null) ?? '执行只读查询工具';

interface DraftRow {
  readonly key: string;
  readonly label: string;
  done: boolean;
}

/**
 * 把后端真实步骤事件按 index 合并成展示行：
 * - tool_start 与其后同名的 tool_done 原位合并成同一行（同一工具多次调用各自成行）；
 * - 缺少 tool_start 的 tool_done 仍单独成行并标完成，绝不丢事件；
 * - 只有最后一行是「当前正在执行」，其余历史阶段按已有证据标完成，不会永久转动。
 */
export const mergeStepRows = (steps: readonly AgentStepRead[]): StepRow[] => {
  const drafts: DraftRow[] = [];
  // 尚未收到 tool_done 的 tool_start 行下标；同名时取最近一次，绝不按工具名全局去重。
  const openToolRows: number[] = [];

  for (const step of steps) {
    switch (step.kind) {
      case 'analyzing':
        drafts.push({key: `analyzing-${step.index}`, label: '正在理解问题', done: false});
        break;
      case 'finalizing':
        drafts.push({key: `finalizing-${step.index}`, label: '正在生成回答', done: false});
        break;
      case 'tool_start': {
        openToolRows.push(drafts.length);
        drafts.push({key: `tool-${step.index}`, label: toolLabel(step.tool), done: false});
        break;
      }
      case 'tool_done': {
        const label = toolLabel(step.tool);
        let matchedAt = -1;
        for (let cursor = openToolRows.length - 1; cursor >= 0; cursor -= 1) {
          if (drafts[openToolRows[cursor]].label === label) {
            matchedAt = cursor;
            break;
          }
        }
        if (matchedAt === -1) {
          drafts.push({key: `tool-${step.index}`, label, done: true});
          break;
        }
        const rowAt = openToolRows[matchedAt];
        openToolRows.splice(matchedAt, 1);
        const row = drafts[rowAt];
        drafts[rowAt] = {...row, done: true};
        break;
      }
      default:
        break;
    }
  }

  const lastAt = drafts.length - 1;
  return drafts.map((row, at) => ({...row, done: at === lastAt ? row.done : true}));
};

export interface StepTimelineController {
  /** 已合并并按节奏展开的可展示行。 */
  readonly rows: readonly StepRow[];
  /** 用新的运行令牌开始轮询真实执行步骤（会先作废上一轮的全部在途响应）。 */
  readonly start: (runToken: string) => void;
  /** 停止轮询并清空展示行；回答返回、重置对话与组件卸载都必须调用。 */
  readonly stop: () => void;
}

/**
 * 风险查询助手的运行期步骤时间线控制器。
 *
 * 生命周期边界只做三件事：轮询真实步骤、按真实事件时序合并成行、按短间隔逐行展开。
 * 「开始/结束」「重置」「卸载」统一由 stop() 收口：令牌自增使在途旧响应立即失效，
 * 定时器随之清理，因此旧快照既不会重复追加，也不会让步骤回退。
 */
export const useRiskAssistantStepTimeline = (): StepTimelineController => {
  const [draftRows, setDraftRows] = useState<readonly StepRow[]>([]);
  const [revealed, setRevealed] = useState(0);
  // 运行令牌：stop 时自增，在途响应的 then/catch 必须比对令牌后才能写状态。
  const generationRef = useRef(0);
  const pollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const stop = useCallback(() => {
    generationRef.current += 1;
    if (pollTimerRef.current !== null) {
      clearTimeout(pollTimerRef.current);
      pollTimerRef.current = null;
    }
    setDraftRows([]);
  }, []);

  const start = useCallback((runToken: string) => {
    stop();
    const generation = generationRef.current;
    let appliedCount = 0;

    const poll = (): void => {
      if (generation !== generationRef.current) return;
      // 单飞轮询：本次响应落地后才安排下一次，从结构上杜绝请求重叠。
      api.chatSteps(runToken).then(
        (data) => {
          if (generation !== generationRef.current) return;
          // 只接受比已展示更长的快照：重复快照与乱序旧响应既不重复追加，也不让行数回退。
          if (data.steps.length > appliedCount) {
            appliedCount = data.steps.length;
            setDraftRows(mergeStepRows(data.steps));
          }
          if (generation === generationRef.current) {
            pollTimerRef.current = setTimeout(poll, STEP_POLL_INTERVAL_MS);
          }
        },
        () => {
          // 轮询失败静默忽略：不打断主对话，下一轮继续尝试。
          if (generation === generationRef.current) {
            pollTimerRef.current = setTimeout(poll, STEP_POLL_INTERVAL_MS);
          }
        },
      );
    };

    pollTimerRef.current = setTimeout(poll, STEP_POLL_INTERVAL_MS);
  }, [stop]);

  // 卸载（以及 start 的换轮）时收口：令牌自增 + 清表，在途响应无法回写。
  useEffect(() => stop, [stop]);

  // 首行立即可见，其余按 REVEAL_INTERVAL_MS 依次追加；行数不足时不会出现运行态空转。
  // 依赖只取 draftRows：新快照落地时补出首行（幂等），后续推进交给下面的节拍 effect。
  useEffect(() => {
    setRevealed((count) => Math.min(count === 0 ? 1 : count, draftRows.length));
  }, [draftRows]);

  useEffect(() => {
    if (revealed >= draftRows.length) return undefined;
    const timer = setTimeout(() => setRevealed((count) => count + 1), REVEAL_INTERVAL_MS);
    return () => clearTimeout(timer);
  }, [draftRows.length, revealed]);

  const rows = useMemo(() => draftRows.slice(0, revealed), [draftRows, revealed]);

  return {rows, start, stop};
};