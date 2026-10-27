import React, { useCallback, useEffect, useId, useRef, useState } from 'react';
import { UploadCloud, X, FileText, CheckCircle2 } from 'lucide-react';
import { clsx } from 'clsx';
import { normalizeUploadFilename } from '../../utils/uploadFilename';
import { createStableUploadFile, FILE_READ_ERROR } from '../../utils/stableUploadFile';
import { MAX_COMBINED_UPLOAD_BYTES } from '../../utils/transportLimits';

interface Props {
  title: string;
  description: string;
  file: File | null;
  onFileSelect: (file: File | null) => void;
  required?: boolean;
  error?: string | null;
  onReadingChange?: (reading: boolean) => void;
}

const ALLOWED_EXTENSIONS = ['.xls', '.xlsx', '.json'];
const EXTENSIONLESS_MIME_TYPES = [
  '', 'application/octet-stream', 'application/zip', 'application/x-zip-compressed', 'text/plain',
  'application/vnd.ms-excel', 'application/msexcel', 'application/x-msexcel',
  'application/excel', 'application/x-excel',
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'application/json', 'text/json',
];

export const FileUploader: React.FC<Props> = ({ title, description, file, onFileSelect, required = false, error, onReadingChange }) => {
  const inputRef = useRef<HTMLInputElement>(null);
  const inputId = useId();
  const errorId = `${inputId}-error`;
  const [validationError, setValidationError] = useState<string | null>(null);
  const [isReading, setIsReading] = useState(false);
  const selectionVersion = useRef(0);
  useEffect(() => () => { selectionVersion.current += 1; }, []);
  const visibleError = error || validationError;
  const hasFile = file !== null;

  const validateAndSelect = useCallback(async (selectedFile: File) => {
    const version = ++selectionVersion.current;
    // Drop a previous snapshot even when its replacement is invalid/unreadable.
    if (hasFile) onFileSelect(null);
    setIsReading(false);
    onReadingChange?.(false);
    const filename = normalizeUploadFilename(selectedFile.name);
    const dotIndex = filename.lastIndexOf('.');
    const ext = dotIndex < 0 ? '' : filename.substring(dotIndex).toLowerCase();
    const extensionlessCandidate = !ext && EXTENSIONLESS_MIME_TYPES.includes(selectedFile.type.toLowerCase());
    if (!ALLOWED_EXTENSIONS.includes(ext) && !extensionlessCandidate) {
      setValidationError('Unsupported file type. Supported formats: .xls, .xlsx and .json.');
      return;
    }
    if (selectedFile.size > MAX_COMBINED_UPLOAD_BYTES) {
      setValidationError('The selected file exceeds the 25 MB combined upload limit.');
      return;
    }
    setValidationError(null);
    setIsReading(true);
    onReadingChange?.(true);
    try {
      const snapshot = await createStableUploadFile(selectedFile);
      if (selectionVersion.current === version) onFileSelect(snapshot);
    } catch {
      if (selectionVersion.current === version) setValidationError(FILE_READ_ERROR);
    } finally {
      if (selectionVersion.current === version) {
        setIsReading(false);
        onReadingChange?.(false);
      }
    }
  }, [hasFile, onFileSelect, onReadingChange]);

  const handleDrop = useCallback((e: React.DragEvent<HTMLButtonElement>) => {
    e.preventDefault();
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      validateAndSelect(e.dataTransfer.files[0]);
    }
  }, [validateAndSelect]);

  const handleDragOver = (e: React.DragEvent<HTMLButtonElement>) => {
    e.preventDefault();
  };

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files.length > 0) {
      validateAndSelect(e.target.files[0]);
    }
    // Release the DOM FileList and permit selecting the same source again.
    e.target.value = '';
  };

  const clearFile = (e: React.MouseEvent) => {
    e.stopPropagation();
    selectionVersion.current += 1;
    setIsReading(false);
    onReadingChange?.(false);
    setValidationError(null);
    onFileSelect(null);
    if (inputRef.current) {
      inputRef.current.value = '';
    }
  };

  return (
    <section className="flex h-full flex-col overflow-hidden rounded-panel border border-line bg-white">
      <div className="flex min-h-[72px] items-start justify-between gap-3 border-b border-line bg-surface-subtle px-4 py-3.5">
        <div className="min-w-0">
          <h3 className="text-[15px] font-semibold text-ink">{title}</h3>
          <p className="mt-1 text-xs leading-4 text-muted">{description}</p>
        </div>
        <span className={clsx(
          "shrink-0 rounded-full border px-2 py-0.5 text-[11px] font-semibold",
          required
            ? "border-brand-200 bg-brand-50 text-brand-700"
            : "border-slate-200 bg-white text-slate-500"
        )}>
          {required ? 'Required' : 'Optional'}
        </span>
      </div>

      <input
        id={inputId}
        type="file"
        accept=".xls,.xlsx,.json,application/vnd.ms-excel,application/msexcel,application/x-msexcel,application/excel,application/x-excel,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/json"
        className="hidden"
        ref={inputRef}
        // Provider metadata may omit both extension and MIME; validate again after selection.
        aria-label={`${title} file`}
        aria-describedby={visibleError ? errorId : undefined}
        onChange={handleChange}
      />

      <div className="flex flex-1 p-4" aria-busy={isReading}>
        {isReading ? (
          <div className="flex min-h-40 w-full items-center justify-between gap-3 rounded-control border border-line px-5 py-6">
            <span role="status" className="text-sm text-muted">Reading file…</span>
            <button type="button" onClick={clearFile} aria-label={`Remove ${title} file`} className="text-sm font-semibold text-slate-600">Remove</button>
          </div>
        ) : !file ? (
          <button
            type="button"
            className={clsx(
              "flex min-h-40 w-full flex-col items-center justify-center rounded-control border border-dashed px-5 py-6 text-center transition-colors",
              visibleError
                ? "border-rose-300 bg-rose-50/60 hover:bg-rose-50"
                : "border-line-strong bg-surface-subtle hover:border-brand-300 hover:bg-brand-50/60"
            )}
            onClick={() => inputRef.current?.click()}
            onDrop={handleDrop}
            onDragOver={handleDragOver}
            aria-controls={inputId}
            aria-label={`Browse ${title} file`}
            aria-describedby={visibleError ? errorId : undefined}
          >
            <div className="mb-3 flex h-10 w-10 items-center justify-center rounded-full border border-brand-100 bg-brand-50 text-brand-600">
              <UploadCloud className="h-5 w-5" aria-hidden="true" />
            </div>
            <span className="text-sm font-semibold text-ink">Click to browse or drag &amp; drop</span>
            <span className="mt-1.5 text-xs text-muted">.xls, .xlsx and .json · 25 MB combined upload limit</span>
            {visibleError && (
              <span id={errorId} className="mt-3 text-xs font-medium leading-4 text-rose-700" role="alert">
                {visibleError}
              </span>
            )}
          </button>
        ) : (
          <div className="flex min-h-40 w-full items-center rounded-control border border-brand-200 bg-brand-50/60 px-5 py-6">
            <div className="flex min-w-0 flex-1 items-start gap-3">
              <div className="shrink-0 rounded-control border border-brand-100 bg-white p-2.5 text-brand-600">
                <FileText className="h-6 w-6" aria-hidden="true" />
              </div>
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-semibold text-ink" title={file.name}>{file.name}</p>
                <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted">
                  <span>{(file.size / 1024).toFixed(1)} KB</span>
                  <span aria-hidden="true">·</span>
                  <span className="flex items-center font-semibold text-status-perfect">
                    <CheckCircle2 className="mr-1 h-3.5 w-3.5" aria-hidden="true" />
                    Ready
                  </span>
                </div>
              </div>
            </div>
            <button
              type="button"
              onClick={clearFile}
              className="ml-3 shrink-0 px-2 py-1.5 text-xs font-semibold text-slate-600 hover:bg-white hover:text-rose-700"
              title="Remove file"
              aria-label={`Remove ${file.name}`}
            >
              <X className="mr-1 inline h-4 w-4" aria-hidden="true" />
              Remove
            </button>
          </div>
        )}
      </div>
    </section>
  );
};
