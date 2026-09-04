import {afterEach, describe, expect, it, vi} from 'vitest';
import {cleanup, render, screen} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {api, ApiError, type AuthUser} from '../api';
import {UserPasswordResetModal} from './UserPasswordResetModal';

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>();
  return {...actual, api: {...actual.api, auth: {...actual.api.auth, resetPassword: vi.fn()}}};
});

const analyst = {id: 2, username: 'analyst01', display_name: '分析员', email: 'analyst@example.com', role: 'risk_analyst', status: 'active', last_login_at: null, created_at: '2026-01-02T00:00:00Z'} satisfies AuthUser;

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('UserPasswordResetModal', () => {
  it('只提交新密码并在成功后回调', async () => {
    const user = userEvent.setup();
    const onReset = vi.fn();
    vi.mocked(api.auth.resetPassword).mockResolvedValue({detail: '密码已重置'});

    render(<UserPasswordResetModal isOpen user={analyst} onClose={vi.fn()} onReset={onReset} onRequestError={vi.fn()} />);
    await user.type(screen.getByLabelText('新密码'), 'N3wP@ss!');
    await user.click(screen.getByRole('button', {name: '确认重置密码'}));

    expect(api.auth.resetPassword).toHaveBeenCalledWith(2, {new_password: 'N3wP@ss!'});
    expect(onReset).toHaveBeenCalledWith(analyst);
  });

  it('403 时展示后端消息并通知 App 错误处理器', async () => {
    const user = userEvent.setup();
    const onRequestError = vi.fn();
    vi.mocked(api.auth.resetPassword).mockRejectedValue(new ApiError(403, '仅可由平台管理员重置'));

    render(<UserPasswordResetModal isOpen user={analyst} onClose={vi.fn()} onReset={vi.fn()} onRequestError={onRequestError} />);
    await user.type(screen.getByLabelText('新密码'), 'N3wP@ss!');
    await user.click(screen.getByRole('button', {name: '确认重置密码'}));

    expect(await screen.findByRole('alert')).toHaveTextContent('仅可由平台管理员重置');
    expect(onRequestError).toHaveBeenCalledWith(expect.objectContaining({status: 403}));
  });
});
