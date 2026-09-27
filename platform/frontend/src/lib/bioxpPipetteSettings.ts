import { api } from './api';

export type TipTray = 1 | 2 | 3 | 4;
export const pipetteFlags = ['CheckForStaticTipLoss', 'LogPressure'] as const;
export type PipetteFlag = typeof pipetteFlags[number];
export type OperationParameters = { runtime_values: Record<PipetteFlag, boolean>; application_semantics: string };
export type ManualTipSetResult = { measured_z_steps: number; committed_revision_id: string };

export async function setManualTipTray(generation: number, tray: TipTray) {
    return (await api.post<ManualTipSetResult>('/api/bioxp/calibration-settings/manual-tip-set',
        { expected_connection_generation: generation, tray })).data;
}
export async function readOperationParameters(generation: number) {
    return (await api.get<OperationParameters>('/api/bioxp/operation-parameters',
        { params: { expected_connection_generation: generation } })).data;
}
export async function saveOperationParameters(generation: number, changes: Partial<Record<PipetteFlag, boolean>>) {
    return (await api.patch<OperationParameters>('/api/bioxp/operation-parameters',
        { expected_connection_generation: generation, ...changes })).data;
}
