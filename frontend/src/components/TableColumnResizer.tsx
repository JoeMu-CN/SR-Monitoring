import {useRef, type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent} from 'react';

export const MIN_COLUMN_WIDTH = 80;
export const MAX_COLUMN_WIDTH = 1200;
const KEYBOARD_STEP = 16;

const clampWidth = (width: number) => Math.min(MAX_COLUMN_WIDTH, Math.max(MIN_COLUMN_WIDTH, width));

interface TableColumnResizerProps {
  readonly label: string;
  readonly width: number;
  readonly onResize: (width: number) => void;
}

/**
 * 表头右侧的列宽把手：指针拖拽靠 Pointer Capture 跟随，回调只由本组件发起，
 * 因此调整过程既不会冒泡到单元格文本（不会误开浮层），也不会残留未释放的捕获。
 */
export const TableColumnResizer = ({label, width, onResize}: TableColumnResizerProps) => {
  const dragOrigin = useRef<{pointerId: number; startX: number; startWidth: number} | null>(null);

  const endDrag = (event: ReactPointerEvent<HTMLButtonElement>) => {
    if (dragOrigin.current?.pointerId !== event.pointerId) return;
    dragOrigin.current = null;
    event.currentTarget.releasePointerCapture?.(event.pointerId);
  };

  return (
    <button
      type="button"
      role="separator"
      aria-orientation="vertical"
      aria-label={`调整「${label}」列宽`}
      aria-valuenow={width}
      aria-valuemin={MIN_COLUMN_WIDTH}
      aria-valuemax={MAX_COLUMN_WIDTH}
      onPointerDown={(event: ReactPointerEvent<HTMLButtonElement>) => {
        if (event.button !== 0) return;
        // 阻止文本选择与冒泡：拖拽期间不得触发任何单元格浮层。
        event.preventDefault();
        event.stopPropagation();
        event.currentTarget.setPointerCapture?.(event.pointerId);
        dragOrigin.current = {pointerId: event.pointerId, startX: event.clientX, startWidth: width};
      }}
      onPointerMove={(event: ReactPointerEvent<HTMLButtonElement>) => {
        const origin = dragOrigin.current;
        if (origin === null || origin.pointerId !== event.pointerId) return;
        onResize(clampWidth(origin.startWidth + (event.clientX - origin.startX)));
      }}
      onPointerUp={endDrag}
      onPointerCancel={endDrag}
      onLostPointerCapture={endDrag}
      onKeyDown={(event: ReactKeyboardEvent<HTMLButtonElement>) => {
        if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
        event.preventDefault();
        onResize(clampWidth(width + (event.key === 'ArrowRight' ? KEYBOARD_STEP : -KEYBOARD_STEP)));
      }}
      className="absolute inset-y-0 right-0 z-20 w-2 cursor-col-resize touch-none rounded-sm bg-transparent hover:bg-[#007aff]/30 focus:outline-none focus-visible:bg-[#007aff]/40"
    />
  );
};