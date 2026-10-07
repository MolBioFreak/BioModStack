import { api } from './api';

// Full passive readers; no action invocation, hardware collection or polling.
export type RepairManualReaderKind = 'settings' | 'position-table';
export type RepairManualReaderData = Record<string, unknown>;
export async function repairManualRead(kind: RepairManualReaderKind, generation: number) {
    return (await api.get<RepairManualReaderData>(`/api/bioxp/operator-controls/readers/${kind}`,
        { params: { expected_connection_generation: generation } })).data;
}
