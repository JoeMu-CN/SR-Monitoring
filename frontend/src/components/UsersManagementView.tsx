import {useEffect, useRef, useState} from 'react';
import {AlertTriangle, KeyRound, Pencil, RefreshCw, UserPlus, Users} from 'lucide-react';
import {api, ApiError, type AuthUser, type UserRole, type UserStatus} from '../api';
import {UserEditorModal} from './UserEditorModal';
import {UserPasswordResetModal} from './UserPasswordResetModal';

const roleLabels: Record<UserRole, string> = {viewer: '查看者', risk_analyst: '风险分析员', risk_admin: '风险管理员', platform_admin: '平台管理员'};
const statusLabels: Record<UserStatus, string> = {pending: '待激活', active: '已启用', disabled: '已停用'};
const statusClasses: Record<UserStatus, string> = {pending: 'bg-amber-50 text-amber-900 dark:bg-amber-950/40 dark:text-amber-100', active: 'bg-green-50 text-green-800 dark:bg-green-950/40 dark:text-green-200', disabled: 'bg-slate-100 text-[#101d28] dark:bg-slate-800 dark:text-white'};

interface UsersManagementViewProps {
  readonly currentUser: AuthUser;
  readonly onRequestError: (error: ApiError) => void;
  readonly onCurrentUserUpdated?: (user: AuthUser) => void;
}

type EditorState = {readonly mode: 'create'} | {readonly mode: 'edit'; readonly user: AuthUser};

const formatDate = (value: string | null) => {
  if (!value) return '从未登录';
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? '—' : new Intl.DateTimeFormat('zh-CN', {dateStyle: 'medium', timeStyle: 'short'}).format(date);
};

const UserActions = ({user, currentUser, onEdit, onReset}: {readonly user: AuthUser; readonly currentUser: AuthUser; readonly onEdit: () => void; readonly onReset: () => void}) => (
  <div className="flex flex-wrap gap-2">
    <button type="button" onClick={onEdit} className="inline-flex min-h-11 items-center justify-center gap-1.5 rounded-lg border border-[#c2c6d2] bg-white px-3 text-sm font-bold text-[#007aff] hover:bg-[#eef6ff] focus:outline-none focus:ring-2 focus:ring-[#007aff] dark:border-slate-600 dark:bg-slate-900"><Pencil aria-hidden="true" className="h-4 w-4" />编辑</button>
    {user.id === currentUser.id ? <span className="flex min-h-11 items-center text-xs text-[#727782] dark:text-slate-400">请使用个人设置修改本人密码</span> : <button type="button" onClick={onReset} className="inline-flex min-h-11 items-center justify-center gap-1.5 rounded-lg border border-[#c2c6d2] bg-white px-3 text-sm font-bold text-[#424751] hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-[#007aff] dark:border-slate-600 dark:bg-slate-900 dark:text-slate-200"><KeyRound aria-hidden="true" className="h-4 w-4" />重置密码</button>}
  </div>
);

export const UsersManagementView = ({currentUser, onRequestError, onCurrentUserUpdated}: UsersManagementViewProps) => {
  const [users, setUsers] = useState<readonly AuthUser[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const [retryKey, setRetryKey] = useState(0);
  const [editor, setEditor] = useState<EditorState | null>(null);
  const [resetUser, setResetUser] = useState<AuthUser | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const listGenerationRef = useRef(0);

  useEffect(() => {
    const generation = ++listGenerationRef.current;
    let active = true;
    setLoading(true);
    setError(null);
    void api.auth.listUsers().then((nextUsers) => {
      if (!active) return;
      if (generation === listGenerationRef.current) {
        setUsers(nextUsers);
      }
      setLoading(false);
    }).catch((caught: unknown) => {
      if (!active) return;
      if (generation === listGenerationRef.current) {
        const nextError = caught instanceof Error ? caught : new Error('用户列表加载失败');
        if (nextError instanceof ApiError && (nextError.status === 401 || nextError.status === 403)) onRequestError(nextError);
        setError(nextError);
      }
      setLoading(false);
    });
    return () => { active = false; };
  }, [onRequestError, retryKey]);

  const saveUser = (savedUser: AuthUser, sessionRevoked: boolean) => {
    setUsers((current) => current === null ? [savedUser] : current.some((user) => user.id === savedUser.id) ? current.map((user) => user.id === savedUser.id ? savedUser : user) : [...current, savedUser]);
    setNotice(sessionRevoked ? `已保存 ${savedUser.username} 的权限或状态更改；该用户现有会话已撤销。` : `已保存 ${savedUser.username} 的资料。`);
    setEditor(null);
    listGenerationRef.current += 1;
    if (onCurrentUserUpdated && savedUser.id === currentUser.id) {
      onCurrentUserUpdated(savedUser);
    }
  };

  const resetPassword = (user: AuthUser) => {
    setNotice(`已重置 ${user.username} 的密码；该用户现有会话已撤销。`);
    setResetUser(null);
  };

  return (
    <div className="space-y-5 pb-20 lg:pb-8">
      <header className="flex flex-col items-start justify-between gap-4 sm:flex-row sm:items-center">
        <div><h1 className="text-xl font-black tracking-tight text-slate-900 lg:text-2xl dark:text-white">用户管理</h1><p className="mt-0.5 text-xs text-[#424751] dark:text-slate-400">管理平台账号、角色和访问状态。</p></div>
        <button type="button" onClick={() => setEditor({mode: 'create'})} className="inline-flex min-h-11 w-full items-center justify-center gap-2 rounded-xl bg-[#007aff] px-4 text-sm font-bold text-white hover:bg-[#0062cc] focus:outline-none focus:ring-2 focus:ring-[#007aff] focus:ring-offset-2 sm:w-auto"><UserPlus aria-hidden="true" className="h-4 w-4" />创建用户</button>
      </header>
      {notice && <div role="status" className="flex items-start justify-between gap-3 rounded-xl border border-green-200 bg-green-50 p-3 text-sm text-green-800 dark:border-green-900 dark:bg-green-950/40 dark:text-green-200"><span>{notice}</span><button type="button" aria-label="关闭提示" onClick={() => setNotice(null)} className="min-h-11 px-2 text-xs font-bold underline focus:outline-none focus:ring-2 focus:ring-[#007aff]">关闭</button></div>}
      <section className="overflow-hidden rounded-2xl border border-slate-200/80 bg-white/80 shadow-sm dark:border-slate-700/60 dark:bg-slate-800/60">
        <div className="flex items-center gap-3 border-b border-slate-200/80 p-4 dark:border-slate-700"><span className="flex h-10 w-10 items-center justify-center rounded-xl bg-[#eef6ff] text-[#007aff] dark:bg-slate-800"><Users aria-hidden="true" className="h-5 w-5" /></span><div><h2 className="text-sm font-bold text-[#101d28] dark:text-white">平台用户</h2><p className="text-xs text-[#727782] dark:text-slate-400">角色和状态变更会撤销目标用户会话。</p></div></div>
        {loading && <div className="p-8 text-center text-sm text-slate-500" role="status">正在加载用户列表…</div>}
        {!loading && error && <div className="p-8 text-center"><p role="alert" className="flex justify-center gap-2 text-sm font-bold text-[#93000a]"><AlertTriangle aria-hidden="true" className="h-4 w-4" />用户列表加载失败</p><p className="mt-1 text-xs text-[#727782] dark:text-slate-400">{error.message}</p><button type="button" onClick={() => setRetryKey((value) => value + 1)} className="mt-4 inline-flex min-h-11 items-center gap-2 rounded-xl bg-[#007aff] px-4 text-sm font-bold text-white hover:bg-[#0062cc] focus:outline-none focus:ring-2 focus:ring-[#007aff]"><RefreshCw aria-hidden="true" className="h-4 w-4" />重试</button></div>}
        {!loading && !error && users?.length === 0 && <div className="p-8 text-center"><p className="text-sm font-bold text-[#101d28] dark:text-white">暂无用户</p><p className="mt-1 text-xs text-[#727782] dark:text-slate-400">创建第一个平台用户以分配访问权限。</p></div>}
        {!loading && !error && users && users.length > 0 && <>
          <div className="hidden overflow-x-auto md:block"><table aria-label="平台用户列表" className="w-full min-w-[900px] border-collapse text-left text-sm"><thead className="bg-slate-100/70 text-xs font-bold text-slate-600 dark:bg-slate-800/80 dark:text-slate-300"><tr><th className="p-3">用户名</th><th className="p-3">姓名与邮箱</th><th className="p-3">角色</th><th className="p-3">状态</th><th className="p-3">上次登录</th><th className="p-3">创建时间</th><th className="p-3">操作</th></tr></thead><tbody className="divide-y divide-[#c2c6d2]/50">{users.map((user) => <tr key={user.id}><td className="p-3 font-mono font-bold text-[#101d28] dark:text-white">{user.username}</td><td className="p-3"><p className="break-words font-medium text-[#101d28] dark:text-white">{user.display_name || '—'}</p><p className="break-all text-xs text-[#727782] dark:text-slate-400">{user.email || '—'}</p></td><td className="p-3">{roleLabels[user.role]}</td><td className="p-3"><span className={`inline-flex rounded-full px-2 py-1 text-xs font-bold ${statusClasses[user.status]}`}>{statusLabels[user.status]}</span></td><td className="p-3 font-mono text-xs text-[#424751] dark:text-slate-300">{formatDate(user.last_login_at)}</td><td className="p-3 font-mono text-xs text-[#424751] dark:text-slate-300">{formatDate(user.created_at)}</td><td className="p-3"><UserActions user={user} currentUser={currentUser} onEdit={() => setEditor({mode: 'edit', user})} onReset={() => setResetUser(user)} /></td></tr>)}</tbody></table></div>
          <div className="divide-y divide-[#c2c6d2]/50 md:hidden">{users.map((user) => <article key={user.id} className="space-y-3 p-4"><div className="min-w-0"><p className="break-words font-mono text-sm font-bold text-[#101d28] dark:text-white">{user.username}</p><p className="break-words text-sm text-[#424751] dark:text-slate-300">{user.display_name || '—'}</p><p className="break-all text-xs text-[#727782] dark:text-slate-400">{user.email || '—'}</p></div><div className="flex flex-wrap gap-2"><span className="rounded-full bg-[#eef6ff] px-2 py-1 text-xs font-bold text-[#007aff] dark:bg-slate-800">{roleLabels[user.role]}</span><span className={`rounded-full px-2 py-1 text-xs font-bold ${statusClasses[user.status]}`}>{statusLabels[user.status]}</span></div><dl className="grid grid-cols-2 gap-3 text-xs"><div><dt className="text-[#727782] dark:text-slate-400">上次登录</dt><dd className="mt-1 break-words font-mono text-[#424751] dark:text-slate-300">{formatDate(user.last_login_at)}</dd></div><div><dt className="text-[#727782] dark:text-slate-400">创建时间</dt><dd className="mt-1 break-words font-mono text-[#424751] dark:text-slate-300">{formatDate(user.created_at)}</dd></div></dl><UserActions user={user} currentUser={currentUser} onEdit={() => setEditor({mode: 'edit', user})} onReset={() => setResetUser(user)} /></article>)}</div>
        </>}
      </section>
      {editor?.mode === 'edit'
        ? <UserEditorModal isOpen mode="edit" user={editor.user} isCurrentUser={editor.user.id === currentUser.id} onClose={() => setEditor(null)} onSaved={saveUser} onRequestError={onRequestError} />
        : <UserEditorModal isOpen={editor !== null} mode="create" onClose={() => setEditor(null)} onSaved={saveUser} onRequestError={onRequestError} />}
      <UserPasswordResetModal isOpen={resetUser !== null} user={resetUser} onClose={() => setResetUser(null)} onReset={resetPassword} onRequestError={onRequestError} />
    </div>
  );
};
