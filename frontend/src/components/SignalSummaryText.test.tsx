import {cleanup, render, screen} from '@testing-library/react';
import {afterEach, describe, expect, it} from 'vitest';
import {SignalSummaryText, splitSummaryPhrases} from './SignalSummaryText';

/** 与后端 render_key_summary 同构的真实摘要（含被 Oracle 点名的两个短语）。 */
const summary = '重点命中：风险总览：E2E Supplier 001 get_risk_overview 确定性记录；司法解析：E2E Supplier 001 get_judicial_case 确定性记录；失信/被执行：E2E Supplier 001 get_default_event_info 确定性记录；开庭公告：E2E Supplier 001 get_hearing_notice 确定性记录；法院公告：E2E Supplier 001 get_court_notice 确定性…';

afterEach(cleanup);

describe('splitSummaryPhrases 摘要短语切分', () => {
  it('逐字符保留原文：真实摘要、连续分隔符、换行、空串与无分隔长文本', () => {
    const inputs = [
      summary,
      'a：；b',
      '；开头',
      '只有；；分隔符',
      '第一行\n第二行：',
      '',
      '无分隔长文本91310000STUB000001AB',
    ];
    for (const input of inputs) {
      expect(splitSummaryPhrases(input).map((phrase) => phrase.text).join(''), `输入：${input}`).toBe(input);
    }
  });

  it('短标签及尾随分隔符受保护：司法解析/失信被执行/重点命中', () => {
    const protectedTexts = splitSummaryPhrases(summary).filter((phrase) => phrase.protect).map((phrase) => phrase.text);
    expect(protectedTexts).toContain('重点命中：');
    expect(protectedTexts).toContain('风险总览：');
    expect(protectedTexts).toContain('司法解析：');
    expect(protectedTexts).toContain('失信/被执行：');
    expect(protectedTexts).toContain('开庭公告：');
  });

  it('长正文（含长标识符）不保护，保持可断行', () => {
    const phrases = splitSummaryPhrases(summary);
    const longBody = phrases.find((phrase) => phrase.text.includes('get_judicial_case'));
    expect(longBody).toBeDefined();
    expect(longBody?.protect).toBe(false);
    const defaultEventBody = phrases.find((phrase) => phrase.text.includes('get_default_event_info'));
    expect(defaultEventBody?.protect).toBe(false);
  });

  it('保护阈值按 Unicode 码点：12 个受保护、13 个不保护（含代理对）', () => {
    expect(splitSummaryPhrases(`${'一'.repeat(12)}：`)).toEqual([{text: `${'一'.repeat(12)}：`, protect: true}]);
    expect(splitSummaryPhrases(`${'一'.repeat(13)}：`)).toEqual([{text: `${'一'.repeat(13)}：`, protect: false}]);
    // 12 个 emoji 是 24 个 UTF-16 单元、12 个码点：按码点计仍受保护
    expect(splitSummaryPhrases(`${'😀'.repeat(12)}：`)).toEqual([{text: `${'😀'.repeat(12)}：`, protect: true}]);
  });

  it('换行分隔符保持普通文本，不进入 nowrap 容器', () => {
    const phrases = splitSummaryPhrases('第一行\n第二行：');
    expect(phrases.map((phrase) => phrase.text).join('')).toBe('第一行\n第二行：');
    expect(phrases.find((phrase) => phrase.text === '\n')?.protect).toBe(false);
    expect(phrases.find((phrase) => phrase.text === '第二行：')?.protect).toBe(true);
  });

  it('超长无分隔文本不套 nowrap（可断行）', () => {
    const token = 'E2E-9F3A2B4C5D6E7F80123456789ABCDEF0123456789';
    expect(splitSummaryPhrases(token)).toEqual([{text: token, protect: false}]);
  });
});

describe('SignalSummaryText 语义换行渲染', () => {
  it('textContent 与原始摘要逐字符一致', () => {
    const {container} = render(<SignalSummaryText text={summary} />);
    expect(container.textContent).toBe(summary);
  });

  it('司法解析/失信被执行由 whitespace-nowrap span 保护', () => {
    render(<SignalSummaryText text={summary} />);
    const protectedSpans = screen.getAllByTestId('summary-protected-phrase');
    const protectedTexts = protectedSpans.map((span) => span.textContent ?? '');
    expect(protectedTexts).toContain('司法解析：');
    expect(protectedTexts).toContain('失信/被执行：');
    for (const span of protectedSpans) {
      expect(span.className).toContain('whitespace-nowrap');
    }
  });

  it('长正文不在 nowrap span 内（保留父级 break-words 断行能力）', () => {
    render(<SignalSummaryText text={summary} />);
    for (const span of screen.getAllByTestId('summary-protected-phrase')) {
      expect(span.textContent ?? '').not.toContain('E2E Supplier');
      expect(span.textContent ?? '').not.toContain('get_judicial_case');
      expect(span.textContent ?? '').not.toContain('get_default_event_info');
    }
  });
});
