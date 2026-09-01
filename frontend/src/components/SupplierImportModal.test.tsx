import {cleanup, render, screen} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {afterEach, describe, expect, it, vi} from 'vitest';
import {SupplierImportError, supplierImportApi} from '../supplierImportApi';
import {SupplierImportModal} from './SupplierImportModal';

const renderModal = () => {
  const handlers = {onClose: vi.fn(), onImported: vi.fn(), onRequestError: vi.fn()};
  render(<SupplierImportModal isOpen onClose={handlers.onClose} onImported={handlers.onImported} onRequestError={handlers.onRequestError} />);
  return handlers;
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('供应商 Excel 导入弹窗', () => {
  it.each([
    ['文本文件', new File(['data'], 'suppliers.txt'), '仅支持 .xlsx 文件'],
    ['空文件', new File([], 'suppliers.xlsx'), '文件内容不能为空'],
    ['超限文件', new File([new Uint8Array(5 * 1024 * 1024 + 1)], 'suppliers.xlsx'), '文件大小不能超过 5 MB'],
  ])('拒绝%s且不发起上传', async (_caseName, file, expectedMessage) => {
    const user = userEvent.setup({applyAccept: false});
    const upload = vi.spyOn(supplierImportApi, 'upload');
    renderModal();

    await user.upload(screen.getByLabelText('选择 Excel 文件'), file);

    expect(screen.getByRole('alert')).toHaveTextContent(expectedMessage);
    expect(upload).not.toHaveBeenCalled();
  });

  it('显示文件信息、成功汇总并通知列表刷新', async () => {
    const user = userEvent.setup();
    vi.spyOn(supplierImportApi, 'upload').mockResolvedValue({
      createdSuppliers: 2,
      updatedSuppliers: 1,
      aliases: 3,
      sites: 4,
      products: 5,
    });
    const handlers = renderModal();

    const file = new File(['xlsx'], 'supplier-import.xlsx');
    await user.upload(screen.getByLabelText('选择 Excel 文件'), file);
    expect(screen.getByText('supplier-import.xlsx')).toBeInTheDocument();
    await user.click(screen.getByRole('button', {name: '开始导入'}));

    expect(await screen.findByText('导入完成')).toBeInTheDocument();
    expect(screen.getByText('新增供应商')).toBeInTheDocument();
    expect(screen.getByText('2')).toBeInTheDocument();
    expect(handlers.onImported).toHaveBeenCalledTimes(1);
  });

  it('逐列展示服务端工作表、行、字段与错误说明', async () => {
    const user = userEvent.setup();
    vi.spyOn(supplierImportApi, 'upload').mockRejectedValue(new SupplierImportError(422, '导入文件校验失败', [
      {sheet: '供应商', row: 8, field: '法人主体名称', message: '字段不能为空'},
    ]));
    renderModal();

    await user.upload(screen.getByLabelText('选择 Excel 文件'), new File(['xlsx'], 'invalid.xlsx'));
    await user.click(screen.getByRole('button', {name: '开始导入'}));

    expect(await screen.findByRole('table', {name: '导入错误明细'})).toHaveTextContent('供应商');
    expect(screen.getByRole('table', {name: '导入错误明细'})).toHaveTextContent('8');
    expect(screen.getByRole('table', {name: '导入错误明细'})).toHaveTextContent('法人主体名称');
    expect(screen.getByRole('table', {name: '导入错误明细'})).toHaveTextContent('字段不能为空');
    expect(screen.getByText('invalid.xlsx')).toBeInTheDocument();
  });

  it('401 与 403 错误上报会话边界', async () => {
    const user = userEvent.setup();
    vi.spyOn(supplierImportApi, 'upload').mockRejectedValue(new SupplierImportError(403, '权限不足'));
    const handlers = renderModal();

    await user.upload(screen.getByLabelText('选择 Excel 文件'), new File(['xlsx'], 'valid.xlsx'));
    await user.click(screen.getByRole('button', {name: '开始导入'}));

    expect(await screen.findByRole('alert')).toHaveTextContent('权限不足');
    expect(handlers.onRequestError).toHaveBeenCalledWith(expect.objectContaining({status: 403}));
  });
});
