import type { GstrMergeMode } from '../../types/api';

export const QUARTERLY_SLOTS = [
  ['Q1', 'April–June'], ['Q2', 'July–September'], ['Q3', 'October–December'], ['Q4', 'January–March'],
] as const;

export const MONTHLY_SLOTS = [
  ['M04', 'April'], ['M05', 'May'], ['M06', 'June'], ['M07', 'July'], ['M08', 'August'], ['M09', 'September'],
  ['M10', 'October'], ['M11', 'November'], ['M12', 'December'], ['M01', 'January'], ['M02', 'February'], ['M03', 'March'],
] as const;

export function slotsFor(mode: GstrMergeMode) {
  return mode === 'QUARTERLY' ? QUARTERLY_SLOTS : MONTHLY_SLOTS;
}
