import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from './api.js';

export interface BioXpConnectionSnapshot {
    configured: boolean;
    active: boolean;
    generation: number;
    target_url: string | null;
    reachable: boolean | null;
    runtime_ready: boolean | null;
    hardware_ready: boolean | null;
    capabilities: string[];
    observed_at: string | null;
    freshness_budget_seconds: number;
    fresh: boolean | null;
    last_error: string | null;
    command_active: boolean;
}

export interface BioXpStatusResponse {
    connection: BioXpConnectionSnapshot;
    available_commands: string[];
    available_controls: string[];
    unavailable_commands: Record<string, string>;
    emergency_stop: {
        delivery_available: boolean;
        physical_effect_verifiable: false;
    };
    startup_warnings: string[];
    legacy_job_migration: {
        migrated: number;
        quarantined: number;
    };
}

export interface BioXpProfileView {
    configured: boolean;
    valid: boolean;
    display_name: string | null;
    target_url: string | null;
    detail?: string;
}

export interface BioXpProfileWrite {
    schema_version?: 1;
    display_name: string;
    api_url: string;
}

export type BioXpProtocolStep =
    | { action: 'initialize_motors' }
    | { action: 'start_job'; job_id: string }
    | { action: 'pause_job'; job_id: string }
    | { action: 'resume_job'; job_id: string }
    | { action: 'stop_job'; job_id: string }
    | { action: 'recover_runtime' };

export interface BioXpProtocol {
    schema_version?: 1;
    name: string;
    steps: BioXpProtocolStep[];
}

export interface BioXpCompiledProtocol {
    protocol: BioXpProtocol;
    compiled_hash: string;
    validation_status: 'validated_offline';
    robot_compatible: null;
    executable: false;
    required_capabilities: string[];
    blockers: string[];
}

export interface BioXpJob {
    job_id: string;
    idempotency_key: string;
    protocol: BioXpProtocol;
    compiled_hash: string;
    state: string;
    created_at: string;
    updated_at: string;
    detail: string | null;
    generation: number | null;
    remote_job_id: string | null;
}

export interface BioXpJobListResponse {
    jobs: BioXpJob[];
}

export interface BioXpProtocolSubmissionResponse {
    job: BioXpJob;
    delivery_attempted: false;
    robot_compatible: null;
}

export interface BioXpEmergencyResult {
    idempotency_key: string;
    generation: number;
    attempted_at: string;
    delivery_attempted: boolean;
    remote_acknowledged: boolean;
    physical_effect_verified: false;
    detail: string;
}

const statusKey = ['bioxp', 'status'] as const;
const profileKey = ['bioxp', 'profile'] as const;
const jobsKey = ['bioxp', 'jobs'] as const;
export function bioXpErrorText(error: unknown): string {
    if (error && typeof error === 'object') {
        const response = 'response' in error
            ? (error as { response?: { data?: { detail?: unknown } } }).response
            : undefined;
        const detail = response?.data?.detail;
        if (typeof detail === 'string' && detail.trim()) return detail;
        if (Array.isArray(detail)) {
            const normalized = detail
                .map((entry) => {
                    if (!entry || typeof entry !== 'object') return String(entry);
                    const item = entry as { loc?: unknown; msg?: unknown };
                    const location = Array.isArray(item.loc) ? item.loc.map(String).join('.') : '';
                    const message = typeof item.msg === 'string' ? item.msg : JSON.stringify(entry);
                    return location ? `${location}: ${message}` : message;
                })
                .filter(Boolean)
                .join('; ');
            if (normalized) return normalized;
        }
        if ('message' in error && typeof error.message === 'string') return error.message;
    }
    return String(error ?? 'Unknown error');
}

export const useBioXpStatus = (enabled = true) => useQuery({
    queryKey: statusKey,
    queryFn: async () => (await api.get<BioXpStatusResponse>('/api/bioxp/status')).data,
    enabled,
    refetchInterval: enabled ? 5_000 : false,
    retry: false,
});

export const useBioXpProfile = (enabled = true) => useQuery({
    queryKey: profileKey,
    queryFn: async () => (await api.get<BioXpProfileView>('/api/bioxp/profile')).data,
    enabled,
    retry: false,
});

export const useBioXpJobs = (enabled = true) => useQuery({
    queryKey: jobsKey,
    queryFn: async () => {
        const response = await api.get<BioXpJobListResponse>('/api/bioxp/jobs');
        return response.data.jobs;
    },
    enabled,
    refetchInterval: enabled ? 10_000 : false,
});

const useRefreshMutation = <TVariables, TData>(
    mutationFn: (variables: TVariables) => Promise<TData>,
) => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn,
        onSuccess: async () => {
            await Promise.all([
                queryClient.invalidateQueries({ queryKey: statusKey }),
                queryClient.invalidateQueries({ queryKey: profileKey }),
                queryClient.invalidateQueries({ queryKey: jobsKey }),
            ]);
        },
    });
};

export const useSaveBioXpProfile = () => useRefreshMutation(
    async (profile: BioXpProfileWrite) => (
        await api.put<BioXpProfileView>('/api/bioxp/profile', profile)
    ).data,
);

export const useForgetBioXpProfile = () => useRefreshMutation(
    async () => (await api.delete<{ forgotten: boolean }>('/api/bioxp/profile')).data,
);

export const useConnectBioXp = () => useRefreshMutation(
    async () => (await api.post<BioXpConnectionSnapshot>('/api/bioxp/connection/connect')).data,
);

export const useDisconnectBioXp = () => useRefreshMutation(
    async () => (await api.post<BioXpConnectionSnapshot>('/api/bioxp/connection/disconnect')).data,
);

export const useProbeBioXp = () => useRefreshMutation(
    async () => (await api.post<BioXpConnectionSnapshot>('/api/bioxp/connection/probe')).data,
);

export const useCompileBioXpProtocol = () => useMutation({
    mutationFn: async (protocol: BioXpProtocol) => (
        await api.post<BioXpCompiledProtocol>('/api/bioxp/protocols/compile', protocol)
    ).data,
});

export const useSubmitBioXpProtocol = () => useRefreshMutation(
    async ({ protocol, idempotencyKey }: {
        protocol: BioXpProtocol;
        idempotencyKey: string;
    }) => (
        await api.post<BioXpProtocolSubmissionResponse>('/api/bioxp/protocols/submit', {
            protocol,
            idempotency_key: idempotencyKey,
        })
    ).data,
);

export const useBioXpCommand = () => useRefreshMutation(
    async (payload: Record<string, unknown>) => (
        await api.post('/api/bioxp/commands', payload)
    ).data,
);

export const useBioXpEmergencyStop = () => useRefreshMutation(
    async ({ generation }: { generation: number }) => (
        await api.post<BioXpEmergencyResult>('/api/bioxp/emergency-stop', {
            expected_generation: generation,
            idempotency_key: crypto.randomUUID(),
        })
    ).data,
);
