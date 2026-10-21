import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {cleanup, render, screen, waitFor, within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type {ComponentProps} from 'react';
import {api, ApiError, type PushMatrixRow} from '../api';
import {PushMatrixModal} from './PushMatrixModal';

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>();
  return {...actual, api: {...actual.api, notifications: {getPushMatrix: vi.fn(), updatePushMatrix: vi.fn()}}};
});

const adminRow: PushMatrixRow = {
  user_id: 1, username: 'platform-admin', display_name: '平台管理员', role: 'platform_admin',
  email: 'admin@example.com', push_p1: false, push_p2: false, push_p3: false, push_monthly_report: false,
  supplier_count: 0,
};

const analystWithoutEmail: PushMatrixRow = {
  user_id: 2, username: 'analyst-no-mail', display_name: '无邮箱分析员', role: 'risk_analyst',
  email: null, push_p1: false, push_p2: false, push_p3: false, push_monthly_report: false,
  supplier_count: 0,
};

const analystRow: PushMatrixRow = {
  user_id: 3, username: 'analyst01', display_name: '张分析员', role: 'risk_analyst',
  email: 'analyst@example.com', push_p1: true, push_p2: false, push_p3: false, push_monthly_report: false,
  supplier_count: 4,
};

const viewerRow: PushMatrixRow = {
  user_id: 4, username: 'viewer01', display_name: '访客', role: 'viewer',
  email: 'viewer@example.com', push_p1: false, push_p2: false, push_p3: false, push_monthly_report: false,
  supplier_count: 0,
};

const rows: readonly PushMatrixRow[] = [adminRow, analystWithoutEmail, analystRow, viewerRow];

const rowOf = (displayName: string) => {
  const cell = screen.getByText(displayName);
  const row = cell.closest('tr');
  if (!row) throw new Error(`未找到 ${displayName} 所在行`);
  return row as HTMLElement;
};

const renderModal = (overrides: Partial<ComponentProps<typeof PushMatrixModal>> = {}) =>
  render(<PushMatrixModal isOpen onClose={vi.fn()} {...overrides} />);

beforeEach(() => {
  vi.mocked(api.notifications.getPushMatrix).mockResolvedValue(rows);
  vi.mocked(api.notifications.updatePushMatrix).mockResolvedValue(rows);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('PushMatrixModal', () => {
  it('加载失败时展示错误信息', async () => {
    vi.mocked(api.notifications.getPushMatrix).mockRejectedValue(new ApiError(500, '服务暂不可用'));

    renderModal();

    expect(await screen.findByRole('alert')).toHaveTextContent('推送方案加载失败');
  });

  it('查看者行显示占位符且没有勾选框', async () => {
    renderModal();

    await screen.findByText('访客');
    const viewer = rowOf('访客');
    expect(within(viewer).getAllByLabelText('查看者不参与推送')).toHaveLength(4);
    expect(within(viewer).queryAllByRole('checkbox')).toHaveLength(0);
  });

  it('没有邮箱的用户勾选框被禁用并给出提示', async () => {
    renderModal();

    await screen.findByText('无邮箱分析员');
    const checkbox = screen.getByRole('checkbox', {name: '无邮箱分析员 P1'});
    expect(checkbox).toBeDisabled();
    expect(checkbox).toHaveAttribute('title', '未设置邮箱，无法开启推送');
  });

  it('未分配供应商的风险分析员行给出警告图标', async () => {
    renderModal();

    await screen.findByText('无邮箱分析员');
    const row = rowOf('无邮箱分析员');
    expect(within(row).getByTitle('未分配负责供应商，不会推送实时风险')).toBeInTheDocument();
  });

  it('表头全选只作用于可勾选行', async () => {
    const user = userEvent.setup();
    renderModal();

    await screen.findByText('平台管理员');
    await user.click(screen.getByRole('checkbox', {name: '全选 P1'}));

    expect(screen.getByRole('checkbox', {name: '平台管理员 P1'})).toBeChecked();
    expect(screen.getByRole('checkbox', {name: '张分析员 P1'})).toBeChecked();
    expect(screen.getByRole('checkbox', {name: '无邮箱分析员 P1'})).not.toBeChecked();
    expect(within(rowOf('访客')).queryAllByRole('checkbox')).toHaveLength(0);
  });

  it('保存时只提交发生变化的行', async () => {
    const user = userEvent.setup();
    const onSaved = vi.fn();
    const onClose = vi.fn();
    renderModal({onSaved, onClose});

    await screen.findByText('平台管理员');
    await user.click(screen.getByRole('checkbox', {name: '平台管理员 P1'}));
    await user.click(screen.getByRole('button', {name: '保存'}));

    await waitFor(() => expect(api.notifications.updatePushMatrix).toHaveBeenCalledTimes(1));
    expect(api.notifications.updatePushMatrix).toHaveBeenCalledWith([{
      user_id: 1, push_p1: true, push_p2: false, push_p3: false, push_monthly_report: false,
    }]);
    expect(onSaved).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('没有改动时保存按钮不可用', async () => {
    renderModal();

    await screen.findByText('平台管理员');
    expect(screen.getByRole('button', {name: '保存'})).toBeDisabled();
  });

  it('保存失败时保留弹窗并展示错误', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    vi.mocked(api.notifications.updatePushMatrix).mockRejectedValue(new ApiError(422, '用户 1 未设置邮箱'));
    renderModal({onClose});

    await screen.findByText('平台管理员');
    await user.click(screen.getByRole('checkbox', {name: '平台管理员 P1'}));
    await user.click(screen.getByRole('button', {name: '保存'}));

    expect(await screen.findByRole('alert')).toHaveTextContent('用户 1 未设置邮箱');
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByRole('dialog', {name: '推送管理'})).toBeInTheDocument();
  });

  it('有未保存改动时关闭需要二次确认', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    renderModal({onClose});

    await screen.findByText('平台管理员');
    await user.click(screen.getByRole('checkbox', {name: '平台管理员 P1'}));
    await user.click(screen.getByRole('button', {name: '取消'}));

    expect(await screen.findByText('有未保存的推送方案更改，确认关闭？')).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();

    await user.click(screen.getByRole('button', {name: '放弃修改并关闭'}));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
