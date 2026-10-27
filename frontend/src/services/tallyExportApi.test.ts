import { beforeEach, expect, it, vi } from 'vitest';
const shared=vi.hoisted(()=>({get:vi.fn(),mutate:vi.fn(),headers:vi.fn(),download:vi.fn()}));
vi.mock('./api',()=>({workspaceGet:shared.get,workspaceMutation:shared.mutate,workspaceMutationWithHeaders:shared.headers,workspaceGetDownload:shared.download}));
import { downloadTallyBatch, generateTallyExport, getTallyCandidates, saveTallyProfile } from './tallyExportApi';
const target={workspaceId:'workspace 1',clientId:'client/1',gstinId:'gstin-1'};
beforeEach(()=>vi.resetAllMocks());

it('uses encoded tenant routes and backend candidate IDs',async()=>{
 shared.get.mockResolvedValue({items:[]});
 await getTallyCandidates(target,'session/1',{page:2,pageSize:25,search:'ABC & Co'});
 expect(shared.get).toHaveBeenCalledWith('workspace%201/clients/client%2F1/gstins/gstin-1/tally-export/runs/session%2F1/candidates?page=2&page_size=25&search=ABC%20%26%20Co');
});

it('sends CSRF mutations through the shared layer and preserves the caller idempotency key',async()=>{
 shared.mutate.mockResolvedValue({});shared.headers.mockResolvedValue({});
 await saveTallyProfile(target,{company_name:'Books'},false);
 expect(shared.mutate).toHaveBeenCalledWith(expect.stringMatching(/tally-export\/profile$/),{company_name:'Books'},'POST');
 await generateTallyExport(target,'session',['purchase_invoice:one'],4,false,'stable-key');
 expect(shared.headers).toHaveBeenCalledWith(expect.stringMatching(/\/batches$/),{candidate_ids:['purchase_invoice:one'],expected_run_version:4,allow_reexport:false},{'Idempotency-Key':'stable-key'});
});

it('downloads only through the secure backend batch route',async()=>{
 shared.download.mockResolvedValue({blob:new Blob(['xml']),filename:'safe.xml'});
 await downloadTallyBatch(target,'batch-1');
 expect(shared.download).toHaveBeenCalledWith(expect.stringMatching(/tally-export\/batches\/batch-1\/download$/),'tally-purchases-batch-1.xml');
});
