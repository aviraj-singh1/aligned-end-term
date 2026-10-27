// Upload transport safeguard; this is not an engine capacity limit.
export const MAX_COMBINED_UPLOAD_BYTES = 25_000_000;
export const UPLOAD_LIMIT_LABEL = '25 MB combined upload limit';

export function validateCombinedUpload(files: (File | null | undefined)[]): void {
  const bytes = files.reduce((total, file) => total + (file?.size || 0), 0);
  if (bytes > MAX_COMBINED_UPLOAD_BYTES) {
    throw new Error(`The selected files exceed the 25 MB combined upload limit. Selected total: ${(bytes / 1_000_000).toFixed(2)} MB (${bytes.toLocaleString('en-IN')} bytes).`);
  }
}
