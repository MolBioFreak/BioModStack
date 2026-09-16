import { useState } from 'react';
export type FampnnResidueSelector = { chain_id: string; author_number: number; insertion_code: string };
export type FampnnAnalysisOverrides = { summary?: FampnnResidueSelector[] | null; mutation?: FampnnResidueSelector[] | null };
export type FampnnAnalysisControlsProps = {
    value?: unknown;
    onChange: (value: FampnnAnalysisOverrides | undefined) => void;
    summaryDefault: string;
    allowSummaryOverride?: boolean;
    mutationDefault?: string;
};
// Never replay server-owned admission declarations as user settings.
export function fampnnUserParams<T extends Record<string, unknown>>(params: T): T {
    const next = { ...params };
    for (const key of ['fampnn_analysis_declaration', 'fampnn_analysis_policy', 'core_protein_scientific_contract', 'core_protein_requested_params']) delete next[key];
    return next;
}
export function hydrateFampnnOverrides(value: unknown, allowSummaryOverride = true): FampnnAnalysisOverrides | undefined {
    if (value === undefined) return undefined;
    if (!value || typeof value !== 'object' || Array.isArray(value) || Object.keys(value).some(key => key !== 'summary' && key !== 'mutation')) throw Error('Invalid FA-MPNN analysis overrides');
    if (!allowSummaryOverride && Object.hasOwn(value, 'summary')) throw Error('FA-MPNN summary overrides are forbidden for antibody workflows');
    for (const selection of Object.values(value)) {
        if (selection === null) continue;
        if (!Array.isArray(selection)) throw Error('FA-MPNN scope must be a residue list');
        const seen = new Set<string>();
        for (const residue of selection) {
            if (!residue || typeof residue !== 'object' || Array.isArray(residue) || Object.keys(residue).some(key => !['chain_id', 'author_number', 'insertion_code'].includes(key)) || typeof residue.chain_id !== 'string' || !/^[^:\s]$/.test(residue.chain_id) || !Number.isSafeInteger(residue.author_number) || (residue.insertion_code !== undefined && (typeof residue.insertion_code !== 'string' || !/^[^:\s]?$/.test(residue.insertion_code)))) throw Error('Invalid FA-MPNN residue selector');
            const key = `${residue.chain_id}:${residue.author_number}:${residue.insertion_code ?? ''}`;
            if (seen.has(key)) throw Error('Duplicate FA-MPNN residue selector');
            seen.add(key);
        }
    }
    return value as FampnnAnalysisOverrides;
}
export function fampnnOverridePayload(value?: unknown, allowSummaryOverride = true): { fampnn_analysis_overrides?: FampnnAnalysisOverrides } {
    const validated = hydrateFampnnOverrides(value, allowSummaryOverride);
    return validated === undefined ? {} : { fampnn_analysis_overrides: validated };
}
function ScopeControl({ field, value, onChange }: { field: 'summary' | 'mutation'; value?: FampnnResidueSelector[] | null; onChange: (value: FampnnResidueSelector[] | undefined) => void }) {
    const [chain, setChain] = useState('');
    const [number, setNumber] = useState('');
    const [insertion, setInsertion] = useState('');
    const selected = value != null;
    const residue = { chain_id: chain, author_number: Number(number), insertion_code: insertion };
    const valid = /^[^:\s]$/.test(chain) && /^-?\d+$/.test(number) && Number.isSafeInteger(residue.author_number) && /^[^:\s]?$/.test(insertion);
    const duplicate = value?.some(item => item.chain_id === chain && item.author_number === residue.author_number && item.insertion_code === insertion);
    return <fieldset className="space-y-2 rounded border border-slate-700 p-3">
        <label><input type="checkbox" aria-label={`Override ${field} scope`} checked={selected} onChange={event => onChange(event.target.checked ? [] : undefined)} /> Override {field} scope</label>
        {selected && <>
            <p className="text-xs">An empty selection explicitly selects no residues; uncheck to use the workflow default.</p>
            <div className="flex flex-wrap gap-2">
                <label>Chain <input aria-label={`${field} chain`} value={chain} maxLength={1} size={2} onChange={event => setChain(event.target.value)} /></label>
                <label>Author residue <input aria-label={`${field} author residue`} type="number" step={1} value={number} onChange={event => setNumber(event.target.value)} /></label>
                <label>Insertion code <input aria-label={`${field} insertion code`} value={insertion} maxLength={1} size={2} onChange={event => setInsertion(event.target.value)} /></label>
                <button type="button" disabled={!valid || duplicate} onClick={() => onChange([...(value || []), residue])}>Add {field} residue</button>
            </div>
            <ul>{value.map((item, index) => <li key={`${item.chain_id}:${item.author_number}:${item.insertion_code}`}>{item.chain_id}:{item.author_number}:{item.insertion_code || '(no insertion)'} <button type="button" aria-label={`Remove ${field} residue ${index + 1}`} onClick={() => onChange(value.filter((_, i) => i !== index))}>Remove</button></li>)}</ul>
        </>}
    </fieldset>;
}
export function FampnnAnalysisControls({ value, onChange, summaryDefault, allowSummaryOverride = true, mutationDefault = 'authorized sequence-design residues minus fixed/protected positions' }: FampnnAnalysisControlsProps) {
    let validated: FampnnAnalysisOverrides | undefined;
    try { validated = hydrateFampnnOverrides(value, allowSummaryOverride); } catch (error) {
        return <section role="alert"><p>FA-MPNN scopes are invalid. Launch is blocked: {String(error)}</p><button type="button" onClick={() => onChange(undefined)}>Discard invalid FA-MPNN overrides and use workflow defaults</button></section>;
    }
    const update = (field: 'summary' | 'mutation', selection: FampnnResidueSelector[] | undefined) => {
        const next = { ...validated };
        if (selection === undefined) delete next[field]; else next[field] = selection;
        onChange(Object.keys(next).length ? next : undefined);
    };
    return <section className="space-y-2 rounded border border-slate-700 p-4">
        <h3>FA-MPNN analysis scopes</h3>
        <p className="text-sm">Summary describes model probabilities. Default: {summaryDefault}. {allowSummaryOverride ? 'A summary override must stay within the workflow input protein domain.' : 'Summary scope is fixed by the antibody workflow; summary overrides are not permitted.'}</p>
        <p className="text-sm">Mutation opportunities default to {mutationDefault}. Overrides may only narrow this set; they do not authorize sequence changes.</p>
        <p className="text-xs">Use source chain, author residue number and optional insertion code. Deferred inputs retain this logical selection; physical identity is bound only after preparation. The server validates biological bounds before scheduling.</p>
        {allowSummaryOverride && <ScopeControl field="summary" value={validated?.summary} onChange={selection => update('summary', selection)} />}
        <ScopeControl field="mutation" value={validated?.mutation} onChange={selection => update('mutation', selection)} />
    </section>;
}
