import { describe, expect, it } from 'vitest';
import { resolveApiBaseUrl } from './api';

describe('resolveApiBaseUrl', () => {
  it('keeps local cookie requests on the hostname used by the browser', () => {
    expect(resolveApiBaseUrl('http://127.0.0.1:8000', 'http://localhost:5173'))
      .toBe('http://localhost:8000');
    expect(resolveApiBaseUrl('http://localhost:8000', 'http://127.0.0.1:5173'))
      .toBe('http://127.0.0.1:8000');
  });

  it('does not rewrite a configured remote API', () => {
    expect(resolveApiBaseUrl('https://api.aligned.example', 'https://app.aligned.example'))
      .toBe('https://api.aligned.example');
  });
});
