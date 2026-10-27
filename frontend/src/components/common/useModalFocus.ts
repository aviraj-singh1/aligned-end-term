import { useEffect, useEffectEvent, type RefObject } from 'react';

let openModals = 0;
let previousBodyOverflow = '';

// Keep keyboard focus in the topmost dialog, including nested confirmation.
export function useModalFocus(ref: RefObject<HTMLElement | null>, active: boolean, onClose: () => void, canClose = true) {
  const close = useEffectEvent(() => { if (canClose) onClose(); });

  useEffect(() => {
    const dialog = ref.current;
    if (!active || !dialog) return;
    const trigger = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    if (openModals++ === 0) {
      previousBodyOverflow = document.body.style.overflow;
      document.body.style.overflow = 'hidden';
    }
    const isTopmost = () => Array.from(document.querySelectorAll('[role="dialog"][aria-modal="true"]')).at(-1) === dialog;
    const controls = () => Array.from(dialog.querySelectorAll<HTMLElement>(
      'button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex="0"]',
    )).filter(element => !element.closest('[hidden], [inert], [aria-hidden="true"]'));
    const focusFirst = () => (controls()[0] || dialog).focus();
    if (isTopmost()) focusFirst();

    const onKeyDown = (event: KeyboardEvent) => {
      if (!isTopmost()) return;
      if (event.key === 'Escape') {
        event.preventDefault();
        close();
      }
      if (event.key !== 'Tab') return;
      const elements = controls();
      const first = elements[0];
      const last = elements.at(-1);
      if (!first) {
        event.preventDefault();
        dialog.focus();
      } else if (!dialog.contains(document.activeElement) || document.activeElement === dialog) {
        event.preventDefault();
        (event.shiftKey ? last : first)?.focus();
      } else if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last?.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    const onFocus = (event: FocusEvent) => {
      if (isTopmost() && !dialog.contains(event.target as Node)) focusFirst();
    };
    document.addEventListener('keydown', onKeyDown);
    document.addEventListener('focusin', onFocus);
    return () => {
      document.removeEventListener('keydown', onKeyDown);
      document.removeEventListener('focusin', onFocus);
      if (--openModals === 0) document.body.style.overflow = previousBodyOverflow;
      if (trigger?.isConnected) trigger.focus();
    };
  }, [active, ref]);
}
