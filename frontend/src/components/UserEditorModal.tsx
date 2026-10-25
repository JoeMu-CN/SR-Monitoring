import {useEffect, useRef, useState, type FormEvent} from 'react';
import {AlertTriangle, Save, UserPlus, X} from 'lucide-react';
import {api, ApiError, type AuthUser, type UserCreatePayload, type UserRole, type UserStatus, type UserUpdatePayload} from '../api';
import {useDialogFocus} from '../hooks/useDialogFocus';
import {SupplierMultiSelect, type SupplierOption} from './SupplierMultiSelect';

const roleOptions: ReadonlyArray<readonly [UserRole, string]> = [
  ['viewer', '查看者'], ['risk_analyst', '风险分析员'], ['risk_admin', '风险管理员'], ['platform_admin', '平台管理员'],
];

const statusOptions: ReadonlyArray<readonly [UserStatus, string]> = [
  ['pending', '待激活'], ['active', '已启用'], ['disabled', '已停用'],
];

const isUserRole = (value: string): value is UserRole => roleOptions.some(([role]) => role === value);
const isUserStatus = (value: string): value is UserStatus => statusOptions.some(([status]) => status === value);

interface UserEditorModalBaseProps {
  readonly isOpen: boolean;
  readonly onClose: () => void;
  readonly onSaved: (user: AuthUser, sessionRevoked: boolean) => void;
  readonly onRequestError: (error: ApiError) => void;
}

type UserEditorModalProps = UserEditorModalBaseProps & (
  | {readonly mode: 'create'}
  | {readonly mode: 'edit'; readonly user: AuthUser; readonly isCurrentUser: boolean}
);

const fieldClassName = 'min-h-11 w-full rounded-lg border border-[#c2c6d2] bg-[#f1f5f9] px-3 text-sm text-[#101d28] outline-none focus:ring-2 focus:ring-[#007aff] disabled:cursor-not-allowed disabled:opacity-60 dark:border-slate-700 dark:bg-slate-800 dark:text-white';

/** 负责供应商多选控件的搜索框 id，供外层 label 关联。 */
const supplierSearchId = 'user-editor-supplier-search';

/** 按 id 集合比较两组供应商（顺序无关）。 */
const sameSupplierSet = (left: readonly number[], right: readonly number[]) => {
  if (left.length !== right.length) return false;
  const rightSet = new Set(right);
  return left.every((id) => rightSet.has(id));
};

export const UserEditorModal = (props: UserEditorModalProps) => {
  const {isOpen, onClose, onSaved, onRequestError} = props;
  const isEditing = props.mode === 'edit';
  const user = isEditing ? props.user : undefined;
  const isCurrentUser = isEditing && props.isCurrentUser;
  const usernameInputRef = useRef<HTMLInputElement>(null);
  const displayNameInputRef = useRef<HTMLInputElement>(null);
  const {dialogRef} = useDialogFocus({isOpen, disabled: false, onClose});
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [email, setEmail] = useState('');
  const [role, setRole] = useState<UserRole>('viewer');
  const [status, setStatus] = useState<UserStatus>('active');
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  // 负责供应商：已选项与初始值分开保存，切换角色离开 risk_analyst 时只保留在本地不提交。
  const [suppliers, setSuppliers] = useState<readonly SupplierOption[]>([]);
  const [initialSupplierIds, setInitialSupplierIds] = useState<readonly number[]>([]);
  const [supplierError, setSupplierError] = useState<string | null>(null);
  const supplierRequestRef = useRef(0);

  useEffect(() => {
    if (!isOpen) return;
    setUsername(user?.username ?? '');
    setPassword('');
    setDisplayName(user?.display_name ?? '');
    setEmail(user?.email ?? '');
    setRole(user?.role ?? 'viewer');
    setStatus(user?.status ?? 'active');
    setError(null);
    setSuppliers([]);
    setInitialSupplierIds([]);
    setSupplierError(null);
    if (!isEditing || !user) return;
    const requestId = ++supplierRequestRef.current;
    void api.auth.getUserSuppliers(user.id).then((items) => {
      if (requestId !== supplierRequestRef.current) return;
      const options = items.map((item) => ({id: item.id, supplier_code: item.supplier_code, legal_name: item.legal_name}));
      setSuppliers(options);
      setInitialSupplierIds(options.map((option) => option.id));
    }).catch((caught: unknown) => {
      if (requestId !== supplierRequestRef.current) return;
      setSupplierError(caught instanceof Error ? caught.message : '负责供应商加载失败');
    });
  }, [isOpen, isEditing, user]);

  useEffect(() => {
    if (!isOpen) return;
    if (isEditing) {
      displayNameInputRef.current?.focus();
    } else {
      usernameInputRef.current?.focus();
    }
  }, [isOpen, isEditing]);

  if (!isOpen) return null;

  const reportError = (caught: unknown) => {
    if (caught instanceof ApiError) {
      setError(caught.message);
      if (caught.status === 401 || caught.status === 403) onRequestError(caught);
      return;
    }
    if (caught instanceof Error) {
      setError(caught.message);
      return;
    }
    throw caught;
  };

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const supplierIds = suppliers.map((option) => option.id);
      if (!isEditing) {
        const payload: UserCreatePayload = {
          username: username.trim(), password, role,
          ...(displayName.trim() ? {display_name: displayName.trim()} : {}),
          ...(email.trim() ? {email: email.trim()} : {}),
        };
        const saved = await api.auth.createUser(payload);
        // 只有风险分析员存在负责供应商归属；其他角色不写入。
        if (role === 'risk_analyst') {
          await api.auth.updateUserSuppliers(saved.id, supplierIds);
        }
        onSaved(saved, false);
        return;
      }
      if (!user) return;
      const payload: UserUpdatePayload = {
        ...(displayName.trim() !== (user.display_name ?? '') ? {display_name: displayName.trim()} : {}),
        ...(email.trim() !== (user.email ?? '') ? {email: email.trim()} : {}),
        ...(!isCurrentUser && role !== user.role ? {role} : {}),
        ...(!isCurrentUser && status !== user.status ? {status} : {}),
      };
      // 离开 risk_analyst 角色时已选供应商保留在表单内但不提交。
      const supplierChanged = !sameSupplierSet(supplierIds, initialSupplierIds);
      const shouldSaveSuppliers = role === 'risk_analyst' && supplierChanged;
      if (Object.keys(payload).length === 0 && !shouldSaveSuppliers) {
        setError('未检测到可保存的更改');
        return;
      }
      const sessionRevoked = !isCurrentUser && (role !== user.role || status !== user.status);
      const saved = Object.keys(payload).length > 0 ? await api.auth.updateUser(user.id, payload) : user;
      if (shouldSaveSuppliers) {
        await api.auth.updateUserSuppliers(saved.id, supplierIds);
      }
      onSaved(saved, sessionRevoked);
    } catch (caught) {
      reportError(caught);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/60 p-4 backdrop-blur-xs">
      <section ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="user-editor-title" className="flex max-h-[calc(100dvh-2rem)] w-full max-w-xl flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-2xl dark:border-slate-700 dark:bg-slate-900">
        <header className="flex items-start justify-between gap-4 border-b border-slate-200 p-5 dark:border-slate-700">
          <div className="min-w-0">
            <h2 id="user-editor-title" className="text-lg font-bold text-[#101d28] dark:text-white">{isEditing ? '编辑用户' : '创建用户'}</h2>
            {isEditing && <p className="mt-1 text-sm text-[#424751] dark:text-slate-400">角色或状态变更将撤销该用户会话。</p>}
          </div>
          <button type="button" aria-label="关闭用户编辑弹窗" onClick={onClose} disabled={saving} className="flex min-h-11 min-w-11 items-center justify-center rounded-lg text-slate-500 hover:bg-slate-100 focus:outline-none focus:ring-2 focus:ring-[#007aff] disabled:opacity-40 dark:hover:bg-slate-800"><X aria-hidden="true" className="h-5 w-5" /></button>
        </header>
        <form onSubmit={(event) => void handleSubmit(event)} className="min-h-0 space-y-4 overflow-y-auto p-5">
          <div className="grid gap-4 sm:grid-cols-2">
            <label className="block text-sm font-bold text-[#101d28] dark:text-white">用户名
              <input ref={usernameInputRef} value={username} onChange={(event) => setUsername(event.target.value)} disabled={isEditing || saving} required autoComplete="username" className={`mt-1.5 ${fieldClassName}`} />
            </label>
            {!isEditing && <label className="block text-sm font-bold text-[#101d28] dark:text-white">初始密码
              <input value={password} onChange={(event) => setPassword(event.target.value)} disabled={saving} required type="password" autoComplete="new-password" className={`mt-1.5 ${fieldClassName}`} />
            </label>}
            <label className="block text-sm font-bold text-[#101d28] dark:text-white">显示名称
              <input ref={displayNameInputRef} value={displayName} onChange={(event) => setDisplayName(event.target.value)} disabled={saving} autoComplete="name" className={`mt-1.5 ${fieldClassName}`} />
            </label>
            <label className="block text-sm font-bold text-[#101d28] dark:text-white">邮箱
              <input value={email} onChange={(event) => setEmail(event.target.value)} disabled={saving} type="email" autoComplete="email" className={`mt-1.5 ${fieldClassName}`} />
            </label>
            <label className="block text-sm font-bold text-[#101d28] dark:text-white">角色
              <select value={role} onChange={(event) => { if (isUserRole(event.target.value)) setRole(event.target.value); }} disabled={saving || isCurrentUser} className={`mt-1.5 ${fieldClassName}`}>
                {roleOptions.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </select>
            </label>
            {isEditing && <label className="block text-sm font-bold text-[#101d28] dark:text-white">状态
              <select value={status} onChange={(event) => { if (isUserStatus(event.target.value)) setStatus(event.target.value); }} disabled={saving || isCurrentUser} className={`mt-1.5 ${fieldClassName}`}>
                {statusOptions.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </select>
            </label>}
          </div>
          {isCurrentUser && <p className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-100">不能修改本人角色或状态，请由其他平台管理员操作。</p>}
          {role === 'risk_analyst' && (
            <div className="space-y-2">
              <label htmlFor={supplierSearchId} className="block text-sm font-bold text-[#101d28] dark:text-white">负责供应商</label>
              {supplierError && <p role="alert" className="flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-100"><AlertTriangle aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0" />{supplierError}</p>}
              <SupplierMultiSelect inputId={supplierSearchId} value={suppliers} onChange={setSuppliers} disabled={saving} />
            </div>
          )}
          {error && <p role="alert" className="flex items-start gap-2 rounded-xl border border-red-200 bg-[#ffdad6] p-3 text-sm text-[#93000a]"><AlertTriangle aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0" />{error}</p>}
          <footer className="flex flex-col-reverse gap-3 border-t border-slate-200 pt-4 sm:flex-row sm:justify-end dark:border-slate-700">
            <button type="button" onClick={onClose} disabled={saving} className="min-h-11 rounded-xl border border-[#c2c6d2] px-5 text-sm font-bold text-[#424751] hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-[#007aff] disabled:opacity-40 dark:border-slate-600 dark:text-slate-200 dark:hover:bg-slate-800">取消</button>
            <button type="submit" disabled={saving} className="inline-flex min-h-11 items-center justify-center gap-2 rounded-xl bg-[#007aff] px-5 text-sm font-bold text-white hover:bg-[#0062cc] focus:outline-none focus:ring-2 focus:ring-[#007aff] focus:ring-offset-2 disabled:opacity-40"><>{isEditing ? <Save aria-hidden="true" className="h-4 w-4" /> : <UserPlus aria-hidden="true" className="h-4 w-4" />}</>{saving ? '正在保存…' : isEditing ? '保存更改' : '创建用户'}</button>
          </footer>
        </form>
      </section>
    </div>
  );
};
