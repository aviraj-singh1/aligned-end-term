import { normalizeUploadFilename } from './uploadFilename';

export const FILE_READ_ERROR = "We couldn't read this file from its current location. Save a copy to your device and select it again.";

/** Materialize bytes now: wrapping the original Blob alone can retain its backing source. */
export async function createStableUploadFile(original: File): Promise<File> {
  const name = normalizeUploadFilename(original.name);
  const type = original.type;
  const bytes = await original.arrayBuffer();
  // No original File or temporary buffer is stored in application state.
  // Keep extensionless names: backend content detection remains authoritative.
  return new File([bytes], name, { type, lastModified: Date.now() });
}
