export const triggerBrowserDownload = (blob: Blob, filename: string) => {
  const url = window.URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.style.display = 'none';
  a.href = url;
  a.download = filename;
  
  // Need to append the anchor to document body for some browsers (e.g. Firefox)
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);

  // Let the browser consume the object URL before releasing it. Immediate
  // revocation can cancel downloads in some browser implementations.
  window.setTimeout(() => window.URL.revokeObjectURL(url), 0);
};
