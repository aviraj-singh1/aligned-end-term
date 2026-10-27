import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import WorkspaceApp from './WorkspaceApp';
import { AuthProvider } from './AuthContext';
import { GstrMergeTool } from '../components/gstr-merge/GstrMergeTool';
import { MONTHLY_SLOTS, QUARTERLY_SLOTS } from '../components/gstr-merge/gstrMergeConfig';
import { GstrMergeResult } from '../components/gstr-merge/GstrMergeResult';
import * as api from '../services/api';
import type { GstrMergeCreateResponse, GstrMergeRun } from '../types/api';

vi.mock('../services/api');
vi.mock('../services/supabase', () => ({ getSupabaseSession: vi.fn().mockResolvedValue(null), clearSupabaseSession: vi.fn() }));

const client = { id: 'client-1', name: 'Example Client', status: 'ACTIVE' as const, legal_name: null, pan: 'AAAAA0000A' };
const detail = { ...client, gstins: [
  { id: 'gstin-active', gstin: '06AAAAA0000A1Z5', status: 'ACTIVE' as const },
  { id: 'gstin-inactive', gstin: '07AAAAA0000A1Z4', status: 'INACTIVE' as const },
], membership_ids: [] };

function run(overrides: Partial<GstrMergeRun> = {}): GstrMergeRun {
  return {
    id: 'merge-1', workspace_id: 'workspace-1', client_id: client.id, client_gstin_id: 'gstin-active',
    mode: 'QUARTERLY', financial_year: '2026-27', status: 'UPLOADING', version: 1, attempt_count: 0,
    files: [], declared_total_size_bytes: 4, uploaded_total_size_bytes: null, result_summary: null,
    artifact_available: false, error_code: null, safe_error_message: null, queued_at: null, started_at: null,
    completed_at: null, failed_at: null, expires_at: null, created_at: '2026-09-15T00:00:00Z', updated_at: '2026-09-15T00:00:00Z',
    ...overrides,
  };
}

function created(mode: 'QUARTERLY' | 'MONTHLY'): GstrMergeCreateResponse {
  const definitions = mode === 'QUARTERLY' ? QUARTERLY_SLOTS : MONTHLY_SLOTS;
  return {
    run: run({ mode, files: definitions.map(([code]) => ({ period_code: code, filename: `${code}.xlsx`, expected_size_bytes: 4, actual_size_bytes: null, upload_state: 'PENDING' })) }),
    upload_targets: definitions.map(([code]) => ({ period_code: code, provider: 's3', method: 'PUT', url: `https://storage.example/${code}`, headers: { 'Content-Type': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' }, expires_at: '2099-01-01T00:00:00Z' })),
  };
}

function mockWorkspaceData(items: GstrMergeRun[] = []) {
  vi.mocked(api.workspaceGet).mockImplementation(async path => {
    if (path.endsWith('/dashboard')) return { workspace_name: 'Example Firm', clients: 1, accountants: null, runs: 0, completed: 0, daily: [], recent: [] };
    if (path.endsWith(`/clients/${client.id}`)) return detail;
    if (path.includes('gstr-merges')) return { items };
    return { items: [client], has_more: false };
  });
  vi.mocked(api.authErrorMessage).mockReturnValue('Safe request error.');
  vi.mocked(api.gstrMergeDownloadUrl).mockReturnValue('https://api.example/download');
}

function selectFiles(container: HTMLElement, count: number, extension = '.xlsx') {
  const inputs = [...container.querySelectorAll<HTMLInputElement>('input[type="file"]')];
  inputs.slice(0, count).forEach((input, index) => fireEvent.change(input, {
    target: { files: [new File(['xlsx'], `period-${index + 1}${extension}`, { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' })] },
  }));
}

beforeEach(() => {
  vi.resetAllMocks();
  vi.stubEnv('VITE_GSTR_CONSOLIDATION_ENABLED', 'true');
  mockWorkspaceData();
  vi.mocked(api.getAuthProfile).mockResolvedValue({
    user: { id: 'user-1', name: 'Owner', email: 'owner@example.test', email_verified: true },
    workspaces: [{ id: 'workspace-1', name: 'Example Firm', type: 'FIRM', role: 'OWNER' }],
    reconciliation_available: true,
  });
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllEnvs();
});

describe('native workspace route and client scope', () => {
  it('hides GSTR Consolidation and resolves its hash to the dashboard even if the legacy flag is set', async () => {
    window.history.replaceState(null, '', '/#/gstr-merge');
    render(<AuthProvider enabled><WorkspaceApp /></AuthProvider>);
    expect(await screen.findByRole('heading', { level: 1, name: 'Dashboard' })).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'GSTR Consolidation' })).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { level: 1, name: 'GSTR Consolidation' })).not.toBeInTheDocument();
  });
});

describe('GSTR upload workflow', () => {
  it('renders four labelled quarterly slots and blocks submission until all are selected', () => {
    const { container } = render(<GstrMergeTool workspaceId="workspace-1" client={client} />);
    expect(screen.getByText('April–June')).toBeInTheDocument();
    expect(screen.getByText('January–March')).toBeInTheDocument();
    expect(container.querySelectorAll('input[type="file"]')).toHaveLength(4);
    expect(screen.getByRole('button', { name: 'Upload selected files' })).toBeDisabled();
  });

  it('switches to twelve monthly slots in Indian FY order', () => {
    const { container } = render(<GstrMergeTool workspaceId="workspace-1" client={client} />);
    fireEvent.click(screen.getByRole('radio', { name: /Monthly/ }));
    const headings = [...container.querySelectorAll('.gstr-upload-slot h3')].map(node => node.textContent);
    expect(headings).toEqual(['April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December', 'January', 'February', 'March']);
    expect(container.querySelectorAll('input[type="file"]')).toHaveLength(12);
  });

  it('accepts case-insensitive XLSX and rejects XLS and oversized files', () => {
    const { container } = render(<GstrMergeTool workspaceId="workspace-1" client={client} />);
    const inputs = [...container.querySelectorAll<HTMLInputElement>('input[type="file"]')];
    fireEvent.change(inputs[0], { target: { files: [new File(['ok'], 'Q1.XLSX')] } });
    expect(screen.getByText('Q1.XLSX')).toBeInTheDocument();
    fireEvent.change(inputs[1], { target: { files: [new File(['old'], 'Q2.xls')] } });
    expect(screen.getByText(/Legacy .xls files are not supported/)).toBeInTheDocument();
    const large = new File(['large'], 'Q3.xlsx');
    Object.defineProperty(large, 'size', { value: 25 * 1024 * 1024 + 1 });
    fireEvent.change(inputs[2], { target: { files: [large] } });
    expect(screen.getByText(/exceeds the 25 MB per-file limit/)).toBeInTheDocument();
  });

  it('creates the exact run, uploads every file, then submits QUEUED', async () => {
    const response = created('QUARTERLY');
    vi.mocked(api.createGstrMerge).mockResolvedValue(response);
    vi.mocked(api.uploadGstrFile).mockImplementation(async (_target, _file, options) => options?.onProgress?.(100));
    vi.mocked(api.submitGstrMerge).mockResolvedValue(run({ status: 'QUEUED', queued_at: '2026-09-15T00:01:00Z' }));
    const { container } = render(<GstrMergeTool workspaceId="workspace-1" client={client} />);
    selectFiles(container, 4);
    const upload = screen.getByRole('button', { name: 'Upload selected files' });
    await waitFor(() => expect(upload).toBeEnabled());
    fireEvent.click(upload);
    await waitFor(() => expect(api.uploadGstrFile).toHaveBeenCalledTimes(4));
    expect(api.createGstrMerge).toHaveBeenCalledWith('workspace-1', client.id, expect.objectContaining({
      client_gstin_id: 'gstin-active', mode: 'QUARTERLY', files: expect.arrayContaining([expect.objectContaining({ period_code: 'Q1' }), expect.objectContaining({ period_code: 'Q4' })]),
    }));
    const submit = await screen.findByRole('button', { name: 'Submit for consolidation' });
    expect(submit).toBeEnabled();
    fireEvent.click(submit);
    await waitFor(() => expect(api.submitGstrMerge).toHaveBeenCalledWith('workspace-1', client.id, 'merge-1', 1));
    expect(await screen.findByRole('heading', { name: 'Queued for processing' })).toBeInTheDocument();
  });

  it('identifies an individual upload failure and retries its still-valid target', async () => {
    vi.mocked(api.createGstrMerge).mockResolvedValue(created('QUARTERLY'));
    vi.mocked(api.uploadGstrFile).mockRejectedValueOnce(new Error('Private upload unavailable.')).mockResolvedValue(undefined);
    const { container } = render(<GstrMergeTool workspaceId="workspace-1" client={client} />);
    selectFiles(container, 4);
    const upload = screen.getByRole('button', { name: 'Upload selected files' });
    await waitFor(() => expect(upload).toBeEnabled());
    fireEvent.click(upload);
    expect(await screen.findByText('Private upload unavailable.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(api.uploadGstrFile).toHaveBeenCalledTimes(5));
    expect(screen.queryByText('Private upload unavailable.')).not.toBeInTheDocument();
  });

  it('rediscovers a processing run, polls through completion, and stops on terminal state', async () => {
    vi.useFakeTimers();
    const processing = run({ status: 'PROCESSING' });
    mockWorkspaceData([processing]);
    vi.mocked(api.getGstrMerge)
      .mockResolvedValueOnce(run({ status: 'PROCESSING' }))
      .mockResolvedValueOnce(run({ status: 'COMPLETED', artifact_available: true, result_summary: { files_processed: 4, total_rows_merged: 12, integrity_passed: true } }));
    render(<GstrMergeTool workspaceId="workspace-1" client={client} />);
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    expect(api.getGstrMerge).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    expect(api.getGstrMerge).toHaveBeenCalledTimes(2);
    expect(screen.getByRole('heading', { name: 'GSTR consolidation complete' })).toBeInTheDocument();
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(api.getGstrMerge).toHaveBeenCalledTimes(2);
  });

  it('aborts status polling when the selected context unmounts', async () => {
    mockWorkspaceData([run({ status: 'PROCESSING' })]);
    let observed: AbortSignal | undefined;
    vi.mocked(api.getGstrMerge).mockImplementation((_workspace, _client, _run, signal) => {
      observed = signal;
      return new Promise(() => {});
    });
    vi.useFakeTimers();
    const view = render(<GstrMergeTool workspaceId="workspace-1" client={client} />);
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    expect(observed?.aborted).toBe(false);
    view.unmount();
    expect(observed?.aborted).toBe(true);
  });

  it('shows completed history with native download and a sanitized failed message', async () => {
    mockWorkspaceData([run({ status: 'COMPLETED', artifact_available: true }), run({ id: 'failed', status: 'FAILED', safe_error_message: 'Workbook GSTIN validation failed.' })]);
    render(<GstrMergeTool workspaceId="workspace-1" client={client} />);
    expect(await screen.findByRole('link', { name: 'Download' })).toHaveAttribute('href', 'https://api.example/download');
    const detailButtons = screen.getAllByRole('button', { name: 'View details' });
    fireEvent.click(detailButtons[1]);
    expect(screen.getByRole('alert')).toHaveTextContent('Workbook GSTIN validation failed.');
    expect(screen.getByRole('button', { name: 'Start New Consolidation' })).toBeInTheDocument();
  });
});

it('distinguishes success with warnings and uses a browser-native download link', () => {
  render(<GstrMergeResult workspaceId="workspace-1" clientId={client.id} gstin="06AAAAA0000A1Z5" onStartNew={() => {}} run={run({
    status: 'COMPLETED', artifact_available: true, result_summary: {
      files_processed: 4, total_rows_merged: 42, categories_merged: ['B2B'], rows_merged_per_category: { B2B: 42 },
      duplicate_warning_count: 2, metadata_verification_states: { gstin: 'PARTIALLY VERIFIED' }, integrity_passed: true,
    },
  })} />);
  expect(screen.getByText('SUCCESS WITH WARNINGS')).toBeInTheDocument();
  expect(screen.getByText('B2B: 42')).toBeInTheDocument();
  expect(screen.getByRole('link', { name: /Download consolidated workbook/ })).toHaveAttribute('href', 'https://api.example/download');
});
