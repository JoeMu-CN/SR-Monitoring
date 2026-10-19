import React from 'react';
import {cleanup, fireEvent, render, screen, within} from '@testing-library/react';
import {afterEach, describe, expect, it, vi} from 'vitest';
import {RuleEngineMatchColumns} from './RuleEngineMatchColumns';

/**
 * 匹配柱中文标签（todo 19 标签补齐）。
 *
 * 经济/金融维度的 `currency` 匹配柱必须显示「结算币种」，与后端
 * `MatchColumn` 字面量（backend/app/risks/workbench_schemas.py:17）一一对应；
 * 未收录的枚举继续回退原始键名，绝不臆造中文。
 */

afterEach(cleanup);

const renderColumns = (options: string[], value: string[], disabled = false) => {
  const onChange = vi.fn();
  render(<RuleEngineMatchColumns options={options} value={value} onChange={onChange} disabled={disabled} />);
  return onChange;
};

describe('RuleEngineMatchColumns 匹配柱中文标签', () => {
  it('currency 匹配柱显示「结算币种」', () => {
    renderColumns(['entity', 'currency'], []);

    const currency = screen.getByText('结算币种');
    expect(currency).toBeInTheDocument();
    // 未知键仍回退原文，不会被新标签误伤
    expect(screen.getByText('主体')).toBeInTheDocument();
  });

  it('未知匹配柱键回退显示原始枚举值（不臆造中文）', () => {
    renderColumns(['currency', 'not_a_real_column'], []);

    expect(screen.getByText('not_a_real_column')).toBeInTheDocument();
    expect(screen.queryByText('未知匹配柱')).not.toBeInTheDocument();
  });

  it('currency 可勾选并回传，勾选态与既有柱一致', () => {
    const onChange = renderColumns(['entity', 'currency', 'industry'], ['entity']);

    fireEvent.click(screen.getByText('结算币种'));

    expect(onChange).toHaveBeenCalledWith(['entity', 'currency']);
  });

  it('currency 勾选态由 value 受控渲染', () => {
    renderColumns(['entity', 'currency'], ['entity', 'currency']);

    const section = within(screen.getByText('结算币种').closest('label') as HTMLElement);
    expect(section.getByRole('checkbox')).toBeChecked();
  });

  it('disabled 时 currency 勾选框不可用', () => {
    renderColumns(['currency'], [], true);

    const checkbox = within(screen.getByText('结算币种').closest('label') as HTMLElement).getByRole('checkbox');
    expect(checkbox).toBeDisabled();
  });
});
