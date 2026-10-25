import {cleanup, render, screen} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {afterEach, describe, expect, it, vi} from 'vitest';
import type {AuthUser} from '../api';
import {UserMenu} from './UserMenu';

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

afterEach(cleanup);

describe('UserMenu 账户入口', () => {
  it('点击头像后展示资料，并从菜单退出登录', async () => {
    const onLogout = vi.fn();
    const actor = userEvent.setup();
    render(<UserMenu user={user} onLogout={onLogout} compact />);

    await actor.click(screen.getByRole('button', {name: '打开账户菜单'}));
    expect(screen.getByText('平台管理员')).toBeInTheDocument();

    await actor.click(screen.getByRole('button', {name: /退出登录/}));
    expect(onLogout).toHaveBeenCalledTimes(1);
  });
});
