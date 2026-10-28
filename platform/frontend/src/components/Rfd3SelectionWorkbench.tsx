import { useState } from 'react';
import { projectRfd3SourceSelection, type Rfd3SourceSelection } from '../lib/rfd3SourceSelection';

export type Rfd3AppliedSelection = { field: string; value: unknown };
/** Receives temporary source-qualified picks. Only explicit Apply changes the native draft. */
export function Rfd3SelectionWorkbench({ value, onChange, visual, onInspect }: {
    value: Record<string, unknown>; onChange: (value: Record<string, unknown>) => void;
    visual?: Rfd3SourceSelection; onInspect?: (selection: Rfd3AppliedSelection) => void;
}) {
    const [field, setField] = useState('select_fixed_atoms');
    const [undo, setUndo] = useState<{ before: Record<string, unknown>; after: Record<string, unknown> }>();
    const projected = visual ? projectRfd3SourceSelection(visual) : { issue: 'Select residues or atoms in the active source.' };
    const fields = [
        ['select_fixed_atoms', 'Fix exact coordinate atoms'], ['select_unfixed_sequence', 'Unfix sequence'],
        ['contig', 'Replace contig with selected fragments'], ['unindex', 'Unindex residues'],
        ['select_hotspots', 'Hotspots'], ['select_hbond_donor', 'H-bond donors (exact atoms)'],
        ['select_hbond_acceptor', 'H-bond acceptors (exact atoms)'], ['select_buried', 'Buried atoms'],
        ['select_exposed', 'Exposed atoms'], ['select_partially_buried', 'Partially buried atoms'],
        ['ligand', 'Ligand / context residues'], ['redesign_motif_sidechains', 'Redesign motif sidechains'],
    ];
    return <fieldset data-rfd3-selection-workbench><legend>Apply source selection</legend>
        <p>Temporary picks do not edit the request. Coordinate atoms and sequence masks are independent. Apply replaces only the chosen field; manual values and the native parser remain authoritative.</p>
        <select aria-label="RFD3 selection destination" value={field} onChange={e => setField(e.target.value)}>{fields.map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select>
        <p role="status">{projected.issue ?? `Original author residues: ${projected.residues}`}</p>
        <button type="button" onClick={() => {
            if (!projected.residues || !projected.atoms) return;
            const atomField = ['select_fixed_atoms', 'select_hbond_donor', 'select_hbond_acceptor', 'select_buried', 'select_exposed', 'select_partially_buried'].includes(field);
            const next = { ...value, [field]: atomField ? projected.atoms : projected.residues };
            setUndo({ before: value, after: next }); onChange(next);
        }}>Apply RFD3 selection</button>
        <button type="button" disabled={!undo || JSON.stringify(value) !== JSON.stringify(undo.after)} onClick={() => { if (undo) { onChange(undo.before); setUndo(undefined); } }}>Undo RFD3 selection</button>
        <h4>Applied native selections</h4>
        {fields.filter(([key]) => Object.hasOwn(value, key)).map(([key, label]) => <div key={key}><strong>{label}</strong><code>{JSON.stringify(value[key])}</code>{onInspect && <button type="button" onClick={() => onInspect({ field: key, value: value[key] })}>Inspect {key}</button>}</div>)}
    </fieldset>;
}
