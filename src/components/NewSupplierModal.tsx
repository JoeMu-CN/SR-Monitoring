import React, { useState } from 'react';
import { Building2, X, ShieldCheck } from 'lucide-react';
import { Supplier } from '../types';

interface NewSupplierModalProps {
  isOpen: boolean;
  onClose: () => void;
  onAddSupplier: (supplier: Supplier) => void;
}

export const NewSupplierModal: React.FC<NewSupplierModalProps> = ({
  isOpen,
  onClose,
  onAddSupplier,
}) => {
  const [legalName, setLegalName] = useState('');
  const [registrationNo, setRegistrationNo] = useState('');
  const [productionLocation, setProductionLocation] = useState('广东 深圳');
  const [tier, setTier] = useState<'Tier 1' | 'Tier 2' | 'Tier 3'>('Tier 1');
  const [category, setCategory] = useState('电子元器件');
  const [suppliedProduct, setSuppliedProduct] = useState('');

  if (!isOpen) return null;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!legalName || !suppliedProduct) {
      alert('请填写完整的供应商法人主体及供应产品信息');
      return;
    }

    const newSup: Supplier = {
      id: `sup-${Date.now()}`,
      code: `VND-${Math.floor(10000 + Math.random() * 90000)}`,
      legalName,
      registrationNo: registrationNo || '91440300MA5FXXXXXX',
      productionLocation,
      countryRegion: '中国',
      tier,
      category,
      suppliedProduct,
      monitoringStatus: 'normal',
      riskLevel: 'P4',
      riskScore: 15,
      lastUpdated: new Date().toISOString().replace('T', ' ').slice(0, 16),
    };

    onAddSupplier(newSup);
    alert(`供应商 [${legalName}] 已成功导入并开启实时监控！`);
    onClose();
  };

  return (
    <div className="fixed inset-0 z-50 bg-slate-900/60 backdrop-blur-xs flex items-center justify-center p-4 animate-in fade-in">
      <div className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-3xl w-full max-w-lg shadow-2xl overflow-hidden p-6 space-y-5">
        <div className="flex justify-between items-center pb-3 border-b border-slate-100 dark:border-slate-800">
          <div className="flex items-center gap-2 font-bold text-[18px] text-slate-900 dark:text-white">
            <Building2 className="w-5 h-5 text-[#007aff]" />
            <span>导入 / 新建供应商</span>
          </div>
          <button onClick={onClose} className="p-1 hover:bg-slate-100 dark:hover:bg-slate-800 rounded-lg cursor-pointer">
            <X className="w-5 h-5 text-slate-400" />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4 text-[13px]">
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
              className="w-full bg-slate-100/80 dark:bg-slate-800 border border-slate-200/80 dark:border-slate-700 rounded-xl p-2.5 font-medium focus:outline-none focus:ring-2 focus:ring-[#007aff] text-slate-900 dark:text-white"
            />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="font-bold text-slate-700 dark:text-slate-300 block mb-1">
                统一社会信用代码 / 注册号
              </label>
              <input
                type="text"
                placeholder="91330100MA2XXXXXX"
                value={registrationNo}
                onChange={(e) => setRegistrationNo(e.target.value)}
                className="w-full bg-slate-100/80 dark:bg-slate-800 border border-slate-200/80 dark:border-slate-700 rounded-xl p-2.5 font-mono text-[12px] text-slate-900 dark:text-white"
              />
            </div>

            <div>
              <label className="font-bold text-slate-700 dark:text-slate-300 block mb-1">
                供应商层级
              </label>
              <select
                value={tier}
                onChange={(e) => setTier(e.target.value as any)}
                className="w-full bg-slate-100/80 dark:bg-slate-800 border border-slate-200/80 dark:border-slate-700 rounded-xl p-2.5 font-bold text-[#007aff] dark:text-blue-300"
              >
                <option value="Tier 1">Tier 1 (核心一类)</option>
                <option value="Tier 2">Tier 2 (关键二类)</option>
                <option value="Tier 3">Tier 3 (基础三类)</option>
              </select>
            </div>
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
                className="w-full bg-slate-100/80 dark:bg-slate-800 border border-slate-200/80 dark:border-slate-700 rounded-xl p-2.5 font-medium text-slate-900 dark:text-white"
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
                className="w-full bg-slate-100/80 dark:bg-slate-800 border border-slate-200/80 dark:border-slate-700 rounded-xl p-2.5 font-medium text-slate-900 dark:text-white"
              />
            </div>
          </div>

          <div className="p-3 bg-blue-50/80 dark:bg-blue-950/40 border border-blue-200 dark:border-blue-800 rounded-2xl text-[11px] text-[#007aff] dark:text-blue-300 flex items-center gap-2">
            <ShieldCheck className="w-4 h-4 shrink-0" />
            <span>保存后系统将自动联网对接天眼查、失信被执行人及海关数据源开启24小时轮询。</span>
          </div>

          <div className="pt-2 flex justify-end gap-3 border-t border-slate-100 dark:border-slate-800">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 border border-slate-200 dark:border-slate-700 rounded-xl font-medium text-slate-600 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-800 cursor-pointer"
            >
              取消
            </button>
            <button
              type="submit"
              className="px-5 py-2 bg-[#007aff] hover:bg-[#0062cc] text-white font-bold rounded-xl shadow-2xs cursor-pointer"
            >
              确认导入并开启监控
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};
