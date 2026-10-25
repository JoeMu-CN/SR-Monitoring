import {useState} from 'react';
import {ExternalLink, LogOut} from 'lucide-react';
import type {AuthUser} from '../api';

interface UserMenuProps {
  readonly user: AuthUser;
  readonly onLogout: () => void;
  readonly compact?: boolean;
  readonly menuPlacement?: 'up' | 'down';
  readonly menuAlign?: 'left' | 'right';
}

/** 账户入口：桌面侧栏左下角与移动端浮动头像共用；只负责资料、接口文档与退出。 */
export const UserMenu = ({user, onLogout, compact = false, menuPlacement = 'up', menuAlign = 'right'}: UserMenuProps) => {
  const [open, setOpen] = useState(false);
  const initials = (user.display_name || user.username || 'SR').slice(0, 2).toUpperCase();
  const menuPosition = menuPlacement === 'down' ? 'top-full mt-2' : 'bottom-full mb-2';
  const menuAlignment = menuAlign === 'left' ? 'left-0' : 'right-0';

  return (
    <div className="relative">
      <button
        type="button"
        aria-label="打开账户菜单"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        className={`flex items-center rounded-lg text-slate-600 transition-colors hover:bg-slate-200/70 dark:text-slate-300 dark:hover:bg-slate-800 ${compact ? 'h-8 w-8 justify-center' : 'gap-2 px-2 py-1.5'}`}
      >
        <span className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-[#004782] text-[10px] font-black text-white">{initials}</span>
        {!compact && (
          <span className="min-w-0 text-left">
            <span className="block truncate text-[11px] font-bold">{user.display_name || user.username}</span>
            <span className="block truncate text-[9px] text-slate-500 dark:text-slate-400">{user.role}</span>
          </span>
        )}
      </button>

      {open && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
          <div className={`absolute z-50 w-56 rounded-xl border border-slate-200 bg-white/95 p-2 shadow-2xl backdrop-blur-xl dark:border-slate-700 dark:bg-slate-900/95 ${menuAlignment} ${menuPosition}`}>
            <div className="border-b border-slate-100 px-2 pb-2 dark:border-slate-800">
              <div className="truncate text-[12px] font-bold text-slate-900 dark:text-white">{user.display_name || user.username}</div>
              <div className="mt-0.5 truncate text-[10px] text-slate-500 dark:text-slate-400">{user.role}</div>
            </div>
            <a
              href="/api/docs"
              target="_blank"
              rel="noreferrer"
              className="mt-1 flex items-center gap-2 rounded-lg px-2 py-1.5 text-[12px] text-slate-700 hover:bg-slate-100 dark:text-slate-200 dark:hover:bg-slate-800"
            >
              <ExternalLink className="h-3.5 w-3.5" />接口文档
            </a>
            <button
              type="button"
              onClick={() => { setOpen(false); onLogout(); }}
              className="mt-0.5 flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-[12px] text-red-700 hover:bg-red-50 dark:text-red-300 dark:hover:bg-red-950/30"
            >
              <LogOut className="h-3.5 w-3.5" />退出登录
            </button>
          </div>
        </>
      )}
    </div>
  );
};
