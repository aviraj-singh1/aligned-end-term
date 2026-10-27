import { beforeEach, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({ get: vi.fn(), request: vi.fn(), post: vi.fn(), delete: vi.fn() }));
vi.mock('axios', () => ({
  default: { create: () => ({ ...mocks, interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } } }) },
  isAxiosError: () => false,
}));

import { setExpectedUserId, uploadGstrFile } from './api';

class FakeRequest {
  static instances: FakeRequest[] = [];
  upload: { onprogress?: (event: ProgressEvent) => void } = {};
  headers: Record<string, string> = {};
  method = '';
  url = '';
  status = 200;
  withCredentials = false;
  onload?: () => void;
  onerror?: () => void;
  onabort?: () => void;
  onloadend?: () => void;
  constructor() { FakeRequest.instances.push(this); }
  open(method: string, url: string) { this.method = method; this.url = url; }
  setRequestHeader(name: string, value: string) { this.headers[name] = value; }
  send(_file: File) {
    this.upload.onprogress?.({ lengthComputable: true, loaded: 2, total: 4 } as ProgressEvent);
    this.onload?.();
    this.onloadend?.();
  }
  abort() { this.onabort?.(); this.onloadend?.(); }
}

beforeEach(() => {
  vi.resetAllMocks();
  FakeRequest.instances = [];
  vi.stubGlobal('XMLHttpRequest', FakeRequest);
  setExpectedUserId('user-1');
  mocks.get.mockResolvedValue({ data: { csrf_token: 'csrf-token' } });
});

it('uploads S3 bytes directly without cookies or control-plane CSRF', async () => {
  const progress = vi.fn();
  await uploadGstrFile({ period_code:'Q1',provider:'s3',method:'PUT',url:'https://storage.example/object',headers:{'Content-Type':'xlsx'},expires_at:'2099-01-01T00:00:00Z' },new File(['xlsx'],'q1.xlsx'),{onProgress:progress});
  const request=FakeRequest.instances[0];
  expect(request.url).toBe('https://storage.example/object');
  expect(request.withCredentials).toBe(false);
  expect(request.headers).toEqual({'Content-Type':'xlsx'});
  expect(mocks.get).not.toHaveBeenCalled();
  expect(progress).toHaveBeenCalledWith(50);
});

it('uses the authenticated local target with CSRF and expected-user protection', async () => {
  await uploadGstrFile({ period_code:'Q1',provider:'local',method:'PUT',url:'/api/workspaces/w/clients/c/gstr-merges/r/uploads/Q1',headers:{'Content-Type':'xlsx'},expires_at:'2099-01-01T00:00:00Z' },new File(['xlsx'],'q1.xlsx'));
  const request=FakeRequest.instances[0];
  expect(request.url).toContain('/api/workspaces/w/clients/c/gstr-merges/r/uploads/Q1');
  expect(request.withCredentials).toBe(true);
  expect(request.headers).toMatchObject({'X-CSRF-Token':'csrf-token','X-Expected-User-ID':'user-1'});
});
