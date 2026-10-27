import React, { useState } from 'react';
import { Copy as DocumentDuplicateIcon, Check as CheckIcon } from 'lucide-react';
import type { ResultTabType } from '../results/ResultsTabs';

interface SummaryPanelProps {
  summaryText: string;
  counts: Record<string, number>;
  onNavigateToTab: (tab: ResultTabType) => void;
}

export const SummaryPanel: React.FC<SummaryPanelProps> = ({ summaryText, counts, onNavigateToTab }) => {
  const [copied, setCopied] = useState(false);
  // Translate generated source labels only; preserve supplier/invoice text and copied data.
  const displaySummary = summaryText
    .replace(/^Present in GSTR-2B but not in Tally:/gm, 'Present in GSTR-2B but not in Accounts:')
    .replace(/^(\d+) Tally invoices\b/gm, '$1 Accounts invoices')
    .replace(/^  GSTR:/gm, '  GSTR-2B:');

  const handleCopy = () => {
    navigator.clipboard.writeText(summaryText).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  const majorDiffs = counts.major_difference || 0;
  const missingIn2B = counts.missing_in_gstr_2b || 0;
  const notInTally = counts.not_in_tally || 0;

  return (
    <div className="bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden mt-6 mb-6">
      <div className="px-6 py-4 border-b border-slate-200 bg-slate-50 flex justify-between items-center">
        <h3 className="text-lg font-medium text-slate-900">Reconciliation Summary</h3>
        <button
          onClick={handleCopy}
          className="inline-flex items-center text-sm font-medium text-slate-600 hover:text-brand-600 transition-colors focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-brand-500 rounded-md p-1"
          title="Copy Summary"
        >
          {copied ? (
            <>
              <CheckIcon className="h-4 w-4 mr-1 text-green-500" aria-hidden="true" />
              <span className="text-green-600">Summary copied</span>
            </>
          ) : (
            <>
              <DocumentDuplicateIcon className="h-4 w-4 mr-1" aria-hidden="true" />
              <span>Copy Summary</span>
            </>
          )}
        </button>
      </div>
      
      <div className="p-6">
        <div className="prose prose-sm prose-slate max-w-none whitespace-pre-wrap font-mono bg-slate-50 p-4 rounded-lg border border-slate-200 text-slate-700 [overflow-wrap:anywhere]">
          {displaySummary || 'No summary available.'}
        </div>
        
        {/* Important Differences Highlight */}
        <div className="mt-6 flex flex-wrap gap-4">
          <button
            onClick={() => onNavigateToTab('Major Difference')}
            className="inline-flex items-center px-4 py-2 border border-orange-200 bg-orange-50 text-orange-700 rounded-md hover:bg-orange-100 transition-colors text-sm font-medium focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-orange-500"
          >
            <span className="bg-white text-orange-600 border border-orange-200 rounded-full h-6 w-6 flex items-center justify-center text-xs font-bold mr-2">
              {majorDiffs}
            </span>
            View Major Differences
          </button>
          
          <button
            onClick={() => onNavigateToTab('Missing in GSTR-2B')}
            className="inline-flex items-center px-4 py-2 border border-red-200 bg-red-50 text-red-700 rounded-md hover:bg-red-100 transition-colors text-sm font-medium focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-red-500"
          >
            <span className="bg-white text-red-600 border border-red-200 rounded-full h-6 w-6 flex items-center justify-center text-xs font-bold mr-2">
              {missingIn2B}
            </span>
            View Missing Invoices
          </button>
          
          <button
            onClick={() => onNavigateToTab('Not in Tally')}
            className="inline-flex items-center px-4 py-2 border border-slate-300 bg-white text-slate-700 rounded-md hover:bg-slate-50 transition-colors text-sm font-medium shadow-sm focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-slate-500"
          >
            <span className="bg-slate-100 text-slate-600 border border-slate-200 rounded-full h-6 w-6 flex items-center justify-center text-xs font-bold mr-2">
              {notInTally}
            </span>
            View Not in Accounts
          </button>
        </div>
      </div>
    </div>
  );
};
