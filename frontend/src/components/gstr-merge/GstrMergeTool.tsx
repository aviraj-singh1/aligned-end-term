import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { UploadCloud } from 'lucide-react';
import { ToastContainer, type ToastMessage } from '../common/Toast';
import {
  authErrorMessage,
  createGstrMerge,
  getGstrMerge,
  gstrMergeDownloadUrl,
  submitGstrMerge,
  uploadGstrFile,
} from '../../services/api';
import type { GstrMergeMode, GstrMergeRun, GstrMergeUploadTarget } from '../../types/api';
import { useWorkspaceData, type ClientDetail, type ClientSummary } from '../../workspace/management';
import { GstrMergeProgress } from './GstrMergeProgress';
import { GstrMergeResult } from './GstrMergeResult';
import { GstrMergeUploadSlot, type GstrSlotState } from './GstrMergeUploadSlot';
import { slotsFor } from './gstrMergeConfig';
import './gstr-merge.css';

const MAX_FILE_BYTES = 25 * 1024 * 1024;
const TERMINAL = new Set<GstrMergeRun['status']>(['COMPLETED', 'FAILED', 'EXPIRED', 'DELETED']);

function emptySlots(mode: GstrMergeMode): Record<string, GstrSlotState> {
  return Object.fromEntries(slotsFor(mode).map(([code]) => [code, { status: 'waiting', progress: null }]));
}

function financialYears(now = new Date()): string[] {
  const currentStart = now.getMonth() >= 3 ? now.getFullYear() : now.getFullYear() - 1;
  return Array.from({ length: 6 }, (_, index) => {
    const start = currentStart - index;
    return `${start}-${String((start + 1) % 100).padStart(2, '0')}`;
  });
}

export function GstrMergeTool({ workspaceId, client }: { workspaceId: string; client: ClientSummary }) {
  const [mode, setMode] = useState<GstrMergeMode>('QUARTERLY');
  const years = useMemo(financialYears, []);
  const [financialYear, setFinancialYear] = useState(years[0]);
  const [gstinId, setGstinId] = useState('');
  const [slotState, setSlotState] = useState<Record<string, GstrSlotState>>(() => emptySlots('QUARTERLY'));
  const [targets, setTargets] = useState<Record<string, GstrMergeUploadTarget>>({});
  const [run, setRun] = useState<GstrMergeRun | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [toasts, setToasts] = useState<ToastMessage[]>([]);
  const uploadController = useRef<AbortController | null>(null);
  const detail = useWorkspaceData<ClientDetail>(`${workspaceId}/clients/${client.id}`);
  const { data: historyData, error: historyError, reload: reloadHistory } = useWorkspaceData<{ items: GstrMergeRun[] }>(`${workspaceId}/clients/${client.id}/gstr-merges?limit=10`);
  const activeGstins = useMemo(() => detail.data?.gstins.filter(item => item.status === 'ACTIVE') || [], [detail.data?.gstins]);
  const slotDefinitions = slotsFor(mode);
  const currentRunId = run?.id;
  const currentRunStatus = run?.status;

  const notify = useCallback((message: string, type: ToastMessage['type']) => {
    setToasts(items => [...items, { id: crypto.randomUUID(), message, type }]);
  }, []);

  useEffect(() => {
    if (activeGstins.length === 1 && !gstinId) setGstinId(activeGstins[0].id);
  }, [activeGstins, gstinId]);

  useEffect(() => {
    if (run || !historyData) return;
    const durable = historyData.items.find(item => item.status === 'QUEUED' || item.status === 'PROCESSING');
    if (durable) setRun(durable);
  }, [historyData, run]);

  useEffect(() => {
    if (!currentRunId || (currentRunStatus !== 'QUEUED' && currentRunStatus !== 'PROCESSING')) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      try {
        const latest = await getGstrMerge(workspaceId, client.id, currentRunId, controller.signal);
        if (controller.signal.aborted) return;
        setRun(latest);
        if (TERMINAL.has(latest.status)) reloadHistory();
        else timer = setTimeout(() => void poll(), 3000);
      } catch (failure) {
        if (!controller.signal.aborted) {
          setError(authErrorMessage(failure));
          timer = setTimeout(() => void poll(), 5000);
        }
      }
    };
    timer = setTimeout(() => void poll(), 3000);
    return () => {
      controller.abort();
      if (timer) clearTimeout(timer);
    };
  }, [client.id, currentRunId, currentRunStatus, reloadHistory, workspaceId]);

  useEffect(() => () => uploadController.current?.abort(), []);

  function chooseFile(code: string, file: File) {
    const lower = file.name.toLowerCase();
    const validation = !lower.endsWith('.xlsx') || lower.endsWith('.xls')
      ? 'Choose an .xlsx workbook. Legacy .xls files are not supported.'
      : file.size <= 0 ? 'The workbook is empty.'
        : file.size > MAX_FILE_BYTES ? 'This workbook exceeds the 25 MB per-file limit.' : '';
    setSlotState(items => ({ ...items, [code]: validation
      ? { file, status: 'failed', progress: null, error: validation }
      : { file, status: 'selected', progress: null } }));
  }

  function changeMode(next: GstrMergeMode) {
    if (next === mode) return;
    if (Object.values(slotState).some(item => item.file)
      && !window.confirm('Changing mode clears the selected workbooks. Continue?')) return;
    setMode(next);
    setSlotState(emptySlots(next));
    setError('');
  }

  async function performUpload(code: string, target: GstrMergeUploadTarget, file: File) {
    if (new Date(target.expires_at).getTime() <= Date.now()) {
      setSlotState(items => ({ ...items, [code]: { ...items[code], status: 'failed', progress: null, error: 'This upload target expired. Start a new consolidation to receive safe replacement targets.' } }));
      return false;
    }
    setSlotState(items => ({ ...items, [code]: { ...items[code], status: 'uploading', progress: null, error: undefined } }));
    try {
      await uploadGstrFile(target, file, {
        signal: uploadController.current?.signal,
        onProgress: progress => setSlotState(items => ({ ...items, [code]: { ...items[code], progress } })),
      });
      setSlotState(items => ({ ...items, [code]: { ...items[code], status: 'uploaded', progress: 100 } }));
      return true;
    } catch (failure) {
      if (failure instanceof DOMException && failure.name === 'AbortError') return false;
      setSlotState(items => ({ ...items, [code]: { ...items[code], status: 'failed', progress: null, error: failure instanceof Error ? failure.message : 'Upload failed. Retry this workbook.' } }));
      return false;
    }
  }

  async function beginUploads() {
    if (!gstinId || !financialYear || slotDefinitions.some(([code]) => slotState[code]?.status !== 'selected')) return;
    setBusy(true);
    setError('');
    uploadController.current?.abort();
    uploadController.current = new AbortController();
    try {
      const created = await createGstrMerge(workspaceId, client.id, {
        client_gstin_id: gstinId,
        financial_year: financialYear,
        mode,
        files: slotDefinitions.map(([code]) => ({ period_code: code, filename: slotState[code].file!.name, size_bytes: slotState[code].file!.size })),
      });
      setRun(created.run);
      const issued = Object.fromEntries(created.upload_targets.map(target => [target.period_code, target]));
      setTargets(issued);
      const outcomes = await Promise.all(slotDefinitions.map(([code]) => performUpload(code, issued[code], slotState[code].file!)));
      if (outcomes.every(Boolean)) notify('All workbooks uploaded. Review and submit the consolidation.', 'success');
      else notify('One or more workbooks failed to upload. Retry the marked slots.', 'error');
    } catch (failure) {
      setError(authErrorMessage(failure));
    } finally {
      setBusy(false);
    }
  }

  async function retryUpload(code: string) {
    const target = targets[code];
    const file = slotState[code]?.file;
    if (!target || !file) return;
    await performUpload(code, target, file);
  }

  async function submit() {
    if (!run || run.status !== 'UPLOADING' || slotDefinitions.some(([code]) => slotState[code]?.status !== 'uploaded')) return;
    setBusy(true);
    setError('');
    try {
      const queued = await submitGstrMerge(workspaceId, client.id, run.id, run.version);
      setRun(queued);
      reloadHistory();
      notify('Consolidation queued. You may safely leave this page.', 'success');
    } catch (failure) {
      setError(authErrorMessage(failure));
    } finally {
      setBusy(false);
    }
  }

  function startNew() {
    uploadController.current?.abort();
    uploadController.current = null;
    setRun(null);
    setTargets({});
    setSlotState(emptySlots(mode));
    setError('');
  }

  const uploaded = slotDefinitions.filter(([code]) => slotState[code]?.status === 'uploaded').length;
  const selected = slotDefinitions.filter(([code]) => slotState[code]?.status === 'selected').length;
  const currentGstin = activeGstins.find(item => item.id === (run?.client_gstin_id || gstinId))?.gstin || '';

  return <div className="gstr-tool">
    <ToastContainer toasts={toasts} removeToast={id => setToasts(items => items.filter(item => item.id !== id))} />
    <section className="management-panel gstr-client-context"><div><p className="auth-eyebrow">SELECTED CLIENT</p><h2>{client.name}</h2></div><div className="gstr-config-fields">
      <label>GST registration<select value={gstinId} disabled={!detail.data || Boolean(run)} onChange={event => { setGstinId(event.target.value); setRun(null); setTargets({}); }}><option value="">Select active GSTIN</option>{activeGstins.map(item => <option key={item.id} value={item.id}>{item.gstin}</option>)}</select></label>
      <label>Financial year<select value={financialYear} disabled={Boolean(run)} onChange={event => setFinancialYear(event.target.value)}>{years.map(year => <option key={year}>{year}</option>)}</select></label>
    </div></section>
    {detail.error ? <p className="auth-notice" role="alert">{detail.error}</p> : null}
    {detail.data && !activeGstins.length ? <p className="auth-notice" role="alert">This client has no active GSTIN registrations. Add or activate one from Clients before consolidating.</p> : null}

    {!run ? <>
      <section className="management-panel gstr-mode-panel"><div><h2>Choose consolidation mode</h2><p>Upload one workbook for every period in the selected Indian financial year.</p></div><fieldset><legend>Period frequency</legend><label><input type="radio" name="gstr-mode" checked={mode === 'QUARTERLY'} onChange={() => changeMode('QUARTERLY')} /> Quarterly · 4 files</label><label><input type="radio" name="gstr-mode" checked={mode === 'MONTHLY'} onChange={() => changeMode('MONTHLY')} /> Monthly · 12 files</label></fieldset></section>
      <section aria-labelledby="gstr-files-heading"><div className="gstr-section-heading"><div><h2 id="gstr-files-heading">Upload period workbooks</h2><p>{mode === 'QUARTERLY' ? 'Four quarterly workbooks' : 'Twelve monthly workbooks in Indian FY order'} · .xlsx only · 25 MB each</p></div><span>{selected} of {slotDefinitions.length} selected</span></div>
        <div className="gstr-upload-grid">{slotDefinitions.map(([code, label]) => <GstrMergeUploadSlot key={code} code={code} label={label} state={slotState[code]} locked={busy} onFile={file => chooseFile(code, file)} onRemove={() => setSlotState(items => ({ ...items, [code]: { status: 'waiting', progress: null } }))} onRetry={() => void retryUpload(code)} />)}</div>
      </section>
      {error ? <p className="auth-notice" role="alert">{error}</p> : null}
      <div className="gstr-primary-action"><button className="management-primary" type="button" disabled={busy || !gstinId || selected !== slotDefinitions.length} onClick={() => void beginUploads()}><UploadCloud size={17} aria-hidden="true" />{busy ? 'Preparing secure uploads…' : 'Upload selected files'}</button></div>
    </> : <>
      <GstrMergeProgress run={run} />
      {run.status === 'UPLOADING' && Object.keys(targets).length ? <section aria-labelledby="gstr-upload-status-heading"><div className="gstr-section-heading"><div><h2 id="gstr-upload-status-heading">Secure uploads</h2><p>Each workbook is uploaded directly to its private destination.</p></div><span>{uploaded} of {slotDefinitions.length} uploaded</span></div><div className="gstr-upload-grid">{slotDefinitions.map(([code, label]) => <GstrMergeUploadSlot key={code} code={code} label={label} state={slotState[code]} locked onFile={() => {}} onRemove={() => {}} onRetry={() => void retryUpload(code)} />)}</div><div className="gstr-primary-action"><button className="management-primary" type="button" disabled={busy || uploaded !== slotDefinitions.length} onClick={() => void submit()}>{busy ? 'Submitting…' : 'Submit for consolidation'}</button><button type="button" onClick={startNew}>Start over</button></div></section> : null}
      {run.status === 'UPLOADING' && !Object.keys(targets).length ? <section className="management-panel"><h2>Upload session cannot be resumed</h2><p>The backend does not refresh expired or lost upload targets. Start a new consolidation to receive a new safe set of private upload destinations.</p><button type="button" onClick={startNew}>Start New Consolidation</button></section> : null}
      {error ? <p className="auth-notice" role="alert">{error}</p> : null}
      <GstrMergeResult workspaceId={workspaceId} clientId={client.id} run={run} gstin={currentGstin} onStartNew={startNew} />
    </>}

    <section className="management-panel gstr-history"><div className="gstr-section-heading"><div><h2>Recent consolidations</h2><p>Durable runs remain visible after refreshing this page.</p></div></div>
      {historyError ? <p role="alert">{historyError} <button type="button" onClick={reloadHistory}>Retry</button></p> : !historyData ? <p role="status">Loading recent consolidations…</p> : historyData.items.length ? historyData.items.map(item => <div className="management-row" key={item.id}><div><strong>{new Date(item.created_at).toLocaleString()}</strong><small>{item.mode === 'QUARTERLY' ? 'Quarterly' : 'Monthly'} · FY {item.financial_year} · {item.status}</small></div><div className="management-actions">{item.artifact_available ? <a href={gstrMergeDownloadUrl(workspaceId, client.id, item.id)}>Download</a> : null}<button type="button" onClick={() => setRun(item)}>{item.status === 'QUEUED' || item.status === 'PROCESSING' ? 'View progress' : 'View details'}</button></div></div>) : <p>No consolidations yet.</p>}
    </section>
  </div>;
}
