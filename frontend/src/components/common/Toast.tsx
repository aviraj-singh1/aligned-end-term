import React, { useEffect } from 'react';
import { X as XMarkIcon, CheckCircle as CheckCircleIcon, AlertCircle as ExclamationCircleIcon } from 'lucide-react';

export type ToastType = 'success' | 'error';

export interface ToastMessage {
  id: string;
  message: string;
  type: ToastType;
}

interface ToastProps {
  toast: ToastMessage;
  onClose: (id: string) => void;
}

export const Toast: React.FC<ToastProps> = ({ toast, onClose }) => {
  useEffect(() => {
    const timer = setTimeout(() => {
      onClose(toast.id);
    }, 4000);
    return () => clearTimeout(timer);
  }, [toast.id, onClose]);

  return (
    <div className={`flex items-center p-4 mb-4 text-sm rounded-lg shadow-md border animate-in slide-in-from-bottom-2 fade-in duration-300 ${
      toast.type === 'success' ? 'text-green-800 bg-green-50 border-green-200' : 'text-red-800 bg-red-50 border-red-200'
    }`} role="alert">
      {toast.type === 'success' ? (
        <CheckCircleIcon className="flex-shrink-0 inline w-5 h-5 mr-3" aria-hidden="true" />
      ) : (
        <ExclamationCircleIcon className="flex-shrink-0 inline w-5 h-5 mr-3" aria-hidden="true" />
      )}
      <span className="sr-only">{toast.type === 'success' ? 'Success' : 'Error'}</span>
      <div className="min-w-0 font-medium mr-4 [overflow-wrap:anywhere]">
        {toast.message}
      </div>
      <button 
        type="button" 
        className={`ml-auto -mx-1.5 -my-1.5 p-1.5 rounded-lg focus:ring-2 focus:ring-offset-1 inline-flex h-8 w-8 transition-colors ${
          toast.type === 'success' 
            ? 'bg-green-50 text-green-500 hover:bg-green-100 focus:ring-green-400' 
            : 'bg-red-50 text-red-500 hover:bg-red-100 focus:ring-red-400'
        }`}
        aria-label="Close"
        onClick={() => onClose(toast.id)}
      >
        <span className="sr-only">Close</span>
        <XMarkIcon className="w-5 h-5" />
      </button>
    </div>
  );
};

interface ToastContainerProps {
  toasts: ToastMessage[];
  removeToast: (id: string) => void;
}

export const ToastContainer: React.FC<ToastContainerProps> = ({ toasts, removeToast }) => {
  return (
    <div className="fixed bottom-4 right-4 z-[80] flex max-w-[calc(100vw-2rem)] flex-col gap-2 pointer-events-none">
      {toasts.map((toast) => (
        <div key={toast.id} className="pointer-events-auto">
          <Toast toast={toast} onClose={removeToast} />
        </div>
      ))}
    </div>
  );
};
