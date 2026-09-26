import {cleanup, render, screen} from '@testing-library/react';
import {MemoryRouter} from 'react-router-dom';
import {afterEach, describe, expect, it} from 'vitest';
import {routePermissions} from '../routes';
import {MobileNav} from './MobileNav';

const allPermissions = Object.values(routePermissions);

const renderNav = (p1RiskCount = 0) => render(
  <MemoryRouter>
    <MobileNav permissions={allPermissions} p1RiskCount={p1RiskCount} />
  </MemoryRouter>,
);

afterEach(cleanup);

describe('MobileNav 移动端底部导航', () => {
  it('「供应商」等中文标签为不可拆分的 nowrap 组，375px 下不得拆字换行', () => {
    renderNav();

    expect(screen.getByText('供应商')).toHaveClass('whitespace-nowrap');
  });

  it('所有可见导航标签统一使用 nowrap 语义组，避免个别项在窄屏拆字换行', () => {
    renderNav();

    for (const label of ['总览', '风险', '助手', '供应商', '数据', '规则', '用户']) {
      expect(screen.getByText(label)).toHaveClass('whitespace-nowrap');
    }
  });

  it('P1 风险角标在有风险时才渲染，导航标签保持不可拆分', () => {
    renderNav(3);

    expect(screen.getByText('3')).toBeInTheDocument();
    expect(screen.getByText('供应商')).toHaveClass('whitespace-nowrap');
  });
});
