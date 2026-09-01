import React, {useEffect, useState} from 'react';
import {Supplier} from '../types';

interface SupplierModalProps {
  isOpen: boolean;
  onClose: () => void;
  mode: 'create' | 'edit';
  initialSupplier?: Supplier;
  onSave: (supplier: Supplier) => Promise<void>;
  // 仅 edit 模式需要：调起后端 DELETE，成功后 App 会关闭 modal
  onDelete?: (supplierId: string) => Promise<void>;
}

export const NewSupplierModal: React.FC<SupplierModalProps> = ({
  isOpen,
  onClose,
  mode,
  initialSupplier,
  onSave,
  onDelete,
}) => {
  const [legalName, setLegalName] = useState('');
  const [supplierCode, setSupplierCode] = useState('');
  const [countryCode, setCountryCode] = useState('CN');
  const [registrationNo, setRegistrationNo] = useState('');
  const [registrationAddress, setRegistrationAddress] = useState('');
  const [productionRegion, setProductionRegion] = useState('');
  const [productionCity, setProductionCity] = useState('');
  const [productionDistrict, setProductionDistrict] = useState('');
  const [productionAddress, setProductionAddress] = useState('');
  const [category, setCategory] = useState('');
  const [suppliedProduct, setSuppliedProduct] = useState('');
  const [isSaving, setIsSaving] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  useEffect(() => {
    if (!isOpen) return;
    setSubmitError(null);
    if (mode === 'edit' && initialSupplier) {
      setLegalName(initialSupplier.legalName ?? '');
      setSupplierCode(initialSupplier.code ?? '');
      setCountryCode(initialSupplier.countryRegion ?? 'CN');
      setRegistrationNo(initialSupplier.registrationNo ?? '');
      setRegistrationAddress(initialSupplier.registrationAddress ?? '');
      setProductionRegion(initialSupplier.productionRegion ?? '');
      setProductionCity(initialSupplier.productionCity ?? '');
      setProductionDistrict(initialSupplier.productionDistrict ?? '');
      setProductionAddress(initialSupplier.productionAddress ?? initialSupplier.productionLocation ?? '');
      setCategory(initialSupplier.category ?? '');
      setSuppliedProduct(initialSupplier.suppliedProduct ?? '');
    } else {
      setLegalName('');
      setSupplierCode('');
      setCountryCode('CN');
      setRegistrationNo('');
      setRegistrationAddress('');
      setProductionRegion('广东省');
      setProductionCity('深圳市');
      setProductionDistrict('南山区');
      setProductionAddress('');
      setCategory('电子元器件');
      setSuppliedProduct('');
    }
  }, [isOpen, mode, initialSupplier]);

  if (!isOpen) return null;

  const isEdit = mode === 'edit' && Boolean(initialSupplier);
  const codeLocked = isEdit;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!supplierCode.trim() || !legalName.trim() || !suppliedProduct.trim()) {
      setSubmitError('请填写供应商编码、法人主体及供应产品。');
      return;
    }
    if (!/^[A-Za-z]{2}$/.test(countryCode.trim())) {
      setSubmitError('国家/地区代码必须为两位 ISO 代码，例如 CN、US、DE。');
      return;
    }

    const baseSupplier: Supplier = {
      id: initialSupplier?.id ?? '',
      code: supplierCode.trim(),
      legalName: legalName.trim(),
      registrationNo: registrationNo.trim(),
      registrationAddress: registrationAddress.trim(),
      productionLocation: [productionCity, productionDistrict].filter(Boolean).join(' ') || productionAddress.trim(),
      productionAddress: productionAddress.trim(),
      productionRegion: productionRegion.trim(),
      productionCity: productionCity.trim(),
      productionDistrict: productionDistrict.trim(),
      countryRegion: countryCode.trim().toUpperCase(),
      tier: initialSupplier?.tier ?? '重点供应商',
      category: category.trim(),
      suppliedProduct: suppliedProduct.trim(),
      monitoringStatus: initialSupplier?.monitoringStatus ?? 'normal',
      lastUpdated: isEdit ? '正在保存…' : '刚刚创建',
    };

    setIsSaving(true);
    setSubmitError(null);
    try {
      await onSave(baseSupplier);
      onClose();
    } catch (caught) {
      setSubmitError(caught instanceof Error ? caught.message : '供应商保存失败，请稍后重试。');
    } finally {
      setIsSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!isEdit || !initialSupplier || !onDelete) return;
    if (!window.confirm(`确认删除供应商【${initialSupplier.legalName}】（编码 ${initialSupplier.code}）？该操作不可撤销。`)) return;
    setIsDeleting(true);
    setSubmitError(null);
    try {
      await onDelete(initialSupplier.id);
      onClose();
    } catch (caught) {
      setSubmitError(caught instanceof Error ? caught.message : '供应商删除失败，请稍后重试。');
    } finally {
      setIsDeleting(false);
    }
  };

  const titleText = isEdit ? `编辑供应商 · ${initialSupplier?.code ?? ''}` : '新增供应商';
  const submitText = isSaving ? '正在保存…' : isEdit ? '保存修改' : '新增并开启监控';
  const hintText = isEdit
    ? '保存后将立即生效。监控启停仍由「启停监控」按钮统一控制，删除供应商会同时清理其事件匹配记录。'
    : '保存后该供应商将开启监控；风险信号由当前已配置的数据源及其采集计划提供。';

  return (
    <div className="fixed inset-0 z-50 bg-slate-900/60 backdrop-blur-xs flex items-center justify-center p-4 animate-in fade-in">
      <div className="w-full max-w-3xl space-y-5 overflow-hidden rounded-3xl border border-slate-200 bg-white p-6 shadow-2xl dark:border-slate-800 dark:bg-slate-900">
        <div className="flex justify-between items-center pb-3 border-b border-slate-100 dark:border-slate-800">
          <div className="flex items-center gap-2 font-bold text-[18px] text-[#101d28] dark:text-white">
            <span className="material-symbols-outlined text-[#004782] text-[22px]">factory</span>
            <span>{titleText}</span>
          </div>
          <button onClick={onClose} className="p-1 hover:bg-slate-100 rounded-lg">
            <span className="material-symbols-outlined text-[20px] text-slate-400">close</span>
          </button>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4 text-[13px]">
          {/* md+ (电脑/平板) 横向 2 列布局；mobile (grid-cols-1) 保持纵向 */}
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2 md:gap-x-4 md:gap-y-3">
            <div>
              <label className="font-bold text-slate-700 dark:text-slate-300 block mb-1">
                供应商编码 *
              </label>
              <input
                type="text"
                required
                disabled={codeLocked}
                placeholder="例如: SUP-0001"
                value={supplierCode}
                onChange={(e) => setSupplierCode(e.target.value)}
                className="w-full bg-[#f7f9ff] border border-[#c2c6d2] rounded-lg p-2.5 font-mono text-[12px] focus:outline-none focus:ring-2 focus:ring-[#004782] disabled:bg-slate-100 disabled:text-slate-500"
              />
              {codeLocked && <p className="mt-1 text-[10px] text-slate-400">编码作为唯一标识，编辑时不可修改</p>}
            </div>
            <div>
              <label className="font-bold text-slate-700 dark:text-slate-300 block mb-1">
                国家/地区代码 *
              </label>
              <input
                type="text"
                required
                maxLength={2}
                placeholder="CN"
                value={countryCode}
                onChange={(e) => setCountryCode(e.target.value.toUpperCase())}
                className="w-full bg-[#f7f9ff] border border-[#c2c6d2] rounded-lg p-2.5 font-mono text-[12px] uppercase focus:outline-none focus:ring-2 focus:ring-[#004782]"
              />
            </div>

            <div className="md:col-span-2">
              <label className="font-bold text-slate-700 dark:text-slate-300 block mb-1">
                法人主体名称 *
              </label>
              <input
                type="text"
                required
                placeholder="例如: 杭州智芯半导体有限公司"
                value={legalName}
                onChange={(e) => setLegalName(e.target.value)}
                className="w-full bg-[#f7f9ff] border border-[#c2c6d2] rounded-lg p-2.5 font-medium focus:outline-none focus:ring-2 focus:ring-[#004782]"
              />
            </div>

            <div>
              <label className="font-bold text-slate-700 dark:text-slate-300 block mb-1">
                统一社会信用代码 / 注册号
              </label>
              <input
                type="text"
                placeholder="91330100MA2XXXXXX"
                value={registrationNo}
                onChange={(e) => setRegistrationNo(e.target.value)}
                className="w-full bg-[#f7f9ff] border border-[#c2c6d2] rounded-lg p-2.5 font-mono text-[12px]"
              />
            </div>
            <div>
              <label className="font-bold text-slate-700 dark:text-slate-300 block mb-1">
                工商注册地址
              </label>
              <input
                type="text"
                placeholder="例如: 广东省深圳市南山区科技园1号"
                value={registrationAddress}
                onChange={(e) => setRegistrationAddress(e.target.value)}
                className="w-full bg-[#f7f9ff] border border-[#c2c6d2] rounded-lg p-2.5 font-medium"
              />
            </div>

            <div>
              <label className="font-bold text-slate-700 dark:text-slate-300 block mb-1">
                生产省州
              </label>
              <input
                type="text"
                value={productionRegion}
                onChange={(e) => setProductionRegion(e.target.value)}
                className="w-full bg-[#f7f9ff] border border-[#c2c6d2] rounded-lg p-2.5 font-medium"
              />
            </div>
            <div>
              <label className="font-bold text-slate-700 dark:text-slate-300 block mb-1">
                生产城市
              </label>
              <input
                type="text"
                value={productionCity}
                onChange={(e) => setProductionCity(e.target.value)}
                className="w-full bg-[#f7f9ff] border border-[#c2c6d2] rounded-lg p-2.5 font-medium"
              />
            </div>

            <div>
              <label className="font-bold text-slate-700 dark:text-slate-300 block mb-1">
                生产区县
              </label>
              <input
                type="text"
                value={productionDistrict}
                onChange={(e) => setProductionDistrict(e.target.value)}
                className="w-full bg-[#f7f9ff] border border-[#c2c6d2] rounded-lg p-2.5 font-medium"
              />
            </div>
            <div>
              <label className="font-bold text-slate-700 dark:text-slate-300 block mb-1">
                生产详细地址
              </label>
              <input
                type="text"
                placeholder="例如: 西丽街道留仙洞工业区2号厂房"
                value={productionAddress}
                onChange={(e) => setProductionAddress(e.target.value)}
                className="w-full bg-[#f7f9ff] border border-[#c2c6d2] rounded-lg p-2.5 font-medium"
              />
            </div>

            <div>
              <label className="font-bold text-slate-700 dark:text-slate-300 block mb-1">
                主要供应产品 *
              </label>
              <input
                type="text"
                required
                placeholder="例如: 功率半导体, 光学镜片"
                value={suppliedProduct}
                onChange={(e) => setSuppliedProduct(e.target.value)}
                className="w-full bg-[#f7f9ff] border border-[#c2c6d2] rounded-lg p-2.5 font-medium"
              />
            </div>
            <div>
              <label className="font-bold text-slate-700 dark:text-slate-300 block mb-1">
                行业分类
              </label>
              <input
                type="text"
                placeholder="例如: 电子元器件"
                value={category}
                onChange={(e) => setCategory(e.target.value)}
                className="w-full bg-[#f7f9ff] border border-[#c2c6d2] rounded-lg p-2.5 font-medium"
              />
            </div>
          </div>

          <div className="md:col-span-2 p-3 bg-blue-50 border border-blue-200 rounded-xl text-[11px] text-[#004782] flex items-center gap-2">
            <span className="material-symbols-outlined text-[18px]">verified</span>
            <span>{hintText}</span>
          </div>

          {submitError && (
            <div className="p-3 bg-red-50 border border-red-200 rounded-xl text-[11px] text-red-700">{submitError}</div>
          )}

          <div className="pt-2 flex items-center justify-between gap-3 border-t border-slate-100 dark:border-slate-800">
            <div>
              {isEdit && onDelete && (
                <button
                  type="button"
                  onClick={() => void handleDelete()}
                  disabled={isSaving || isDeleting}
                  className="px-3 py-2 text-[12px] font-bold text-[#ba1a1a] hover:bg-red-50 disabled:opacity-50 rounded-lg flex items-center gap-1.5"
                >
                  <span className="material-symbols-outlined text-[16px]">delete</span>
                  <span>{isDeleting ? '正在删除…' : '删除供应商'}</span>
                </button>
              )}
            </div>
            <div className="flex gap-3">
              <button
                type="button"
                onClick={onClose}
                className="px-4 py-2 border border-[#c2c6d2] rounded-xl font-medium text-slate-600 hover:bg-slate-50"
              >
                取消
              </button>
              <button
                type="submit"
                disabled={isSaving || isDeleting}
                className="px-5 py-2 bg-[#004782] hover:bg-[#185fa5] disabled:opacity-50 text-white font-bold rounded-xl shadow-sm"
              >
                {submitText}
              </button>
            </div>
          </div>
        </form>
      </div>
    </div>
  );
};
