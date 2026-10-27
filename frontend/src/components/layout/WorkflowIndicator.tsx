import React from 'react';
import { clsx } from 'clsx';
import { Check } from 'lucide-react';

export type WorkflowStep = 'upload' | 'review' | 'reconcile' | 'results' | 'export';

interface Props {
  currentStep: WorkflowStep;
}

const steps = [
  { id: 'upload', label: '1 Upload' },
  { id: 'review', label: '2 Review Data' },
  { id: 'reconcile', label: '3 Reconcile' },
  { id: 'results', label: '4 Review Results' },
  { id: 'export', label: '5 Export' },
];

export const WorkflowIndicator: React.FC<Props> = ({ currentStep }) => {
  const currentIndex = steps.findIndex(s => s.id === currentStep);

  return (
    <nav className="border-b border-line bg-surface-subtle" aria-label="Reconciliation progress">
      <ol tabIndex={0} aria-label="Workflow steps" className="mx-auto flex max-w-[90rem] items-center overflow-x-auto px-4 py-2.5 sm:px-6 focus-visible:outline-2 focus-visible:outline-brand-500 focus-visible:-outline-offset-2">
        {steps.map((step, idx) => {
          const isActive = idx === currentIndex;
          const isPast = idx < currentIndex;
          return (
            <li key={step.id} className="flex flex-1 items-center last:flex-none">
              <div
                aria-current={isActive ? 'step' : undefined}
                className={clsx(
                  "flex items-center whitespace-nowrap text-xs font-medium transition-colors sm:text-[13px]",
                  isActive ? "text-brand-700" : isPast ? "text-slate-600" : "text-slate-400"
                )}
              >
                {isPast ? (
                  <span className="mr-2 flex h-5 w-5 items-center justify-center rounded-full border border-brand-200 bg-brand-50 text-brand-700">
                    <Check className="h-3 w-3" strokeWidth={2.5} aria-hidden="true" />
                  </span>
                ) : (
                  <span className={clsx(
                    "mr-2 flex h-5 w-5 items-center justify-center rounded-full border text-[11px] font-semibold",
                    isActive
                      ? "border-brand-600 bg-brand-600 text-white"
                      : "border-slate-300 bg-white text-slate-400"
                  )}>
                    {idx + 1}
                  </span>
                )}
                <span>{step.label.replace(/^\d+\s/, '')}</span>
              </div>
              {idx < steps.length - 1 && (
                <span
                  className={clsx(
                    "mx-3 h-px min-w-5 flex-1 sm:mx-4",
                    isPast ? "bg-brand-200" : "bg-slate-300"
                  )}
                  aria-hidden="true"
                />
              )}
            </li>
          );
        })}
      </ol>
    </nav>
  );
};
