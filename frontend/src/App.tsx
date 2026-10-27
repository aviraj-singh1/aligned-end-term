import { useState } from 'react';
import { Header } from './components/layout/Header';
import { WorkflowIndicator } from './components/layout/WorkflowIndicator';
import type { WorkflowStep } from './components/layout/WorkflowIndicator';
import { FileUploader } from './components/upload/FileUploader';
import { MappingTable } from './components/mapping/MappingTable';
import { PreviewTable } from './components/preview/PreviewTable';
import { inspectFiles, deleteSession, reconcileSession, manualMatch, type ReconciliationContext, type OpenRun } from './services/api';
import type { InspectionResponse, ReconciliationResponse } from './types/api';
import { Loader2, AlertCircle, RefreshCw, AlertTriangle } from 'lucide-react';
import { ResultsDashboard } from './components/results/ResultsDashboard';
import { ToastContainer } from './components/common/Toast';
import type { ToastMessage } from './components/common/Toast';
import { getUploadErrorMessage } from './utils/uploadError';
import { validateCombinedUpload, UPLOAD_LIMIT_LABEL } from './utils/transportLimits';
import type { WorkspaceRole } from './types/tallyExport';

const normalizeHeader = (s: string) => s.toLowerCase().replace(/[\s\-_]+/g, '');

const findAlias = (available: string[], canonicalAliases: string[]) => {
  const normalizedAliases = canonicalAliases.map(normalizeHeader);
  const matches = available.filter(col => normalizedAliases.includes(normalizeHeader(col)));
  return matches.length === 1 ? matches[0] : undefined;
};

const semanticKeys: Record<string, string> = {
  'Supplier': 'supplier_name',
  'GSTIN': 'gstin',
  'Invoice Number': 'invoice_number',
  'Invoice Date': 'invoice_date',
  'Taxable Value': 'taxable_value',
  'Gross Total': 'gross_total',
  'Invoice Value': 'total_invoice_value',
  'IGST': 'igst',
  'CGST': 'cgst',
  'SGST': 'sgst'
};

function App({ context, initial, workspaceRole }: { context?: ReconciliationContext; initial?: OpenRun; workspaceRole?: WorkspaceRole } = {}) {
  const [step, setStep] = useState<WorkflowStep>(initial?.phase === 'COMPLETED' ? 'export' : initial ? 'review' : 'upload');
  const [accountsFile, setAccountsFile] = useState<File | null>(null);
  const [gstrFile, setGstrFile] = useState<File | null>(null);
  const [dnFile, setDnFile] = useState<File | null>(null);
  const [accountsReading, setAccountsReading] = useState(false);
  const [gstrReading, setGstrReading] = useState(false);
  const [dnReading, setDnReading] = useState(false);
  const isReadingFiles = accountsReading || gstrReading || dnReading;
  
  const [isInspecting, setIsInspecting] = useState(false);
  const [inspectError, setInspectError] = useState<string | null>(null);
  const [inspectionData, setInspectionData] = useState<InspectionResponse | null>(initial?.phase === 'READY' ? initial.data : null);

  const [activeTab, setActiveTab] = useState<'accounts' | 'gstr' | 'debit_notes' | 'credit_notes'>('accounts');

  // Mapping state: { type: { semanticField: fileHeader } }
  const [mappings, setMappings] = useState<{accounts: Record<string, string>, gstr: Record<string, string>}>(() => {
    const result = { accounts: {} as Record<string, string>, gstr: {} as Record<string, string> };
    if (initial?.phase === 'READY') for (const source of ['accounts', 'gstr'] as const) {
      result[source] = { ...initial.data.mappings[source] };
      for (const [label, semantic] of Object.entries(semanticKeys)) if (result[source][semantic]) result[source][label] = result[source][semantic];
    }
    return result;
  });

  // Reconciliation State
  const [isReconciling, setIsReconciling] = useState(false);
  const [reconcileError, setReconcileError] = useState<string | null>(null);
  const [reconciliationData, setReconciliationData] = useState<ReconciliationResponse | null>(initial?.phase === 'COMPLETED' ? initial.data : null);

  const handleInspect = async () => {
    if (!accountsFile || !gstrFile || isReadingFiles) return;
    
    setIsInspecting(true);
    setInspectError(null);
    try {
      validateCombinedUpload([accountsFile, gstrFile, dnFile]);
      const data = context ? await inspectFiles(accountsFile, gstrFile, dnFile, context) : await inspectFiles(accountsFile, gstrFile, dnFile);
      setInspectionData(data);

      const tallyDefaults: Record<string, string[]> = {
        'Supplier': ['Particulars', 'Party Name', 'Account'],
        'GSTIN': ['GSTIN', 'TIN/GSTIN No.'],
        'Invoice Number': ['Voucher No.', 'Vch/Bill No'],
        'Invoice Date': ['Date'],
        'Taxable Value': ['Value', 'Taxable Amt.'],
        'Gross Total': ['Gross Total', 'Gross Amount', 'Grand Total', 'Total Amount'],
        'IGST': ['IGST', 'Igst'],
        'CGST': ['CGST', 'Cgst'],
        'SGST': ['SGST', 'Sgst']
      };

      const gstrDefaults: Record<string, string[]> = {
        'Supplier': ['Party Name'],
        'GSTIN': ['GSTIN/UIN'],
        'Invoice Number': ['Invoice No.'],
        'Invoice Date': ['Invoice Date'],
        'Taxable Value': ['Taxable Value'],
        'Invoice Value': ['Invoice Value', 'Invoice Value(₹)', 'Invoice Value (₹)', 'Total Invoice Value'],
        'IGST': ['IGST Amount'],
        'CGST': ['CGST Amount'],
        'SGST': ['SGST Amount']
      };

      const newAccountsMappings = { ...data.mappings.accounts };
      const newGstrMappings = { ...data.mappings.gstr };

      for (const [sysField, defaultCols] of Object.entries(tallyDefaults)) {
        if (!newAccountsMappings[sysField]) {
          const detectedCol = data.mappings.accounts[semanticKeys[sysField]];
          const matchedCol = detectedCol && data.available_columns.accounts.includes(detectedCol)
            ? detectedCol
            : findAlias(data.available_columns.accounts, defaultCols);
          if (matchedCol) {
            newAccountsMappings[sysField] = matchedCol;
          }
        }
      }

      for (const [sysField, defaultCols] of Object.entries(gstrDefaults)) {
        if (!newGstrMappings[sysField]) {
          const detectedCol = data.mappings.gstr[semanticKeys[sysField]];
          const matchedCol = detectedCol && data.available_columns.gstr.includes(detectedCol)
            ? detectedCol
            : findAlias(data.available_columns.gstr, defaultCols);
          if (matchedCol) {
            newGstrMappings[sysField] = matchedCol;
          }
        }
      }

      setMappings({
        accounts: newAccountsMappings,
        gstr: newGstrMappings
      });
      setStep('review');
    } catch (error: any) {
      setInspectError(getUploadErrorMessage(error));
    } finally {
      setIsInspecting(false);
    }
  };

  const handleMappingChange = (type: 'accounts' | 'gstr', semanticField: string, newHeader: string) => {
    setMappings(prev => ({
      ...prev,
      [type]: {
        ...prev[type],
        [semanticField]: newHeader
      }
    }));
  };

  const handleReconcile = async () => {
    if (!inspectionData) return;
    
    // Minimal validation to proceed
    const accInvoice = mappings.accounts['Invoice Number'];
    const accTaxable = mappings.accounts['Taxable Value'];
    const gstrInvoice = mappings.gstr['Invoice Number'];
    const gstrTaxable = mappings.gstr['Taxable Value'];

    if (!accInvoice || !accTaxable || !gstrInvoice || !gstrTaxable) {
      alert("Please ensure required fields (Invoice Number, Taxable Value) are mapped before reconciling.");
      return;
    }

    setIsReconciling(true);
    setReconcileError(null);

    try {
      // mapping_overrides exactly as expected by backend ReconciliationRequest
      const data = await reconcileSession(inspectionData.session_id, mappings);
      setReconciliationData(data);
      setStep('export');
    } catch (error: any) {
      const msg = error.response?.data?.detail || error.message || 'The reconciliation could not be completed.';
      setReconcileError(msg.includes('expired') ? 'Your session may have expired. Please upload the files again.' : msg);
    } finally {
      setIsReconciling(false);
    }
  };

  const handleManualMatch = async (resultId: string, candidateId: string) => {
    if (!reconciliationData) return;
    try {
      const updated = await manualMatch(reconciliationData.session_id, resultId, candidateId);
      setReconciliationData(updated);
      addToast({ message: 'Manual match applied successfully.', type: 'success' });
    } catch (error: any) {
      const status = error.response?.status;
      const detail = error.response?.data?.detail;
      addToast({
        message: status === 409
          ? detail || (context ? 'The run changed. Reopen it to load the current results.' : 'That GSTR-2B candidate has already been matched.')
          : detail || 'Unable to apply the manual match. Please try again.',
        type: 'error',
      });
      if (status === 404 || status === 410) handleSessionExpired();
      throw error;
    }
  };

  const [toasts, setToasts] = useState<ToastMessage[]>([]);
  const addToast = (toast: Omit<ToastMessage, 'id'>) => {
    setToasts(prev => [...prev, { ...toast, id: Math.random().toString(36).substring(2, 9) }]);
  };
  const removeToast = (id: string) => {
    setToasts(prev => prev.filter(t => t.id !== id));
  };

  const resetSession = async () => {
    if (context) {
      if (!window.confirm('Start a new upload? This run remains in client history until it expires or you delete it there.')) return;
    } else if (inspectionData) {
      if (!window.confirm("Start a new reconciliation? This will clear the current data.")) return;
      try {
        await deleteSession(inspectionData.session_id);
      } catch (e) {} // ignore if already gone
    }
    setInspectionData(null);
    setReconciliationData(null);
    setStep('upload');
    setAccountsFile(null);
    setGstrFile(null);
    setDnFile(null);
  };

  const handleSessionExpired = () => {
    setInspectionData(null);
    setReconciliationData(null);
    setStep('upload');
    setAccountsFile(null);
    setGstrFile(null);
    setDnFile(null);
  };

  return (
    <div className="reconciliation-tool min-h-screen flex flex-col">
      {!context && <Header />}
      <WorkflowIndicator currentStep={step} />

      <main className="mx-auto w-full max-w-[90rem] flex-grow px-4 py-6 sm:px-6 sm:py-8 [overflow-wrap:anywhere]">
        
        {step === 'upload' && (
          <div className="space-y-6 animate-in fade-in duration-500">
            <div>
              <h2 className="text-2xl font-semibold text-ink">Upload source data</h2>
              <p className="mt-1 text-sm text-muted">Select the source files you want to reconcile. Accounts Purchase Data and GSTR-2B are required.</p>
              <p className="mt-2 text-sm text-muted">{UPLOAD_LIMIT_LABEL}.</p>
              <p className="mt-1 text-xs text-muted">Production tested with approximately 1,000 invoices per source.</p>
            </div>

            <div className="grid grid-cols-1 gap-5 md:grid-cols-3 lg:gap-6">
              <FileUploader 
                title="Accounts Purchase Data"
                description="Tally, BUSY, DayBook or Purchase Register"
                required 
                file={accountsFile} 
                onFileSelect={setAccountsFile} 
                onReadingChange={setAccountsReading}
              />
              <FileUploader 
                title="GSTR-2B" 
                description="GST portal / GSTR-2B export"
                required 
                file={gstrFile} 
                onFileSelect={setGstrFile} 
                onReadingChange={setGstrReading}
              />
              <FileUploader 
                title="Debit Notes" 
                description="Optional debit-note register for Notes reconciliation"
                file={dnFile} 
                onFileSelect={setDnFile} 
                onReadingChange={setDnReading}
              />
            </div>

            {inspectError && (
              <div role="alert" className="bg-rose-50 border border-rose-200 text-rose-700 p-4 rounded-lg flex items-start">
                <AlertCircle className="w-5 h-5 mr-3 shrink-0 mt-0.5" />
                <p className="text-sm font-medium">{inspectError}</p>
              </div>
            )}

            <div className="flex flex-col gap-3 border-t border-line pt-4 sm:flex-row sm:items-center sm:justify-between">
              <p className="text-xs text-muted">
                {isReadingFiles ? 'Reading selected files before inspection…' : accountsFile && gstrFile
                  ? 'Required source files are ready for inspection.'
                  : 'Add both required source files to continue.'}
              </p>
              <button
                onClick={handleInspect}
                disabled={!accountsFile || !gstrFile || isInspecting || isReadingFiles}
                aria-busy={isInspecting}
                className="flex self-end items-center rounded-control bg-brand-600 px-5 py-2.5 font-semibold text-white transition-colors hover:bg-brand-700 disabled:cursor-not-allowed disabled:opacity-50 sm:self-auto"
              >
                {isInspecting ? (
                  <>
                    <Loader2 className="w-5 h-5 mr-2 animate-spin" />
                    Inspecting files...
                  </>
                ) : (
                  'Inspect Files'
                )}
              </button>
            </div>
          </div>
        )}

        {step === 'review' && inspectionData && (
          <div className="space-y-6 animate-in fade-in duration-500">
            <div className="flex items-start justify-between gap-4">
              <div>
                <h2 className="text-2xl font-semibold text-ink">Review source data</h2>
                <p className="mt-1 text-sm text-muted">Confirm detected source columns before running reconciliation.</p>
              </div>
              <button 
                onClick={resetSession}
                className="flex shrink-0 items-center rounded-control border border-line-strong bg-white px-3 py-2 text-sm font-semibold text-slate-700 transition-colors hover:bg-surface-subtle hover:text-ink"
              >
                <RefreshCw className="w-4 h-4 mr-2" />
                Change Files
              </button>
            </div>

            {/* Warnings */}
            {inspectionData.warnings && inspectionData.warnings.length > 0 && (
              <div className="rounded-panel border border-amber-200 bg-amber-50/70 px-4 py-3" role="status">
                <h4 className="mb-1.5 flex items-center text-sm font-semibold text-amber-900">
                  <AlertTriangle className="mr-2 h-4 w-4" aria-hidden="true" />
                  Inspection Warnings
                </h4>
                <ul className="list-disc space-y-1 pl-6 text-xs leading-5 text-amber-900">
                  {inspectionData.warnings.map((w, i) => <li key={i}>{w}</li>)}
                </ul>
              </div>
            )}

            {/* Mappings */}
            <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-2">
              <MappingTable 
                title="Accounts Source Mapping"
                sourceType="accounts"
                detectedMappings={mappings.accounts}
                availableHeaders={inspectionData.available_columns.accounts}
                requiredFields={['Invoice Number', 'Taxable Value']}
                fileInfo={inspectionData.files.accounts}
                onMappingChange={(field, val) => handleMappingChange('accounts', field, val)}
              />
              <MappingTable 
                title="GSTR-2B Source Mapping"
                sourceType="gstr"
                detectedMappings={mappings.gstr}
                availableHeaders={inspectionData.available_columns.gstr}
                requiredFields={['GSTIN', 'Invoice Number', 'Taxable Value']}
                fileInfo={inspectionData.files.gstr}
                onMappingChange={(field, val) => handleMappingChange('gstr', field, val)}
              />
            </div>

            {/* Data Previews */}
            <section className="overflow-hidden rounded-panel border border-line bg-white">
              <div className="border-b border-line bg-surface-subtle px-5 py-3.5">
                <h3 className="text-base font-semibold text-ink">Data Preview</h3>
                <p className="mt-0.5 text-xs text-muted">Verify a sample of the detected transaction data.</p>
              </div>
              <div className="flex overflow-x-auto border-b border-line bg-white">
                <TabButton active={activeTab === 'accounts'} onClick={() => setActiveTab('accounts')} label={`Accounts Source (${inspectionData.files.accounts.row_count} rows)`} />
                <TabButton active={activeTab === 'gstr'} onClick={() => setActiveTab('gstr')} label={`GSTR-2B (${inspectionData.files.gstr.row_count} rows)`} />
                {inspectionData.preview.debit_notes && inspectionData.preview.debit_notes.length > 0 && (
                  <TabButton active={activeTab === 'debit_notes'} onClick={() => setActiveTab('debit_notes')} label={`Debit Notes`} />
                )}
                {inspectionData.preview.credit_notes && inspectionData.preview.credit_notes.length > 0 && (
                  <TabButton active={activeTab === 'credit_notes'} onClick={() => setActiveTab('credit_notes')} label={`Credit Notes`} />
                )}
              </div>
              
              <div className="bg-white">
                {activeTab === 'accounts' && <PreviewTable records={inspectionData.preview.accounts} type="accounts" />}
                {activeTab === 'gstr' && <PreviewTable records={inspectionData.preview.gstr} type="gstr" />}
                {activeTab === 'debit_notes' && inspectionData.preview.debit_notes && <PreviewTable records={inspectionData.preview.debit_notes} type="debit_notes" />}
                {activeTab === 'credit_notes' && inspectionData.preview.credit_notes && <PreviewTable records={inspectionData.preview.credit_notes} type="credit_notes" />}
              </div>
            </section>

            {reconcileError && (
              <div role="alert" className="bg-rose-50 border border-rose-200 text-rose-700 p-4 rounded-lg flex items-start mt-6">
                <AlertCircle className="w-5 h-5 mr-3 shrink-0 mt-0.5" />
                <div>
                  <p className="text-sm font-medium">{reconcileError}</p>
                  {reconcileError.includes('expired') && (
                    <button onClick={resetSession} className="mt-2 text-sm text-brand-600 font-medium hover:underline">
                      Upload Files Again
                    </button>
                  )}
                </div>
              </div>
            )}

            <div className="flex flex-col gap-3 border-t border-line pt-4 sm:flex-row sm:items-center sm:justify-between">
              <p className="text-xs text-muted">Reconciliation will use the source mappings shown above.</p>
              <button
                onClick={handleReconcile}
                disabled={!inspectionData.available_columns.accounts.length || !inspectionData.available_columns.gstr.length || isReconciling}
                aria-busy={isReconciling}
                className="flex self-end items-center rounded-control bg-brand-600 px-6 py-2.5 font-semibold text-white transition-colors hover:bg-brand-700 disabled:cursor-not-allowed disabled:bg-slate-300 sm:self-auto"
              >
                {isReconciling ? (
                  <>
                    <Loader2 className="w-5 h-5 mr-2 animate-spin" />
                    Reconciling records...
                  </>
                ) : (
                  'Run Reconciliation'
                )}
              </button>
            </div>
            
            {isReconciling && (
              <p className="mt-2 text-center text-sm text-slate-500" role="status" aria-live="polite">Comparing suppliers, invoice numbers, taxable values and tax amounts. This may take a moment for larger files.</p>
            )}
          </div>
        )}

        {step === 'export' && reconciliationData && (
          <div className="space-y-6">
            <div className="mb-6 flex flex-col items-start justify-between gap-4 sm:flex-row">
              <div>
                <h1 className="text-2xl font-semibold text-ink">Reconciliation Results</h1>
                <p className="mt-1 text-sm text-muted">Review matched records, differences and items that need attention.</p>
              </div>
              <button 
                onClick={resetSession}
                className="text-brand-600 hover:text-brand-800 text-sm font-medium px-4 py-2 bg-brand-50 rounded-lg hover:bg-brand-100 transition-colors max-sm:whitespace-nowrap"
              >
                Start New Reconciliation
              </button>
            </div>
            
            <ResultsDashboard 
              data={reconciliationData} 
              onManualMatch={handleManualMatch} 
              addToast={addToast}
              onSessionExpired={handleSessionExpired}
              tallyTarget={context?.gstinId ? { workspaceId: context.workspaceId, clientId: context.clientId, gstinId: context.gstinId } : undefined}
              workspaceRole={workspaceRole}
            />
          </div>
        )}

      </main>
      <ToastContainer toasts={toasts} removeToast={removeToast} />
    </div>
  );
}

const TabButton = ({ active, label, onClick }: { active: boolean, label: string, onClick: () => void }) => (
  <button
    onClick={onClick}
    className={`px-6 py-4 font-medium text-sm whitespace-nowrap transition-colors border-b-2 ${active ? 'border-brand-600 text-brand-700 bg-white' : 'border-transparent text-slate-500 hover:text-slate-800 hover:bg-slate-100'}`}
  >
    {label}
  </button>
)

export default App;
