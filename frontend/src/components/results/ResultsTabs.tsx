import React from 'react';

export type ResultTabType =
  | 'All' | 'Perfectly Matched' | 'Minor Rounding Difference' | 'Major Difference'
  | 'Taxable Value Missing' | 'Missing in GSTR-2B' | 'Not in Tally'
  | 'Needs Review / Ambiguous' | 'Potential Duplicate' | 'Date Mismatches'
  | 'Invoice Mismatches' | 'Cancelled Vouchers'
  | 'Notes All' | 'Notes Perfect' | 'Notes Minor' | 'Notes Tax Mismatch'
  | 'Notes Missing in GSTR' | 'Notes Missing in Debit Notes'
  | 'Notes Needs Review' | 'Notes Potential Duplicate';

export type ResultDomain = 'purchase' | 'notes';
type ResultGroup = 'All' | 'Matched' | 'Differences' | 'Missing' | 'Review';
interface ResultFilter { id: ResultTabType; label: string; countKey: string; }
interface GroupDefinition { id: ResultGroup; defaultCategory: ResultTabType; filters: ResultFilter[]; countKey?: string; }

const purchaseGroups: GroupDefinition[] = [
  { id: 'All', defaultCategory: 'All', countKey: 'all', filters: [{ id: 'All', label: 'All results', countKey: 'all' }] },
  { id: 'Matched', defaultCategory: 'Perfectly Matched', filters: [
    { id: 'Perfectly Matched', label: 'Perfect', countKey: 'perfectly_matched' },
    { id: 'Minor Rounding Difference', label: 'Minor', countKey: 'minor_rounding_difference' },
  ] },
  { id: 'Differences', defaultCategory: 'Major Difference', filters: [
    { id: 'Major Difference', label: 'Major', countKey: 'major_difference' },
    { id: 'Date Mismatches', label: 'Date Mismatch', countKey: 'date_mismatches' },
    { id: 'Invoice Mismatches', label: 'Invoice Mismatch', countKey: 'invoice_mismatches' },
  ] },
  { id: 'Missing', defaultCategory: 'Missing in GSTR-2B', filters: [
    { id: 'Missing in GSTR-2B', label: 'Missing in GSTR-2B', countKey: 'missing_in_gstr_2b' },
    { id: 'Not in Tally', label: 'Not in Accounts', countKey: 'not_in_tally' },
  ] },
  { id: 'Review', defaultCategory: 'Needs Review / Ambiguous', filters: [
    { id: 'Needs Review / Ambiguous', label: 'Needs Review', countKey: 'needs_review_ambiguous' },
    { id: 'Potential Duplicate', label: 'Potential Duplicate', countKey: 'potential_duplicate' },
    { id: 'Taxable Value Missing', label: 'Taxable Missing', countKey: 'taxable_value_missing' },
    { id: 'Cancelled Vouchers', label: 'Cancelled', countKey: 'cancelled_vouchers' },
  ] },
];

const notesGroups: GroupDefinition[] = [
  { id: 'All', defaultCategory: 'Notes All', countKey: 'notes_all', filters: [{ id: 'Notes All', label: 'All notes', countKey: 'notes_all' }] },
  { id: 'Matched', defaultCategory: 'Notes Perfect', filters: [
    { id: 'Notes Perfect', label: 'Perfect', countKey: 'notes_perfect' },
    { id: 'Notes Minor', label: 'Minor', countKey: 'notes_minor' },
  ] },
  { id: 'Differences', defaultCategory: 'Notes Tax Mismatch', filters: [
    { id: 'Notes Tax Mismatch', label: 'Tax Mismatch', countKey: 'notes_major' },
  ] },
  { id: 'Missing', defaultCategory: 'Notes Missing in GSTR', filters: [
    { id: 'Notes Missing in GSTR', label: 'Missing in GSTR', countKey: 'notes_missing_in_gstr' },
    { id: 'Notes Missing in Debit Notes', label: 'Missing in Debit Notes', countKey: 'notes_missing_in_debit_notes' },
  ] },
  { id: 'Review', defaultCategory: 'Notes Needs Review', filters: [
    { id: 'Notes Needs Review', label: 'Needs Review', countKey: 'notes_needs_review' },
    { id: 'Notes Potential Duplicate', label: 'Potential Duplicate', countKey: 'notes_potential_duplicate' },
  ] },
];

interface ResultsTabsProps {
  domain?: ResultDomain;
  activeTab: ResultTabType;
  onDomainChange?: (domain: ResultDomain) => void;
  onChange: (tab: ResultTabType) => void;
  counts: Record<string, number>;
}

export const ResultsTabs: React.FC<ResultsTabsProps> = ({ domain, activeTab, onDomainChange, onChange, counts }) => {
  const effectiveDomain = domain ?? (activeTab.startsWith('Notes') ? 'notes' : 'purchase');
  const groups = effectiveDomain === 'notes' ? notesGroups : purchaseGroups;
  const activeGroup = groups.find(group => group.filters.some(filter => filter.id === activeTab)) ?? groups[0];
  const selectGroup = (group: GroupDefinition) => {
    const populated = group.filters.find(filter => (counts[filter.countKey] || 0) > 0);
    onChange(populated?.id ?? group.defaultCategory);
  };
  return (
    <div className="border-b border-slate-200 bg-white px-4 pt-3 sm:px-5">
      <div className="grid max-w-md grid-cols-2 rounded-lg bg-slate-100 p-1" aria-label="Reconciliation engine">
        {(['purchase', 'notes'] as ResultDomain[]).map(value => (
          <button key={value} type="button" aria-pressed={effectiveDomain === value} onClick={() => onDomainChange?.(value)}
            className={`rounded-md px-4 py-2 text-sm font-semibold transition ${effectiveDomain === value ? 'bg-white text-brand-700 shadow-sm' : 'text-slate-500 hover:text-slate-800'}`}>
            {value === 'purchase' ? 'Purchase Reconciliation' : 'Notes Reconciliation'}
            <span className="ml-2 text-xs text-slate-400">{value === 'purchase' ? counts.all || 0 : counts.notes_all || 0}</span>
          </button>
        ))}
      </div>
      <nav className="mt-2 flex flex-wrap gap-x-5 gap-y-1" aria-label={`${effectiveDomain === 'notes' ? 'Notes' : 'Purchase'} result groups`}>
        {groups.map(group => {
          const selected = activeGroup.id === group.id;
          const count = group.countKey ? counts[group.countKey] || 0 : null;
          return <button key={group.id} type="button" aria-pressed={selected} onClick={() => selectGroup(group)}
            aria-label={count === null ? group.id : `${group.id}, ${count} results`}
            className={`border-b-2 px-0.5 py-3 text-sm font-semibold transition-colors ${selected ? 'border-brand-500 text-brand-700' : 'border-transparent text-slate-500 hover:border-slate-300 hover:text-slate-800'}`}>
            {group.id}{count !== null && <span className="ml-1.5 text-xs font-medium text-slate-400">{count}</span>}
          </button>;
        })}
      </nav>
      {activeGroup.filters.length > 1 && <div className="flex flex-wrap items-center gap-2 border-t border-slate-100 py-3" aria-label={`${activeGroup.id} filters`}>
        <span className="mr-1 text-xs font-medium uppercase tracking-wide text-slate-400">Filter</span>
        {activeGroup.filters.map(filter => {
          const selected = filter.id === activeTab;
          const count = counts[filter.countKey] || 0;
          return <button key={filter.id} type="button" aria-pressed={selected} onClick={() => onChange(filter.id)} aria-label={`${filter.label}, ${count} results`}
            className={`inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1.5 text-xs font-medium transition-colors ${selected ? 'border-brand-300 bg-brand-50 text-brand-700' : 'border-slate-200 bg-white text-slate-600 hover:border-slate-300 hover:bg-slate-50'}`}>
            {filter.label}<span className={selected ? 'text-brand-500' : 'text-slate-400'}>{count}</span>
          </button>;
        })}
      </div>}
    </div>
  );
};
