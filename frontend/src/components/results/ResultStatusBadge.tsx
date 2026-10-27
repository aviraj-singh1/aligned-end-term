import React from 'react';

export const formatCurrency = (amount: string | number | null | undefined): string => {
  if (amount === null || amount === undefined) return '—';
  const val = typeof amount === 'string' ? parseFloat(amount) : amount;
  if (isNaN(val)) return '—';
  
  const formatter = new Intl.NumberFormat('en-IN', {
    style: 'currency',
    currency: 'INR',
    minimumFractionDigits: 2,
    maximumFractionDigits: 2
  });

  const formatted = formatter.format(Math.abs(val));
  return val < 0 ? `-${formatted}` : formatted;
};

interface StatusBadgeProps {
  status: string;
  prefix?: string;
}

export const ResultStatusBadge: React.FC<StatusBadgeProps> = ({ status, prefix = "" }) => {
  const displayStatus = status === 'Not in Tally' ? 'Not in Accounts'
    : status === 'Taxable Value Missing' ? 'Taxable Missing'
    : status;
  const getStyle = () => {
    switch (status) {
      case 'Perfectly Matched':
        return 'bg-status-perfect-soft text-status-perfect border-status-perfect-line';
      case 'Minor Rounding Difference':
        return 'bg-status-minor-soft text-status-minor border-status-minor-line';
      case 'Major Difference':
        return 'bg-status-major-soft text-status-major border-status-major-line';
      case 'Missing in GSTR-2B':
        return 'bg-rose-100 text-rose-800 border-rose-200';
      case 'Not in Tally':
        return 'bg-status-review-soft text-status-review border-status-review-line';
      case 'Needs Review / Ambiguous':
      case 'Potential Duplicate':
        return 'bg-status-review-soft text-status-review border-status-review-line';
      case 'Invoice Number Mismatch':
        return 'bg-status-major-soft text-status-major border-status-major-line';
      case 'Taxable Value Missing':
        return 'bg-status-warning-soft text-status-warning border-status-warning-line';
      default:
        return 'bg-slate-100 text-slate-800 border-slate-200';
    }
  };

  return (
    <span className={`px-2.5 py-1 text-xs font-semibold rounded-full border inline-block w-fit ${getStyle()}`}>
      {prefix}{displayStatus}
    </span>
  );
};
