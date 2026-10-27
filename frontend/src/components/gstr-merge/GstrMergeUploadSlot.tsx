import { CheckCircle2, FileSpreadsheet, RotateCcw, Trash2 } from 'lucide-react';

export type GstrSlotStatus = 'waiting' | 'selected' | 'uploading' | 'uploaded' | 'failed';

export interface GstrSlotState {
  file?: File;
  status: GstrSlotStatus;
  progress: number | null;
  error?: string;
}

export function GstrMergeUploadSlot({ code, label, state, locked, onFile, onRemove, onRetry }: {
  code: string;
  label: string;
  state: GstrSlotState;
  locked: boolean;
  onFile: (file: File) => void;
  onRemove: () => void;
  onRetry: () => void;
}) {
  const inputId = `gstr-file-${code}`;
  const statusLabel = state.status === 'waiting' ? 'Waiting' : state.status[0].toUpperCase() + state.status.slice(1);
  return <article className={`gstr-upload-slot is-${state.status}`}>
    <div className="gstr-slot-heading">
      <span className="gstr-period-code">{code}</span>
      <div><h3>{label}</h3><small>{statusLabel}</small></div>
      {state.status === 'uploaded' ? <CheckCircle2 aria-label="Uploaded" /> : <FileSpreadsheet aria-hidden="true" />}
    </div>
    {state.file ? <div className="gstr-file-detail">
      <strong>{state.file.name}</strong>
      <span>{(state.file.size / 1_048_576).toFixed(2)} MB</span>
    </div> : <p>Select one Excel workbook for this return period.</p>}
    {state.status === 'uploading' && state.progress !== null
      ? <progress aria-label={`${label} upload progress`} max="100" value={state.progress}>{state.progress}%</progress>
      : null}
    {state.error ? <p className="gstr-slot-error" role="alert">{state.error}</p> : null}
    <div className="gstr-slot-actions">
      <label className={`gstr-file-button ${locked ? 'is-disabled' : ''}`} htmlFor={inputId}>{state.file ? `Replace ${code} file` : `Choose ${code} file`}</label>
      <input id={inputId} type="file" accept=".xlsx,.XLSX" disabled={locked} onChange={event => {
        const file = event.currentTarget.files?.[0];
        if (file) onFile(file);
        event.currentTarget.value = '';
      }} />
      {state.file && !locked ? <button type="button" onClick={onRemove}><Trash2 size={15} aria-hidden="true" /> Remove</button> : null}
      {state.status === 'failed' ? <button type="button" onClick={onRetry}><RotateCcw size={15} aria-hidden="true" /> Retry</button> : null}
    </div>
  </article>;
}
