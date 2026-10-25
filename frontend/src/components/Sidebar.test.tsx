import {cleanup, render, screen} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {MemoryRouter} from 'react-router-dom';
import {afterEach, describe, expect, it, vi} from 'vitest';
import type {AuthUser} from '../api';
import {routePermissions} from '../routes';
import {Sidebar, type SidebarStage} from './Sidebar';

const user: AuthUser = {
  id: 1,
  username: 'mushd',
  email: null,
  display_name: '平台管理员',
  role: 'platform_admin',
  status: 'active',
  last_login_at: null,
  created_at: '2026-01-01T00:00:00Z',
};

const permissions = Object.values(routePermissions);

const renderSidebar = (stage: SidebarStage, onAdvanceStage = vi.fn()) => render(
  <MemoryRouter initialEntries={['/overview']}>
    <Sidebar
      permissions={permissions}
      onOpenSettingsModal={vi.fn()}
      p1RiskCount={2}
      user={user}
      onLogout={vi.fn()}
      stage={stage}
      onAdvanceStage={onAdvanceStage}
    />
  </MemoryRouter>,
);

afterEach(cleanup);

describe('Sidebar 桌面导航两段折叠', () => {
  it('展开态显示文字标签与整行蓝色选中底，点击后进入一级折叠', async () => {
    const actor = userEvent.setup();
    const onAdvanceStage = vi.fn();
    renderSidebar(0, onAdvanceStage);

    expect(screen.getByText('风险总览')).toBeInTheDocument();
    expect(screen.getByRole('link', {name: '风险总览'})).toHaveClass('bg-[#185fa5]');
    expect(screen.getByRole('button', {name: '设置'})).toHaveTextContent('设置');
    expect(screen.getByRole('button', {name: '帮助'})).toHaveTextContent('帮助');
    expect(screen.getByRole('button', {name: '打开账户菜单'})).toHaveTextContent('个人中心');
    await actor.click(screen.getByRole('button', {name: '收起导航到一级'}));

    expect(onAdvanceStage).toHaveBeenCalledTimes(1);
  });

  it('一级折叠隐藏文字但保留 LOGO，可继续收到二级', async () => {
    const actor = userEvent.setup();
    const onAdvanceStage = vi.fn();
    renderSidebar(1, onAdvanceStage);

    expect(screen.queryByText('风险总览')).not.toBeInTheDocument();
    expect(screen.getByAltText('供应商风险监控平台')).toBeInTheDocument();
    expect(screen.getByRole('link', {name: '风险总览'})).toHaveAttribute('title', '风险总览');
    expect(screen.getByRole('button', {name: '设置'})).toHaveAttribute('title', '设置');
    expect(screen.getByRole('button', {name: '帮助'})).toHaveAttribute('title', '帮助');
    expect(screen.getByRole('button', {name: '打开账户菜单'})).toHaveAttribute('title', '个人中心');
    await actor.click(screen.getByRole('button', {name: '继续收起到二级'}));

    expect(onAdvanceStage).toHaveBeenCalledTimes(1);
  });

  it('二级折叠完全卸载导航栏，只保留左边缘展开手柄', () => {
    renderSidebar(2);

    expect(screen.queryByRole('navigation', {name: '主导航'})).not.toBeInTheDocument();
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
    expect(screen.queryByAltText('供应商风险监控平台')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', {name: '设置'})).not.toBeInTheDocument();
    expect(screen.queryByRole('button', {name: '帮助'})).not.toBeInTheDocument();
    expect(screen.getByRole('button', {name: '展开导航'})).toHaveAttribute('aria-expanded', 'false');
  });
});
