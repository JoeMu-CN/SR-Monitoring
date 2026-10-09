import type {ReactNode} from 'react';
import type {StepRow} from './useRiskAssistantStepTimeline';

/** 三个跳动点的逐点错峰延迟（毫秒）；只做视觉错峰，不改变任何完成状态。 */
const DOT_DELAYS_MS = [0, 150, 300] as const;

/** 运行态的三个跳动点：真实句点字符 + 逐点错峰动画，prefers-reduced-motion 下停摆。 */
const StepDots = (): ReactNode => (
  <span className="ml-1 inline-flex items-baseline" data-testid="execution-step-dots" aria-hidden="true">
    {DOT_DELAYS_MS.map((delay) => (
      <span
        key={delay}
        data-testid="execution-step-dot"
        className="step-timeline-dot motion-reduce:animate-none"
        style={{animationDelay: `${delay}ms`}}
      >
        .
      </span>
    ))}
  </span>
);

const StepRowLine = ({row}: {readonly row: StepRow}): ReactNode => (
  <div
    data-testid="execution-step-row"
    data-done={row.done ? 'true' : 'false'}
    className={`flex items-baseline gap-0.5 text-[12px] ${
      row.done ? 'text-emerald-600 dark:text-emerald-400' : 'text-[#185fa5] dark:text-blue-300'
    }`}
  >
    <span className={row.done ? '' : 'italic'}>{row.label}</span>
    {row.done ? (
      // 文本对钩表示「这一步已结束」，不代表业务查询成功；tool_done 只表示执行结束。
      <span className="shrink-0" data-testid="execution-step-check">
        ✓
      </span>
    ) : (
      <StepDots />
    )}
  </div>
);

/**
 * 风险查询助手的运行步骤时间线：每次真实步骤只占一行，没有行首图标。
 * 正在执行的行文字后跟三个跳动点；已结束的绿色文字末尾是文本对钩 ✓。
 */
export const RiskAssistantStepTimeline = ({rows}: {readonly rows: readonly StepRow[]}): ReactNode => (
  <div className="space-y-1.5 p-2" data-testid="execution-steps" aria-label="运行步骤">
    {rows.length === 0 ? (
      <div className="flex items-baseline text-[12px] italic text-[#185fa5] dark:text-blue-300">
        <span>正在处理</span>
        <StepDots />
      </div>
    ) : (
      rows.map((row) => <StepRowLine key={row.key} row={row} />)
    )}
  </div>
);