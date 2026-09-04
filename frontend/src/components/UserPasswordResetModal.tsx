import {useEffect, useRef, useState, type FormEvent} from 'react';
import {AlertTriangle, KeyRound, X} from 'lucide-react';
import {api, ApiError, type AuthUser} from '../api';
import {useDialogFocus} from '../hooks/useDialogFocus';

interface UserPasswordResetModalProps {
  readonly isOpen: boolean;
  readonly user: AuthUser | null;
  readonly onClose: () => void;
  readonly onReset: (user: AuthUser) => void;
  readonly onRequestError: (error: ApiError) => void;
}

export const UserPasswordResetModal = ({isOpen, user, onClose, onReset, onRequestError}: UserPasswordResetModalProps) => {
  const passwordRef = useRef<HTMLInputElement>(null);
  const [newPassword, setNewPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const {dialogRef} = useDialogFocus({isOpen, disabled: saving, onClose});

  useEffect(() => {
    if (!isOpen) return;
    setNewPassword('');
    setError(null);
    passwordRef.current?.focus();
  }, [isOpen, user]);

  if (!isOpen || !user) return null;

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      await api.auth.resetPassword(user.id, {new_password: newPassword});
      onReset(user);
    } catch (caught) {
      if (caught instanceof ApiError) {
        setError(caught.message);
        if (caught.status === 401 || caught.status === 403) onRequestError(caught);
      } else if (caught instanceof Error) setError(caught.message);
      else throw caught;
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/60 p-4 backdrop-blur-xs">
      <section ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="user-password-reset-title" className="w-full max-w-md rounded-2xl border border-slate-200 bg-white shadow-2xl dark:border-slate-700 dark:bg-slate-900">
        <header className="flex items-start justify-between gap-4 border-b border-slate-200 p-5 dark:border-slate-700">
          <div className="min-w-0"><h2 id="user-password-reset-title" className="text-lg font-bold text-[#101d28] dark:text-white">重置密码</h2><p className="mt-1 text-sm text-[#424751] dark:text-slate-400">为 {user.username} 设置新密码后，该用户现有会话会立即撤销。</p></div>
          <button type="button" aria-label="关闭重置密码弹窗" onClick={onClose} disabled={saving} className="flex min-h-11 min-w-11 items-center justify-center rounded-lg text-slate-500 hover:bg-slate-100 focus:outline-none focus:ring-2 focus:ring-[#007aff] disabled:opacity-40 dark:hover:bg-slate-800"><X aria-hidden="true" className="h-5 w-5" /></button>
        </header>
        <form onSubmit={(event) => void handleSubmit(event)} className="space-y-4 p-5">
          <label className="block text-sm font-bold text-[#101d28] dark:text-white">新密码
            <input ref={passwordRef} value={newPassword} onChange={(event) => setNewPassword(event.target.value)} disabled={saving} required type="password" autoComplete="new-password" className="mt-1.5 min-h-11 w-full rounded-lg border border-[#c2c6d2] bg-[#f1f5f9] px-3 text-sm text-[#101d28] outline-none focus:ring-2 focus:ring-[#007aff] disabled:opacity-60 dark:border-slate-700 dark:bg-slate-800 dark:text-white" />
          </label>
          {error && <p role="alert" className="flex items-start gap-2 rounded-xl border border-red-200 bg-[#ffdad6] p-3 text-sm text-[#93000a]"><AlertTriangle aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0" />{error}</p>}
          <footer className="flex flex-col-reverse gap-3 border-t border-slate-200 pt-4 sm:flex-row sm:justify-end dark:border-slate-700">
            <button type="button" onClick={onClose} disabled={saving} className="min-h-11 rounded-xl border border-[#c2c6d2] px-5 text-sm font-bold text-[#424751] hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-[#007aff] disabled:opacity-40 dark:border-slate-600 dark:text-slate-200 dark:hover:bg-slate-800">取消</button>
            <button type="submit" disabled={saving} className="inline-flex min-h-11 items-center justify-center gap-2 rounded-xl bg-[#007aff] px-5 text-sm font-bold text-white hover:bg-[#0062cc] focus:outline-none focus:ring-2 focus:ring-[#007aff] focus:ring-offset-2 disabled:opacity-40"><KeyRound aria-hidden="true" className="h-4 w-4" />{saving ? '正在重置…' : '确认重置密码'}</button>
          </footer>
        </form>
      </section>
    </div>
  );
};
