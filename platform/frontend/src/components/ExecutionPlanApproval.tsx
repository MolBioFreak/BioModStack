import { useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Button, Dialog, DialogBody, DialogFooter, HTMLTable } from '@blueprintjs/core';
import '@blueprintjs/core/lib/css/blueprint.css';

export interface ExecutionPlanPreview {
    schema: 'bms.job.execution-preview.v1';
    approval_digest: string;
    admissible: boolean;
    request: { model_id: string; mode: string; execution_target_id?: string | null };
    plan: {
        requested_json: Record<string, unknown>;
        effective_json: Record<string, unknown>;
        source_identity: { revision: string; tree: string };
        metadata: {
            static_components: Array<{ component_key: string }>;
            dynamic_templates: Array<{ component_key: string }>;
            external_services: Array<{ logical_id: string; provider: string | null; state: string }>;
        };
    };
    deferred_preparation: string[];
    blockers: Array<{ reason: string }>;
}

const settingsRows = (value: unknown, prefix = ''): Array<[string, string]> => {
    if (value !== null && typeof value === 'object') {
        const entries = Object.entries(value);
        if (!entries.length) return [[prefix, Array.isArray(value) ? '(empty list)' : '(empty object)']];
        return entries.flatMap(([key, item]) => settingsRows(item, prefix ? `${prefix}.${key}` : key));
    }
    return [[prefix, value === undefined ? '—' : value === null ? 'None' : String(value)]];
};

export function ExecutionPlanApproval({ preview, finish }: {
    preview: ExecutionPlanPreview; finish: (approved: boolean) => void;
}) {
    const [showAll, setShowAll] = useState(false);
    const requested = new Map(settingsRows(preview.plan.requested_json));
    const effective = new Map(settingsRows(preview.plan.effective_json));
    const keys = [...new Set([...requested.keys(), ...effective.keys()])].sort();
    const changed = keys.filter(key => requested.get(key) !== effective.get(key));
    return <Dialog isOpen title="Review remote execution plan" onClose={() => finish(false)} style={{ width: 760 }}>
        <DialogBody>
            <p><strong>{preview.request.model_id} / {preview.request.mode}</strong> → {preview.request.execution_target_id}</p>
            <p>Selected components: {[...preview.plan.metadata.static_components, ...preview.plan.metadata.dynamic_templates]
                .map(row => row.component_key).join(', ')}</p>
            {preview.plan.metadata.external_services.map(service => <p key={service.logical_id}>
                {service.logical_id}: {service.provider ?? 'No provider'} ({service.state})
            </p>)}
            {preview.deferred_preparation.length > 0 && <p>Input preparation remains required before execution: {preview.deferred_preparation.join(', ')}. This preview does not contact an MSA provider.</p>}
            {!preview.admissible && preview.blockers.length === 0 && <p role="alert">The selected execution plan is incomplete and cannot be submitted.</p>}
            {preview.blockers.map((blocker, index) => <p role="alert" key={index}>{blocker.reason}</p>)}
            <h4>Compiled settings ({changed.length} additions or changes)</h4>
            <Button small onClick={() => setShowAll(!showAll)}>{showAll ? 'Show changes only' : 'Show all settings'}</Button>
            <div style={{ maxHeight: '40vh', overflow: 'auto', marginTop: 8 }}>
                <HTMLTable striped compact style={{ width: '100%', overflowWrap: 'anywhere' }}>
                    <thead><tr><th>Setting</th><th>Requested</th><th>Effective</th></tr></thead>
                    <tbody>{(showAll ? keys : changed).map(key => <tr key={key}>
                        <td>{key}</td><td>{requested.get(key) ?? '—'}</td><td>{effective.get(key) ?? '—'}</td>
                    </tr>)}</tbody>
                </HTMLTable>
            </div>
            <p>Source: <code>{preview.plan.source_identity.revision}</code></p>
            <p>Approval: <code style={{ overflowWrap: 'anywhere' }}>{preview.approval_digest}</code></p>
        </DialogBody>
        <DialogFooter actions={<><Button onClick={() => finish(false)}>Cancel</Button>
            <Button intent="primary" disabled={!preview.admissible} onClick={() => finish(true)}>Approve and submit</Button></>} />
    </Dialog>;
}

/** One explicit review for every existing browser submitJob caller. */
export function reviewExecutionPlan(preview: ExecutionPlanPreview): Promise<boolean> {
    if (typeof document === 'undefined') return Promise.reject(new Error('Explicit execution-plan approval is required'));
    return new Promise(resolve => {
        const host = document.createElement('div');
        document.body.appendChild(host);
        const root = createRoot(host);
        const finish = (approved: boolean) => {
            root.unmount();
            host.remove();
            resolve(approved);
        };
        root.render(<ExecutionPlanApproval preview={preview} finish={finish} />);
    });
}
