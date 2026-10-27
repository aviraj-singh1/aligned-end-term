// Strip boundary whitespace (including NBSP/BOM) and zero-width spaces only.
// Do not rewrite internal characters, suffixes, or Unicode direction controls.
export const normalizeUploadFilename = (name: string): string =>
  name.replace(/^[\s\u200B]+|[\s\u200B]+$/g, '');
