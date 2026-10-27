import { useState } from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { FileUploader } from './components/upload/FileUploader';
import { normalizeUploadFilename } from './utils/uploadFilename';
import { inspectFiles } from './services/api';
import { FILE_READ_ERROR } from './utils/stableUploadFile';
import { getUploadErrorMessage } from './utils/uploadError';
import App from './App';

const { post } = vi.hoisted(() => ({ post: vi.fn().mockResolvedValue({ data: {} }) }));
vi.mock('axios', () => ({ default: { create: () => ({ post, get: vi.fn().mockResolvedValue({ data: { status: 'ok' } }), interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } } }) } }));
const excelMime = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';
const legacyExcelMime = 'application/vnd.ms-excel';
const cases = [
  { name: '4a.xlsx', type: excelMime, expected: '4a.xlsx' },
  { name: '4a.XLSX', type: excelMime, expected: '4a.XLSX' },
  { name: '4a.xls ', type: legacyExcelMime, expected: '4a.xls' },
  { name: 'Legacy workbook', type: legacyExcelMime, expected: 'Legacy workbook' },
  { name: 'Legacy export', type: 'application/x-msexcel', expected: 'Legacy export' },
  { name: ' 4a.xlsx ', type: excelMime, expected: '4a.xlsx' },
  { name: '4a.xlsx\u00a0\u2003\ufeff', type: '', expected: '4a.xlsx' },
  { name: '\u200b4a.xlsx\u200b ', type: '', expected: '4a.xlsx' },
  { name: '4a.xlsx', type: '', expected: '4a.xlsx' },
  { name: '4a.xlsx', type: 'application/octet-stream', expected: '4a.xlsx' },
  { name: '4a.json ', type: '', expected: '4a.json' },
  { name: '4a', type: excelMime, expected: '4a' },
  { name: '4a', type: 'application/octet-stream', expected: '4a' },
  { name: 'Account Source', type: excelMime, expected: 'Account Source' },
  { name: 'sample', type: 'application/json', expected: 'sample' },
  { name: 'sample', type: '', expected: 'sample' },
  { name: 'sample', type: 'application/pdf', error: /Unsupported file type/ },
  { name: '4a.xlsx (1)', type: excelMime, error: /Unsupported file type/ },
  { name: '4a.exe', type: excelMime, error: /Unsupported file type/ },
  { name: '4a.xlsx.exe', type: excelMime, error: /Unsupported file type/ },
  { name: '4a.xl\u200bsx', type: excelMime, error: /Unsupported file type/ },
  { name: '4a.xlsx\u202e', type: excelMime, error: /Unsupported file type/ },
];

describe.each(['browse', 'drop'] as const)('%s upload metadata validation', mode => {
  it.each(cases)('validates $name with MIME $type conservatively', async ({ name, type, expected, error }) => {
    const onFileSelect = vi.fn();
    render(<FileUploader title="Accounts" description="Synthetic test" file={null} onFileSelect={onFileSelect} />);
    // Deliberately not workbook bytes: frontend checks metadata, backend checks contents.
    const original = new File(['invalid workbook bytes'], name, { type, lastModified: 1234 });
    const readBytes = vi.spyOn(original, 'arrayBuffer');
    if (mode === 'browse') {
      fireEvent.change(screen.getByLabelText('Accounts file'), { target: { files: [original] } });
    } else {
      fireEvent.drop(screen.getByRole('button', { name: 'Browse Accounts file' }), { dataTransfer: { files: [original] } });
    }
    if (error) {
      expect(readBytes).not.toHaveBeenCalled();
      expect(onFileSelect).not.toHaveBeenCalled();
      expect(screen.getByRole('alert')).toHaveTextContent(error);
      return;
    }
    await waitFor(() => expect(onFileSelect).toHaveBeenCalledOnce());
    expect(readBytes).toHaveBeenCalledOnce();
    const accepted: File = onFileSelect.mock.calls[0][0];
    expect(accepted.name).toBe(expected);
    expect(accepted.type).toBe(type);
    expect(accepted.size).toBe(original.size);
    expect(accepted).not.toBe(original);
    expect(original.name).toBe(name);
    const read = (file: File) => new Promise<string>((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result as string);
      reader.onerror = reject;
      reader.readAsText(file);
    });
    expect(await read(accepted)).toBe('invalid workbook bytes');
    await inspectFiles(accepted, accepted, accepted);
    const [url, form] = post.mock.calls.at(-1)! as [string, FormData];
    expect(url).toBe('/api/inspect');
    for (const key of ['accounts_file', 'gstr_file', 'debit_notes_file']) {
      expect(form.get(key)).toBe(accepted);
      expect(form.getAll(key)).toHaveLength(1);
      expect((form.get(key) as File).name).toBe(expected);
      expect((form.get(key) as File).size).toBe(original.size);
    }
  });

  it.each(['4a.xlsx ', 'Account Source'])('enforces the pilot transport limit for %s', name => {
    const onFileSelect = vi.fn();
    render(<FileUploader title="Accounts" description="Synthetic test" file={null} onFileSelect={onFileSelect} />);
    const oversized = new File(['x'], name);
    const readBytes = vi.spyOn(oversized, 'arrayBuffer');
    Object.defineProperty(oversized, 'size', { value: 25_000_001 });
    if (mode === 'browse') fireEvent.change(screen.getByLabelText('Accounts file'), { target: { files: [oversized] } });
    else fireEvent.drop(screen.getByRole('button', { name: 'Browse Accounts file' }), { dataTransfer: { files: [oversized] } });
    expect(onFileSelect).not.toHaveBeenCalled();
    expect(readBytes).not.toHaveBeenCalled();
    expect(screen.getByRole('alert')).toHaveTextContent('25 MB combined upload limit');
  });
});

function Slot({ onSnapshot }: { onSnapshot?: (file: File | null) => void } = {}) {
  const [file, setFile] = useState<File | null>(null);
  return <FileUploader title="Accounts" description="Test" file={file} onFileSelect={next => { setFile(next); onSnapshot?.(next); }} />;
}

function deferredFile(name = 'accounts.xlsx') {
  let resolve!: (bytes: ArrayBuffer) => void;
  let reject!: (error: Error) => void;
  const file = new File(['bytes'], name);
  const read = vi.fn(() => new Promise<ArrayBuffer>((yes, no) => { resolve = yes; reject = no; }));
  Object.defineProperty(file, 'arrayBuffer', { value: read });
  return { file, read, resolve: () => resolve(new Uint8Array([0, 1, 255, 42]).buffer), reject: () => reject(new DOMException('gone', 'NotReadableError')) };
}

it('reads all three sources immediately and uploads only independent snapshots after the providers expire', async () => {
  post.mockClear();
  post.mockImplementationOnce(() => new Promise(() => {}));
  render(<App />);
  const sources = ['Accounts Purchase Data', 'GSTR-2B', 'Debit Notes'].map((title, i) => {
    const source = deferredFile(`source-${i}.xlsx`);
    fireEvent.change(screen.getByLabelText(`${title} file`), { target: { files: [source.file] } });
    expect(source.read).toHaveBeenCalledOnce();
    expect(screen.getByLabelText(`${title} file`)).toHaveValue('');
    return source;
  });
  expect(post).not.toHaveBeenCalled();
  expect(screen.queryByText('Ready')).not.toBeInTheDocument();
  await act(async () => { sources[0].resolve(); sources[1].resolve(); });
  expect(screen.getByText('Inspect Files')).toBeDisabled(); // Optional slot still reading.
  await act(async () => sources[2].resolve());
  expect(screen.getAllByText('Ready')).toHaveLength(3);
  sources.forEach(source => source.read.mockRejectedValue(new Error('provider expired')));
  fireEvent.click(screen.getByText('Inspect Files'));
  expect(post).toHaveBeenCalledOnce();
  const form = post.mock.calls[0][1] as FormData;
  const copies = ['accounts_file', 'gstr_file', 'debit_notes_file'].map((key, i) => {
    expect(form.getAll(key)).toHaveLength(1);
    const copy = form.get(key) as File;
    expect(copy).not.toBe(sources[i].file);
    expect(sources[i].read).toHaveBeenCalledOnce();
    return copy;
  });
  expect(new Set(copies).size).toBe(3);
  for (const copy of copies) expect(new Uint8Array(await copy.arrayBuffer())).toEqual(new Uint8Array([0, 1, 255, 42]));
});

it('drops the old snapshot on replacement and drops the replacement on Remove', async () => {
  const onSnapshot = vi.fn();
  render(<Slot onSnapshot={onSnapshot} />);
  const first = deferredFile('first.xlsx');
  fireEvent.change(screen.getByLabelText('Accounts file'), { target: { files: [first.file] } });
  await act(async () => first.resolve());
  expect(screen.getByText('first.xlsx')).toBeInTheDocument();
  const oldSnapshot = onSnapshot.mock.calls.at(-1)![0];
  const second = deferredFile('second.xlsx');
  fireEvent.change(screen.getByLabelText('Accounts file'), { target: { files: [second.file] } });
  expect(screen.queryByText('first.xlsx')).not.toBeInTheDocument();
  expect(screen.queryByText('Ready')).not.toBeInTheDocument();
  expect(onSnapshot).toHaveBeenLastCalledWith(null);
  await act(async () => second.resolve());
  expect(screen.getByText('second.xlsx')).toBeInTheDocument();
  const replacement = onSnapshot.mock.calls.at(-1)![0];
  expect(replacement).not.toBe(oldSnapshot);
  expect(replacement).not.toBe(second.file);
  await inspectFiles(replacement, replacement);
  expect((post.mock.calls.at(-1)![1] as FormData).get('accounts_file')).toBe(replacement);
  fireEvent.click(screen.getByRole('button', { name: 'Remove second.xlsx' }));
  expect(screen.queryByText('second.xlsx')).not.toBeInTheDocument();
  expect(onSnapshot).toHaveBeenLastCalledWith(null);
  expect(screen.queryByText('Ready')).not.toBeInTheDocument();
});

it('shows an actionable read failure without Ready and permits retrying the same file', async () => {
  render(<Slot />);
  const source = deferredFile();
  fireEvent.change(screen.getByLabelText('Accounts file'), { target: { files: [source.file] } });
  await act(async () => source.reject());
  expect(screen.getByRole('alert')).toHaveTextContent(FILE_READ_ERROR);
  expect(screen.queryByText('Ready')).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Accounts file'), { target: { files: [source.file] } });
  await act(async () => source.resolve());
  expect(screen.getByText('Ready')).toBeInTheDocument();
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});

it.each(['resolve', 'reject'] as const)('ignores a removed pending read that later %ss', async outcome => {
  render(<Slot />);
  const source = deferredFile();
  fireEvent.change(screen.getByLabelText('Accounts file'), { target: { files: [source.file] } });
  fireEvent.click(screen.getByRole('button', { name: 'Remove Accounts file' }));
  await act(async () => source[outcome]());
  expect(screen.queryByText('Ready')).not.toBeInTheDocument();
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});

it('does not restore a stale selection when overlapping reads finish out of order', async () => {
  render(<Slot />);
  const old = deferredFile('old.xlsx');
  const latest = deferredFile('latest.xlsx');
  fireEvent.change(screen.getByLabelText('Accounts file'), { target: { files: [old.file] } });
  fireEvent.change(screen.getByLabelText('Accounts file'), { target: { files: [latest.file] } });
  await act(async () => latest.resolve());
  await act(async () => old.resolve());
  expect(screen.getByText('latest.xlsx')).toBeInTheDocument();
  expect(screen.queryByText('old.xlsx')).not.toBeInTheDocument();
});

it('does not publish a snapshot after unmount', async () => {
  const selected = vi.fn();
  const { unmount } = render(<FileUploader title="Accounts" description="Test" file={null} onFileSelect={selected} />);
  const source = deferredFile();
  fireEvent.change(screen.getByLabelText('Accounts file'), { target: { files: [source.file] } });
  unmount();
  await act(async () => source.resolve());
  expect(selected).not.toHaveBeenCalled();
});

it.each([
  { message: 'net::ERR_UPLOAD_FILE_CHANGED' },
  { name: 'NotReadableError' },
  { message: 'Network Error', cause: { code: 'ERR_UPLOAD_FILE_CHANGED' } },
])('maps unreadable upload errors to actionable nontechnical text: %j', error => {
  expect(getUploadErrorMessage(error)).toBe('This file could not be read during upload. Try selecting it again, or save a local copy first.');
});

it('does not diagnose a generic network error as a definite provider or connectivity failure', () => {
  expect(getUploadErrorMessage({ message: 'Network Error' })).toContain('no server response');
  expect(getUploadErrorMessage({ response: { data: { detail: 'Invalid workbook' } }, message: 'Network Error' })).toBe('Invalid workbook');
});

it.each([
  ['net::ERR_UPLOAD_FILE_CHANGED', 'This file could not be read during upload.'],
  ['Network Error', 'The upload did not complete and no server response was received.'],
])('displays the mapped inspection failure for %s', async (message, expected) => {
  render(<App />);
  for (const title of ['Accounts Purchase Data', 'GSTR-2B']) {
    fireEvent.change(screen.getByLabelText(`${title} file`), { target: { files: [new File(['{}'], 'source.json')] } });
  }
  await waitFor(() => expect(screen.getByText('Inspect Files')).not.toBeDisabled());
  post.mockRejectedValueOnce(new Error(message));
  fireEvent.click(screen.getByText('Inspect Files'));
  expect(await screen.findByRole('alert')).toHaveTextContent(expected);
  expect(screen.getByText('Inspect Files')).not.toBeDisabled();
});

it('preserves logical filename characters and performs no Unicode rewriting', () => {
  expect(normalizeUploadFilename('  April report 4a.XLSX\u200b')).toBe('April report 4a.XLSX');
  expect(normalizeUploadFilename('report\u200bname.xlsx')).toBe('report\u200bname.xlsx');
  expect(normalizeUploadFilename('4a.xlsx (1)')).toBe('4a.xlsx (1)');
});
