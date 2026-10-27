import React, { useEffect, useRef, useState } from 'react';
import { ArrowLeftRight } from 'lucide-react';

interface WideTableFrameProps {
  children: React.ReactNode;
  label: string;
}

export const WideTableFrame: React.FC<WideTableFrameProps> = ({ children, label }) => {
  const topScroller = useRef<HTMLDivElement>(null);
  const tableScroller = useRef<HTMLDivElement>(null);
  const [contentWidth, setContentWidth] = useState(0);

  useEffect(() => {
    const scroller = tableScroller.current;
    if (!scroller) return;
    const measure = () => setContentWidth(scroller.scrollWidth);
    measure();
    if (typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(measure);
    observer.observe(scroller);
    if (scroller.firstElementChild) observer.observe(scroller.firstElementChild);
    return () => observer.disconnect();
  }, [children]);

  const syncScroll = (source: 'top' | 'table') => {
    const from = source === 'top' ? topScroller.current : tableScroller.current;
    const to = source === 'top' ? tableScroller.current : topScroller.current;
    if (from && to && to.scrollLeft !== from.scrollLeft) to.scrollLeft = from.scrollLeft;
  };

  return (
    <div className="relative" data-testid="wide-results-table">
      <div className="flex items-center justify-between gap-3 border-b border-slate-200 bg-white px-4 py-2 text-xs text-slate-500">
        <span className="inline-flex items-center gap-1.5">
          <ArrowLeftRight className="h-3.5 w-3.5" aria-hidden="true" />
          Scroll horizontally to compare every column
        </span>
        <span className="hidden sm:inline">Supplier and action stay visible</span>
      </div>
      <div
        ref={topScroller}
        onScroll={() => syncScroll('top')}
        className="overflow-x-auto overflow-y-hidden border-b border-slate-200 bg-slate-50"
        aria-hidden="true"
        data-testid="results-top-scrollbar"
      >
        <div style={{ width: contentWidth, height: 8 }} />
      </div>
      <div
        ref={tableScroller}
        onScroll={() => syncScroll('table')}
        className="overflow-x-auto overscroll-x-contain"
        tabIndex={0}
        role="region"
        aria-label={label}
      >
        {children}
      </div>
    </div>
  );
};
