import { render,screen,fireEvent } from '@testing-library/react';
import { beforeEach,it,expect,vi } from 'vitest';
import { DashboardPage } from './DashboardPage';
import { AuthProvider } from './AuthContext';
import * as api from '../services/api';
vi.mock('../services/api');
beforeEach(()=>{vi.resetAllMocks();vi.mocked(api.getAuthProfile).mockResolvedValue({user:{id:'u',name:'User',email:'u@example.com',email_verified:true},workspaces:[{id:'w1',name:'First',type:'FIRM',role:'ADMIN'},{id:'w2',name:'Second',type:'FIRM',role:'ACCOUNTANT'}],reconciliation_available:true});});
it('uses server metrics and clears the previous workspace on switching',async()=>{
  const data={workspace_name:'First',clients:12,accountants:3,runs:6,completed:5,daily:[{date:'2026-09-01',runs:2}],recent:[{id:'r',client_name:'Private Client',status:'COMPLETED',created_at:'2026-09-01T00:00:00Z',deleted:false}]};
  vi.mocked(api.workspaceGet).mockImplementation(async path=>path.startsWith('w1/')?data:{...data,clients:0,accountants:null,runs:0,completed:0,recent:[]});
  render(<AuthProvider enabled><DashboardPage/></AuthProvider>);
  expect(await screen.findByText('Private Client')).toBeInTheDocument();
  expect(screen.getByText('12')).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Workspace'),{target:{value:'w2'}});
  expect(screen.queryByText('Private Client')).not.toBeInTheDocument();
  expect(await screen.findByText('No reconciliations yet')).toBeInTheDocument();
  expect(screen.queryByText('Active accountants')).not.toBeInTheDocument();
  expect(api.clearRequestScope).toHaveBeenCalled();
});
it('shows a retryable error instead of invented zero metrics',async()=>{
  vi.mocked(api.workspaceGet).mockRejectedValue(new Error('offline'));
  vi.mocked(api.authErrorMessage).mockReturnValue('Dashboard unavailable');
  render(<AuthProvider enabled><DashboardPage/></AuthProvider>);
  expect(await screen.findByRole('alert')).toHaveTextContent('Dashboard unavailable');
  expect(screen.queryByText('Active clients')).not.toBeInTheDocument();
  expect(screen.getByRole('button',{name:'Retry dashboard'})).toBeInTheDocument();
});
