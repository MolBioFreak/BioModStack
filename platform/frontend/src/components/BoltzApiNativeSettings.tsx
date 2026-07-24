import { useState } from 'react';

import type { BoltzApiCliUpdateStatus, BoltzApiProviderStatus } from '../lib/api';

interface BoltzApiNativeSettingsPanelProps {
    status: BoltzApiProviderStatus | null;
}

const localControlLabels = [
    'GPU pinning',
    'diffusion sampling',
    'recycling',
    'potentials',
    'denoiser chunking',
    'parallelism',
    'OOM retry',
    'conditioning',
];

const updateStatusCopy = (update: BoltzApiCliUpdateStatus): string => {
    switch (update.check_status) {
        case 'current':
            return 'CLI is current according to the provider update feed.';
        case 'update_available':
            return 'An update is available according to the provider update feed.';
        case 'unavailable':
            return 'Update availability is unavailable.';
        case 'unavailable_pending_official_feed_verification':
            return 'Update availability is unavailable pending official feed verification.';
    }
};

export function BoltzApiNativeSettingsPanel({ status }: BoltzApiNativeSettingsPanelProps) {
    const [showUpdateStatus, setShowUpdateStatus] = useState(false);
    const capabilities = status?.capabilities;
    const update = status?.cli_update;

    return (
        <section className="rounded-lg border border-blue-500/25 bg-slate-950/30 p-3 space-y-3" data-bms-boltz-api-native-settings>
            <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                    <h4 className="text-sm font-medium text-blue-200">Boltz API–native settings</h4>
                    <p className="mt-1 text-xs text-slate-400">
                        This remote provider accepts provider-native entities, MSA choice, and sample count only.
                    </p>
                </div>
                {capabilities && (
                    <span className="rounded-full border border-blue-400/25 bg-blue-500/10 px-2 py-1 text-[10px] font-medium text-blue-100">
                        {capabilities.contract_version}
                    </span>
                )}
            </div>

            {capabilities ? (
                <div className="grid gap-2 text-xs text-slate-300 md:grid-cols-3">
                    <div className="rounded border border-slate-700/70 bg-slate-900/50 px-2.5 py-2">
                        <span className="block text-slate-500">Entities</span>
                        <span>{capabilities.entities.types.join(', ')}</span>
                    </div>
                    <div className="rounded border border-slate-700/70 bg-slate-900/50 px-2.5 py-2">
                        <span className="block text-slate-500">MSA</span>
                        <span>Supported; provider default is {capabilities.msa.provider_default}.</span>
                    </div>
                    <div className="rounded border border-slate-700/70 bg-slate-900/50 px-2.5 py-2">
                        <span className="block text-slate-500">Samples</span>
                        <span>{capabilities.num_samples.minimum}–{capabilities.num_samples.maximum} provider-native samples.</span>
                    </div>
                </div>
            ) : (
                <p className="text-xs text-slate-500">Provider capability details are loading.</p>
            )}

            {capabilities?.templates.status === 'unavailable_pending_schema_verification' ? (
                <p className="rounded border border-amber-500/20 bg-amber-500/5 px-2.5 py-2 text-xs text-amber-100">
                    Templates are unavailable pending provider schema verification.
                </p>
            ) : capabilities && (
                <p className="rounded border border-amber-500/20 bg-amber-500/5 px-2.5 py-2 text-xs text-amber-100">
                    Templates are unavailable because the provider did not advertise a supported template schema.
                </p>
            )}

            {capabilities && (
                <p className="text-xs text-slate-500">
                    Local controls are not sent to this provider: {localControlLabels.join(', ')}.
                </p>
            )}

            <div className="border-t border-slate-800 pt-3">
                <button
                    type="button"
                    data-bms-boltz-api-update-status
                    aria-expanded={showUpdateStatus}
                    onClick={() => setShowUpdateStatus((visible) => !visible)}
                    className="rounded border border-slate-600 bg-slate-900 px-2.5 py-1.5 text-xs font-medium text-slate-200 transition-colors hover:bg-slate-800"
                >
                    Update status
                </button>
                {showUpdateStatus && (
                    <div className="mt-2 rounded border border-slate-700/70 bg-slate-900/50 px-2.5 py-2 text-xs text-slate-300" data-bms-boltz-api-update-disclosure>
                        {update ? (
                            <>
                                <p>{updateStatusCopy(update)}</p>
                                {update.installed_version && <p className="mt-1">Installed CLI version: {update.installed_version}</p>}
                                {(update.check_status === 'current' || update.check_status === 'update_available') && update.latest_version && (
                                    <p className="mt-1">Latest provider-feed version: {update.latest_version}</p>
                                )}
                                {(update.check_status === 'current' || update.check_status === 'update_available') && update.release_url && (
                                    <a className="mt-1 inline-block text-blue-300 underline" href={update.release_url} target="_blank" rel="noreferrer">Provider release details</a>
                                )}
                            </>
                        ) : (
                            <p>Update status is loading from the provider status response.</p>
                        )}
                    </div>
                )}
            </div>
        </section>
    );
}
