import { useState } from 'react';
import { api } from '../lib/api';
import type { MolecularDynamicsNativePreview } from './molecularDynamicsUiState';
import { BindCraft2SettingsReadback } from './BindCraft2NativeResults';

/** Retained server preview only; never reconstruct scientific settings in the browser. */
export function BinderMdPreviewReview({ rootId, roundId, stepId, preview, launchRequest, digest, onApproved }: {
    rootId: string; roundId: string; stepId: string; preview?: MolecularDynamicsNativePreview;
    launchRequest?: unknown; digest?: string; onApproved: () => Promise<unknown>;
}) {
    const [busy, setBusy] = useState(false), [error, setError] = useState('');
    const approve = async () => {
        setBusy(true); setError('');
        try {
            await api.post(`/api/binder-continuation/${encodeURIComponent(rootId)}/round/${encodeURIComponent(roundId)}/steps/${encodeURIComponent(stepId)}/md-approval`, { preview_digest: digest });
            const result = await onApproved() as { isError?: boolean } | undefined;
            if (result?.isError) throw Error('Approval returned, but persisted round readback failed. Refresh to inspect its state.');
        } catch (reason) { setError(String(reason)); } finally { setBusy(false); }
    };
    return <section aria-label={`Retained native MD preview ${stepId}`}>
        <h4>Native MD preview</h4><p>Preview digest: {digest}</p>
        <h5>Source identity</h5><BindCraft2SettingsReadback value={{ source: preview?.source, input_identity: preview?.input_identity }} />
        <h5>Effective request</h5><BindCraft2SettingsReadback value={preview?.effective_request} />
        <h5>Warnings and blockers</h5><BindCraft2SettingsReadback value={{ warnings: preview?.warnings, blockers: preview?.blockers }} />
        <h5>Execution plan</h5><BindCraft2SettingsReadback value={{ execution_target_id: preview?.execution_target_id, execution_policy: preview?.execution_policy, execution_plan: preview?.execution_plan }} />
        <details><summary>Retained MD launch request</summary><BindCraft2SettingsReadback value={launchRequest} /></details>
        <button type="button" disabled={busy} onClick={() => void approve()}>Approve native MD preview</button>
        {error && <p role="alert">{error}</p>}
    </section>;
}
