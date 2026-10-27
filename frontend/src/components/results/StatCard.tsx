import React from 'react';

interface StatCardProps {
  title: string;
  value: number | null | undefined;
  variant: 'outcome' | 'attention';
  tone?: 'neutral' | 'perfect' | 'minor' | 'major' | 'warning' | 'missing' | 'review';
}

export const StatCard: React.FC<StatCardProps> = ({ title, value, variant, tone = 'neutral' }) => {
  const displayValue = value ?? 0;
  const getColors = () => {
    switch (tone) {
      case 'perfect': return 'bg-status-perfect-soft text-status-perfect border-status-perfect-line';
      case 'minor': return 'bg-status-minor-soft text-status-minor border-status-minor-line';
      case 'major': return 'bg-status-major-soft text-status-major border-status-major-line';
      case 'warning': return 'bg-status-warning-soft text-status-warning border-status-warning-line';
      case 'missing': return 'bg-rose-50 text-rose-800 border-rose-200';
      case 'review': return 'bg-status-review-soft text-status-review border-status-review-line';
      default: return 'bg-white text-ink border-line';
    }
  };

  if (variant === 'outcome') {
    return (
      <div className={`min-w-0 border ${getColors()} px-4 py-3.5 sm:px-5 ${displayValue === 0 ? 'opacity-65' : ''}`}>
        <p className="text-xs font-medium text-current opacity-80">{title}</p>
        <p className="mt-1 text-2xl font-semibold leading-none tabular-nums">{displayValue.toLocaleString('en-IN')}</p>
      </div>
    );
  }

  return (
    <div className={`flex min-w-0 items-center justify-between gap-3 rounded-control border ${getColors()} px-3.5 py-3 ${displayValue === 0 ? 'opacity-60' : ''}`}>
      <p className="truncate text-xs font-semibold">{title}</p>
      <p className="shrink-0 text-lg font-semibold leading-none tabular-nums">{displayValue.toLocaleString('en-IN')}</p>
    </div>
  );
};
