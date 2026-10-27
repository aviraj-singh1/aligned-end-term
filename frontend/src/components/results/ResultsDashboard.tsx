import React, { useEffect, useState } from 'react';
import type { CancelledVoucher, ReconciliationResponse, ReconciliationResult, NotInTallyResult } from '../../types/api';
import { getResultPage } from '../../services/api';
import { StatCard } from './StatCard';
import { ResultsTabs } from './ResultsTabs';
import type { ResultDomain, ResultTabType } from './ResultsTabs';
import { ResultsTable } from './ResultsTable';
import { InvoiceComparisonDrawer } from './InvoiceComparisonDrawer';
import { AmbiguousReview } from '../review/AmbiguousReview';
import { NotesTable } from '../notes/NotesTable';
import { SearchInput } from '../common/SearchInput';
import { Pagination } from '../common/Pagination';
import { ExportPanel } from '../export/ExportPanel';
import { SummaryPanel } from '../export/SummaryPanel';
import type { ToastMessage } from '../common/Toast';
import { CancelledVouchersTable } from './CancelledVouchersTable';
import { TaxableValueMissingTable } from './TaxableValueMissingTable';
import { Loader2 } from 'lucide-react';
import { getTallyCandidates, tallyError } from '../../services/tallyExportApi';
import type { TallyCandidate, TallyCandidateList, TallyTarget, WorkspaceRole } from '../../types/tallyExport';
import { TallyExportFlow } from '../tally-export/TallyExportFlow';
import { TallyExportHistory } from '../tally-export/TallyExportHistory';

const categoryEmptyMessages: Record<ResultTabType, string> = {
  'All': 'No reconciliation records found.',
  'Perfectly Matched': 'No perfectly matched invoices found.',
  'Minor Rounding Difference': 'No minor rounding differences found.',
  'Major Difference': 'No major differences found.',
  'Taxable Value Missing': 'No records have a missing taxable value.',
  'Missing in GSTR-2B': 'No Accounts invoices are missing from GSTR-2B.',
  'Not in Tally': 'No GSTR-2B invoices are missing from Accounts.',
  'Needs Review / Ambiguous': 'No invoices require manual review.',
  'Potential Duplicate': 'No potential duplicates found.',
  'Date Mismatches': 'No date mismatches found.',
  'Invoice Mismatches': 'No invoice number mismatches found.',
  'Cancelled Vouchers': 'No cancelled vouchers found.',
  'Notes All': 'No Notes reconciliation records found.',
  'Notes Perfect': 'No perfectly matched Notes records found.',
  'Notes Minor': 'No minor rounding differences found for Notes.',
  'Notes Tax Mismatch': 'No Notes tax mismatches found.',
  'Notes Missing in GSTR': 'No debit notes are missing from GSTR.',
  'Notes Missing in Debit Notes': 'No GSTR credit notes are missing from Accounts debit notes.',
  'Notes Needs Review': 'No Notes records require manual review.',
  'Notes Potential Duplicate': 'No potential duplicate Notes records found.',
};

interface DashboardProps {
  data: ReconciliationResponse;
  onManualMatch: (resultId: string, candidateId: string) => Promise<void>;
  addToast: (toast: Omit<ToastMessage, 'id'>) => void;
  onSessionExpired: () => void;
  tallyTarget?: TallyTarget;
  workspaceRole?: WorkspaceRole;
}

export const ResultsDashboard: React.FC<DashboardProps> = ({ data, onManualMatch, addToast, onSessionExpired, tallyTarget, workspaceRole }) => {
  const [activeTab, setActiveTab] = useState<ResultTabType>('All');
  const [domain, setDomain] = useState<ResultDomain>('purchase');
  const [searchQuery, setSearchQuery] = useState('');
  
  // Sorting
  const [sortField, setSortField] = useState<string>('supplier');
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('asc');
  
  // Pagination
  const [currentPage, setCurrentPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [pageItems, setPageItems] = useState<Array<ReconciliationResult | NotInTallyResult | CancelledVoucher>>([]);
  const [pageTotal, setPageTotal] = useState(0);
  const [isPageLoading, setIsPageLoading] = useState(true);
  const [pageError, setPageError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [debouncedSearch, setDebouncedSearch] = useState('');

  // Modals
  const [selectedResult, setSelectedResult] = useState<ReconciliationResult | NotInTallyResult | null>(null);
  const [drawerType, setDrawerType] = useState<'purchase' | 'note' | 'notin'>('purchase');
  const [reviewingAmbiguous, setReviewingAmbiguous] = useState<ReconciliationResult | null>(null);
  const [tallyCandidates,setTallyCandidates]=useState<TallyCandidateList|null>(null);
  const [selectedTally,setSelectedTally]=useState<Record<string,TallyCandidate>>({});
  const [tallyFlow,setTallyFlow]=useState(false),[tallyHistory,setTallyHistory]=useState(false),[tallyLoading,setTallyLoading]=useState(false),[tallyStale,setTallyStale]=useState(false);

  // Handle Tab Change
  const handleTabChange = (tab: ResultTabType) => {
    setActiveTab(tab);
    setCurrentPage(1);
    setSearchQuery('');
  };

  const handleSearchChange = (q: string) => {
    setSearchQuery(q);
    setCurrentPage(1);
  };

  const handleDomainChange = (nextDomain: ResultDomain) => {
    setDomain(nextDomain);
    handleTabChange(nextDomain === 'notes' ? 'Notes All' : 'All');
  };

  useEffect(() => {
    if (activeTab==='Not in Tally' && tallyTarget) return;
    const timer = window.setTimeout(() => setDebouncedSearch(searchQuery), 300);
    return () => window.clearTimeout(timer);
  }, [searchQuery]);

  useEffect(() => {
    let cancelled = false;
    setIsPageLoading(true);
    setPageError(null);
    getResultPage(data.session_id, {
      category: activeTab, page: currentPage, pageSize,
      search: debouncedSearch, sortBy: sortField, sortOrder,
    }).then(response => {
      if (cancelled) return;
      if (response.total_pages > 0 && currentPage > response.total_pages) {
        setCurrentPage(response.total_pages);
        return;
      }
      setPageItems(response.items);
      setPageTotal(response.total);
    }).catch((error: any) => {
      if (cancelled) return;
      if (error.response?.status === 404) onSessionExpired();
      setPageError(error.response?.data?.detail || 'Unable to load this result page.');
    }).finally(() => {
      if (!cancelled) setIsPageLoading(false);
    });
    return () => { cancelled = true; };
  }, [data, activeTab, currentPage, pageSize, debouncedSearch, sortField, sortOrder, onSessionExpired, reloadKey]);

  useEffect(()=>{
    if(activeTab!=='Not in Tally'||!tallyTarget)return;
    let cancelled=false;setTallyLoading(true);setPageError(null);
    getTallyCandidates(tallyTarget,data.session_id,{page:currentPage,pageSize,search:debouncedSearch}).then(r=>{if(!cancelled){setTallyCandidates(r);setPageTotal(r.total);setTallyStale(false)}}).catch(e=>{if(!cancelled)setPageError(tallyError(e).message)}).finally(()=>{if(!cancelled)setTallyLoading(false)});
    return()=>{cancelled=true};
  },[activeTab,tallyTarget?.workspaceId,tallyTarget?.clientId,tallyTarget?.gstinId,data.session_id,currentPage,pageSize,debouncedSearch,reloadKey]);

  const startTallyExport=()=>{const rows=Object.values(selectedTally);if(!rows.length)return;if(rows.some(x=>x.previously_exported)&&!window.confirm('One or more invoices were included in a previous Tally export. Export them again?'))return;setTallyFlow(true)};

  const handleSort = (field: string) => {
    if (sortField === field) {
      setSortOrder(sortOrder === 'asc' ? 'desc' : 'asc');
    } else {
      setSortField(field);
      setSortOrder('asc');
    }
    setCurrentPage(1);
  };

  const openDrawer = (result: ReconciliationResult | NotInTallyResult, type: 'purchase' | 'note' | 'notin') => {
    setSelectedResult(result);
    setDrawerType(type);
  };

  const handleRowClick = (result: ReconciliationResult) => {
    if ((result.status === 'Needs Review / Ambiguous' || result.status === 'Taxable Value Missing') && result.candidates && result.candidates.length > 0) {
      setReviewingAmbiguous(result);
    } else {
      openDrawer(result, domain === 'notes' ? 'note' : 'purchase');
    }
  };

  const notes = data.notes_summary || {};
  const resultCounts: Record<string, number> = {
    ...data.counts,
    notes_all: data.counts.total_notes || 0,
    notes_perfect: notes.perfect || 0,
    notes_minor: notes.minor || 0,
    notes_major: notes.major || 0,
    notes_missing_in_gstr: notes.missing_in_gstr || 0,
    notes_missing_in_debit_notes: notes.missing_in_debit_notes || 0,
    notes_needs_review: notes.needs_review || 0,
    notes_potential_duplicate: notes.potential_duplicate || 0,
  };

  return (
    <div className="space-y-6 animate-in fade-in duration-500">
      <div className="rounded-panel border border-sky-200 bg-sky-50 px-4 py-3 text-sm text-sky-900" role="note">
        <p className="font-medium">Reconciliation results reflect data matching only and do not determine ITC eligibility or GST compliance.</p>
        <p className="mt-1 text-sky-800">Review eligibility separately before claiming ITC.</p>
      </div>

      <section aria-labelledby="outcome-heading">
        <div className="mb-2.5 flex items-baseline justify-between gap-4">
          <h2 id="outcome-heading" className="text-base font-semibold text-ink">Outcome</h2>
          <p className="text-xs text-muted">{domain === 'notes' ? 'Debit note and GSTR credit note summary' : 'Authoritative purchase reconciliation summary'}</p>
        </div>
        <div className="grid grid-cols-2 gap-px overflow-hidden rounded-panel border border-line bg-line md:grid-cols-4">
          <StatCard title={domain === 'notes' ? 'Accounts debit notes' : 'Accounts invoices'} value={domain === 'notes' ? notes.total_debit_notes || 0 : data.counts.accounts_invoices} variant="outcome" />
          <StatCard title={domain === 'notes' ? 'GSTR credit notes' : 'GSTR-2B invoices'} value={domain === 'notes' ? notes.total_gstr_credit_notes || 0 : data.counts.gstr_invoices} variant="outcome" />
          <StatCard title="Perfect" value={domain === 'notes' ? notes.perfect || 0 : data.counts.perfectly_matched} variant="outcome" tone="perfect" />
          <StatCard title="Minor" value={domain === 'notes' ? notes.minor || 0 : data.counts.minor_rounding_difference} variant="outcome" tone="minor" />
        </div>
      </section>

      <section aria-labelledby="attention-heading">
        <div className="mb-2.5 flex flex-col gap-1 sm:flex-row sm:items-baseline sm:justify-between sm:gap-4">
          <h2 id="attention-heading" className="text-base font-semibold text-ink">Attention</h2>
          <p className="text-xs text-muted">Signals may overlap and should be reviewed individually</p>
        </div>
        <div className="grid grid-cols-2 gap-2.5 md:grid-cols-3 xl:grid-cols-6">
          {domain === 'notes' ? <>
            <StatCard title="Missing in Debit Notes" value={notes.missing_in_debit_notes || 0} variant="attention" tone="review" />
            <StatCard title="Missing in GSTR" value={notes.missing_in_gstr || 0} variant="attention" tone="warning" />
            <StatCard title="Needs Review" value={notes.needs_review || 0} variant="attention" tone="review" />
            <StatCard title="Tax Mismatch" value={notes.major || 0} variant="attention" tone="major" />
            <StatCard title="Potential Duplicate" value={notes.potential_duplicate || 0} variant="attention" tone="review" />
          </> : <>
            <StatCard title="Not in Accounts" value={data.counts.not_in_tally} variant="attention" tone="review" />
            <StatCard title="Taxable Missing" value={data.counts.taxable_value_missing || 0} variant="attention" tone="warning" />
            <StatCard title="Date Mismatch" value={data.counts.date_mismatches} variant="attention" tone="minor" />
            <StatCard title="Invoice Mismatch" value={data.counts.invoice_mismatches} variant="attention" tone="major" />
            <StatCard title="Needs Review" value={data.counts.needs_review_ambiguous} variant="attention" tone="review" />
            <StatCard title="Major" value={data.counts.major_difference} variant="attention" tone="major" />
          </>}
        </div>
      </section>

      <div className="overflow-hidden rounded-panel border border-slate-200 bg-white shadow-sm">
        <ResultsTabs domain={domain} activeTab={activeTab} onDomainChange={handleDomainChange} onChange={handleTabChange} counts={resultCounts} />
        
        <div className="flex flex-col gap-2 border-b border-slate-200 bg-slate-50 px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="w-full max-w-lg">
            <SearchInput value={searchQuery} onChange={handleSearchChange} placeholder={domain === 'notes' ? 'Search supplier, GSTIN, debit or credit note...' : 'Search supplier, GSTIN, invoice number...'} />
          </div>
          {activeTab==='Not in Tally'&&tallyTarget?<div className="flex flex-wrap items-center gap-2"><span className="text-xs text-muted">{Object.keys(selectedTally).length} selected</span><button type="button" disabled={!Object.keys(selectedTally).length||tallyStale} onClick={startTallyExport} className="rounded-control bg-brand-700 px-3 py-2 text-sm font-semibold text-white disabled:opacity-40">Export to Tally</button><button type="button" onClick={()=>setTallyHistory(true)} className="rounded-control border bg-white px-3 py-2 text-sm font-semibold">Export history</button></div>:<p className="text-xs text-slate-500">Sort using the table headers</p>}
        </div>

        {(activeTab==='Not in Tally'&&tallyTarget?tallyLoading:isPageLoading) ? (
          <div className="flex min-h-40 items-center justify-center gap-2 px-6 py-10 text-sm text-slate-500" role="status" aria-live="polite">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> Loading results...
          </div>
        ) : pageError ? (
          <div className="px-6 py-10 text-center" role="alert">
            <p className="text-sm font-semibold text-rose-700">Unable to load results</p>
            <p className="mt-1 text-sm text-rose-700">{pageError}</p>
            <button type="button" onClick={() => setReloadKey(key => key + 1)} className="mt-3 rounded-control border border-rose-200 bg-white px-3 py-2 text-sm font-semibold text-rose-700 hover:bg-rose-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose-500">Retry</button>
          </div>
        ) : activeTab === 'Cancelled Vouchers' ? (
          <CancelledVouchersTable results={pageItems as CancelledVoucher[]} emptyMessage={searchQuery ? 'No results match your search.' : categoryEmptyMessages[activeTab]} />
        ) : activeTab === 'Taxable Value Missing' ? (
          <TaxableValueMissingTable results={pageItems as ReconciliationResult[]} onRowClick={handleRowClick} emptyMessage={searchQuery ? 'No results match your search.' : categoryEmptyMessages[activeTab]} />
        ) : domain === 'notes' ? (
          <NotesTable 
            results={pageItems as ReconciliationResult[]}
            onRowClick={handleRowClick}
            sortField={sortField}
            sortOrder={sortOrder}
            onSort={handleSort}
            emptyMessage={searchQuery ? 'No results match your search.' : categoryEmptyMessages[activeTab]}
          />
        ) : activeTab === 'Not in Tally' && tallyTarget ? (
          <div className="overflow-x-auto"><table className="min-w-full divide-y divide-slate-200"><thead className="bg-slate-50"><tr><th className="px-4 py-3 text-left"><span className="sr-only">Select</span></th><th className="px-4 py-3 text-left text-xs font-medium uppercase text-slate-500">Supplier</th><th className="px-4 py-3 text-left text-xs font-medium uppercase text-slate-500">GSTIN</th><th className="px-4 py-3 text-left text-xs font-medium uppercase text-slate-500">GSTR-2B invoice</th><th className="px-4 py-3 text-right text-xs font-medium uppercase text-slate-500">Invoice total</th><th className="px-4 py-3 text-left text-xs font-medium uppercase text-slate-500">Export status</th></tr></thead><tbody className="divide-y bg-white">{tallyCandidates?.items.map(c=><tr key={c.candidate_id} className="hover:bg-brand-50"><td className="px-4 py-3"><input type="checkbox" aria-label={`Select ${c.supplier_name} invoice ${c.document_number}`} checked={!!selectedTally[c.candidate_id]} onChange={e=>setSelectedTally(v=>{const next={...v};if(e.target.checked)next[c.candidate_id]=c;else delete next[c.candidate_id];return next})}/></td><td className="px-4 py-3 text-sm font-medium">{c.supplier_name}</td><td className="px-4 py-3 text-sm text-muted">{c.supplier_gstin}</td><td className="px-4 py-3 text-sm">{c.document_number}</td><td className="px-4 py-3 text-right text-sm tabular-nums">₹{Number(c.invoice_total||c.taxable_value).toLocaleString('en-IN')}</td><td className="px-4 py-3 text-sm">{c.previously_exported?<span className="font-semibold text-amber-700">Previously exported</span>:<span className="text-muted">Not exported</span>}</td></tr>)}{!tallyCandidates?.items.length&&<tr><td colSpan={6} className="px-6 py-12 text-center text-muted">{categoryEmptyMessages['Not in Tally']}</td></tr>}</tbody></table></div>
        ) : activeTab === 'Not in Tally' ? (
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-slate-200">
              <thead className="bg-slate-50">
                <tr>
                  <th className="px-4 py-3 text-left text-xs font-medium text-slate-500 uppercase tracking-wider">Supplier</th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-slate-500 uppercase tracking-wider">GSTIN</th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-slate-500 uppercase tracking-wider">GSTR-2B Invoice</th>
                  <th className="px-4 py-3 text-right text-xs font-medium text-slate-500 uppercase tracking-wider">Taxable Value</th>
                  <th className="px-4 py-3 text-right text-xs font-medium text-slate-500 uppercase tracking-wider">Action</th>
                </tr>
              </thead>
              <tbody className="bg-white divide-y divide-slate-200">
                {(pageItems as NotInTallyResult[]).map((r, idx) => (
                  <tr key={idx} onClick={() => openDrawer(r, 'notin')} className="hover:bg-brand-50 cursor-pointer transition-colors">
                    <td className="px-4 py-3 text-sm text-slate-900">{r.supplier}</td>
                    <td className="px-4 py-3 text-sm text-slate-500">{r.gstin}</td>
                    <td className="px-4 py-3 text-sm text-slate-900">{r.gst_invoice}</td>
                    <td className="px-4 py-3 text-right text-sm tabular-nums text-slate-900">{r.taxable_value}</td>
                    <td className="px-4 py-3 text-sm text-right text-brand-600 font-medium"><button type="button" onClick={(event) => { event.stopPropagation(); openDrawer(r, 'notin'); }} className="rounded px-1 py-0.5 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">View</button></td>
                  </tr>
                ))}
                {pageItems.length === 0 && (
                  <tr><td colSpan={5} className="px-6 py-12 text-center text-slate-500">
                    {searchQuery ? 'No results match your search.' : categoryEmptyMessages[activeTab]}
                  </td></tr>
                )}
              </tbody>
            </table>
          </div>
        ) : (
          <ResultsTable 
            results={pageItems as ReconciliationResult[]}
            onRowClick={handleRowClick}
            sortField={sortField}
            sortOrder={sortOrder}
            onSort={handleSort}
            emptyMessage={
              searchQuery ? 'No results match your search.' : categoryEmptyMessages[activeTab]
            }
          />
        )}
        
        <Pagination 
          currentPage={currentPage}
          totalItems={pageTotal}
          pageSize={pageSize}
          onPageChange={setCurrentPage}
          onPageSizeChange={(size) => {
            setPageSize(size);
            setCurrentPage(1);
          }}
        />
      </div>

      {domain === 'purchase' && <SummaryPanel
        summaryText={data.summary_text}
        counts={data.counts}
        onNavigateToTab={handleTabChange}
      />}

      <ExportPanel 
        sessionId={data.session_id}
        counts={data.counts}
        notesCount={data.counts.total_notes || 0}
        addToast={addToast} 
        onSessionExpired={onSessionExpired} 
      />

      <InvoiceComparisonDrawer 
        isOpen={!!selectedResult} 
        onClose={() => setSelectedResult(null)} 
        result={selectedResult} 
        type={drawerType}
      />

      <AmbiguousReview
        isOpen={!!reviewingAmbiguous}
        onClose={() => setReviewingAmbiguous(null)}
        result={reviewingAmbiguous}
        onManualMatch={onManualMatch}
      />
      {tallyTarget&&workspaceRole&&<><TallyExportFlow open={tallyFlow} onClose={()=>setTallyFlow(false)} target={tallyTarget} sessionId={data.session_id} role={workspaceRole} selected={Object.values(selectedTally)} runVersion={tallyCandidates?.run_version||1} allowReexport={Object.values(selectedTally).some(x=>x.previously_exported)} addToast={addToast} onStale={()=>{setTallyStale(true);setReloadKey(x=>x+1);addToast({type:'error',message:'Reconciliation results changed. Candidate data was refreshed; review your selection before retrying.'})}}/><TallyExportHistory open={tallyHistory} onClose={()=>setTallyHistory(false)} target={tallyTarget}/></>}
    </div>
  );
};
