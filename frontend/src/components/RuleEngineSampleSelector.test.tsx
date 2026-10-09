import React from 'react';
import {cleanup, fireEvent, render, screen, within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {afterEach, describe, expect, it, vi} from 'vitest';
import type {DimensionTraceSampleRead, RuleEngineOptions} from '../api';
import {RuleEngineSampleSelector} from './RuleEngineSampleSelector';

/**
 * 样例事件选择器（#17）：选择器从规则矩阵表迁回维度级『证据』卡内，本文件验证其自身契约——
 * 样例列表/内置兜底/选中回传/键盘可达/12px chip 圆角；与矩阵高亮的联动由
 * RuleEngineRuleMatrix.test.tsx 的组合用例覆盖，壳层接线由 RuleEngineView.test.tsx 覆盖。
 */

const options = (): RuleEngineOptions => ({
  match_columns: ['entity', 'location', 'product', 'country', 'industry'],
  event_types: [
    {value: 'weather', label: '天气'},
    {value: 'geopolitical', label: '地缘政治'},
  ],
  event_subtypes: [],
});

const samples: DimensionTraceSampleRead[] = [
  {id: 11, supplier_id: 1, supplier_name: '沿海科技', level: 'P2', event_summary: '沿岸强台风预警', updated_at: '2026-09-13T08:00:00Z'},
  {id: 12, supplier_id: 2, supplier_name: '北岭实业', level: 'P1', event_summary: '出口管制清单更新', updated_at: '2026-09-12T08:00:00Z'},
];

type SelectorProps = React.ComponentProps<typeof RuleEngineSampleSelector>;

const renderSelector = (overrides: Partial<SelectorProps> = {}): SelectorProps => {
  const props: SelectorProps = {
    options: options(),
    samples,
    selectedSampleId: null,
    onSelectSample: vi.fn(),
    ...overrides,
  };
  render(<RuleEngineSampleSelector {...props} />);
  return props;
};

afterEach(cleanup);

describe('样例事件选择器（#17：证据卡内）', () => {
  it('真实样例存在：默认「最近一条」选中，点击样例会回传该样例 id', () => {
    const onSelectSample = vi.fn();
    renderSelector({onSelectSample});

    expect(screen.getByTestId('rule-engine-sample-selector')).toBeInTheDocument();
    expect(screen.queryByTestId('rule-matrix-sample-builtin')).not.toBeInTheDocument();
    expect(screen.getByTestId('rule-matrix-sample-latest')).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByTestId('rule-matrix-sample-11')).toHaveAttribute('aria-pressed', 'false');
    expect(screen.getByTestId('rule-matrix-sample-12')).toHaveAttribute('aria-pressed', 'false');
    expect(screen.getByText(/当前展示该维度最近一条提醒的轨迹/)).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('rule-matrix-sample-12'));
    expect(onSelectSample).toHaveBeenCalledWith(12);
  });

  it('选中某条样例：该按钮 aria-pressed=true，并展示已选摘要', () => {
    renderSelector({selectedSampleId: 12});

    expect(screen.getByTestId('rule-matrix-sample-12')).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByTestId('rule-matrix-sample-latest')).toHaveAttribute('aria-pressed', 'false');
    expect(screen.getByText('已选样例：北岭实业 · P1')).toBeInTheDocument();
  });

  it('无真实样例：使用内置样例事件兜底并显式标注非真实数据，点击回传 null', () => {
    const onSelectSample = vi.fn();
    renderSelector({samples: [], selectedSampleId: null, onSelectSample});

    const builtinChip = screen.getByTestId('rule-matrix-sample-builtin');
    expect(builtinChip).toHaveAttribute('aria-pressed', 'true');
    expect(screen.queryByTestId('rule-matrix-sample-latest')).not.toBeInTheDocument();
    expect(screen.getByText(/非真实数据/)).toBeInTheDocument();
    expect(screen.getByText(/事件类型「天气」/)).toBeInTheDocument();

    fireEvent.click(builtinChip);
    expect(onSelectSample).toHaveBeenCalledWith(null);
  });

  it('键盘可达：样例按钮为原生 button，Enter 可触发选择', async () => {
    const user = userEvent.setup();
    const onSelectSample = vi.fn();
    renderSelector({onSelectSample});

    const chip = screen.getByTestId('rule-matrix-sample-11');
    expect(chip.tagName).toBe('BUTTON');
    expect(chip).toHaveAttribute('type', 'button');
    expect(chip.className).toContain('focus-visible:ring-2');
    chip.focus();
    expect(chip).toHaveFocus();
    await user.keyboard('{Enter}');
    expect(onSelectSample).toHaveBeenCalledWith(11);
  });

  it('胶囊按钮以风险等级结尾，等级不被公司名省略号吞掉', () => {
    renderSelector();

    const chip = screen.getByTestId('rule-matrix-sample-11');
    expect(within(chip).getByText('沿海科技').className).toContain('truncate');
    expect(within(chip).getByText('· P2').className).toContain('shrink-0');
  });

  it('chip 使用 12px 圆角（#33 对齐原型 .chip）', () => {
    renderSelector();

    for (const testId of ['rule-matrix-sample-latest', 'rule-matrix-sample-11']) {
      const chip = screen.getByTestId(testId);
      expect(chip.className).toContain('rounded-xl');
      expect(chip.className).not.toContain('rounded-md');
    }
  });
});
