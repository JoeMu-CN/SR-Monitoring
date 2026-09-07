import React from 'react';

interface EventTypeOption {
  value: string;
  label: string;
}

interface RuleEngineEventTypesProps {
  options: EventTypeOption[];
  value: string[];
  onChange: (next: string[]) => void;
  disabled: boolean;
}

export const RuleEngineEventTypes: React.FC<RuleEngineEventTypesProps> = ({options, value, onChange, disabled}) => {
  const toggle = (eventType: string) => {
    onChange(value.includes(eventType) ? value.filter((item) => item !== eventType) : [...value, eventType]);
  };

  return (
    <section className="rounded-xl bg-[#f7f9ff] dark:bg-slate-950/40 border border-slate-200 dark:border-slate-800 p-3">
      <h3 className="text-[12px] font-bold text-[#424751] dark:text-slate-300 mb-2">事件类型</h3>
      <div className="flex flex-wrap gap-1.5">
        {options.map((option) => {
          const checked = value.includes(option.value);
          return (
            <label
              key={option.value}
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
                onChange={() => toggle(option.value)}
                className="sr-only peer"
              />
              <span aria-hidden="true" className="material-symbols-outlined text-[14px]">{checked ? 'check_box' : 'check_box_outline_blank'}</span>
              {option.label}
            </label>
          );
        })}
      </div>
      <p className="text-[11px] text-slate-500 dark:text-slate-400 mt-2">事件类型在启用维度间互斥，冲突时后端会拒绝保存。</p>
    </section>
  );
};
