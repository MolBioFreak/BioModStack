import { useRef, useState } from 'react';
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

/** Restart the robot-control service, or reboot the robot computer itself. Both go over the configured service SSH target. */
export function BioXpServiceRestart({ generation }: { generation: number }) {
    const client = useQueryClient();
    const submitting = useRef(false);
    const [confirmReboot, setConfirmReboot] = useState(false);
    const control = useQuery({
        queryKey: ['bioxp', 'service-control', generation],
        queryFn: async ({ signal }) => (await api.get<ServiceControl>('/api/bioxp/service', { signal, timeout: 10000 })).data,
        staleTime: 0,
        refetchInterval: 5000,
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
    const reboot = useMutation({
        mutationKey: ['bioxp', 'robot-reboot'],
        mutationFn: async () => (await api.post<{ reboot_requested: boolean }>('/api/bioxp/service/reboot', {}, { timeout: 75000 })).data,
        retry: false,
        onSuccess: () => { void client.invalidateQueries({ queryKey: ['bioxp'] }); },
        onSettled: () => { submitting.current = false; },
    });
    const unavailable = control.isError ? 'Robot service control availability could not be read.'
        : control.data?.detail ?? 'Robot service control is not configured.';
    const ready = control.data?.available === true && !restart.isPending && !reboot.isPending;
    // Service control is deliberately independent of robot reachability, controller
    // readiness and retained command outcomes. The API owns authorization.
    return <div aria-label="Robot service restart" className="bx-service-restart">
        <div className="bx-status-actions" title="Restart service restarts only the robot API: it interrupts robot work and does not home motors or reset coordinates. Reboot robot restarts the robot computer. Neither is a motor Stop.">
            <button type="button" disabled={!ready}
                title={control.data?.available === true ? 'Restart the robot-control service, even if its API is unresponsive.' : unavailable}
                onClick={() => {
                    if (submitting.current || control.data?.available !== true) return;
                    submitting.current = true;
                    restart.mutate();
                }}>
                {restart.isPending ? 'Restarting…' : 'Restart service'}
            </button>
            <button type="button" className="bx-danger" disabled={!ready} aria-haspopup="dialog" aria-expanded={confirmReboot}
                title={control.data?.available === true ? 'Reboot the robot computer.' : unavailable}
                onClick={() => setConfirmReboot(true)}>
                {reboot.isPending ? 'Rebooting…' : 'Reboot robot'}
            </button>
        </div>
        {confirmReboot && <div role="dialog" aria-label="Reboot the robot computer" className="bx-popover bx-popover-danger">
            <b>Reboot the robot computer?</b>
            <p>Stops any running work. Motor power drops, so axes need homing afterwards. The robot is unreachable until it has started again.</p>
            <div className="bx-popover-actions">
                <button type="button" onClick={() => setConfirmReboot(false)}>Cancel</button>
                <button type="button" className="bx-danger-fill" onClick={() => {
                    setConfirmReboot(false);
                    if (submitting.current || control.data?.available !== true) return;
                    submitting.current = true;
                    reboot.mutate();
                }}>Reboot now</button>
            </div>
        </div>}
        {(control.data?.available !== true && !control.isPending || restart.isSuccess || restart.isError || reboot.isSuccess || reboot.isError) && <div className="bx-status-note">
            {control.data?.available !== true && !control.isPending && <p role="status">{unavailable}</p>}
            {restart.isSuccess && <p role="status">{restart.data.active_state === 'active' && restart.data.sub_state === 'running'
                ? 'Robot service restarted. Controls may take a moment to reconnect. If the BMS link is disconnected, reconnect it.'
                : `Restart request completed; robot service reports ${restart.data.active_state} / ${restart.data.sub_state}. This does not confirm that the robot API is ready.`}</p>}
            {restart.isError && <p role="alert">{bioXpErrorText(restart.error)} No automatic retry was made.</p>}
            {reboot.isSuccess && <p role="status">Reboot started. The robot goes offline now; reconnect and home the axes once it is back.</p>}
            {reboot.isError && <p role="alert">{bioXpErrorText(reboot.error)} No automatic retry was made.</p>}
        </div>}
    </div>;
}
