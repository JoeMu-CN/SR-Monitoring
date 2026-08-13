import {useState, type FormEvent} from 'react';

interface LoginViewProps {
  onSubmit: (username: string, password: string) => Promise<void>;
  error: string | null;
}

export function LoginView({onSubmit, error}: LoginViewProps) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!username.trim() || !password || submitting) return;
    setSubmitting(true);
    try {
      await onSubmit(username.trim(), password);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className="min-h-screen bg-[#f7f9ff] dark:bg-[#101d28] flex items-center justify-center p-5 text-[#101d28] dark:text-slate-100">
      <section className="w-full max-w-md bg-white dark:bg-slate-900 border border-[#c2c6d2] dark:border-slate-800 rounded-2xl shadow-xl p-7">
        <div className="flex items-center gap-3 mb-8">
          <img src="/logo.svg" alt="SR Monitoring" className="w-12 h-12 rounded-xl" />
          <div>
            <h1 className="text-xl font-extrabold">供应商风险监控平台</h1>
            <p className="text-xs text-slate-500 mt-1">登录后访问风险数据与助手</p>
          </div>
        </div>
        <form onSubmit={submit} className="space-y-4">
          <label className="block text-sm font-bold">
            用户名
            <input autoComplete="username" value={username} onChange={(event) => setUsername(event.target.value)}
              className="mt-1.5 w-full rounded-xl border border-[#c2c6d2] dark:border-slate-700 bg-[#f7f9ff] px-4 py-3 font-normal text-slate-900 placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-[#004782]"
              placeholder="请输入用户名" />
          </label>
          <label className="block text-sm font-bold">
            密码
            <input type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)}
              className="mt-1.5 w-full rounded-xl border border-[#c2c6d2] dark:border-slate-700 bg-[#f7f9ff] px-4 py-3 font-normal text-slate-900 placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-[#004782]"
              placeholder="请输入密码" />
          </label>
          {error && <div role="alert" className="rounded-xl border border-red-200 bg-red-50 text-red-700 px-4 py-3 text-sm">{error}</div>}
          <button type="submit" disabled={submitting || !username.trim() || !password}
            className="w-full rounded-xl bg-[#004782] hover:bg-[#185fa5] text-white font-bold py-3 disabled:opacity-50">
            {submitting ? '登录中…' : '登录'}
          </button>
        </form>
      </section>
    </main>
  );
}
