import {useEffect, useId, useMemo, useRef, useState, type FocusEvent, type KeyboardEvent} from 'react';
import {Search, X} from 'lucide-react';
import {api} from '../api';

/** 多选控件使用的供应商条目（与后端 UserSupplierRead 同构）。 */
export interface SupplierOption {
  id: number;
  supplier_code: string;
  legal_name: string;
}

interface SupplierMultiSelectProps {
  readonly value: readonly SupplierOption[];
  readonly onChange: (next: readonly SupplierOption[]) => void;
  readonly disabled?: boolean;
  /** 允许外部用 <label htmlFor> 关联搜索框；不传则组件内部生成稳定 id。 */
  readonly inputId?: string;
}

/** 下拉结果最多展示的条数，避免长列表把弹窗撑开。 */
const MAX_VISIBLE_RESULTS = 8;
/** 搜索防抖毫秒数。 */
const SEARCH_DEBOUNCE_MS = 300;
/** 超过该数量后 chips 区域限高滚动。 */
const CHIPS_SCROLL_THRESHOLD = 50;

export const SupplierMultiSelect = ({value, onChange, disabled = false, inputId}: SupplierMultiSelectProps) => {
  const generatedId = useId();
  const searchId = inputId ?? `supplier-search-${generatedId}`;
  const listboxId = `${searchId}-listbox`;
  const [keyword, setKeyword] = useState('');
  const [results, setResults] = useState<readonly SupplierOption[]>([]);
  const [loading, setLoading] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [highlight, setHighlight] = useState(0);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const trimmed = keyword.trim();
    if (!trimmed || disabled) {
      setResults([]);
      setLoading(false);
      setSearchError(null);
      return;
    }
    let active = true;
    setLoading(true);
    const timer = window.setTimeout(() => {
      void api.searchSuppliers(trimmed).then((response) => {
        if (!active) return;
        setResults(response.items.map((item) => ({id: item.id, supplier_code: item.supplier_code, legal_name: item.legal_name})));
        setSearchError(null);
        setLoading(false);
      }).catch(() => {
        if (!active) return;
        setResults([]);
        setSearchError('供应商搜索失败，请重试');
        setLoading(false);
      });
    }, SEARCH_DEBOUNCE_MS);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [keyword, disabled]);

  /** 已选中的供应商不再出现在下拉结果里，但 chips 始终保留全部已选项。 */
  const visibleResults = useMemo(() => {
    const selectedIds = new Set(value.map((item) => item.id));
    return results.filter((item) => !selectedIds.has(item.id)).slice(0, MAX_VISIBLE_RESULTS);
  }, [results, value]);

  const addSupplier = (option: SupplierOption) => {
    if (value.some((item) => item.id === option.id)) return;
    onChange([...value, option]);
    setHighlight(0);
  };

  const removeSupplier = (option: SupplierOption) => {
    onChange(value.filter((item) => item.id !== option.id));
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Escape') {
      if (!open) return;
      event.preventDefault();
      event.stopPropagation();
      setOpen(false);
      return;
    }
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      setOpen(true);
      if (visibleResults.length === 0) return;
      setHighlight((current) => {
        const delta = event.key === 'ArrowDown' ? 1 : -1;
        return (current + delta + visibleResults.length) % visibleResults.length;
      });
      return;
    }
    if (event.key === 'Enter') {
      const target = visibleResults[highlight];
      if (!target) return;
      event.preventDefault();
      addSupplier(target);
    }
  };

  const handleBlur = (event: FocusEvent<HTMLDivElement>) => {
    const next = event.relatedTarget;
    if (next instanceof Node && containerRef.current?.contains(next)) return;
    setOpen(false);
  };

  const chipsAreaClassName = value.length > CHIPS_SCROLL_THRESHOLD
    ? 'flex max-h-32 flex-wrap gap-2 overflow-y-auto'
    : 'flex flex-wrap gap-2';

  return (
    <div ref={containerRef} onBlur={handleBlur} className="space-y-2">
      {value.length > 0 && (
        <div className={chipsAreaClassName} data-testid="supplier-chips">
          {value.map((item) => (
            <span key={item.id} className="inline-flex items-center gap-1.5 rounded-xl border border-[#c2c6d2] bg-[#eef6ff] px-2 py-1 text-xs font-bold text-[#101d28] dark:border-slate-600 dark:bg-slate-800 dark:text-white">
              <span className="font-mono text-[11px] font-bold text-[#007aff] dark:text-sky-300">{item.supplier_code}</span>
              <span className="font-normal">· {item.legal_name}</span>
              <button type="button" aria-label={`移除 ${item.legal_name}`} disabled={disabled} onClick={() => removeSupplier(item)} className="flex h-4 w-4 items-center justify-center rounded text-[#424751] hover:bg-white hover:text-[#C92A2A] focus:outline-none focus:ring-2 focus:ring-[#007aff] disabled:opacity-40 dark:text-slate-300 dark:hover:bg-slate-700 dark:hover:text-red-400">
                <X aria-hidden="true" className="h-3.5 w-3.5" />
              </button>
            </span>
          ))}
        </div>
      )}
      <div className="relative">
        <label htmlFor={searchId} className="sr-only">搜索供应商</label>
        <Search aria-hidden="true" className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-[#727782] dark:text-slate-400" />
        <input
          id={searchId}
          type="text"
          role="combobox"
          aria-expanded={open}
          aria-controls={listboxId}
          aria-autocomplete="list"
          aria-activedescendant={open && visibleResults[highlight] ? `${listboxId}-option-${visibleResults[highlight].id}` : undefined}
          value={keyword}
          disabled={disabled}
          placeholder="输入供应商编码或名称搜索"
          onChange={(event) => { setKeyword(event.target.value); setOpen(true); setHighlight(0); }}
          onFocus={() => setOpen(true)}
          onKeyDown={handleKeyDown}
          className="min-h-11 w-full rounded-lg border border-[#c2c6d2] bg-[#f1f5f9] pl-9 pr-3 text-sm text-[#101d28] outline-none focus:ring-2 focus:ring-[#007aff] disabled:cursor-not-allowed disabled:opacity-60 dark:border-slate-700 dark:bg-slate-800 dark:text-white"
        />
        {open && (
          <ul id={listboxId} role="listbox" aria-label="供应商搜索结果" className="absolute z-20 mt-1 max-h-64 w-full overflow-y-auto rounded-xl border border-[#c2c6d2] bg-white py-1 shadow-lg dark:border-slate-700 dark:bg-slate-900">
            {loading && <li role="option" aria-selected="false" aria-disabled="true" className="px-3 py-2 text-xs text-[#424751] dark:text-slate-300">搜索中…</li>}
            {!loading && searchError && <li role="option" aria-selected="false" aria-disabled="true" className="px-3 py-2 text-xs text-[#93000a] dark:text-red-300">{searchError}</li>}
            {!loading && !searchError && keyword.trim() === '' && <li role="option" aria-selected="false" aria-disabled="true" className="px-3 py-2 text-xs text-[#727782] dark:text-slate-400">输入关键字搜索供应商</li>}
            {!loading && !searchError && keyword.trim() !== '' && visibleResults.length === 0 && <li role="option" aria-selected="false" aria-disabled="true" className="px-3 py-2 text-xs text-[#727782] dark:text-slate-400">未找到匹配供应商</li>}
            {!loading && visibleResults.map((item, index) => (
              <li
                key={item.id}
                id={`${listboxId}-option-${item.id}`}
                role="option"
                aria-selected={index === highlight}
                onMouseDown={(event) => { event.preventDefault(); addSupplier(item); }}
                onMouseEnter={() => setHighlight(index)}
                className={`cursor-pointer px-3 py-2 text-sm text-[#101d28] dark:text-white ${index === highlight ? 'bg-[#eef6ff] dark:bg-slate-800' : ''}`}
              >
                <span className="font-mono text-xs text-[#007aff] dark:text-sky-300">{item.supplier_code}</span>
                <span className="ml-1.5">{item.legal_name}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
};
