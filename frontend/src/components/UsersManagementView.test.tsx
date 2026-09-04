import {afterEach, describe, expect, it, vi} from 'vitest';
import {act, cleanup, render, screen, waitFor, within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {api, ApiError, type AuthUser} from '../api';
import {UsersManagementView} from './UsersManagementView';

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>();
  return {...actual, api: {...actual.api, auth: {...actual.api.auth, listUsers: vi.fn(), createUser: vi.fn(), updateUser: vi.fn(), resetPassword: vi.fn()}}};
});

const admin = {id: 1, username: 'platform-admin', display_name: '平台管理员', email: 'admin@example.com', role: 'platform_admin', status: 'active', last_login_at: null, created_at: '2026-01-01T00:00:00Z'} satisfies AuthUser;
const analyst = {id: 2, username: 'analyst01', display_name: '分析员', email: 'analyst@example.com', role: 'risk_analyst', status: 'active', last_login_at: '2026-02-01T00:00:00Z', created_at: '2026-01-02T00:00:00Z'} satisfies AuthUser;

interface Deferred<Value> {
  readonly promise: Promise<Value>;
  readonly resolve: (value: Value) => void;
}

const createDeferred = <Value,>(): Deferred<Value> => {
  let resolvePromise: ((value: Value) => void) | null = null;
  const promise = new Promise<Value>((resolve) => { resolvePromise = resolve; });

  return {
    promise,
    resolve: (value) => { if (resolvePromise) resolvePromise(value); },
  };
};

const renderView = (onRequestError = vi.fn(), onCurrentUserUpdated?: (user: AuthUser) => void) =>
  render(<UsersManagementView currentUser={admin} onRequestError={onRequestError} onCurrentUserUpdated={onCurrentUserUpdated} />);

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('UsersManagementView', () => {
  it('加载后展示空态', async () => {
    vi.mocked(api.auth.listUsers).mockResolvedValue([]);

    renderView();

    expect(screen.getByRole('status')).toHaveTextContent('正在加载用户列表');
    expect(await screen.findByText('暂无用户')).toBeInTheDocument();
  });

  it('列表失败后可重试', async () => {
    const user = userEvent.setup();
    vi.mocked(api.auth.listUsers).mockRejectedValueOnce(new ApiError(500, '服务暂不可用')).mockResolvedValueOnce([analyst]);

    renderView();

    expect(await screen.findByRole('alert')).toHaveTextContent('用户列表加载失败');
    await user.click(screen.getByRole('button', {name: '重试'}));
    expect((await screen.findAllByText('analyst01')).length).toBeGreaterThan(0);
    expect(api.auth.listUsers).toHaveBeenCalledTimes(2);
  });

  it.each([401, 403])('列表返回 %i 时通知 App 错误处理器', async (status) => {
    const onRequestError = vi.fn();
    vi.mocked(api.auth.listUsers).mockRejectedValue(new ApiError(status, '访问被拒绝'));

    renderView(onRequestError);

    await waitFor(() => expect(onRequestError).toHaveBeenCalledWith(expect.objectContaining({status})));
  });

  it('保护当前管理员的重置密码入口', async () => {
    vi.mocked(api.auth.listUsers).mockResolvedValue([admin, analyst]);

    renderView();

    expect((await screen.findAllByText('platform-admin')).length).toBeGreaterThan(0);
    expect(screen.getAllByText('请使用个人设置修改本人密码')).toHaveLength(2);
    expect(screen.getAllByRole('button', {name: '重置密码'})).toHaveLength(2);
  });

  it('写入成功后不会由较早的用户列表响应覆盖', async () => {
    const user = userEvent.setup();
    const staleList = createDeferred<readonly AuthUser[]>();
    const savedUser = {...analyst, id: 3, username: 'saved-user', display_name: '新用户'};
    vi.mocked(api.auth.listUsers).mockReturnValue(staleList.promise);
    vi.mocked(api.auth.createUser).mockResolvedValue(savedUser);

    renderView();
    await user.click(screen.getByRole('button', {name: '创建用户'}));
    const dialog = screen.getByRole('dialog', {name: '创建用户'});
    await user.type(within(dialog).getByLabelText('用户名'), 'saved-user');
    await user.type(within(dialog).getByLabelText('初始密码'), 'N3wP@ss!');
    await user.click(within(dialog).getByRole('button', {name: '创建用户'}));
    expect(await screen.findByText('已保存 saved-user 的资料。')).toBeInTheDocument();

    await act(async () => {
      staleList.resolve([analyst]);
      await staleList.promise;
    });

    expect(screen.getAllByText('saved-user').length).toBeGreaterThan(0);
    expect(screen.queryByText('analyst01')).not.toBeInTheDocument();
  });

  it('保存本人资料后触发 onCurrentUserUpdated 回调', async () => {
    const user = userEvent.setup();
    const onCurrentUserUpdated = vi.fn();
    const updatedAdmin = {...admin, display_name: '新管理员'};
    vi.mocked(api.auth.listUsers).mockResolvedValue([admin, analyst]);
    vi.mocked(api.auth.updateUser).mockResolvedValue(updatedAdmin);

    renderView(vi.fn(), onCurrentUserUpdated);
    expect((await screen.findAllByText('platform-admin')).length).toBeGreaterThan(0);

    const editButtons = screen.getAllByRole('button', {name: '编辑'});
    await user.click(editButtons[0]);
    const dialog = screen.getByRole('dialog', {name: '编辑用户'});
    await user.clear(within(dialog).getByLabelText('显示名称'));
    await user.type(within(dialog).getByLabelText('显示名称'), '新管理员');
    await user.click(within(dialog).getByRole('button', {name: '保存更改'}));

    await waitFor(() => expect(onCurrentUserUpdated).toHaveBeenCalledWith(updatedAdmin));
  });
});
