import { beforeEach, expect, it, vi } from 'vitest';
const mocks = vi.hoisted(()=>({get:vi.fn(),request:vi.fn(),post:vi.fn(),delete:vi.fn()}));
const hooks = vi.hoisted(() => ({
 request:null as null | ((config: {url:string; headers:{set:(name:string,value:string)=>void}; signal?:AbortSignal})=>unknown),
 failure:null as null | ((error:unknown)=>Promise<never>),
}));
vi.mock('axios',()=>({default:{create:()=>({...mocks,interceptors:{request:{use:(fn:NonNullable<typeof hooks.request>)=>{hooks.request=fn;}},response:{use:(_success:unknown,fn:NonNullable<typeof hooks.failure>)=>{hooks.failure=fn;}}}}),isAxiosError:(e: {isAxiosError?:boolean})=>e?.isAxiosError}}));
import { authErrorMessage, clearRequestScope, createGstrMerge, inspectFiles, reconcileSession, openOwnedRun, setExpectedUserId, submitGstrMerge } from './api';
const context={workspaceId:'workspace-1',clientId:'client-1',gstinId:'gstin-1'};
beforeEach(()=>{vi.resetAllMocks();clearRequestScope();setExpectedUserId(null);mocks.get.mockResolvedValue({data:{csrf_token:'csrf'}});});

it('binds protected requests to the displayed user but leaves profile discovery unbound',()=>{
 setExpectedUserId('displayed-admin');
 const set=vi.fn();
 hooks.request!({url:'/api/workspaces/firm/clients',headers:{set}});
 expect(set).toHaveBeenCalledWith('X-Expected-User-ID','displayed-admin');
 set.mockClear();
 hooks.request!({url:'/api/me',headers:{set}});
 hooks.request!({url:'/api/auth/login',headers:{set}});
 expect(set).not.toHaveBeenCalled();
});

it('requests profile synchronization for a cookie mismatch without retrying a mutation',async()=>{
 const listener=vi.fn();window.addEventListener('aligned-account-changed',listener);
 const error={isAxiosError:true,response:{status:409,data:{detail:{code:'ACCOUNT_CHANGED',message:'Review your current account.'}}}};
 try {
  await expect(hooks.failure!(error)).rejects.toBe(error);
  expect(listener).toHaveBeenCalledTimes(1);
  expect(authErrorMessage(error)).toBe('Review your current account.');
  expect(mocks.request).not.toHaveBeenCalled();
 } finally {window.removeEventListener('aligned-account-changed',listener);}
});

it('explains the server retry interval without treating throttling as invalid credentials',()=>{
 expect(authErrorMessage({isAxiosError:true,response:{status:429,headers:{'retry-after':'17'},data:{detail:'Too many attempts'}}})).toContain('Wait 17 seconds');
 expect(authErrorMessage({isAxiosError:true,response:{status:429,headers:{}}})).toContain('wait a few minutes');
});

it('retains an upload operation key across uncertain failures and uses the returned version',async()=>{
 const accounts=new File(['accounts'],'accounts.xlsx'),gst=new File(['gst'],'gst.xlsx');
 mocks.request.mockRejectedValueOnce({isAxiosError:true,response:{status:503}}).mockResolvedValueOnce({data:{session_id:'session-1'},headers:{'x-run-version':'1'}});
 await expect(inspectFiles(accounts,gst,null,context)).rejects.toBeTruthy();
 await inspectFiles(accounts,gst,null,context);
 const first=mocks.request.mock.calls[0][0],retry=mocks.request.mock.calls[1][0];
 expect(retry.headers['Idempotency-Key']).toBe(first.headers['Idempotency-Key']);
 expect(retry.headers).toMatchObject({'X-Workspace-ID':'workspace-1','X-Client-ID':'client-1','X-Client-GSTIN-ID':'gstin-1','X-CSRF-Token':'csrf'});
 mocks.request.mockResolvedValueOnce({data:{session_id:'session-1'},headers:{'x-run-version':'2'}});
 await reconcileSession('session-1',{});
 expect(mocks.request.mock.lastCall?.[0].headers['X-Run-Version']).toBe('1');
});

it('restores a saved run version and cancels queued mutations on scope changes',async()=>{
 mocks.get.mockResolvedValueOnce({data:{phase:'COMPLETED',version:8,data:{session_id:'saved'}}});
 await openOwnedRun(context,'saved');
 let finish!: (value:unknown)=>void;
 mocks.get.mockReturnValueOnce(new Promise(resolve=>{finish=resolve;}));
 mocks.request.mockImplementationOnce(async config=>{expect(config.signal.aborted).toBe(true);throw new Error('canceled');});
 const pending=reconcileSession('saved');
 clearRequestScope();
 finish({data:{csrf_token:'csrf'}});
 await expect(pending).rejects.toThrow('canceled');
 expect(mocks.request.mock.lastCall?.[0].headers['X-Run-Version']).toBe('8');
});

it('creates and submits GSTR runs through CSRF, idempotency, and optimistic-version headers',async()=>{
 const payload={client_gstin_id:'gstin-1',financial_year:'2025-26',mode:'QUARTERLY' as const,files:[{period_code:'Q1',filename:'q1.xlsx',size_bytes:4}]};
 mocks.request.mockResolvedValueOnce({data:{run:{id:'merge-1'},upload_targets:[]}});
 await createGstrMerge('workspace-1','client-1',payload);
 const create=mocks.request.mock.calls[0][0];
 expect(create.url).toBe('/api/workspaces/workspace-1/clients/client-1/gstr-merges');
 expect(create.data).toBe(payload);
 expect(create.headers['X-CSRF-Token']).toBe('csrf');
 expect(create.headers['Idempotency-Key']).toBeTruthy();
 mocks.request.mockResolvedValueOnce({data:{id:'merge-1',status:'QUEUED'}});
 await submitGstrMerge('workspace-1','client-1','merge-1',3);
 const submit=mocks.request.mock.calls[1][0];
 expect(submit.url).toBe('/api/workspaces/workspace-1/clients/client-1/gstr-merges/merge-1/submit');
 expect(submit.headers).toMatchObject({'X-CSRF-Token':'csrf','X-Run-Version':'3'});
 expect(submit.headers['Idempotency-Key']).toBeTruthy();
});
