import { useEffect, useMemo, useRef, useState } from 'react';

import {
    useBioXpProfile,
    useBioXpStatus,
    bioXpErrorText,
    useConnectBioXp,
    useDisconnectBioXp,
    useForgetBioXpProfile,
    useProbeBioXp,
    useSaveBioXpProfile,
} from '../lib/bioxpClient';
import { deriveBioXpStatus } from './bioxpInterlinkStatus';

const toneClass = {
    neutral: 'border-slate-600/60 bg-slate-800/80 text-slate-200',
    warning: 'border-amber-500/50 bg-amber-500/10 text-amber-200',
    danger: 'border-red-500/50 bg-red-500/10 text-red-200',
    success: 'border-emerald-500/50 bg-emerald-500/10 text-emerald-200',
} as const;

export function BioXpInterlinkMenu() {
    const [open, setOpen] = useState(false);
    const [displayName, setDisplayName] = useState('BioXP 3200');
    const [apiUrl, setApiUrl] = useState('');
    const [operatorToken, setOperatorToken] = useState('');
    const [nowMs, setNowMs] = useState(() => Date.now());
    const rootRef = useRef<HTMLDivElement>(null);
    const statusQuery = useBioXpStatus(true);
    const profileQuery = useBioXpProfile(open);
    const saveProfile = useSaveBioXpProfile();
    const forgetProfile = useForgetBioXpProfile();
    const connect = useConnectBioXp();
    const disconnect = useDisconnectBioXp();
    const probe = useProbeBioXp();

    useEffect(() => {
        if (profileQuery.data?.display_name) setDisplayName(profileQuery.data.display_name);
    }, [profileQuery.data?.display_name]);

    useEffect(() => {
        const timer = window.setInterval(() => setNowMs(Date.now()), 1_000);
        return () => window.clearInterval(timer);
    }, []);

    useEffect(() => {
        const close = (event: MouseEvent) => {
            if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
        };
        document.addEventListener('mousedown', close);
        return () => document.removeEventListener('mousedown', close);
    }, []);

    const connection = statusQuery.isError ? undefined : statusQuery.data?.connection;
    const derived = useMemo(
        () => connection ? deriveBioXpStatus(connection, nowMs) : null,
        [connection, nowMs],
    );
    const pending = saveProfile.isPending || forgetProfile.isPending
        || connect.isPending || disconnect.isPending || probe.isPending;
    const mutationError = saveProfile.error || forgetProfile.error
        || connect.error || disconnect.error || probe.error;

    return (
        <div ref={rootRef} className="relative" data-bms-bioxp-interlink-menu="true">
            <button
                type="button"
                onClick={() => setOpen((value) => !value)}
                className={`flex items-center gap-2 rounded-md border px-3 py-1.5 text-xs ${
                    derived ? toneClass[derived.tone] : toneClass.neutral
                }`}
                aria-expanded={open}
            >
                BioXP {derived?.label ?? 'UNKNOWN'}
            </button>

            {open && (
                <div className="absolute right-0 z-50 mt-2 w-[390px] rounded-lg border border-slate-700 bg-slate-950 p-4 shadow-2xl">
                    <div className="mb-3 flex items-center justify-between">
                        <div>
                            <p className="text-sm font-semibold text-white">BioXP Status</p>
                            <p className="text-xs text-slate-400">Generation {connection?.generation ?? 0}</p>
                        </div>
                        <span className={`rounded border px-2 py-1 text-[11px] ${
                            derived ? toneClass[derived.tone] : toneClass.neutral
                        }`}>{derived?.label ?? 'UNKNOWN'}</span>
                    </div>
                    <p className="mb-3 text-xs text-slate-300">{derived?.detail ?? 'Status is unavailable.'}</p>

                    <dl className="mb-4 grid grid-cols-2 gap-2 text-xs">
                        <dt className="text-slate-500">configured</dt><dd>{String(connection?.configured ?? false)}</dd>
                        <dt className="text-slate-500">active</dt><dd>{String(connection?.active ?? false)}</dd>
                        <dt className="text-slate-500">reachable</dt><dd>{String(connection?.reachable ?? 'UNKNOWN')}</dd>
                        <dt className="text-slate-500">runtime_ready</dt><dd>{String(connection?.runtime_ready ?? 'UNKNOWN')}</dd>
                        <dt className="text-slate-500">hardware_ready</dt><dd>{String(connection?.hardware_ready ?? 'UNKNOWN')}</dd>
                        <dt className="text-slate-500">target</dt><dd>{connection?.target_url ?? 'not configured'}</dd>
                    </dl>

                    <div className="mb-4 rounded border border-slate-800 p-3">
                        <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">Profile</p>
                        <input
                            type="password"
                            value={operatorToken}
                            onChange={(event) => setOperatorToken(event.target.value)}
                            autoComplete="off"
                            placeholder="Transient operator token"
                            className="mb-2 w-full rounded border border-slate-700 bg-slate-900 px-2 py-1.5 text-xs"
                            aria-label="BioXP transient operator token"
                        />
                        <input
                            value={displayName}
                            onChange={(event) => setDisplayName(event.target.value)}
                            className="mb-2 w-full rounded border border-slate-700 bg-slate-900 px-2 py-1.5 text-xs"
                            aria-label="BioXP profile display name"
                        />
                        <input
                            value={apiUrl}
                            onChange={(event) => setApiUrl(event.target.value)}
                            placeholder="http://approved-host:8123"
                            className="w-full rounded border border-slate-700 bg-slate-900 px-2 py-1.5 text-xs"
                            aria-label="BioXP API URL"
                        />
                        <p className="mt-1 text-[11px] text-slate-500">Saved targets are masked on read; re-enter the URL to change it.</p>
                        {profileQuery.data?.valid === false && (
                            <p className="mt-2 text-xs text-red-300">
                                Invalid saved profile: {profileQuery.data.detail ?? 'malformed profile'}
                            </p>
                        )}
                        <div className="mt-2 flex gap-2">
                            <button
                                type="button"
                                disabled={pending || !apiUrl.trim() || !operatorToken}
                                onClick={() => saveProfile.mutate({
                                    profile: { display_name: displayName, api_url: apiUrl },
                                    token: operatorToken,
                                })}
                                className="flex items-center gap-1 rounded bg-blue-600 px-2 py-1 text-xs disabled:opacity-40"
                            >Save</button>
                            <button
                                type="button"
                                disabled={pending || !connection?.configured || !operatorToken}
                                onClick={() => forgetProfile.mutate(operatorToken)}
                                className="flex items-center gap-1 rounded border border-red-700 px-2 py-1 text-xs text-red-300 disabled:opacity-40"
                            >Forget</button>
                        </div>
                    </div>

                    <div className="flex flex-wrap gap-2">
                        <button type="button" disabled={pending || !operatorToken || !connection?.configured || connection?.active} onClick={() => connect.mutate(operatorToken)} className="flex items-center gap-1 rounded bg-emerald-700 px-2 py-1 text-xs disabled:opacity-40">Connect</button>
                        <button type="button" disabled={pending || !operatorToken || !connection?.active} onClick={() => disconnect.mutate(operatorToken)} className="flex items-center gap-1 rounded bg-slate-700 px-2 py-1 text-xs disabled:opacity-40">Disconnect</button>
                        <button type="button" disabled={pending || !operatorToken || !connection?.active} onClick={() => probe.mutate(operatorToken)} className="flex items-center gap-1 rounded bg-slate-700 px-2 py-1 text-xs disabled:opacity-40">Probe</button>
                    </div>
                    <p className="mt-3 text-[11px] text-slate-500">Connecting activates an API client only. It does not prove runtime readiness or hardware state.</p>
                    {statusQuery.isError && <p className="mt-2 text-xs text-red-300">Status unavailable; cached readiness is suppressed.</p>}
                    {mutationError && <p className="mt-2 text-xs text-red-300">{bioXpErrorText(mutationError)}</p>}
                </div>
            )}
        </div>
    );
}
