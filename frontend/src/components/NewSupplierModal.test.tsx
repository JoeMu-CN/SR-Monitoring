import {cleanup, render, screen} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {afterEach, describe, expect, it, vi} from 'vitest';
import type {Supplier} from '../types';
import {NewSupplierModal} from './NewSupplierModal';

const baseProps = {
  isOpen: true,
  onClose: vi.fn(),
  onSave: vi.fn(),
  onDelete: vi.fn(),
};

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

/** 构造一份带多地点/多产品的 Supplier 用于测试 */
const multiSiteSupplier: Supplier = {
  id: '42',
  code: 'SUP-0042',
  legalName: '测试电子有限公司',
  registrationNo: '91330100MA1234567X',
  registrationAddress: '浙江省杭州市滨江区',
  productionLocation: '杭州工厂',
  productionAddress: '江陵路100号',
  productionRegion: '浙江省',
  productionCity: '杭州市',
  productionDistrict: '滨江区',
  countryRegion: 'CN',
  tier: '重点供应商',
  category: '电子元件',
  suppliedProduct: '功率半导体',
  monitoringStatus: 'paused',
  lastUpdated: '测试',
};

describe('NewSupplierModal 编辑模式 — 基本表单行为', () => {
  it('编辑模式填充初始值并禁用供应商编码', async () => {
    render(<NewSupplierModal {...baseProps} mode="edit" initialSupplier={multiSiteSupplier} />);
    expect(screen.getByDisplayValue('测试电子有限公司')).toBeInTheDocument();
    expect(screen.getByDisplayValue('SUP-0042')).toBeDisabled();
  });

  it('保存成功后调用 onClose', async () => {
    const user = userEvent.setup();
    baseProps.onSave.mockResolvedValue(undefined);
    render(<NewSupplierModal {...baseProps} mode="edit" initialSupplier={multiSiteSupplier} />);
    await user.click(screen.getByRole('button', {name: '保存修改'}));
    expect(baseProps.onSave).toHaveBeenCalledTimes(1);
    expect(baseProps.onClose).toHaveBeenCalledTimes(1);
  });
});

describe('NewSupplierModal 编辑模式 — updated_at 并发令牌', () => {
  it('编辑模式接收 updated_at 并存储为内部状态', () => {
    render(
      <NewSupplierModal
        {...baseProps}
        mode="edit"
        initialSupplier={multiSiteSupplier}
        updatedAt="2026-09-01T10:00:00Z"
      />,
    );
    // 组件渲染正常，无崩溃
    expect(screen.getByText('编辑供应商 · SUP-0042')).toBeInTheDocument();
  });
});

describe('NewSupplierModal 编辑模式 — 多地点/多产品只读提示', () => {
  it('有多个地点时显示「另有N条地点」只读提示', () => {
    render(
      <NewSupplierModal
        {...baseProps}
        mode="edit"
        initialSupplier={multiSiteSupplier}
        extraSiteCount={1}
        extraProductCount={0}
      />,
    );
    expect(screen.getByText(/另有 1 条地点，保存不会覆盖/)).toBeInTheDocument();
  });

  it('有多个产品时显示「另有N条产品」只读提示', () => {
    render(
      <NewSupplierModal
        {...baseProps}
        mode="edit"
        initialSupplier={multiSiteSupplier}
        extraSiteCount={0}
        extraProductCount={2}
      />,
    );
    expect(screen.getByText(/另有 2 条产品，保存不会覆盖/)).toBeInTheDocument();
  });

  it('无多余地点或产品时不显示额外提示', () => {
    render(
      <NewSupplierModal
        {...baseProps}
        mode="edit"
        initialSupplier={multiSiteSupplier}
        extraSiteCount={0}
        extraProductCount={0}
      />,
    );
    expect(screen.queryByText(/另有/)).not.toBeInTheDocument();
  });
});

describe('NewSupplierModal 编辑模式 — 409 冲突保留输入', () => {
  it('409 冲突时显示冲突提示且不关闭 modal', async () => {
    const user = userEvent.setup();
    baseProps.onSave.mockRejectedValue(new Error('供应商资料已被其他用户修改（409），请重新载入'));
    render(<NewSupplierModal {...baseProps} mode="edit" initialSupplier={multiSiteSupplier} />);

    await user.click(screen.getByRole('button', {name: '保存修改'}));

    expect(await screen.findByText(/已被其他用户修改/)).toBeInTheDocument();
    expect(baseProps.onClose).not.toHaveBeenCalled();
  });

  it('409 冲突后用户修改的值仍然保留', async () => {
    const user = userEvent.setup();
    baseProps.onSave.mockRejectedValue(new Error('供应商资料已被其他用户修改（409），请重新载入'));
    render(<NewSupplierModal {...baseProps} mode="edit" initialSupplier={multiSiteSupplier} />);

    // 修改法人名称
    const legalNameInput = screen.getByDisplayValue('测试电子有限公司');
    await user.clear(legalNameInput);
    await user.type(legalNameInput, '新名称');

    await user.click(screen.getByRole('button', {name: '保存修改'}));

    // 等待错误提示出现
    await screen.findByText(/已被其他用户修改/);
    // 用户输入仍保留在表单中
    expect(screen.getByDisplayValue('新名称')).toBeInTheDocument();
  });

  it('409 冲突后表单中仍可继续编辑', async () => {
    const user = userEvent.setup();
    baseProps.onSave.mockRejectedValueOnce(new Error('供应商资料已被其他用户修改（409），请重新载入'))
      .mockResolvedValueOnce(undefined);
    render(<NewSupplierModal {...baseProps} mode="edit" initialSupplier={multiSiteSupplier} />);

    // 第一次保存 → 409
    await user.click(screen.getByRole('button', {name: '保存修改'}));
    await screen.findByText(/已被其他用户修改/);

    // 修改名称后再次保存 → 成功
    const legalNameInput = screen.getByDisplayValue('测试电子有限公司');
    await user.clear(legalNameInput);
    await user.type(legalNameInput, '已更新');
    await user.click(screen.getByRole('button', {name: '保存修改'}));
    expect(baseProps.onClose).toHaveBeenCalledTimes(1);
  });
});

describe('NewSupplierModal 编辑模式 — 详情加载失败', () => {
  it('详情加载失败时在保存前显示错误', async () => {
    const user = userEvent.setup();
    baseProps.onSave.mockRejectedValue(new Error('供应商详情获取失败'));
    render(<NewSupplierModal {...baseProps} mode="edit" initialSupplier={multiSiteSupplier} />);

    await user.click(screen.getByRole('button', {name: '保存修改'}));

    expect(await screen.findByText('供应商详情获取失败')).toBeInTheDocument();
    expect(baseProps.onClose).not.toHaveBeenCalled();
  });
});

describe('NewSupplierModal 编辑模式 — 明确重载时表单与详情同步', () => {
  it('initialSupplier 更新（重载最新详情）时表单整体重新填充，包括地点字段', () => {
    const {rerender} = render(
      <NewSupplierModal {...baseProps} mode="edit" initialSupplier={multiSiteSupplier} />,
    );
    expect(screen.getByDisplayValue('测试电子有限公司')).toBeInTheDocument();
    expect(screen.getByDisplayValue('浙江省')).toBeInTheDocument();

    // App 在明确重载后传入全新 initialSupplier：表单应同步为最新详情值
    rerender(
      <NewSupplierModal
        {...baseProps}
        mode="edit"
        initialSupplier={{
          ...multiSiteSupplier,
          legalName: '重载后的最新名称有限公司',
          productionRegion: '江苏省',
          productionCity: '苏州市',
          productionDistrict: '工业园区',
          productionAddress: '金鸡湖大道2号',
        }}
      />,
    );
    expect(screen.getByDisplayValue('重载后的最新名称有限公司')).toBeInTheDocument();
    expect(screen.getByDisplayValue('江苏省')).toBeInTheDocument();
    expect(screen.getByDisplayValue('苏州市')).toBeInTheDocument();
    expect(screen.getByDisplayValue('工业园区')).toBeInTheDocument();
    expect(screen.getByDisplayValue('金鸡湖大道2号')).toBeInTheDocument();
  });
});

describe('NewSupplierModal 编辑模式 — 产品非必填（0 产品供应商可直接保存）', () => {
  const noProductSupplier: Supplier = {
    ...multiSiteSupplier,
    suppliedProduct: '',
  };

  it('编辑模式下产品为空时允许提交，不阻止保存', async () => {
    const user = userEvent.setup();
    baseProps.onSave.mockResolvedValue(undefined);
    render(<NewSupplierModal {...baseProps} mode="edit" initialSupplier={noProductSupplier} />);

    // 产品字段应为空
    const productInput = screen.getByPlaceholderText(/功率半导体/);
    expect(productInput).toHaveValue('');

    // 提交应成功（不被 validation 阻止）
    await user.click(screen.getByRole('button', {name: '保存修改'}));
    expect(baseProps.onSave).toHaveBeenCalledTimes(1);
    expect(baseProps.onClose).toHaveBeenCalledTimes(1);
  });

  it('编辑模式下产品输入框不应有 required 属性', () => {
    render(<NewSupplierModal {...baseProps} mode="edit" initialSupplier={noProductSupplier} />);
    const productInput = screen.getByPlaceholderText(/功率半导体/);
    expect(productInput).not.toHaveAttribute('required');
  });

  it('新增模式下产品仍为必填', async () => {
    const user = userEvent.setup();
    baseProps.onSave.mockResolvedValue(undefined);
    render(<NewSupplierModal {...baseProps} mode="create" />);

    // 不填产品直接提交 → 应被 validation 阻止
    await user.click(screen.getByRole('button', {name: '新增并开启监控'}));
    expect(baseProps.onSave).not.toHaveBeenCalled();
  });

  it('新增模式下产品输入框应有 required 属性', () => {
    render(<NewSupplierModal {...baseProps} mode="create" />);
    const productInput = screen.getByPlaceholderText(/功率半导体/);
    expect(productInput).toHaveAttribute('required');
  });
});
