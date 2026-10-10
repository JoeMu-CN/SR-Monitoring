import {useEffect, useRef, useState} from 'react';
import {ArrowLeft} from 'lucide-react';
import {Link, useParams, useSearchParams} from 'react-router-dom';
import {api, ApiError, VALIDITY_MODE_LABELS, type SourceSignalListResponse} from '../api';
import {routePaths, type SourceSignalScope} from '../routes';
import {SourceSignalsTable} from './SourceSignalsTable';
import {SourceSignalReportModal, type ActiveSourceSignal} from './SourceSignalReportModal';

const PAGE_SIZE = 100;

interface SourceSignalsViewProps {
  readonly onRequestError: (error: ApiError) => void;
}

export const SourceSignalsView = ({onRequestError}: SourceSignalsViewProps) => {
  const {sourceId = ''} = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const requestSequence = useRef(0);
  const [data, setData] = useState<SourceSignalListResponse | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(true);
  const [retryKey, setRetryKey] = useState(0);
  const [activeSignal, setActiveSignal] = useState<ActiveSourceSignal | null>(null);

  const rawScope = searchParams.get('scope');
  const rawPage = searchParams.get('page');
  const scope: SourceSignalScope = rawScope === 'all' ? 'all' : 'valid';
  const page = rawPage !== null && /^[1-9]\d*$/.test(rawPage) ? Number(rawPage) : 1;
  const isCanonical = rawScope === scope && rawPage === String(page) && Number.isSafeInteger(page);
  const numericSourceId = Number(sourceId);

  useEffect(() => {
    if (!isCanonical) setSearchParams({scope: 'valid', page: '1'}, {replace: true});
  }, [isCanonical, setSearchParams]);

  useEffect(() => {
    if (!isCanonical) return;
    const sequence = requestSequence.current + 1;
    requestSequence.current = sequence;
    setLoading(true);
    setError(null);
    setData(null);
    setActiveSignal(null);

    if (!Number.isSafeInteger(numericSourceId) || numericSourceId < 1) {
      setError(new ApiError(404, '信息源不存在'));
      setLoading(false);
      return;
    }

    void api.sourceSignals(numericSourceId, scope, (page - 1) * PAGE_SIZE, PAGE_SIZE)
      .then((response) => {
        if (requestSequence.current !== sequence) return;
        setData(response);
        setLoading(false);
      })
      .catch((caught: unknown) => {
        if (requestSequence.current !== sequence) return;
        const nextError = caught instanceof Error ? caught : new Error('采集记录加载失败');
        if (nextError instanceof ApiError && (nextError.status === 401 || nextError.status === 403)) {
          onRequestError(nextError);
        }
        setError(nextError);
        setLoading(false);
      });

    return () => {
      if (requestSequence.current === sequence) requestSequence.current += 1;
    };
  }, [isCanonical, numericSourceId, onRequestError, page, retryKey, scope]);

  const changeScope = (nextScope: SourceSignalScope) => {
    if (nextScope !== scope) setSearchParams({scope: nextScope, page: '1'});
  };

  const changePage = (nextPage: number) => {
    setSearchParams({scope, page: String(nextPage)});
  };

  if (loading || !isCanonical) {
    return (
      <div role="status" className="flex min-h-[50vh] items-center justify-center text-slate-600 dark:text-slate-300">
        <span className="sr-only">正在加载采集记录…</span>
      </div>
    );
  }

  if (error) {
    const status = error instanceof ApiError ? error.status : null;
    const title = status === 404 ? '信息源不存在' : status === 403 ? '无权访问采集记录' : '采集记录加载失败';
    return (
      <section role="alert" className="mx-auto flex min-h-[50vh] max-w-xl flex-col items-start justify-center gap-4 rounded-2xl border border-slate-200 bg-white p-6 shadow-sm dark:border-slate-700 dark:bg-slate-800">
        <h1 className="text-xl font-black text-slate-900 dark:text-white">{title}</h1>
        <p className="text-sm text-slate-600 dark:text-slate-300">{error.message}</p>
        <div className="flex flex-wrap gap-2">
          {status !== 404 && status !== 403 && (
            <button type="button" onClick={() => setRetryKey((value) => value + 1)} className="rounded-xl bg-blue-600 px-4 py-2 text-sm font-bold text-white hover:bg-blue-700">重试</button>
          )}
          <Link to={routePaths.sources} className="rounded-xl border border-slate-300 px-4 py-2 text-sm font-bold text-blue-700 hover:bg-blue-50 dark:border-slate-600 dark:text-blue-300 dark:hover:bg-slate-700">返回信息源清单</Link>
        </div>
      </section>
    );
  }

  if (data === null) return null;
  const firstItem = data.total === 0 || data.items.length === 0 ? 0 : data.offset + 1;
  const lastItem = data.offset + data.items.length;
  const hasNextPage = lastItem < data.total;

  return (
    <div className="space-y-5 pb-20 lg:pb-8">
      <header className="space-y-3">
        <Link to={routePaths.sources} className="inline-flex min-h-11 items-center gap-2 rounded-lg text-sm font-bold text-blue-700 hover:underline dark:text-blue-300">
          <ArrowLeft aria-hidden="true" className="h-4 w-4" />返回信息源清单
        </Link>
        <div>
          <h1 className="text-xl font-black tracking-tight text-slate-900 lg:text-2xl dark:text-white">{data.source.name} · 已采集记录</h1>
          <p className="mt-1 text-xs text-slate-600 dark:text-slate-400">
            编码 <span className="font-mono font-bold">{data.source.code}</span> · {data.source.validity_policy
              ? `${VALIDITY_MODE_LABELS[data.source.validity_policy.mode] ?? data.source.validity_policy.mode}${data.source.validity_policy.fixed_days != null ? ` ${data.source.validity_policy.fixed_days} 天` : ''}`
              : (data.source.signal_validity_days === null ? '记录永久有效' : `记录有效期 ${data.source.signal_validity_days} 天`)}
          </p>
        </div>
      </header>

      <section className="rounded-2xl border border-slate-200 bg-white shadow-sm dark:border-slate-700 dark:bg-slate-800" aria-label="采集记录清单">
        <div className="flex flex-col gap-3 border-b border-slate-200 p-4 sm:flex-row sm:items-center sm:justify-between dark:border-slate-700">
          <div role="tablist" aria-label="记录范围" className="flex rounded-xl bg-slate-100 p-1 dark:bg-slate-900">
            {(['valid', 'all'] as const).map((itemScope) => (
              <button
                key={itemScope}
                type="button"
                role="tab"
                aria-selected={scope === itemScope}
                onClick={() => changeScope(itemScope)}
                className={`min-h-10 rounded-lg px-4 text-sm font-bold ${scope === itemScope ? 'bg-white text-blue-700 shadow-sm dark:bg-slate-700 dark:text-blue-300' : 'text-slate-600 hover:text-slate-900 dark:text-slate-400 dark:hover:text-white'}`}
              >
                {itemScope === 'valid' ? '当前有效' : '全部历史'}
              </button>
            ))}
          </div>
          <p className="text-xs text-slate-600 dark:text-slate-400" aria-live="polite">显示 {firstItem}-{lastItem}，共 {data.total.toLocaleString()} 条</p>
        </div>

        {data.items.length === 0 ? (
          <div className="p-10 text-center">
            <h2 className="text-base font-bold text-slate-900 dark:text-white">{scope === 'valid' ? '暂无当前有效记录' : '暂无历史采集记录'}</h2>
            <p className="mt-2 text-sm text-slate-600 dark:text-slate-400">{scope === 'valid' ? '可切换到全部历史，查看已过期的留存记录。' : '该信息源尚未采集到记录。'}</p>
          </div>
        ) : (
          <SourceSignalsTable source={data.source} items={data.items} offset={data.offset} onOpenReport={setActiveSignal} />
        )}

        <nav aria-label="采集记录分页" className="flex items-center justify-between border-t border-slate-200 p-4 dark:border-slate-700">
          <button type="button" onClick={() => changePage(page - 1)} disabled={page <= 1} className="min-h-11 rounded-xl border border-slate-300 px-4 text-sm font-bold text-slate-700 disabled:cursor-not-allowed disabled:opacity-40 dark:border-slate-600 dark:text-slate-200">上一页</button>
          <span className="font-mono text-xs text-slate-600 dark:text-slate-400">第 {page} 页</span>
          <button type="button" onClick={() => changePage(page + 1)} disabled={!hasNextPage} className="min-h-11 rounded-xl border border-slate-300 px-4 text-sm font-bold text-slate-700 disabled:cursor-not-allowed disabled:opacity-40 dark:border-slate-600 dark:text-slate-200">下一页</button>
        </nav>
      </section>

      <SourceSignalReportModal
        sourceId={numericSourceId}
        activeSignal={activeSignal}
        onClose={() => setActiveSignal(null)}
        onRequestError={onRequestError}
      />
    </div>
  );
};