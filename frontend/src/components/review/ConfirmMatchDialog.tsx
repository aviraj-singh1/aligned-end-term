import React, { useRef } from 'react';
import { useModalFocus } from '../common/useModalFocus';
import { AlertTriangle } from 'lucide-react';
import type { CandidateModel } from '../../types/api';

interface ConfirmProps {
  isOpen: boolean;
  onClose: () => void;
  onConfirm: () => void;
  isLoading: boolean;
  tallyInvoice: string;
  candidate: CandidateModel | null;
}

export const ConfirmMatchDialog: React.FC<ConfirmProps> = ({ isOpen, onClose, onConfirm, isLoading, tallyInvoice, candidate }) => {
  const dialogRef = useRef<HTMLDivElement>(null);
  useModalFocus(dialogRef, isOpen && !!candidate, onClose, !isLoading);
  if (!isOpen || !candidate) return null;

  return (
    <>
      <div className="fixed inset-0 bg-slate-900/40 backdrop-blur-sm z-[60]" onClick={!isLoading ? onClose : undefined} />
      <div ref={dialogRef} tabIndex={-1} role="dialog" aria-modal="true" aria-labelledby="confirm-match-title" aria-describedby="confirm-match-description" aria-busy={isLoading}
        className="fixed top-1/2 left-1/2 transform -translate-x-1/2 -translate-y-1/2 w-full max-w-md max-h-[100dvh] overflow-y-auto bg-white rounded-xl shadow-2xl z-[70] p-6 animate-in zoom-in-95 duration-200 [overflow-wrap:anywhere]">
        
        <div className="flex items-center text-brand-600 mb-4">
          <AlertTriangle className="w-6 h-6 mr-3" />
          <h2 id="confirm-match-title" className="text-xl font-semibold text-slate-800">Confirm Match</h2>
        </div>
        
        <p id="confirm-match-description" data-testid="confirm-match-message" className="text-slate-600 mb-6 leading-relaxed">
          Confirm that GSTR-2B invoice <span className="font-semibold text-slate-900">{candidate.gst_invoice}</span> corresponds to Accounts invoice <span className="font-semibold text-slate-900">{tallyInvoice}</span>?
        </p>
        
        <div className="flex items-center justify-end space-x-3">
          <button 
            onClick={onClose} 
            disabled={isLoading}
            className="px-4 py-2 text-slate-700 bg-white border border-slate-300 rounded-lg font-medium hover:bg-slate-50 transition-colors disabled:opacity-50"
          >
            Cancel
          </button>
          <button 
            onClick={onConfirm} 
            disabled={isLoading}
            className="px-5 py-2 text-white bg-brand-600 rounded-lg font-medium shadow hover:bg-brand-700 transition-colors disabled:opacity-75 flex items-center"
          >
            {isLoading ? 'Applying match...' : 'Confirm Match'}
          </button>
        </div>
      </div>
    </>
  );
};
