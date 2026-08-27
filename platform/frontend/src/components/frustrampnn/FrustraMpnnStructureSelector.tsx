import type { FrustraMpnnResultListItem } from '../../lib/frustraMpnnApi.js';

export function FrustraMpnnStructureSelector({
    items,
    selectedInvocationId,
    onSelect,
}: {
    items: readonly FrustraMpnnResultListItem[];
    selectedInvocationId: string | null;
    onSelect: (invocationId: string) => void;
}) {
    return (
        <section aria-label="FrustraMPNN structure collection" className="rounded-xl border border-slate-800 bg-slate-900/60 p-3">
            <label className="block text-xs font-semibold uppercase tracking-[0.14em] text-slate-400">
                Structure
                <select
                    aria-label="Structure"
                    value={selectedInvocationId ?? ''}
                    onChange={(event) => onSelect(event.target.value)}
                    className="mt-2 block w-full rounded border border-slate-700 bg-slate-950 px-3 py-2 text-sm normal-case tracking-normal text-slate-100"
                >
                    {items.map((item) => (
                        <option key={item.invocation_id} value={item.invocation_id}>
                            {item.operator_label} · {item.source_identity.design_id ? `Design ${item.source_identity.design_id} · ` : ''}Artifact {item.source_identity.artifact_id ?? 'unavailable'}
                        </option>
                    ))}
                </select>
            </label>
        </section>
    );
}
