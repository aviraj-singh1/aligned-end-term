import { afterEach, describe, expect, it, vi } from 'vitest';
import { triggerBrowserDownload } from './downloadHelper';

describe('triggerBrowserDownload', () => {
  afterEach(() => vi.restoreAllMocks());

  it('clicks a named download before releasing its object URL', () => {
    vi.useFakeTimers();
    const create = vi.spyOn(window.URL, 'createObjectURL').mockReturnValue('blob:aligned-export');
    const revoke = vi.spyOn(window.URL, 'revokeObjectURL').mockImplementation(() => undefined);
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);

    triggerBrowserDownload(new Blob(['export']), 'aligned-data.zip');

    expect(create).toHaveBeenCalledOnce();
    expect(click).toHaveBeenCalledOnce();
    expect(revoke).not.toHaveBeenCalled();
    vi.runAllTimers();
    expect(revoke).toHaveBeenCalledWith('blob:aligned-export');
    vi.useRealTimers();
  });
});
