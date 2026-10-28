import { useEffect, useRef, useState } from 'react';
import { TargetAntigenSelector, type SelectedTarget } from './TargetAntigenSelector';
import EpitopeMolstarViewer from './EpitopeMolstarViewer';
import { EpitopeSelector } from './EpitopeSelector';
import { createBC2SourceSession, type BC2Document } from '../lib/bindcraft2StructureInputs';
import { materializeStructureTarget } from '../lib/api';
import { laMotifAtoms, laAtomKey, laMotifAtomSpec } from '../lib/laProteinaAtomSelection';
import { LaProteinaTaskCatalog } from './LaProteinaTaskCatalog';
import { LaProteinaFragments } from './LaProteinaFragments';
import { materializeLaProteinaMotifSelection } from '../lib/laProteinaMotifSelection';
import { ProteinDesignPanel, themedInsetStyle, themedInputStyle, themedSelectedStyle } from './ProteinDesignWorkflow';

export interface LaProteinaMotifInspection {
    source: SelectedTarget | null;
    selected: string[];
    model: number;
    atoms?: string[];
}
export interface LaProteinaMotifInputProps {
    path: string;
    contig: string;
    segmentOrder: string;
    atomSpec?: string | null;
    taskName?: string;
    taskCatalog?: Record<string, Record<string, unknown>>;
    onTaskChange?: (key: string) => void;
    atomSelectionMode?: 'all_atom' | 'ca' | 'bb3o' | 'tip_atoms';
    onAtomSelectionModeChange?: (mode: 'all_atom' | 'ca' | 'bb3o' | 'tip_atoms' | undefined) => void;
    onChange: (patch: { path?: string; contig?: string; segmentOrder?: string; atomSpec?: string | null }) => void;
    initialInspection?: LaProteinaMotifInspection;
    onInspectionChange?: (inspection: LaProteinaMotifInspection) => void;
}

/** Inspection is independent of native inputs. Only an explicit action replaces
 * the motif path with a motif-only derivative; manual values are never normalized. */
export function LaProteinaMotifInput({ path, contig, segmentOrder, atomSpec, taskName, taskCatalog, onTaskChange, atomSelectionMode, onAtomSelectionModeChange, onChange, initialInspection, onInspectionChange }: LaProteinaMotifInputProps) {
    const [session] = useState(createBC2SourceSession);
    const [source, setSource] = useState<SelectedTarget | null>(initialInspection?.source?.path ? initialInspection.source : null);
    const [pickerOpen, setPickerOpen] = useState(!initialInspection?.source?.path && !path);
    const restored = useRef(initialInspection);
    const firstPath = useRef(true);
    const inspectCallback = useRef(onInspectionChange);
    inspectCallback.current = onInspectionChange;
    const [document, setDocument] = useState<BC2Document>();
    const [model, setModel] = useState(0);
    const [selected, setSelected] = useState(new Set<string>());
    const [selectedAtoms, setSelectedAtoms] = useState(new Set<string>());
    const [error, setError] = useState('');
    const [busy, setBusy] = useState(false);
    const [pickerKey, setPickerKey] = useState(0);
    const epoch = useRef(0);
    const readEpoch = useRef(0);
    const ownPath = useRef<string | undefined>(undefined);
    const current = useRef({ path, contig, segmentOrder, atomSpec, selected });
    current.current = { path, contig, segmentOrder, atomSpec, selected };
    useEffect(() => () => { epoch.current++; }, []);
    // External draft/template edits must also invalidate in-flight adoption,
    // including A → B → A values that match the old snapshot again.
    useEffect(() => { epoch.current++; setBusy(false); }, [contig, segmentOrder, atomSpec]);
    useEffect(() => {
        if (ownPath.current === path) { ownPath.current = undefined; return; }
        if (firstPath.current) {
            firstPath.current = false;
            if (initialInspection?.source?.path) return;
        }
        epoch.current++; setBusy(false); setDocument(undefined); setSelected(new Set()); setSelectedAtoms(new Set()); setModel(0); setError('');
        setPickerKey(value => value + 1);
        setSource(path ? { type: 'upload', name: path.split('/').pop() || path, path } : null);
    }, [path]);
    useEffect(() => {
        const token = ++readEpoch.current;
        setDocument(undefined); setSelected(new Set()); setSelectedAtoms(new Set()); setModel(0); setError(''); setBusy(false);
        if (!source) return;
        void session.read(source).then(value => {
            if (token !== readEpoch.current) return;
            setDocument(value);
            setPickerOpen(false);
            if (restored.current && restored.current.source?.path === source.path) {
                const previous = restored.current;
                setSelected(new Set(previous.selected));
                setModel(previous.model);
                setSelectedAtoms(new Set(previous.atoms ?? []));
                restored.current = undefined;
            }
        }).catch(reason => { if (token === readEpoch.current) setError(String(reason instanceof Error ? reason.message : reason)); });
        return () => { readEpoch.current++; };
    }, [source, session]);
    useEffect(() => {
        if (source && !document) return;
        inspectCallback.current?.({ source: source?.path ? { ...source, file: undefined } : null, selected: [...selected], model, atoms: [...selectedAtoms] });
    }, [source, document, selected, model, selectedAtoms]);
    const change = (patch: Parameters<typeof onChange>[0]) => { epoch.current++; setBusy(false); onChange(patch); };
    const choose = (next: SelectedTarget | null) => {
        restored.current = undefined;
        epoch.current++; setSource(next); setDocument(undefined); setSelected(new Set()); setSelectedAtoms(new Set()); setBusy(false);
        if (!next) change({ path: '' });
    };
    const clear = () => {
        restored.current = undefined; setPickerOpen(true);
        epoch.current++; setSource(null); setDocument(undefined); setSelected(new Set()); setSelectedAtoms(new Set()); setBusy(false); setError('');
        setPickerKey(value => value + 1); onChange({ path: '' });
    };
    const active = document?.models[model];
    const atoms = active ? laMotifAtoms(active.content, selected) : [];
    const apply = async (withAtoms = false) => {
        const token = ++epoch.current;
        const snapshot = current.current;
        setError('');
        try {
            if (!active) throw new Error('Choose a structure to inspect first. Manual native inputs remain available.');
            const motif = materializeLaProteinaMotifSelection(active.content, selected, contig);
            setBusy(true);
            const retained = await materializeStructureTarget({ name: 'laproteina-motif.pdb', file: new File([motif.pdb], 'laproteina-motif.pdb', { type: 'chemical/x-pdb' }) }, 'inputs');
            if (token !== epoch.current || current.current.path !== snapshot.path || current.current.contig !== snapshot.contig || current.current.segmentOrder !== snapshot.segmentOrder || current.current.atomSpec !== snapshot.atomSpec || current.current.selected !== snapshot.selected) return;
            ownPath.current = retained;
            onChange({ path: retained, contig: motif.contig, segmentOrder: snapshot.segmentOrder, ...(withAtoms ? { atomSpec: laMotifAtomSpec(atoms, selectedAtoms) } : {}) });
        } catch (reason) {
            if (token === epoch.current) setError(reason instanceof Error ? reason.message : String(reason));
        } finally { if (token === epoch.current) setBusy(false); }
    };
    const selectResidues = (value: Set<string>) => { epoch.current++; setBusy(false); setSelected(value); setSelectedAtoms(new Set()); };
    return <ProteinDesignPanel title="La-Proteina motif input">
        {onTaskChange && <LaProteinaTaskCatalog task={taskName ?? ''} catalog={taskCatalog ?? {}} customSource={Boolean(path)} onChange={onTaskChange} />}
        {onAtomSelectionModeChange && <label>Native motif extraction scope<select aria-label="La motif extraction scope" value={atomSelectionMode ?? ''} onChange={e => onAtomSelectionModeChange(e.target.value === '' ? undefined : e.target.value as NonNullable<LaProteinaMotifInputProps['atomSelectionMode']>)}><option value="">Inherit selected task</option><option value="all_atom">All available atoms</option><option value="ca">CA only</option><option value="bb3o">Backbone N / CA / C / O</option><option value="tip_atoms">Native residue-specific tip atoms</option></select><small>{atomSpec ? 'Explicit motif atom specification takes precedence; this scope remains retained but is not used.' : 'Native null/omitted atom specification uses contig-based extraction. This scope is independent of the trained all/tip checkpoint family.'}</small></label>}
        <details open={pickerOpen} onToggle={event => setPickerOpen(event.currentTarget.open)} className="rounded-lg border p-3" style={themedInsetStyle}>
            <summary className="cursor-pointer text-sm font-medium">{source ? `Source: ${source.name} · change source` : 'Choose motif source'}</summary>
            <div className="mt-3"><TargetAntigenSelector key={pickerKey} label="Motif source structure" requiredFormat="pdb" selectedTarget={source} onSelect={choose} /></div>
        </details>
        <button type="button" className="rounded-lg border px-3 py-2 text-sm" style={themedInsetStyle} onClick={clear}>Clear motif input</button>
        {document && document.models.length > 1 && <label className="block text-sm">Source model<select className="ml-2 rounded-lg border px-3 py-2" style={themedInputStyle} aria-label="Source model" value={model} onChange={event => { epoch.current++; setBusy(false); setModel(Number(event.target.value)); setSelected(new Set()); setSelectedAtoms(new Set()); }}>{document.models.map((entry, index) => <option key={entry.number} value={index}>{entry.number}</option>)}</select></label>}
        {active && <div className="grid gap-4 xl:grid-cols-2 min-w-0">
            <EpitopeMolstarViewer pdbData={active.content} sourceLabel="Motif source" selectedResidues={selected} onResidueClick={key => { const next = new Set(selected); if (next.has(key)) next.delete(key); else next.add(key); selectResidues(next); }} />
            <EpitopeSelector chains={active.chains} selectedResidues={selected} onSelectionChange={selectResidues} selectedLabel="Motif residues" />
        </div>}
        <p className="text-sm text-[var(--text-secondary)]">Select motif residues in 3D or the sequence grid. Specify block order and scaffold lengths below, then apply the selection. Only existing source atoms are retained.</p>
        <LaProteinaFragments contig={contig} onChange={contig => change({ contig })} />
        <label className="block text-sm space-y-1"><span>Ordered motif blocks and scaffold lengths</span><input className="w-full rounded-lg border px-3 py-2 text-sm font-mono outline-none" style={themedInputStyle} aria-label="Motif contig" value={contig} onChange={event => change({ contig: event.target.value })} placeholder="10/A25-27/5/B40/10" /></label>
        <label className="block text-sm space-y-1"><span>Native segment order</span><input className="w-full rounded-lg border px-3 py-2 text-sm font-mono outline-none" style={themedInputStyle} aria-label="Segment order" value={segmentOrder} onChange={event => change({ segmentOrder: event.target.value })} /></label>
        <button type="button" className="rounded-lg border px-4 py-2 text-sm font-medium disabled:opacity-50" style={themedSelectedStyle('var(--accent-primary)')} disabled={busy} onClick={() => void apply()}>{busy ? 'Saving motif…' : 'Use selected residues'}</button>
        <details className="rounded border p-3"><summary>Native motif atom selection</summary>
            <p className="text-sm">Choose existing atoms from the selected residues. Native specs address residue and atom name, not altloc; alternate conformers are shown but not independently constrained. Applying atoms also materializes the selected motif source. Clearing visual choices does not clear the saved specification.</p>
            <div className="max-h-64 overflow-auto">{atoms.map((atom, index) => <label key={`${laAtomKey(atom)}:${index}`} className="block"><input type="checkbox" checked={selectedAtoms.has(laAtomKey(atom))} onChange={event => { epoch.current++; setBusy(false); const next = new Set(selectedAtoms); if (event.target.checked) next.add(laAtomKey(atom)); else next.delete(laAtomKey(atom)); setSelectedAtoms(next); }} />{atom.residue} · {atom.atom}{atom.altloc ? ` · altloc ${atom.altloc}` : ''}</label>)}</div>
            <button type="button" disabled={busy} onClick={() => void apply(true)}>Use selected motif atoms</button>
            <label className="block">Native motif atom specification<input aria-label="Native motif atom specification" className="w-full rounded border p-2" style={themedInputStyle} value={atomSpec ?? ''} onChange={event => change({ atomSpec: event.target.value })} placeholder="A64: [O, CG]; A65: [N, CA]" /></label>
            <button type="button" onClick={() => change({ atomSpec: null })}>Clear atom specification (native null)</button>
        </details>
        {error && <p role="status" className="text-sm text-amber-400">{error}</p>}
        {path && <p className="text-xs break-all text-[var(--text-secondary)]">Native motif: {path}</p>}
        <details className="rounded-lg border p-3" style={themedInsetStyle}><summary className="cursor-pointer text-sm">Manual native input compatibility</summary>
            <p className="text-xs my-2">Supply an already prepared motif-only PDB and native contig/order verbatim. Visual selection is optional and does not gate launch.</p>
            <label className="block text-sm">Motif-only PDB path<input className="w-full rounded-lg border px-3 py-2 text-sm font-mono outline-none" style={themedInputStyle} aria-label="Motif PDB path" value={path} onChange={event => change({ path: event.target.value })} /></label>
        </details>
    </ProteinDesignPanel>;
}
export default LaProteinaMotifInput;
