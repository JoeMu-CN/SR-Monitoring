import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {cleanup, render, screen, waitFor} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {api, type SupplierListResponse} from '../api';
import {SupplierMultiSelect, type SupplierOption} from './SupplierMultiSelect';

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>();
  return {...actual, api: {...actual.api, searchSuppliers: vi.fn()}};
});

const supplierAlpha: SupplierOption = {id: 1, supplier_code: 'SUP-001', legal_name: '甲公司'};
const supplierBeta: SupplierOption = {id: 2, supplier_code: 'SUP-002', legal_name: '乙公司'};

const listResponse = (items: readonly SupplierOption[]): SupplierListResponse => ({
  items: items.map((item) => ({...item})) as SupplierListResponse['items'],
  total: items.length,
  limit: 100,
  offset: 0,
});

beforeEach(() => {
  vi.mocked(api.searchSuppliers).mockResolvedValue(listResponse([]));
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('SupplierMultiSelect', () => {
  it('渲染已选 chip 并在移除时回调新列表', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();

    render(<SupplierMultiSelect value={[supplierAlpha]} onChange={onChange} />);

    expect(screen.getByText('SUP-001')).toBeInTheDocument();
    expect(screen.getByText('· 甲公司')).toBeInTheDocument();
    await user.click(screen.getByRole('button', {name: '移除 甲公司'}));
    expect(onChange).toHaveBeenCalledWith([]);
  });

  it('输入关键字后防抖请求搜索，且只请求一次', async () => {
    const user = userEvent.setup();
    vi.mocked(api.searchSuppliers).mockResolvedValue(listResponse([supplierBeta]));

    render(<SupplierMultiSelect value={[]} onChange={vi.fn()} />);
    await user.type(screen.getByRole('combobox'), '乙');

    expect(api.searchSuppliers).not.toHaveBeenCalled();
    await waitFor(() => expect(api.searchSuppliers).toHaveBeenCalledTimes(1));
    expect(api.searchSuppliers).toHaveBeenCalledWith('乙');
  });

  it('选中搜索结果后通过 onChange 回传该供应商', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    vi.mocked(api.searchSuppliers).mockResolvedValue(listResponse([supplierBeta]));

    render(<SupplierMultiSelect value={[]} onChange={onChange} />);
    await user.type(screen.getByRole('combobox'), '乙');
    const option = await screen.findByRole('option', {name: /乙公司/});
    await user.click(option);

    expect(onChange).toHaveBeenCalledWith([supplierBeta]);
  });

  it('没有匹配结果时展示空状态', async () => {
    const user = userEvent.setup();
    vi.mocked(api.searchSuppliers).mockResolvedValue(listResponse([]));

    render(<SupplierMultiSelect value={[]} onChange={vi.fn()} />);
    await user.type(screen.getByRole('combobox'), '不存在');

    expect(await screen.findByText('未找到匹配供应商')).toBeInTheDocument();
  });

  it('关键字为空时提示先输入关键字', async () => {
    const user = userEvent.setup();

    render(<SupplierMultiSelect value={[]} onChange={vi.fn()} />);
    await user.click(screen.getByRole('combobox'));

    expect(await screen.findByText('输入关键字搜索供应商')).toBeInTheDocument();
  });

  it('Escape 关闭下拉结果', async () => {
    const user = userEvent.setup();
    vi.mocked(api.searchSuppliers).mockResolvedValue(listResponse([supplierBeta]));

    render(<SupplierMultiSelect value={[]} onChange={vi.fn()} />);
    const input = screen.getByRole('combobox');
    await user.type(input, '乙');
    await screen.findByRole('option', {name: /乙公司/});

    await user.type(input, '{Escape}');
    await waitFor(() => expect(screen.queryByRole('listbox')).toBeNull());
  });

  it('已选供应商不再出现在下拉结果中', async () => {
    const user = userEvent.setup();
    vi.mocked(api.searchSuppliers).mockResolvedValue(listResponse([supplierAlpha, supplierBeta]));

    render(<SupplierMultiSelect value={[supplierAlpha]} onChange={vi.fn()} />);
    await user.type(screen.getByRole('combobox'), '公司');

    expect(await screen.findByRole('option', {name: /乙公司/})).toBeInTheDocument();
    expect(screen.queryByRole('option', {name: /甲公司/})).toBeNull();
    expect(screen.getByText('SUP-001')).toBeInTheDocument();
  });
});
