import {afterEach, describe, expect, it, vi} from 'vitest';
import {cleanup, render, screen} from '@testing-library/react';
import {CellTextPopover} from './CellTextPopover';

afterEach(() => {
  cleanup();
  document.body.innerHTML = '';
});

describe('CellTextPopover — 面板结构', () => {
  it('外层裁剪圆角、内层独立滚动，滚动条不会把右侧圆角压成直角', () => {
    const anchor = document.createElement('button');
    document.body.appendChild(anchor);

    render(
      <CellTextPopover anchor={anchor} title="信号全文" onClose={vi.fn()}>
        <p>单元格完整内容</p>
      </CellTextPopover>,
    );

    const panel = screen.getByRole('dialog', {name: '信号全文'});
    // 外层：圆角 + overflow-hidden
    expect(panel).toHaveClass('rounded-xl');
    expect(panel).toHaveClass('overflow-hidden');
    expect(panel).not.toHaveClass('overflow-y-auto');

    // 内层：承载全部内容并负责滚动
    const scroller = panel.querySelector<HTMLElement>('.overflow-y-auto');
    expect(scroller).not.toBeNull();
    expect(scroller).toContainElement(screen.getByText('单元格完整内容'));
    expect(scroller).toContainElement(screen.getByRole('button', {name: '关闭完整内容'}));
  });
});
