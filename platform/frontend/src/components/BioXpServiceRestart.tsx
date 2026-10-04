import { useRef } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../lib/api';
import { bioXpErrorText } from '../lib/bioxpClient';

interface ServiceControl {
    available: boolean;
    detail: string | null;
    unit: 'bioxp-api.service';
    restart_in_progress: boolean;
}
interface ServiceRestartResult {
    restarted: boolean;
    unit: 'bioxp-api.service';
    active_state: string;
    sub_state: string;
    invocation_id: string;
    pid: number;
}

export function BioXpServiceRestart({ generation }: { generation: number }) {
    const client = useQueryClient();
    const submitting = useRef(false);
    const control = useQuery({
        queryKey: ['bioxp', 'service-control', generation],
        queryFn: async ({ signal }) => (await api.get<ServiceControl>('/api/bioxp/service', { signal, timeout: 10000 })).data,
        staleTime: 30000,
        retry: false,
        refetchOnWindowFocus: false,
    });
    const restart = useMutation({
        mutationKey: ['bioxp', 'service-restart'],
        mutationFn: async () => (await api.post<ServiceRestartResult>('/api/bioxp/service/restart', {}, { timeout: 75000 })).data,
        retry: false,
        onSuccess: () => { void client.invalidateQueries({ queryKey: ['bioxp'] }); },
        onSettled: () => { submitting.current = false; },
    });
    const unavailable = control.isError ? 'Robot service restart availability could not be read.'
        : control.data?.detail ?? 'Robot service restart is not configured.';
    // Service control is deliberately independent of robot reachability, controller
    // readiness and retained command outcomes. The API owns authorization.
    return <section aria-label="Robot service restart" className="rounded-xl border border-slate-700 p-4"
        style={{ background: 'var(--bg-secondary)', color: 'var(--text-primary)' }}>
        <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
                <p className="font-semibold">Robot-control service</p>
                <p className="text-sm" style={{ color: 'var(--text-secondary)' }}>Restarts only the robot API. Interrupts robot work; does not home motors or reset coordinates. Not a motor Stop.</p>
            </div>
            <button type="button"
                className="rounded bg-slate-700 px-4 py-2 text-sm font-semibold text-white disabled:opacity-35"
                disabled={control.data?.available !== true || restart.isPending}
                title={control.data?.available === true ? 'Restart the robot-control service, even if its API is unresponsive.' : unavailable}
                onClick={() => {
                    if (submitting.current || control.data?.available !== true) return;
                    submitting.current = true;
                    restart.mutate();
                }}>
                {restart.isPending ? 'Restarting robot service…' : 'Restart robot service'}
            </button>
        </div>
        {restart.isSuccess && <p role="status" className="mt-2 text-sm">{restart.data.active_state === 'active' && restart.data.sub_state === 'running'
            ? 'Robot service restarted. Controls may take a moment to reconnect. If the BMS link is disconnected, reconnect it.'
            : `Restart request completed; robot service reports ${restart.data.active_state} / ${restart.data.sub_state}. This does not confirm that the robot API is ready.`}</p>}
        {restart.isError && <p role="alert" className="mt-2 text-sm text-red-300">{bioXpErrorText(restart.error)} No automatic retry was made.</p>}
    </section>;
}
