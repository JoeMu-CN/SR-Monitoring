import {useEffect, useLayoutEffect, useRef, useState, type ReactNode} from 'react';
import {createPortal} from 'react-dom';
import {X} from 'lucide-react';

const VIEWPORT_MARGIN = 8;
const PANEL_MAX_WIDTH = 560;

interface CellTextPopoverProps {
  /** 锚定单元格：浮层优先贴在其下方，视口放不下时翻转到上方。 */
  readonly anchor: HTMLElement;
  readonly title: string;
  readonly children: ReactNode;
  readonly onClose: () => void;
}

interface PanelPosition {
  readonly top: number;
  readonly left: number;
}

/**
 * 紧凑表格单元格的完整内容浮层：锚定、限位于视口内并可滚动。
 * Esc 与浮层外按下均关闭；焦点由调用方交还触发单元格。
 */
export const CellTextPopover = ({anchor, title, children, onClose}: CellTextPopoverProps) => {
  const panelRef = useRef<HTMLDivElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const [position, setPosition] = useState<PanelPosition | null>(null);

  // 先按自然尺寸渲染一次以量出面板大小，再定位；布局阶段完成，浏览器不会看到左上角闪烁。
  useLayoutEffect(() => {
    const rect = anchor.getBoundingClientRect();
    const width = panelRef.current?.offsetWidth || PANEL_MAX_WIDTH;
    const height = panelRef.current?.offsetHeight || 0;
    const below = rect.bottom + VIEWPORT_MARGIN;
    const fitsBelow = below + height + VIEWPORT_MARGIN <= window.innerHeight;
    setPosition({
      left: Math.min(Math.max(rect.left, VIEWPORT_MARGIN), Math.max(VIEWPORT_MARGIN, window.innerWidth - width - VIEWPORT_MARGIN)),
      top: fitsBelow ? below : Math.max(VIEWPORT_MARGIN, rect.top - height - VIEWPORT_MARGIN),
    });
  }, [anchor]);

  useEffect(() => { closeButtonRef.current?.focus(); }, []);

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose(); };
    const handlePointerDown = (event: PointerEvent) => {
      const target = event.target;
      if (!(target instanceof Node) || anchor.contains(target) || panelRef.current?.contains(target)) return;
      onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    document.addEventListener('pointerdown', handlePointerDown);
    return () => {
      window.removeEventListener('keydown', handleKeyDown);
      document.removeEventListener('pointerdown', handlePointerDown);
    };
  }, [anchor, onClose]);

  return createPortal(
    <div
      ref={panelRef}
      role="dialog"
      aria-label={title}
      className="fixed z-[70] flex max-h-[60vh] w-[min(560px,calc(100vw-1rem))] flex-col overflow-hidden rounded-xl border border-slate-200 bg-white shadow-xl dark:border-slate-700 dark:bg-slate-900"
      style={position ?? {top: 0, left: 0}}
    >
      {/* 外层只裁剪圆角，滚动交给内层：否则原生滚动条会把右侧圆角压成直角 */}
      <div className="min-h-0 overflow-y-auto overscroll-contain p-3">
        <div className="flex items-start justify-between gap-3">
          <p className="text-[11px] font-bold text-[#727782] dark:text-slate-400">{title}</p>
          <button
            ref={closeButtonRef}
            type="button"
            aria-label="关闭完整内容"
            onClick={onClose}
            className="-mr-1 -mt-1 flex min-h-8 min-w-8 shrink-0 items-center justify-center rounded-lg text-slate-500 hover:bg-slate-100 focus:outline-none focus:ring-2 focus:ring-[#007aff] dark:hover:bg-slate-800"
          >
            <X aria-hidden="true" className="h-4 w-4" />
          </button>
        </div>
        <div className="mt-1.5">{children}</div>
      </div>
    </div>,
    document.body,
  );
};