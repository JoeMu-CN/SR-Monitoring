import React from 'react';

const MATCH_COLUMN_LABELS: Record<string, string> = {
  entity: '主体',
  location: '地点',
  product: '产品',
  country: '国家/区域',
  industry: '行业/原材料',
};

interface RuleEngineMatchColumnsProps {
  options: string[];
  value: string[];
  onChange: (next: string[]) => void;
  disabled: boolean;
}

export const RuleEngineMatchColumns: React.FC<RuleEngineMatchColumnsProps> = ({options, value, onChange, disabled}) => {
  const toggle = (column: string) => {
    onChange(value.includes(column) ? value.filter((item) => item !== column) : [...value, column]);
  };

  return (
    <section className="rounded-xl bg-[#f8fafc] dark:bg-slate-950/40 border border-slate-200 dark:border-slate-800 p-3">
      <h3 className="text-[12px] font-bold text-[#424751] dark:text-slate-300 mb-2">匹配柱</h3>
      <div className="flex flex-wrap gap-1.5">
        {options.map((column) => {
          const checked = value.includes(column);
          return (
            <label
              key={column}
              className={`inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-[11px] cursor-pointer transition-colors ${
                checked
                  ? 'bg-[#004782] border-[#004782] text-white'
                  : 'bg-white dark:bg-slate-900 border-slate-200 dark:border-slate-700 text-slate-700 dark:text-slate-300'
              } ${disabled ? 'opacity-60 cursor-not-allowed' : ''}`}
            >
              <input
                type="checkbox"
                checked={checked}
                disabled={disabled}
                onChange={() => toggle(column)}
                className="sr-only peer"
              />
              <span aria-hidden="true" className="material-symbols-outlined text-[14px]">{checked ? 'check_box' : 'check_box_outline_blank'}</span>
              {MATCH_COLUMN_LABELS[column] ?? column}
            </label>
          );
        })}
      </div>
      <p className="text-[11px] text-slate-500 dark:text-slate-400 mt-2">至少保留一柱；取消全部匹配柱将无法提交。</p>
    </section>
  );
};
