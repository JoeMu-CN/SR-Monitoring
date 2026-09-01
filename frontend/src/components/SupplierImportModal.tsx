import {useEffect, useRef, useState} from 'react';
import {AlertTriangle, CheckCircle2, Download, FileSpreadsheet, Upload, X} from 'lucide-react';
import {
  SupplierImportError,
  supplierImportApi,
  type SupplierImportIssue,
  type SupplierImportSummary,
} from '../supplierImportApi';

const MAX_FILE_BYTES = 5 * 1024 * 1024;

interface SupplierImportModalProps {
  readonly isOpen: boolean;
  readonly onClose: () => void;
  readonly onImported: () => void | Promise<void>;
  readonly onRequestError: (error: SupplierImportError) => void;
}

const validateFile = (file: File): string | null => {
  if (!file.name.toLowerCase().endsWith('.xlsx')) return '仅支持 .xlsx 文件';
  if (file.size === 0) return '文件内容不能为空';
  if (file.size > MAX_FILE_BYTES) return '文件大小不能超过 5 MB';
  return null;
};

const Summary = ({value}: {readonly value: SupplierImportSummary}) => {
  const metrics = [
    ['新增供应商', value.createdSuppliers],
    ['更新供应商', value.updatedSuppliers],
    ['别名', value.aliases],
    ['生产地点', value.sites],
    ['供应产品', value.products],
  ] as const;
  return (
    <section aria-labelledby="supplier-import-success" className="rounded-xl border border-green-200 bg-green-50 p-4 dark:border-green-900 dark:bg-green-950/40">
      <div className="flex items-center gap-2 text-green-800 dark:text-green-300">
        <CheckCircle2 aria-hidden="true" className="h-5 w-5" />
        <h3 id="supplier-import-success" className="text-sm font-bold">导入完成</h3>
      </div>
      <dl className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-5">
        {metrics.map(([label, count]) => (
          <div key={label} className="min-w-0">
            <dt className="text-xs text-slate-600 dark:text-slate-400">{label}</dt>
            <dd className="mt-1 font-mono text-lg font-bold text-slate-900 dark:text-white">{count}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
};

const IssueTable = ({issues}: {readonly issues: readonly SupplierImportIssue[]}) => (
  <div className="overflow-x-auto rounded-xl border border-red-200 dark:border-red-900">
    <table aria-label="导入错误明细" className="w-full min-w-[560px] border-collapse text-left text-xs">
      <thead className="bg-red-50 text-red-900 dark:bg-red-950/50 dark:text-red-200">
        <tr><th className="p-3">工作表</th><th className="p-3">行</th><th className="p-3">字段</th><th className="p-3">错误说明</th></tr>
      </thead>
      <tbody className="divide-y divide-red-100 dark:divide-red-900">
        {issues.map((issue, index) => (
          <tr key={`${issue.sheet}-${issue.row ?? 0}-${issue.field ?? ''}-${index}`}>
            <td className="p-3 font-bold">{issue.sheet}</td>
            <td className="p-3 font-mono">{issue.row ?? '—'}</td>
            <td className="p-3">{issue.field ?? '—'}</td>
            <td className="max-w-md break-words p-3">{issue.message}</td>
          </tr>
        ))}
      </tbody>
    </table>
  </div>
);

export const SupplierImportModal = ({isOpen, onClose, onImported, onRequestError}: SupplierImportModalProps) => {
  const titleRef = useRef<HTMLHeadingElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [summary, setSummary] = useState<SupplierImportSummary | null>(null);
  const [issues, setIssues] = useState<readonly SupplierImportIssue[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [downloading, setDownloading] = useState(false);

  useEffect(() => {
    if (!isOpen) return;
    setFile(null);
    setSummary(null);
    setIssues([]);
    setError(null);
    titleRef.current?.focus();
  }, [isOpen]);

  useEffect(() => {
    if (!isOpen) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !uploading) onClose();
    };
    window.addEventListener('keydown', closeOnEscape);
    return () => window.removeEventListener('keydown', closeOnEscape);
  }, [isOpen, onClose, uploading]);

  if (!isOpen) return null;

  const handleFile = (selected: File | null) => {
    setSummary(null);
    setIssues([]);
    setFile(selected);
    setError(selected === null ? null : validateFile(selected));
  };

  const handleDownload = async () => {
    setDownloading(true);
    setError(null);
    try {
      const blob = await supplierImportApi.downloadTemplate();
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = '供应商导入模板.xlsx';
      link.click();
      URL.revokeObjectURL(url);
    } catch (caught) {
      if (caught instanceof SupplierImportError) {
        setError(caught.message);
        if (caught.status === 401 || caught.status === 403) onRequestError(caught);
      } else if (caught instanceof Error) setError(caught.message);
      else throw caught;
    } finally {
      setDownloading(false);
    }
  };

  const handleUpload = async () => {
    if (file === null) {
      setError('请先选择要导入的 .xlsx 文件');
      return;
    }
    const validationError = validateFile(file);
    if (validationError !== null) {
      setError(validationError);
      return;
    }
    setUploading(true);
    setError(null);
    setIssues([]);
    try {
      const result = await supplierImportApi.upload(file);
      setSummary(result);
      await onImported();
    } catch (caught) {
      if (caught instanceof SupplierImportError) {
        setError(caught.message);
        setIssues(caught.issues);
        if (caught.status === 401 || caught.status === 403) onRequestError(caught);
      } else if (caught instanceof Error) setError(caught.message);
      else throw caught;
    } finally {
      setUploading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/60 p-4 backdrop-blur-xs">
      <section role="dialog" aria-modal="true" aria-labelledby="supplier-import-title" className="flex max-h-[calc(100dvh-2rem)] w-full max-w-3xl flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-2xl dark:border-slate-700 dark:bg-slate-900">
        <header className="flex items-start justify-between gap-4 border-b border-slate-200 p-5 dark:border-slate-700">
          <div className="flex min-w-0 items-start gap-3">
            <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-[#eef6ff] text-[#007aff] dark:bg-blue-950"><FileSpreadsheet aria-hidden="true" className="h-5 w-5" /></span>
            <div className="min-w-0">
              <h2 ref={titleRef} tabIndex={-1} id="supplier-import-title" className="text-lg font-bold text-[#101d28] outline-none dark:text-white">Excel 导入供应商</h2>
              <p className="mt-1 text-sm text-[#424751] dark:text-slate-400">使用标准三工作表模板，<span className="inline-block whitespace-nowrap">校验通过后</span>统一新增或更新。</p>
            </div>
          </div>
          <button type="button" aria-label="关闭导入弹窗" onClick={onClose} disabled={uploading} className="flex min-h-11 min-w-11 items-center justify-center rounded-lg text-slate-500 hover:bg-slate-100 focus:outline-none focus:ring-2 focus:ring-[#007aff] disabled:opacity-40 dark:hover:bg-slate-800"><X aria-hidden="true" className="h-5 w-5" /></button>
        </header>

        <div className="space-y-4 overflow-y-auto p-5">
          <div className="flex flex-col gap-3 rounded-xl border border-[#c2c6d2] bg-[#f1f5f9] p-4 sm:flex-row sm:items-center sm:justify-between dark:border-slate-700 dark:bg-slate-800">
            <div><h3 className="text-sm font-bold text-[#101d28] dark:text-white">先下载标准模板</h3><p className="mt-1 text-xs text-[#424751] dark:text-slate-400">请勿修改工作表名称和表头；文件最大 5 MB。</p></div>
            <button type="button" onClick={() => void handleDownload()} disabled={downloading || uploading} className="inline-flex min-h-11 shrink-0 items-center justify-center gap-2 rounded-xl border border-[#c2c6d2] bg-white px-4 text-sm font-bold text-[#007aff] hover:bg-[#eef6ff] focus:outline-none focus:ring-2 focus:ring-[#007aff] disabled:opacity-40 dark:border-slate-600 dark:bg-slate-900"><Download aria-hidden="true" className="h-4 w-4" />{downloading ? '正在下载…' : '下载标准模板'}</button>
          </div>

          <label className="block cursor-pointer rounded-xl border border-dashed border-[#c2c6d2] p-5 text-center hover:bg-[#eef6ff]/60 focus-within:ring-2 focus-within:ring-[#007aff] dark:border-slate-600 dark:hover:bg-slate-800">
            <input type="file" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" aria-label="选择 Excel 文件" disabled={uploading} onChange={(event) => handleFile(event.target.files?.item(0) ?? null)} className="sr-only" />
            <Upload aria-hidden="true" className="mx-auto h-6 w-6 text-[#007aff]" />
            <span className="mt-2 block text-sm font-bold text-[#101d28] dark:text-white">选择 Excel 文件</span>
            <span className="mt-1 block text-xs text-[#727782]">仅支持 .xlsx，最大 5 MB</span>
          </label>

          {file !== null && <div className="flex min-w-0 items-center gap-3 rounded-xl border border-slate-200 p-3 dark:border-slate-700"><FileSpreadsheet aria-hidden="true" className="h-5 w-5 shrink-0 text-[#007aff]" /><div className="min-w-0"><p className="truncate text-sm font-bold text-[#101d28] dark:text-white">{file.name}</p><p className="text-xs text-[#727782]">{new Intl.NumberFormat('zh-CN', {maximumFractionDigits: 1}).format(file.size / 1024)} KB</p></div></div>}

          {error !== null && <div role="alert" className="flex items-start gap-2 rounded-xl border border-red-200 bg-[#ffdad6] p-3 text-sm text-[#93000a]"><AlertTriangle aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0" /><span>{error}</span></div>}
          {issues.length > 0 && <IssueTable issues={issues} />}
          {summary !== null && <Summary value={summary} />}
        </div>

        <footer className="flex flex-col-reverse gap-3 border-t border-slate-200 p-4 sm:flex-row sm:justify-end dark:border-slate-700">
          <button type="button" onClick={onClose} disabled={uploading} className="min-h-11 rounded-xl border border-[#c2c6d2] px-5 text-sm font-bold text-[#424751] hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-[#007aff] disabled:opacity-40 dark:border-slate-600 dark:text-slate-200 dark:hover:bg-slate-800">{summary === null ? '取消' : '完成'}</button>
          <button type="button" onClick={() => void handleUpload()} disabled={uploading || file === null || error !== null || summary !== null} className="inline-flex min-h-11 items-center justify-center gap-2 rounded-xl bg-[#007aff] px-5 text-sm font-bold text-white hover:bg-[#0062cc] focus:outline-none focus:ring-2 focus:ring-[#007aff] focus:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-40"><Upload aria-hidden="true" className="h-4 w-4" />{uploading ? '正在导入…' : '开始导入'}</button>
        </footer>
      </section>
    </div>
  );
};
