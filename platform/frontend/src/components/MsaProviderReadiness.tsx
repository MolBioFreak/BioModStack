import React, { useEffect, useState } from 'react';
import { api } from '../lib/api';
import { type MsaSearchProvider, type SavedMsaProvider } from '../lib/msaPolicy';
export interface MsaProviderSetup {
    default_provider: MsaSearchProvider;
    local_search_enabled: false;
    submission_location: 'controller';
    cache_identity: string;
    providers: Record<MsaSearchProvider, {
        configured: boolean;
        credential_configured: boolean | null;
        authentication: 'not_required' | 'not_checked' | 'valid' | 'invalid';
        live_acceptance: 'not_checked_by_setup';
        blockers: string[];
    }>;
}
export function MsaProviderReadiness({ provider }: { provider: SavedMsaProvider }): React.JSX.Element {
    const [setup, setSetup] = useState<MsaProviderSetup | null>(null);
    const [error, setError] = useState(false);
    useEffect(() => {
        const controller = new AbortController();
        api.get<MsaProviderSetup>('/msa/providers', { signal: controller.signal })
            .then(response => setSetup(response.data))
            .catch(() => { if (!controller.signal.aborted) setError(true); });
        return () => controller.abort();
    }, []);
    if (provider === 'local') return <p role="status">Local MSA search is disabled.</p>;
    if (error) return <p role="status">Provider setup could not be read. Saved settings are unchanged; admission will check configuration.</p>;
    const selected = provider === 'auto' ? 'colabfold_api' : provider;
    const state = setup?.providers[selected];
    return <div role="status" className="text-xs">
        {!state ? 'Reading provider setup…' : <>
            <p>{selected}: {state.configured ? 'configured (not proof of live acceptance)' : 'not configured'}.
                Authentication: {state.authentication}; live acceptance: {state.live_acceptance}.</p>
            {state.credential_configured !== null && <p>Server credential: {state.credential_configured ? 'configured' : 'missing'}. Credentials are deployment-owned.</p>}
            {state.blockers.map(blocker => <p key={blocker}>{blocker}</p>)}
        </>}
    </div>;
}
