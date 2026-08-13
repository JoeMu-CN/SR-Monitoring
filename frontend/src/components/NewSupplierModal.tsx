import React, { useState } from 'react';
import { Supplier } from '../types';

interface NewSupplierModalProps {
  isOpen: boolean;
  onClose: () => void;
  onAddSupplier: (supplier: Supplier) => Promise<void>;
}

export const NewSupplierModal: React.FC<NewSupplierModalProps> = ({
  isOpen,
  onClose,
  onAddSupplier,
}) => {
  const [legalName, setLegalName] = useState('');
  const [supplierCode, setSupplierCode] = useState('');
  const [countryCode, setCountryCode] = useState('CN');
  const [registrationNo, setRegistrationNo] = useState('');
  const [productionLocation, setProductionLocation] = useState('广东 深圳');
  const [category, setCategory] = useState('电子元器件');
  const [suppliedProduct, setSuppliedProduct] = useState('');
  const [isSaving, setIsSaving] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  if (!isOpen) return null;

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

    const newSup: Supplier = {
      id: '',
      code: supplierCode.trim(),
      legalName: legalName.trim(),
      registrationNo: registrationNo.trim(),
      productionLocation: productionLocation.trim(),
      countryRegion: countryCode.trim().toUpperCase(),
      tier: '重点供应商',
      category: category.trim(),
      suppliedProduct: suppliedProduct.trim(),
      monitoringStatus: 'normal',
      lastUpdated: '刚刚创建',
    };

    setIsSaving(true);
    setSubmitError(null);
    try {
      await onAddSupplier(newSup);
      setSupplierCode('');
      setLegalName('');
      setRegistrationNo('');
      setSuppliedProduct('');
      onClose();
    } catch (caught) {
      setSubmitError(caught instanceof Error ? caught.message : '供应商保存失败，请稍后重试。');
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 bg-slate-900/60 backdrop-blur-xs flex items-center justify-center p-4 animate-in fade-in">
      <div className="w-full max-w-lg space-y-5 overflow-hidden rounded-3xl border border-slate-200 bg-white p-6 shadow-2xl dark:border-slate-800 dark:bg-slate-900">
        <div className="flex justify-between items-center pb-3 border-b border-slate-100 dark:border-slate-800">
          <div className="flex items-center gap-2 font-bold text-[18px] text-[#101d28] dark:text-white">
            <span className="material-symbols-outlined text-[#004782] text-[22px]">factory</span>
            <span>导入 / 新建供应商</span>
          </div>
          <button onClick={onClose} className="p-1 hover:bg-slate-100 rounded-lg">
            <span className="material-symbols-outlined text-[20px] text-slate-400">close</span>
          </button>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4 text-[13px]">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label className="font-bold text-slate-700 dark:text-slate-300 block mb-1">
                供应商编码 *
              </label>
              <input
                type="text"
                required
                placeholder="例如: SUP-0001"
                value={supplierCode}
                onChange={(e) => setSupplierCode(e.target.value)}
                className="w-full bg-[#f7f9ff] border border-[#c2c6d2] rounded-lg p-2.5 font-mono text-[12px] focus:outline-none focus:ring-2 focus:ring-[#004782]"
              />
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
          </div>

          <div>
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

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="font-bold text-slate-700 dark:text-slate-300 block mb-1">
                主要生产/经营地点
              </label>
              <input
                type="text"
                value={productionLocation}
                onChange={(e) => setProductionLocation(e.target.value)}
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
          </div>

          <div className="p-3 bg-blue-50 border border-blue-200 rounded-xl text-[11px] text-[#004782] flex items-center gap-2">
            <span className="material-symbols-outlined text-[18px]">verified</span>
            <span>保存后该供应商将开启监控；风险信号由当前已配置的数据源及其采集计划提供。</span>
          </div>

          {submitError && (
            <div className="p-3 bg-red-50 border border-red-200 rounded-xl text-[11px] text-red-700">{submitError}</div>
          )}

          <div className="pt-2 flex justify-end gap-3 border-t border-slate-100 dark:border-slate-800">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 border border-[#c2c6d2] rounded-xl font-medium text-slate-600 hover:bg-slate-50"
            >
              取消
            </button>
            <button
              type="submit"
              disabled={isSaving}
              className="px-5 py-2 bg-[#004782] hover:bg-[#185fa5] disabled:opacity-50 text-white font-bold rounded-xl shadow-sm"
            >
              {isSaving ? '正在保存…' : '确认导入并开启监控'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};
