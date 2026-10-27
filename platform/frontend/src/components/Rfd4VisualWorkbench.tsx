import { useState } from 'react';
import type { StructureMaterialization } from '../lib/api';
import { buildRfd4NativeSelection, type Rfd4NativeSelectionIdentity } from '../lib/rfd4NativeSelection';
import './Rfd4VisualWorkbench.css';

export type Rfd4VisualSelection = {
    slot: string; expression?: string; issue?: string;
    identities?: Rfd4NativeSelectionIdentity[]; receipt?: StructureMaterialization;
};

/** Resolve source residues from the receipt, never from a free-form query. */
export function rfd4SelectedFragments(visual?: Rfd4VisualSelection): { tokens: string[]; issue?: string } {
    if (!visual?.receipt || !visual.identities?.length) return { tokens: [], issue: 'Select source residues to add a fragment.' };
    const source = { ...visual.receipt, documentId: visual.receipt.path };
    const qualified = buildRfd4NativeSelection(visual.identities, source);
    if (!qualified.expression) return { tokens: [], issue: qualified.issue };
    const residues = new Map<string, Set<number>>();
    const model = source.model_number ?? source.model_numbers[0] ?? Number(source.source_positions![0].model_number);
    const insertion = (value: unknown) => String(value ?? '').replace(/^[.?]$/, '');
    for (const ref of visual.identities) {
        // The shared native mapper above establishes unique identity, model and atom scope.
        const row = source.source_positions!.find(p =>
            (ref.labelAsymId === undefined || p.label_asym_id === ref.labelAsymId) &&
            (ref.labelSeqId === undefined || Number(p.label_seq_id) === ref.labelSeqId) &&
            (ref.authAsymId === undefined || p.auth_asym_id === ref.authAsymId) &&
            (ref.authSeqId === undefined || Number(p.auth_seq_id) === ref.authSeqId) &&
            (ref.insertionCode === undefined || insertion(p.pdbx_PDB_ins_code) === insertion(ref.insertionCode)) &&
            (ref.entityId === undefined || p.label_entity_id === ref.entityId) &&
            (ref.sourceEntityId === undefined || p.label_entity_id === ref.sourceEntityId) &&
            (ref.componentId === undefined || p.label_comp_id === ref.componentId) &&
            Number(p.model_number) === model);
        if (!row) return { tokens: [], issue: 'Source residue mapping is unavailable; manual fragments remain editable.' };
        const chain = String(row.label_asym_id), residue = Number(row.label_seq_id);
        // Avoid inventing an escaping syntax for native contigs. This affects only this shortcut.
        if (!/^[A-Za-z]+$/.test(chain) || !Number.isSafeInteger(residue) || residue < 0) return { tokens: [], issue: 'This label needs manual native contig authoring.' };
        if (!residues.has(chain)) residues.set(chain, new Set());
        residues.get(chain)!.add(residue);
    }
    const tokens: string[] = [];
    for (const [chain, selected] of residues) {
        const ordered = [...selected].sort((a, b) => a - b);
        let first = ordered[0], last = first;
        for (const residue of ordered.slice(1)) {
            if (residue === last + 1) last = residue;
            else { tokens.push(`${chain}${first}-${last}`); first = last = residue; }
        }
        tokens.push(`${chain}${first}-${last}`);
    }
    return { tokens };
}

export function rfd4SelectionCount(visual?: Rfd4VisualSelection): number {
    return new Set((visual?.identities ?? []).map(ref => JSON.stringify([
        ref.documentId, ref.modelNumber ?? ref.modelId, ref.labelAsymId ?? ref.authAsymId,
        ref.labelSeqId ?? ref.authSeqId, ref.insertionCode,
    ]))).size;
}

export function Rfd4SelectionSummary({ visual, onClearSelection }: { visual?: Rfd4VisualSelection; onClearSelection?: () => void }) {
    return <div className="rfd4-selection-summary" aria-live="polite">
        <strong>{rfd4SelectionCount(visual)} residues selected</strong>
        {visual && <span>{visual.slot}</span>}
        {onClearSelection && <button type="button" onClick={onClearSelection}>Clear selection</button>}
    </div>;
}

/** Direct native-contig editing: token edits never rebuild untouched tokens. */
export function Rfd4Regions({ value, onChange, visual }: { value?: string; onChange: (v: string | undefined) => void; visual?: Rfd4VisualSelection }) {
    const [minimum, setMinimum] = useState(''), [maximum, setMaximum] = useState('');
    const [fragment, setFragment] = useState('');
    const tokens = value === undefined ? [] : value.split(',');
    const selected = rfd4SelectedFragments(visual);
    const append = (...next: string[]) => onChange([...tokens, ...next].join(','));
    const update = (index: number, token: string) => onChange(tokens.map((t, i) => i === index ? token : t).join(','));
    return <fieldset className="rfd4-regions"><legend>Fragments & linkers</legend>
        <div className="rfd4-region-strip" aria-label="Ordered fragments and generated regions">
            {tokens.map((token, i) => {
                const kind = token === '/0' ? 'break' : /^\d+(?:-\d+)?$/.test(token) ? 'generated' : /^[A-Za-z]+\d+-\d+$/.test(token) ? 'fragment' : 'native';
                return <details key={i} className={`rfd4-region rfd4-region--${kind}`}>
                    <summary><small>{kind === 'break' ? 'New chain' : kind === 'generated' ? 'Generated linker' : kind === 'fragment' ? 'Source fragment' : 'Native segment'}</small><strong>{token || 'Empty segment'}</strong><span aria-hidden="true">▾</span></summary>
                    <div className="rfd4-region-editor"><label>Region {i + 1}<input aria-label={`Region ${i + 1}`} value={token} onChange={e => update(i, e.target.value)} /></label>
                        <button type="button" disabled={!i} onClick={() => { const next = [...tokens]; [next[i - 1], next[i]] = [next[i], next[i - 1]]; onChange(next.join(',')); }}>Move region {i + 1} earlier</button>
                        <button type="button" disabled={i === tokens.length - 1} onClick={() => { const next = [...tokens]; [next[i + 1], next[i]] = [next[i], next[i + 1]]; onChange(next.join(',')); }}>Move region {i + 1} later</button>
                        <button type="button" onClick={() => onChange(tokens.filter((_, n) => n !== i).join(','))}>Remove region {i + 1}</button>
                    </div>
                </details>;
            })}
            {!tokens.length && <p className="rfd4-empty">Add source fragments and generated linkers in design order.</p>}
        </div>
        <div className="rfd4-region-actions">
            <button type="button" onClick={() => { if (selected.tokens.length) append(...selected.tokens); }}>Add selected residues as fragment</button>
            {selected.tokens.length > 0 ? <span className="rfd4-fragment-preview">{selected.tokens.join(' → ')}</span> : <small>{selected.issue}</small>}
            <button type="button" onClick={() => append('/0')}>Add chain break</button>
        </div>
        <div className="rfd4-linker-controls"><label>Generated minimum<input aria-label="Generated minimum" type="number" value={minimum} onChange={e => setMinimum(e.target.value)} /></label><span aria-hidden="true">–</span><label>Generated maximum<input aria-label="Generated maximum" type="number" value={maximum} onChange={e => setMaximum(e.target.value)} /></label><button type="button" onClick={() => append(`${minimum}-${maximum}`)}>Append generated region</button></div>
        <details className="rfd4-manual"><summary>Manual fragments & native contig</summary>
            <label>Source fragment<input aria-label="Source fragment" placeholder="Native label chain/residue range" value={fragment} onChange={e => setFragment(e.target.value)} /></label><button type="button" onClick={() => append(fragment)}>Append source fragment</button>
            <button type="button" onClick={() => append('')}>Add native fragment / length range</button>
            <label>Native contig<input aria-label="Native contig" value={value ?? ''} onChange={e => onChange(e.target.value)} /></label>
            <button type="button" onClick={() => onChange(undefined)}>Inherit contig</button>
        </details>
    </fieldset>;
}

const names: Record<string, string> = { C_CRD: 'Coordinates', C_SEQ: 'Sequence', C_IDX: 'Residue index', C_CRDGRP: 'Coordinate group', C_CENTROID: 'Centroid', C_DIS: 'Distance', C_CHA: 'Chain', C_CTR: 'C-terminus', C_NTR: 'N-terminus' };
export const rfd4ConditionName = (family: string) => names[family] ?? (family.startsWith('C_SEQ_PROB_') ? `${family.slice(11)} probability` : family);
