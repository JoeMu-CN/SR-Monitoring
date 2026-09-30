/**
 * 采集记录列表摘要的语义换行渲染。
 *
 * 后端摘要由「短语 + 中文业务分隔符（`：` `；` `、` 与换行）」串联而成；整段直接
 * 交给浏览器会按汉字任意断行，把「司法解析」「失信/被执行」这类短语拆到两行。
 * 本组件按分隔符切分为「语义片段 + 尾随分隔符」：
 * - 短片段（≤12 个 Unicode 码点）用 `whitespace-nowrap` 整体保护，内部不换行；
 * - 长片段保持普通文本，由父级 `break-words` 承接换行，避免 nowrap 造成横向溢出；
 * - 换行分隔符单独输出普通文本，`pre-wrap` 的换行语义保持不变；
 * - 渲染结果是纯文本与 span 序列，`textContent` 与原始摘要逐字符一致。
 */

export const SUMMARY_PROTECTED_PHRASE_MAX_CHARS = 12;

const SUMMARY_SEPARATOR_PATTERN = /[：；、]/;

export interface SummaryPhrase {
  /** 原文片段（含尾随分隔符）。 */
  readonly text: string;
  /** true 表示用 nowrap 整体保护，不在短语内部换行。 */
  readonly protect: boolean;
}

const isProtectable = (content: string) => Array.from(content).length <= SUMMARY_PROTECTED_PHRASE_MAX_CHARS;

export const splitSummaryPhrases = (summary: string): readonly SummaryPhrase[] => {
  const phrases: SummaryPhrase[] = [];
  let buffer = '';
  for (const char of summary) {
    if (SUMMARY_SEPARATOR_PATTERN.test(char)) {
      buffer += char;
      phrases.push({text: buffer, protect: isProtectable(buffer.slice(0, -1))});
      buffer = '';
    } else if (char === '\n') {
      if (buffer !== '') {
        phrases.push({text: buffer, protect: isProtectable(buffer)});
        buffer = '';
      }
      phrases.push({text: char, protect: false});
    } else {
      buffer += char;
    }
  }
  if (buffer !== '') phrases.push({text: buffer, protect: isProtectable(buffer)});
  return phrases;
};

export const SignalSummaryText = ({text}: {readonly text: string}) => (
  <>
    {splitSummaryPhrases(text).map((phrase, index) => (
      phrase.protect
        ? <span key={index} data-testid="summary-protected-phrase" className="whitespace-nowrap">{phrase.text}</span>
        : phrase.text
    ))}
  </>
);
