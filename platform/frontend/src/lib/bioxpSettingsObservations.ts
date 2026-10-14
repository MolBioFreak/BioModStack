import { useQuery, useQueryClient } from '@tanstack/react-query';
import { readCalibrationSettings } from './bioxpCalibration';
import { readOperationParameters } from './bioxpPipetteSettings';

/** Shared observations only. Raw editor drafts remain owned by each editor. */
export const settingsObservationKey = (generation: number) => ['bioxp-settings-observations', generation] as const;
export function useCalibrationObservation(generation: number, enabled: boolean) {
    return useQuery({ queryKey: [...settingsObservationKey(generation), 'calibration'],
        queryFn: () => readCalibrationSettings(generation), enabled, staleTime: Infinity, retry: false });
}
export function useOperationObservation(generation: number, enabled: boolean) {
    return useQuery({ queryKey: [...settingsObservationKey(generation), 'operations'],
        queryFn: () => readOperationParameters(generation), enabled, staleTime: Infinity, retry: false });
}
export function useInvalidateSettings(generation: number) {
    const client = useQueryClient();
    return (resource?: 'calibration' | 'operations') => client.invalidateQueries({ queryKey: resource ? [...settingsObservationKey(generation), resource] : settingsObservationKey(generation), refetchType: 'active' });
}
