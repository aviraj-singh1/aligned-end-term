import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

let service:typeof import('./msg91');

beforeAll(async()=>{
  vi.stubEnv('VITE_MSG91_WIDGET_ID','widget-id');
  vi.stubEnv('VITE_MSG91_WIDGET_TOKEN','widget-token');
  service=await import('./msg91');
});

beforeEach(()=>{
  document.head.innerHTML='';
  delete window.initSendOTP;delete window.sendOtp;delete window.retryOtp;delete window.verifyOtp;
});

describe('MSG91 OTP widget adapter',()=>{
  it.each([
    ['9876543210','+919876543210'],['+919876543210','+919876543210'],['91 98765 43210','+919876543210'],
  ])('normalizes %s to E.164',(input,expected)=>expect(service.normalizeIndianPhone(input)).toBe(expected));

  it.each(['+9109876543210','919876543210123','98765','abcdefghij'])('rejects malformed number %s',value=>{
    expect(()=>service.normalizeIndianPhone(value)).toThrow('valid 10-digit Indian');
  });

  it('maps provider errors without exposing details',()=>{
    expect(service.phoneVerificationErrorMessage({message:'OTP is invalid'},'verify')).toMatch(/incorrect/);
    expect(service.phoneVerificationErrorMessage({message:'OTP expired'},'verify')).toMatch(/expired/);
    expect(service.phoneVerificationErrorMessage({message:'rate limit'},'send')).toMatch(/wait/);
    expect(service.phoneVerificationErrorMessage({message:'captcha required'},'send')).toMatch(/security check/);
    expect(service.phoneVerificationErrorMessage({error:{data:{message:'captcha token missing'}}},'send')).toMatch(/security check/);
    expect(service.phoneVerificationErrorMessage({error:{message:'unauthorized widget token'}},'send')).toMatch(/widget configuration/);
    expect(service.phoneVerificationErrorMessage(new Error('MSG91 request timed out.'),'send')).toMatch(/did not respond/);
  });

  it('provides the callbacks required by the MSG91 initializer',async()=>{
    const init=vi.fn((configuration:Record<string,unknown>)=>{
      expect(configuration).toEqual(expect.objectContaining({
        widgetId:'widget-id',
        tokenAuth:'widget-token',
        exposeMethods:true,
        captchaRenderId:'msg91-captcha',
        success:expect.any(Function),
        failure:expect.any(Function),
      }));
      window.sendOtp=vi.fn();
    });
    window.initSendOTP=init;
    const append=vi.spyOn(document.head,'appendChild').mockImplementation(node=>{
      queueMicrotask(()=>((node as HTMLScriptElement).onload?.(new Event('load'))));
      return node;
    });

    await service.prepareMsg91();

    expect(init).toHaveBeenCalledOnce();
    append.mockRestore();
  });
});
