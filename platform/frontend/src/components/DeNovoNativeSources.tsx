import { useEffect, useRef, useState } from 'react';
import { buildFileDownloadUrl, materializeExactStructure, materializeStructureTarget, type NativeGenerationSourceBinding, type NativeGenerationSourceBindings, type StructureMaterialization, type StructureSourceSelection } from '../lib/api';
import { TargetAntigenSelector, type SelectedTarget } from './TargetAntigenSelector';
import { FileBrowser } from './FileBrowser';
import MolstarViewer from './MolstarViewer';
import { DeNovoNativeDocumentPicker } from './DeNovoNativeDocumentPicker';
import type { MetricSelection } from '../structureViewer/metrics/metricContracts';
import type { Rfd4NativeSelectionIdentity } from '../lib/rfd4NativeSelection';
import { readSavedConditions, savedConditionLayers, type SavedCondition } from '../lib/rfd4SavedConditions';

function NativePositionControls({ slot, receipt, onSelection }: { slot: string; receipt: StructureMaterialization; onSelection: (selection: MetricSelection) => void }) {
    const [position, setPosition] = useState('');
    const [atom, setAtom] = useState('');
    const positions = (receipt.source_positions ?? []).filter(row => receipt.model_number == null || Number(row.model_number) === receipt.model_number);
    const selected = position === '' ? undefined : positions[Number(position)];
    const atoms = (Array.isArray(selected?.atoms) ? selected.atoms : []) as Record<string, unknown>[];
    const text = (value: unknown) => typeof value === 'string' && !['', '.', '?'].includes(value) ? value : undefined;
    const integer = (value: unknown) => (typeof value === 'number' || typeof value === 'string' && /^-?\d+$/.test(value)) && Number.isSafeInteger(Number(value)) ? Number(value) : undefined;
    return <div className="space-y-2">
        <label>Retained source position<select aria-label={`Retained residue for ${slot}`} value={position} onChange={event => { setPosition(event.target.value); setAtom(''); }}>
            <option value="">Choose exact residue</option>{positions.map((row, i) => <option key={i} value={i}>MODEL {String(row.model_number)} · {text(row.label_asym_id) && integer(row.label_seq_id) !== undefined ? `label ${row.label_asym_id}:${row.label_seq_id} · ` : ''}author {text(row.auth_asym_id)?.trim() || '(blank)'}:{String(row.auth_seq_id)}{text(row.pdbx_PDB_ins_code)} · {text(row.label_comp_id) ?? text(row.component_id) ?? 'residue'}</option>)}
        </select></label>
        {selected && <><label>Native atom<select aria-label={`Retained atom for ${slot}`} value={atom} onChange={event => setAtom(event.target.value)}><option value="">Whole residue</option>{atoms.map((row, i) => <option key={i} value={i}>{text(row.label_atom_id) ?? text(row.auth_atom_id) ?? 'Atom'}{text(row.label_atom_id) && text(row.auth_atom_id) && row.label_atom_id !== row.auth_atom_id ? ` · author ${row.auth_atom_id}` : ''} · {String(row.type_symbol)}</option>)}</select></label>
            <button type="button" onClick={() => {
                const selectedAtom = atom === '' ? undefined : atoms[Number(atom)];
                const identity: Rfd4NativeSelectionIdentity = { documentId: receipt.path,
                    modelNumber: integer(selected.model_number), labelAsymId: text(selected.label_asym_id), authAsymId: text(selected.auth_asym_id),
                    labelSeqId: integer(selected.label_seq_id), authSeqId: integer(selected.auth_seq_id), insertionCode: text(selected.pdbx_PDB_ins_code), entityId: text(selected.label_entity_id), componentId: text(selected.label_comp_id),
                    ...(selectedAtom ? { labelAtomId: text(selectedAtom.label_atom_id), authAtomId: text(selectedAtom.auth_atom_id), atomIndex: integer(selectedAtom.model_atom_index), element: text(selectedAtom.type_symbol), altLoc: text(selectedAtom.label_alt_id) } : {}) };
                onSelection({ metricId: 'native-source-selection', origin: 'table', identities: [identity] });
            }}>Add exact selection from {slot}</button></>}
    </div>;
}

const fileBinding = (value: NativeGenerationSourceBinding): value is { kind: 'file'; path: string } => 'kind' in value && value.kind === 'file';
function withModel(source: StructureSourceSelection, model: number | undefined): StructureSourceSelection {
    const next = { ...source, model_number: model };
    if (next.document) next.document = { ...next.document, model };
    if (next.candidate?.kind === 'native_document') next.candidate = { ...next.candidate, model };
    else if (next.candidate?.kind === 'design' && next.candidate.document) next.candidate = { ...next.candidate, document: { ...next.candidate.document, model } };
    return next;
}

function NativeSourceRow({ slot, value, onChange, onRemove, onSelection, selectedIdentities, onInspection, initialReceipt, compact = false }: {
    slot: string; value: NativeGenerationSourceBinding; onChange: (value: NativeGenerationSourceBinding) => void;
    selectedIdentities?: Rfd4NativeSelectionIdentity[];
    initialReceipt?: StructureMaterialization;
    onInspection?: (slot: string, receipt: StructureMaterialization, binding: NativeGenerationSourceBinding) => void;
    compact?: boolean;
    onRemove?: () => void; onSelection?: (slot: string, receipt: StructureMaterialization, selection: MetricSelection) => void;
}) {
    const [receipt, setReceipt] = useState<StructureMaterialization | undefined>(initialReceipt);
    const [saved, setSaved] = useState<{ receipt: StructureMaterialization; rows: SavedCondition[] }>();
    const [savedError, setSavedError] = useState('');
    const savedRows = saved && saved.receipt === receipt ? saved.rows : [];
    const inspectSaved = async () => {
        if (!receipt) return;
        const token = epoch.current;
        setSavedError('');
        try {
            const response = await fetch(buildFileDownloadUrl(receipt.format === 'bcif' ? receipt.decoded_path ?? receipt.path : receipt.path));
            if (!response.ok) throw new Error(`Source readback HTTP ${response.status}`);
            const rows = await readSavedConditions(await response.text(), receipt.path, receipt.model_number ?? undefined);
            if (epoch.current === token) setSaved({ receipt, rows });
        } catch (cause) { if (epoch.current === token) setSavedError(String(cause)); }
    };
    const [picking, setPicking] = useState(false);
    const [nativePicking, setNativePicking] = useState(false);
    const [browser, setBrowser] = useState(false);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState('');
    const epoch = useRef(0);
    const latest = useRef(onChange); latest.current = onChange;
    const identity = JSON.stringify(value);
    const previous = useRef(identity);
    useEffect(() => {
        if (previous.current !== identity) { epoch.current++; setReceipt(undefined); setBusy(false); previous.current = identity; }
    }, [identity]);
    useEffect(() => () => { epoch.current++; }, []);
    const acquire = async (source: StructureSourceSelection, token = ++epoch.current) => {
        setError(''); setBusy(true); setReceipt(undefined);
        try {
            const result = await materializeExactStructure({ ...source, output_format: 'native' });
            if (epoch.current !== token) return;
            const retained = result.source_structure ?? source;
            previous.current = JSON.stringify(retained);
            setReceipt(result); latest.current(retained); onInspection?.(slot, result, retained); setPicking(false); setNativePicking(false); setBrowser(false);
        } catch (cause) { if (epoch.current === token) setError(cause instanceof Error ? cause.message : String(cause)); }
        finally { if (epoch.current === token) setBusy(false); }
    };
    const select = async (source: SelectedTarget | null) => {
        const token = ++epoch.current;
        setReceipt(undefined); setError(''); setBusy(false);
        if (!source) { onRemove?.(); setPicking(false); return; }
        try {
            let selection: StructureSourceSelection;
            if (source.materialization?.source_structure) selection = source.materialization.source_structure;
            else if (source.jobId && source.document?.artifact_id) selection = {
                candidate: source.designId ? { kind: 'design', job_id: source.jobId, design_id: source.designId,
                    document: { artifact_id: source.document.artifact_id,
                        ...(source.document.target_state !== undefined ? { target_state: source.document.target_state } : {}),
                        ...(source.modelNumber !== undefined ? { model: source.modelNumber } : {}) } }
                    : { kind: 'native_document', job_id: source.jobId, artifact_id: source.document.artifact_id,
                        ...(source.document.target_state !== undefined ? { target_state: source.document.target_state } : {}),
                        ...(source.modelNumber !== undefined ? { model: source.modelNumber } : {}) },
                ...(source.path ? { path: source.path } : {}),
                ...(source.document.sha256 ? { expected_sha256: source.document.sha256 } : {}),
            };
            else if (source.designId) selection = { design_id: source.designId, ...(source.modelNumber !== undefined ? { model_number: source.modelNumber } : {}) };
            else selection = { path: await materializeStructureTarget(source, 'inputs'), ...(source.modelNumber !== undefined ? { model_number: source.modelNumber } : {}) };
            if (token === epoch.current) await acquire(selection, token);
        } catch (cause) { if (epoch.current === token) { setError(cause instanceof Error ? cause.message : String(cause)); setBusy(false); } }
    };
    const isFile = fileBinding(value);
    return <section className="space-y-3 rounded border border-slate-700 p-3" aria-label={`Native source ${slot}`}>
        {!compact && <div className="flex flex-wrap items-center gap-3"><strong>{slot}</strong><span>{isFile ? 'Managed file or archive' : 'Exact native structure'}</span>
            {onRemove && <button type="button" onClick={() => { epoch.current++; setBusy(false); onRemove(); }}>Remove source {slot}</button>}</div>}
        {(value.path || !isFile && (value.candidate || value.job_id || value.design_id)) && <details><summary>Source details</summary>
        {!isFile && <p className="break-all text-sm">{value.candidate ? `Job ${value.candidate.job_id} · ${value.candidate.kind === 'native_document' ? `document ${value.candidate.artifact_id}` : value.candidate.kind === 'design' ? `design ${value.candidate.design_id}` : value.candidate.kind}` : value.job_id ? `Job ${value.job_id}` : 'Retained source'}{value.model_number !== undefined ? ` · MODEL ${value.model_number}` : ''}</p>}
        {value.path && <p className="break-all text-xs">{value.path}</p>}</details>}
        <div className="flex flex-wrap gap-3">
            {!isFile && <button type="button" aria-label={`Choose document for ${slot}`} onClick={() => setPicking(!picking)}>{compact ? 'Choose document' : `Choose document for ${slot}`}</button>}
            {!isFile && <button type="button" aria-label={`Choose native result for ${slot}`} onClick={() => setNativePicking(!nativePicking)}>{compact ? 'From results' : `Choose native result for ${slot}`}</button>}
            <button type="button" aria-label={`Browse ${isFile ? 'file or archive' : 'native file'} for ${slot}`} onClick={() => setBrowser(true)}>{compact ? 'Browse files' : `Browse ${isFile ? 'file or archive' : 'native file'} for ${slot}`}</button>
            {!isFile && (value.path || value.candidate || value.design_id) && <button type="button" aria-label={`Inspect retained ${slot}`} onClick={() => void acquire(value)}>{compact ? 'Reload preview' : `Inspect retained ${slot}`}</button>}
        </div>
        {picking && !isFile && <TargetAntigenSelector requiredFormat="native" label={`Native document for ${slot}`} onSelect={source => void select(source)} />}
        {nativePicking && !isFile && <DeNovoNativeDocumentPicker onSelect={source => void acquire(source)} />}
        {browser && <FileBrowser title={`Native source ${slot}`} allowDirectory={isFile} accept={isFile ? undefined : '.pdb,.cif,.mmcif,.bcif,.pdb.gz,.cif.gz,.mmcif.gz,.bcif.gz'} onCancel={() => setBrowser(false)} onSelect={path => {
            if (isFile) { epoch.current++; onChange({ kind: 'file', path }); setBrowser(false); }
            else void acquire({ path, output_format: 'native' });
        }} />}
        {busy && <p role="status">Loading structure…</p>}
        {error && <p role="alert">{error}</p>}
        {receipt && !isFile && <>
            <label>Source MODEL<select aria-label={`Source MODEL for ${slot}`} value={receipt.model_number ?? ''} onChange={event => void acquire(withModel(value, event.target.value === '' ? undefined : Number(event.target.value)))}>
                <option value="">Retain native models</option>{receipt.model_numbers.map(number => <option key={number} value={number}>{number}</option>)}
            </select></label>
            <p className="text-xs">{receipt.author_residues.length} residues · {receipt.native_format}</p>
            {receipt.inspection_error && <p role="status">Native inspection: {receipt.inspection_error}. The retained source is unchanged.</p>}
            {onSelection && receipt.source_positions?.length ? <NativePositionControls key={`${receipt.path}:${receipt.model_number}`} slot={slot} receipt={receipt} onSelection={selection => onSelection?.(slot, receipt, selection)} /> : null}
            {receipt.model_numbers.length > 1 && receipt.model_number == null && <p>Select a MODEL to view its saved condition colors. All-model annotations remain available below.</p>}
            {receipt.format !== 'pdb' && <button type="button" onClick={() => void inspectSaved()}>Read saved conditions for {slot}</button>}
            {savedError && <p role="status">Saved annotation readback: {savedError}. Source and manual request are unchanged.</p>}
            {saved?.receipt === receipt && <details><summary>Saved annotation targets and groups ({savedRows.length})</summary><p>Saved annotations are shown separately from your current selection.</p>{!savedRows.length && <p>No recognized saved condition annotations in this source.</p>}<div className="overflow-auto max-h-64"><table><thead><tr><th>Condition</th><th>Exact source position</th><th>Saved value</th></tr></thead><tbody>{savedRows.map((row, i) => <tr key={i}><td>{row.family}</td><td>{row.identity.documentId} · MODEL {row.identity.modelNumber} · {row.identity.labelAsymId}:{row.identity.labelSeqId} · {row.identity.labelAtomId} · atom {row.identity.atomIndex}</td><td>{Array.isArray(row.value) ? row.value.join(', ') : row.value}</td></tr>)}</tbody></table></div></details>}
            <MolstarViewer structureDocumentId={receipt.path} structureUrl={buildFileDownloadUrl(receipt.format === 'bcif' ? receipt.decoded_path ?? receipt.path : receipt.path)} format={receipt.format === 'bcif' ? 'cif' : receipt.format} height={360}
                metricLayers={receipt.model_numbers.length === 1 || receipt.model_number != null ? savedConditionLayers(savedRows, receipt.path) : []}
                showSequenceTrack controlledResidueSelection={!!onSelection} residueSelections={selectedIdentities ?? []}
                onMetricSelection={selection => onSelection?.(slot, receipt, selection)} />
        </>}
    </section>;
}

/** Task-facing acquisition; the existing row owns materialization and stale reads. */
export function NativeSourceInput({ label, value, bindings, onChange, onAcquire, onInspection, inspection, onSelection, selectedIdentities }: {
    label: string; value: string; bindings: NativeGenerationSourceBindings | undefined;
    onChange: (slot: string) => void;
    onAcquire: (source: NativeGenerationSourceBinding) => string;
    inspection?: StructureMaterialization;
    onSelection?: (slot: string, receipt: StructureMaterialization, selection: MetricSelection) => void;
    selectedIdentities?: Rfd4NativeSelectionIdentity[];
    onInspection?: (slot: string, receipt: StructureMaterialization, binding: NativeGenerationSourceBinding) => void;
}) {
    const [open, setOpen] = useState(false);
    const committedSlot = useRef<string | undefined>(undefined);
    const latest = useRef({ onAcquire, onInspection }); latest.current = { onAcquire, onInspection };
    const binding = bindings?.[value];
    const structureBindings = Object.entries(bindings ?? {}).filter(([, source]) => !fileBinding(source));
    const displayName = (slot: string, source: NativeGenerationSourceBinding) => {
        const path = source.path?.split(/[\\/]/).filter(Boolean).at(-1);
        const model = !fileBinding(source) ? source.model_number ?? source.document?.model ?? (source.candidate?.kind === 'native_document' ? source.candidate.model : source.candidate?.kind === 'design' ? source.candidate.document?.model : undefined) : undefined;
        const name = path && !/^native\.(?:pdb|cif|mmcif|bcif)(?:\.gz)?$/i.test(path) ? path : `Structure ${structureBindings.findIndex(([name]) => name === slot) + 1}`;
        return `${name}${model != null ? ` · MODEL ${model}` : ''}`;
    };
    return <div className="space-y-2 min-w-0" data-native-source-input>
        <div className="flex flex-wrap items-end gap-2">
            <label>{label}{(value || structureBindings.length > 0) && <select aria-label={label} value={value} onChange={event => { setOpen(false); onChange(event.target.value); }}>
                <option value="">Use existing input</option>
                {value && !structureBindings.some(([slot]) => slot === value) && <option value={value}>{value} · not bound</option>}
                {structureBindings.map(([slot, source]) => <option key={slot} value={slot}>{displayName(slot, source)}</option>)}
            </select>}</label>
            <button type="button" aria-label={`Choose structure for ${label}`} onClick={() => { committedSlot.current = undefined; setOpen(true); }}>Choose structure</button>
            {binding && !fileBinding(binding) && <button type="button" aria-label={`Inspect structure for ${label}`} onClick={() => setOpen(!open)}>Inspect</button>}
        </div>
        {open && <div className="space-y-2" role="group" aria-label={`Choose structure for ${label}`}>
            <button type="button" onClick={() => setOpen(false)}>{binding ? 'Close' : 'Cancel'}</button>
            <NativeSourceRow compact slot={value || label} value={binding && !fileBinding(binding) ? binding : { output_format: 'native' }} initialReceipt={inspection} selectedIdentities={selectedIdentities}
                onSelection={onSelection ? (_slot, receipt, selection) => onSelection(committedSlot.current ?? value, receipt, selection) : undefined}
                onChange={source => { committedSlot.current = latest.current.onAcquire(source); if (!value) setOpen(false); }}
                onInspection={(_slot, receipt, source) => latest.current.onInspection?.(committedSlot.current ?? value, receipt, source)} />
        </div>}
    </div>;
}

/** Bind exact documents to native logical names; never compile their science. */
export function DeNovoNativeSources({ value, onChange, onSelection, selection, onInspection }: {
    onInspection?: (slot: string, receipt: StructureMaterialization, binding: NativeGenerationSourceBinding) => void;
    selection?: { slot: string; identities: Rfd4NativeSelectionIdentity[] };
    value: NativeGenerationSourceBindings | undefined;
    onChange: (value: NativeGenerationSourceBindings) => void;
    onSelection?: (slot: string, receipt: StructureMaterialization, selection: MetricSelection) => void;
}) {
    const [name, setName] = useState('');
    const [kind, setKind] = useState('structure');
    return <div className="space-y-3" data-native-source-bindings>
        {Object.entries(value ?? {}).map(([slot, binding]) => <NativeSourceRow key={slot} slot={slot} value={binding} onInspection={onInspection} onSelection={onSelection} selectedIdentities={selection?.slot === slot ? selection.identities : []}
            onChange={next => onChange({ ...value, [slot]: next })} onRemove={() => { const next = { ...value }; delete next[slot]; onChange(next); }} />)}
        <details><summary>Advanced source bindings</summary><div className="flex flex-wrap gap-3"><label>Native source name<input aria-label="Native source name" value={name} onChange={event => setName(event.target.value)} /></label>
            <label>Source kind<select aria-label="Native source kind" value={kind} onChange={event => setKind(event.target.value)}><option value="structure">Structure document</option><option value="file">Files and archives</option></select></label>
            <button type="button" disabled={Object.hasOwn(value ?? {}, name)} onClick={() => { onChange({ ...value, [name]: kind === 'file' ? { kind: 'file', path: '' } : { output_format: 'native' } }); setName(''); }}>Add native source</button>
        </div></details>
    </div>;
}
