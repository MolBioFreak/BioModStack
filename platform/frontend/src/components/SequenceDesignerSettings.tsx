import type { ReactNode } from 'react';

// Presentation only: field types, values, bounds and defaults belong to the model.
const groupFor = (name: string): string => {
    if (/gaussian/.test(name)) return 'Gaussian conformers';
    if (/potts/.test(name)) return 'Potts sampling';
    if (/scn_|relax|repack/.test(name)) return 'Side chains and relaxation';
    if (/self_consistency/.test(name)) return 'Optional self-consistency';
    if (/chain|position|pos_|symmetry|omit|bias|mask|residue|constraint/.test(name)) return 'Chains and constraints';
    if (/worker|batch|verbose|seed/.test(name)) return 'Execution and reproducibility';
    return 'Sequence sampling';
};

export function SequenceDesignerSettings({ fields, renderField }: {
    fields: UntypedApiValue[];
    renderField: (field: UntypedApiValue) => ReactNode;
}) {
    const groups = new Map<string, UntypedApiValue[]>();
    for (const field of fields) {
        const group = groupFor(field.name);
        groups.set(group, [...(groups.get(group) ?? []), field]);
    }
    return <div className="space-y-3">{[...groups].map(([name, rows]) =>
        <details key={name} open={name === 'Sequence sampling'} className="rounded border border-slate-700/50 p-3">
            <summary className="cursor-pointer text-sm font-medium">{name}</summary>
            <div className="mt-3 grid grid-cols-1 gap-3 md:grid-cols-2">{rows.map(field =>
                <div key={field.name} data-sequence-designer-field={field.name}>{renderField(field)}</div>)}</div>
        </details>)}</div>;
}
