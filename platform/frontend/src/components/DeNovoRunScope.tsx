import type { Job } from '../lib/api';

/** Read-only summary of the already serialized optional-operation envelope. */
export function DeNovoRunScope({ task, documents, operations }: { task: string; documents: string[]; operations?: Job['sequence_design'] }) {
    const followOns = operations?.enabled ? operations.schema_version === 2
        ? operations.round_plan.stages.map(stage => stage.kind === 'maturation' ? 'Maturation' : stage.models.map(call => `${call.model_id} · ${call.mode}`).join(' + '))
        : [operations.model_id] : [];
    return <section aria-label="Run scope" className="rounded border p-3 text-sm">
        <strong>{task}</strong><p>Task documents: {documents.length ? documents.join(' · ') : 'None selected'}</p>
        <p>Follow-on operations: {followOns.length ? followOns.map((name, index) => `${index + 1}. ${name}`).join(' · ') : 'None'}</p>
    </section>;
}
