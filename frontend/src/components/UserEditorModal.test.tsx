import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {cleanup, render, screen, waitFor, within} from '@testing-library/react';
import {useState} from 'react';
import userEvent from '@testing-library/user-event';
import {api, ApiError, type AuthUser, type SupplierListResponse} from '../api';
import {UserEditorModal} from './UserEditorModal';

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>();
  return {...actual, api: {...actual.api, searchSuppliers: vi.fn(), auth: {...actual.api.auth, createUser: vi.fn(), updateUser: vi.fn(), getUserSuppliers: vi.fn(), updateUserSuppliers: vi.fn()}}};
});

const analyst = {id: 2, username: 'analyst01', display_name: '分析员', email: 'analyst@example.com', role: 'risk_analyst', status: 'active', last_login_at: null, created_at: '2026-01-02T00:00:00Z'} satisfies AuthUser;

const supplierOption = {id: 5, supplier_code: 'SUP-005', legal_name: '丙公司'};
const supplierOther = {id: 6, supplier_code: 'SUP-006', legal_name: '丁公司'};

type CreateOverrides = Partial<Pick<{onClose: () => void; onSaved: (user: AuthUser, sessionRevoked: boolean) => void; onRequestError: (error: ApiError) => void}, 'onClose' | 'onSaved' | 'onRequestError'>>;
type EditOverrides = Partial<Pick<{onClose: () => void; onSaved: (user: AuthUser, sessionRevoked: boolean) => void; onRequestError: (error: ApiError) => void; isCurrentUser: boolean}, 'onClose' | 'onSaved' | 'onRequestError' | 'isCurrentUser'>>;

const renderCreate = (overrides?: CreateOverrides) =>
  render(<UserEditorModal isOpen mode="create" onClose={vi.fn()} onSaved={vi.fn()} onRequestError={vi.fn()} {...overrides} />);

const renderEdit = (user: AuthUser, overrides?: EditOverrides) =>
  render(<UserEditorModal isOpen mode="edit" user={user} isCurrentUser={false} onClose={vi.fn()} onSaved={vi.fn()} onRequestError={vi.fn()} {...overrides} />);

const CreateFocusHarness = () => {
  const [isOpen, setIsOpen] = useState(false);
  return <>
    <button type="button" onClick={() => setIsOpen(true)}>打开用户编辑</button>
    <button type="button">背景操作</button>
    <UserEditorModal isOpen={isOpen} mode="create" onClose={() => setIsOpen(false)} onSaved={vi.fn()} onRequestError={vi.fn()} />
  </>;
};

beforeEach(() => {
  vi.mocked(api.auth.getUserSuppliers).mockResolvedValue([]);
  vi.mocked(api.auth.updateUserSuppliers).mockResolvedValue([]);
  vi.mocked(api.searchSuppliers).mockResolvedValue({items: [], total: 0, limit: 100, offset: 0} as SupplierListResponse);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('UserEditorModal', () => {
  it('创建用户时提交原始枚举值', async () => {
    const user = userEvent.setup();
    const onSaved = vi.fn();
    const created = {...analyst, id: 3, username: 'new-analyst'};
    vi.mocked(api.auth.createUser).mockResolvedValue(created);

    renderCreate({onSaved});
    await user.type(screen.getByLabelText('用户名'), 'new-analyst');
    await user.type(screen.getByLabelText('初始密码'), 'N3wP@ss!');
    await user.type(screen.getByLabelText('显示名称'), '新分析员');
    await user.type(screen.getByLabelText('邮箱'), 'new@example.com');
    await user.selectOptions(screen.getByLabelText('角色'), 'risk_analyst');
    await user.click(screen.getByRole('button', {name: '创建用户'}));

    await waitFor(() => expect(api.auth.createUser).toHaveBeenCalledWith({username: 'new-analyst', password: 'N3wP@ss!', display_name: '新分析员', email: 'new@example.com', role: 'risk_analyst'}));
    expect(onSaved).toHaveBeenCalledWith(created, false);
  });

  it.each([new ApiError(409, '用户名已存在'), new ApiError(422, '密码强度不足')])('创建失败时保留表单并展示后端消息', async (error) => {
    const user = userEvent.setup();
    vi.mocked(api.auth.createUser).mockRejectedValue(error);

    renderCreate();
    await user.type(screen.getByLabelText('用户名'), 'duplicate');
    await user.type(screen.getByLabelText('初始密码'), 'weak');
    await user.click(screen.getByRole('button', {name: '创建用户'}));

    expect(await screen.findByRole('alert')).toHaveTextContent(error.message);
    expect(screen.getByLabelText('用户名')).toHaveValue('duplicate');
  });

  it('编辑用户时聚焦首个可编辑字段', async () => {
    renderEdit(analyst);

    await waitFor(() => expect(screen.getByLabelText('显示名称')).toHaveFocus());
    expect(screen.getByText('角色或状态变更将撤销该用户会话。')).toBeInTheDocument();
  });

  it('将 Tab 和 Shift+Tab 圈定在弹窗内，并在关闭后恢复触发按钮焦点', async () => {
    const user = userEvent.setup();

    render(<CreateFocusHarness />);
    const trigger = screen.getByRole('button', {name: '打开用户编辑'});
    await user.click(trigger);

    const dialog = screen.getByRole('dialog', {name: '创建用户'});
    const closeButton = within(dialog).getByRole('button', {name: '关闭用户编辑弹窗'});
    const submitButton = within(dialog).getByRole('button', {name: '创建用户'});
    expect(within(dialog).getByLabelText('用户名')).toHaveFocus();

    await user.tab({shift: true});
    expect(closeButton).toHaveFocus();
    await user.tab({shift: true});
    expect(submitButton).toHaveFocus();
    await user.tab();
    expect(closeButton).toHaveFocus();

    await user.click(within(dialog).getByRole('button', {name: '取消'}));
    expect(trigger).toHaveFocus();
  });

  it('仅编辑资料时不提示会话撤销', async () => {
    const user = userEvent.setup();
    const onSaved = vi.fn();
    const updated = {...analyst, display_name: '高级分析员'};
    vi.mocked(api.auth.updateUser).mockResolvedValue(updated);

    renderEdit(analyst, {onSaved});
    await user.clear(screen.getByLabelText('显示名称'));
    await user.type(screen.getByLabelText('显示名称'), '高级分析员');
    await user.click(screen.getByRole('button', {name: '保存更改'}));

    expect(api.auth.updateUser).toHaveBeenCalledWith(2, {display_name: '高级分析员'});
    expect(onSaved).toHaveBeenCalledWith(updated, false);
  });

  it('编辑角色或状态后标记目标会话撤销', async () => {
    const user = userEvent.setup();
    const onSaved = vi.fn();
    const updated = {...analyst, role: 'risk_admin' as const, status: 'disabled' as const};
    vi.mocked(api.auth.updateUser).mockResolvedValue(updated);

    renderEdit(analyst, {onSaved});
    await user.selectOptions(screen.getByLabelText('角色'), 'risk_admin');
    await user.selectOptions(screen.getByLabelText('状态'), 'disabled');
    await user.click(screen.getByRole('button', {name: '保存更改'}));

    expect(onSaved).toHaveBeenCalledWith(updated, true);
  });

  it('编辑本人时禁用角色和状态选择并显示说明', async () => {
    const admin = {id: 1, username: 'platform-admin', display_name: '平台管理员', email: 'admin@example.com', role: 'platform_admin', status: 'active', last_login_at: null, created_at: '2026-01-01T00:00:00Z'} satisfies AuthUser;

    renderEdit(admin, {isCurrentUser: true});

    expect(screen.getByLabelText('角色')).toBeDisabled();
    expect(screen.getByLabelText('状态')).toBeDisabled();
    expect(screen.getByText('不能修改本人角色或状态，请由其他平台管理员操作。')).toBeInTheDocument();
  });

  it('编辑本人时角色或状态变更不包含在请求 payload 中', async () => {
    const user = userEvent.setup();
    const onSaved = vi.fn();
    const admin = {id: 1, username: 'platform-admin', display_name: '管理员', email: 'admin@example.com', role: 'platform_admin', status: 'active', last_login_at: null, created_at: '2026-01-01T00:00:00Z'} satisfies AuthUser;
    const updated = {...admin, display_name: '新名字'};
    vi.mocked(api.auth.updateUser).mockResolvedValue(updated);

    renderEdit(admin, {isCurrentUser: true, onSaved});
    await user.clear(screen.getByLabelText('显示名称'));
    await user.type(screen.getByLabelText('显示名称'), '新名字');
    await user.click(screen.getByRole('button', {name: '保存更改'}));

    await waitFor(() => expect(api.auth.updateUser).toHaveBeenCalledWith(1, {display_name: '新名字'}));
    expect(onSaved).toHaveBeenCalledWith(updated, false);
  });

  it('Escape 键在非保存状态下关闭弹窗', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();

    renderCreate({onClose});
    await user.keyboard('{Escape}');
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('仅风险分析员显示负责供应商控件', async () => {
    const user = userEvent.setup();

    renderCreate();
    expect(screen.queryByLabelText('负责供应商')).toBeNull();

    await user.selectOptions(screen.getByLabelText('角色'), 'risk_analyst');
    expect(screen.getByLabelText('负责供应商')).toBeInTheDocument();
  });

  it('切换角色离开风险分析员后已选供应商保留但不显示', async () => {
    const user = userEvent.setup();
    vi.mocked(api.searchSuppliers).mockResolvedValue({items: [supplierOption], total: 1, limit: 100, offset: 0} as SupplierListResponse);

    renderCreate();
    await user.selectOptions(screen.getByLabelText('角色'), 'risk_analyst');
    await user.type(screen.getByPlaceholderText('输入供应商编码或名称搜索'), '丙');
    await user.click(await screen.findByRole('option', {name: /丙公司/}));
    expect(screen.getByText('SUP-005')).toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText('角色'), 'viewer');
    expect(screen.queryByText('SUP-005')).toBeNull();

    await user.selectOptions(screen.getByLabelText('角色'), 'risk_analyst');
    expect(screen.getByText('SUP-005')).toBeInTheDocument();
  });

  it('编辑态加载已分配的负责供应商', async () => {
    vi.mocked(api.auth.getUserSuppliers).mockResolvedValue([supplierOption]);

    renderEdit(analyst);

    expect(await screen.findByText('SUP-005')).toBeInTheDocument();
  });

  it('负责供应商加载失败时展示行内错误且不阻塞其他字段', async () => {
    vi.mocked(api.auth.getUserSuppliers).mockRejectedValue(new ApiError(500, '供应商加载失败'));

    renderEdit(analyst);

    expect(await screen.findByRole('alert')).toHaveTextContent('供应商加载失败');
    expect(screen.getByLabelText('显示名称')).toBeEnabled();
  });

  it('保存时先更新用户再写入负责供应商', async () => {
    const user = userEvent.setup();
    const onSaved = vi.fn();
    const updated = {...analyst, display_name: '高级分析员'};
    vi.mocked(api.auth.updateUser).mockResolvedValue(updated);
    vi.mocked(api.auth.getUserSuppliers).mockResolvedValue([supplierOption]);
    vi.mocked(api.searchSuppliers).mockResolvedValue({items: [supplierOther], total: 1, limit: 100, offset: 0} as SupplierListResponse);

    renderEdit(analyst, {onSaved});
    await screen.findByText('SUP-005');
    await user.clear(screen.getByLabelText('显示名称'));
    await user.type(screen.getByLabelText('显示名称'), '高级分析员');
    await user.type(screen.getByPlaceholderText('输入供应商编码或名称搜索'), '丁');
    await user.click(await screen.findByRole('option', {name: /丁公司/}));
    await user.click(screen.getByRole('button', {name: '保存更改'}));

    await waitFor(() => expect(api.auth.updateUserSuppliers).toHaveBeenCalledWith(2, [5, 6]));
    expect(api.auth.updateUser).toHaveBeenCalledWith(2, {display_name: '高级分析员'});
    expect(vi.mocked(api.auth.updateUser).mock.invocationCallOrder[0]).toBeLessThan(
      vi.mocked(api.auth.updateUserSuppliers).mock.invocationCallOrder[0]!,
    );
    expect(onSaved).toHaveBeenCalledWith(updated, false);
  });

  it('写入负责供应商失败时停留弹窗并展示错误', async () => {
    const user = userEvent.setup();
    const onSaved = vi.fn();
    vi.mocked(api.auth.getUserSuppliers).mockResolvedValue([supplierOption]);
    vi.mocked(api.auth.updateUserSuppliers).mockRejectedValue(new ApiError(422, '仅风险分析员可分配负责供应商'));

    renderEdit(analyst, {onSaved});
    await screen.findByText('SUP-005');
    await user.click(screen.getByRole('button', {name: '移除 丙公司'}));
    await user.click(screen.getByRole('button', {name: '保存更改'}));

    expect(await screen.findByRole('alert')).toHaveTextContent('仅风险分析员可分配负责供应商');
    expect(onSaved).not.toHaveBeenCalled();
    expect(screen.getByRole('dialog', {name: '编辑用户'})).toBeInTheDocument();
  });
});
