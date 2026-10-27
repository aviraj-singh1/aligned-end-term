import React, { useEffect, useState } from 'react';
import { healthCheck } from '../../services/api';
import { Server, CheckCircle, AlertCircle } from 'lucide-react';

export const Header: React.FC = () => {
  const [healthStatus, setHealthStatus] = useState<'checking' | 'connected' | 'error'>('checking');

  const checkHealth = async () => {
    setHealthStatus('checking');
    try {
      await healthCheck();
      setHealthStatus('connected');
    } catch {
      setHealthStatus('error');
    }
  };

  useEffect(() => {
    checkHealth();
  }, []);

  return (
    <header className="sticky top-0 z-50 border-b border-line bg-white px-4 sm:px-6">
      <div className="mx-auto flex h-16 max-w-[90rem] items-center justify-between gap-4">
        <div className="flex min-w-0 items-center gap-3">
          <picture className="flex shrink-0 items-center">
            <source media="(max-width: 479px)" srcSet="/brand/aligned-mark.png" />
            <img
              src="/brand/aligned-wordmark.png"
              alt="Aligned"
              className="h-10 w-auto object-contain sm:h-12"
            />
          </picture>
          <div className="h-7 w-px shrink-0 bg-line" aria-hidden="true" />
          <p className="truncate text-xs font-medium tracking-[0.01em] text-muted sm:text-[13px]">
            Reconciliation workspace
          </p>
        </div>
        
        <div className="flex shrink-0 items-center">
          {healthStatus === 'checking' && (
            <div className="flex items-center text-xs font-medium text-slate-500 sm:text-sm" role="status">
              <Server className="mr-1.5 h-4 w-4 animate-pulse" aria-hidden="true" />
              <span className="hidden min-[360px]:inline">Checking...</span>
            </div>
          )}
          {healthStatus === 'connected' && (
            <div className="flex items-center text-xs font-medium text-emerald-700 sm:text-sm" role="status">
              <CheckCircle className="mr-1.5 h-4 w-4" aria-hidden="true" />
              <span className="hidden min-[360px]:inline">Connected</span>
            </div>
          )}
          {healthStatus === 'error' && (
            <div className="flex items-center text-xs font-medium text-rose-700 sm:text-sm" role="alert">
              <AlertCircle className="mr-1.5 h-4 w-4" aria-hidden="true" />
              <span className="hidden sm:inline">Backend Unavailable</span>
              <button 
                onClick={checkHealth}
                className="ml-2 px-1 py-0.5 text-xs underline underline-offset-2 hover:text-rose-900"
              >
                Retry
              </button>
            </div>
          )}
        </div>
      </div>
    </header>
  );
};
