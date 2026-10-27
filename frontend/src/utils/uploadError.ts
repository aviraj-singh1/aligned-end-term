export function getUploadErrorMessage(error: unknown): string {
  const failure = error as { response?: { data?: { detail?: unknown } }; message?: string; code?: string; name?: string; cause?: { message?: string; name?: string; code?: string } } | null;
  const detail = failure?.response?.data?.detail;
  if (typeof detail === 'string') return detail;
  const indicators = [failure?.message, failure?.code, failure?.name, failure?.cause?.message, failure?.cause?.name, failure?.cause?.code].join(' ');
  if (!failure?.response && /ERR_UPLOAD_FILE_CHANGED|NotReadableError|could not be read|cannot be read|unreadable|stale.*file/i.test(indicators)) {
    return 'This file could not be read during upload. Try selecting it again, or save a local copy first.';
  }
  // Chrome often exposes only Axios's generic Network Error, not its internal reason.
  if (!failure?.response && /Network Error|ERR_NETWORK/i.test(indicators)) {
    return 'The upload did not complete and no server response was received. Try selecting the files again, or save local copies first. If this continues, check the connection and try again.';
  }
  return failure?.message || 'An error occurred during inspection';
}
