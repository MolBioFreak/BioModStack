import { useEffect, useRef, useState } from 'react';
import { TargetAntigenSelector, type SelectedTarget } from './TargetAntigenSelector';
import { FrameworkBrowser } from './FrameworkBrowser';
import EpitopeMolstarViewer from './EpitopeMolstarViewer';
import { EpitopeSelector } from './EpitopeSelector';
import { createBC2SourceSession, preparePdbStructureSource, type BC2Document, type BC2Source } from '../lib/bindcraft2StructureInputs';
import { nativeBinderField, portableNativeSource, ppiflowHotspotsFromSelection, type NativeBinderModel } from '../lib/nativeBinderAuthoring';
import type { ResidueRef } from '../structureViewer/contracts/structureIdentity';

const input = 'w-full rounded-lg border border-[var(--border-primary)] bg-[var(--bg-primary)] p-2 text-sm';
const button = 'rounded-lg border border-[var(--border-primary)] px-3 py-2 text-sm hover:text-accent';
const errorText = (error: unknown) => error instanceof Error ? error.message : String(error);

/** Shared acquisition and inspection, but no BC2 request or mask semantics. */
export function NativeBinderSource({ model, mode, field, label, values, onPatch, onChains }: {
    model: NativeBinderModel; mode: string; field: string; label: string;
    values: Record<string, UntypedApiValue>; onPatch: (patch: Record<string, UntypedApiValue>) => void;
    onChains: (field: string, chains: string[]) => void;
}) {
    const role = nativeBinderField(field);
    const path = typeof values[field] === 'string' ? values[field] : '';
    const referenceKey = `${field}_source_reference`;
    const selectionKey = `${field}_inspection_residues`;
    const savedReference = values[referenceKey] as BC2Source | undefined;
    const refs: ResidueRef[] = Array.isArray(values[selectionKey]) ? values[selectionKey] : [];
    const [session] = useState(createBC2SourceSession);
    const families = useRef(new Map<string, BC2Document>()).current;
    const inspectedPath = path || savedReference?.path || '';
    const conversionPending = model === 'ppiflow' && !path && !!savedReference?.path;
    const [document, setDocument] = useState<BC2Document>();
    const [family, setFamily] = useState<BC2Document>();
    const [error, setError] = useState('');
    const [busy, setBusy] = useState(false);
    const [library, setLibrary] = useState(false);
    const [inspectionRevision, setInspectionRevision] = useState(0);
    const epoch = useRef(0);
    const latest = useRef({ path, onPatch, onChains }); latest.current = { path, onPatch, onChains };
    // Invalidates reads on clear, replace, mode switch and unmount. An inspection
    // failure does not introduce a new launch precondition.
    useEffect(() => {
        const token = ++epoch.current;
        setDocument(undefined); setFamily(undefined); setError(conversionPending ? 'Native source retained. Select a representable conformation for checked PDB conversion; no primary-document fallback was applied.' : ''); setBusy(false);
        onChains(field, []);
        if (inspectedPath) void session.read({ ...savedReference, path: inspectedPath, name: savedReference?.name || inspectedPath }).then(doc => {
            if (epoch.current !== token) return;
            setDocument(doc); setFamily(families.get(inspectedPath) || doc); latest.current.onChains(field, doc.models[0]?.chains.map(chain => chain.id) || []);
        }).catch(error => { if (epoch.current === token) setError(errorText(error)); });
        return () => { epoch.current++; };
        // A native path is the authoritative document identity. Saved metadata is
        // restored alongside it; editing a mask must not reload the structure.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [inspectedPath, path, field, session, conversionPending, families]);
    const choose = async (source: BC2Source, modelFamily?: BC2Document) => {
        const token = ++epoch.current; setBusy(true); setError('');
        try {
            let result: { path: string; document: BC2Document } = await session.acquire(source);
            if (epoch.current !== token) return;
            if (modelFamily) families.set(result.path, modelFamily);
            if (model === 'ppiflow' && result.document.format !== 'pdb') {
                const native = result;
                try { result = await preparePdbStructureSource(source, session); }
                catch (error) {
                    if (epoch.current !== token) return;
                    latest.current.onPatch({ [field]: '', [referenceKey]: { ...portableNativeSource(native.document.source || source), path: native.path }, [selectionKey]: [] });
                    setDocument(native.document); setFamily(modelFamily || native.document);
                    setError(errorText(error)); return;
                }
                if (epoch.current !== token) return;
                families.set(result.path, modelFamily || native.document);
            }
            latest.current.onPatch({ [field]: result.path, [referenceKey]: { ...portableNativeSource(result.document.source || source), path: result.path }, [selectionKey]: [] });
            setDocument(result.document); setFamily(modelFamily || families.get(result.path) || result.document);
            latest.current.onChains(field, result.document.models[0]?.chains.map(chain => chain.id) || []);
        } catch (error) { if (epoch.current === token) setError(errorText(error)); }
        finally { if (epoch.current === token) setBusy(false); }
    };
    const adopted = useRef(false);
    useEffect(() => {
        if (adopted.current || role !== 'target_pdb' || Object.hasOwn(values, field) || !values.target_source) return;
        adopted.current = true;
        const source = values.target_source as BC2Source;
        const number = typeof values.target_model_number === 'number' ? values.target_model_number : undefined;
        if (number === undefined) { void choose(source); return; }
        const token = ++epoch.current;
        void session.read(source).then(family => {
            if (epoch.current !== token) return;
            const selected = family.models.find(item => item.number === number);
            if (!selected) { setError(`Source model ${number} is not available in this exact document. The source reference is retained; no primary fallback was applied.`); return; }
            void choose({ name: `model-${number}.${family.format}`, file: new File([selected.content], `model-${number}.${family.format}`), derivedFrom: source, modelNumber: number }, family);
        }).catch(error => { if (epoch.current === token) setError(errorText(error)); });
        // The source handoff is adopted once, only into an untouched native field.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [values.target_source, values.target_model_number, field, session]);
    const documentId = path || savedReference?.path || field;
    const chains = document?.models[0]?.chains || [];
    const selectResidues = (next: ResidueRef[]) => onPatch({ [selectionKey]: next });
    const antibodyFramework = role === 'framework_pdb' || model === 'boltzgen' && mode === 'nanobody_binder' && role !== 'target_pdb';
    const isTarget = role === 'target_pdb';
    return <section aria-label={`${label} source`} className="space-y-4 rounded-xl border border-[var(--border-primary)] p-4">
        <header><h3 className="font-semibold">{label}</h3><p className="text-xs text-[var(--text-secondary)]">{isTarget ? 'Target/context document; never assembled into a seed complex.' : model === 'ppiflow' ? 'Separate IMGT framework PDB. Native gaps identify CDR insertion sites; acquisition does not remove loops or renumber a framework.' : 'Optional structural template, independent of target context. Design positions use native chain-local numbering.'}</p></header>
        <TargetAntigenSelector label={label}
            selectedTarget={savedReference && 'type' in savedReference ? savedReference as SelectedTarget : undefined}
            onInspect={() => setInspectionRevision(value => value + 1)} onSelect={source => {
            if (source) void choose(source);
            else { epoch.current++; setDocument(undefined); setFamily(undefined); setError(''); onPatch({ [field]: '', [referenceKey]: null, [selectionKey]: [] }); }
        }} />
        {antibodyFramework && <><button type="button" className={button} onClick={() => setLibrary(!library)}>Antibody framework library</button>{library && <FrameworkBrowser onSelect={source => {
            if (!source) return;
            if (source.filePath) void choose({ path: source.filePath, name: source.name });
            else if (source.pdbContent) void choose({ file: new File([source.pdbContent], 'framework.pdb'), name: source.name });
            else setError('This library entry has no structural document. A sequence is not a PPIFlow framework PDB.');
        }} />}</>}
        <label className="block text-xs">Native {label.toLowerCase()} path<input className={input} aria-label={field} placeholder={isTarget ? 'Target structure' : 'Materialized structure path'} value={path} onChange={event => { epoch.current++; onPatch({ [field]: event.target.value, [referenceKey]: null, [selectionKey]: [] }); }} /></label>
        {savedReference && <p className="break-all text-xs text-[var(--text-secondary)]">{savedReference.name} · {savedReference.document?.target_state || 'source document'} · {savedReference.document?.artifact_id || savedReference.document?.logical_path || savedReference.path} {savedReference.modelNumber !== undefined && `· model ${savedReference.modelNumber}`}</p>}
        {savedReference?.materialization && <p className="break-all text-xs" aria-label="Native and derived source identity">Native {savedReference.materialization.native_format}: {savedReference.materialization.native_path} · consumed {savedReference.materialization.format}: {savedReference.materialization.path} · source models {savedReference.materialization.model_numbers.join(', ')} · author chain/residue identities retained</p>}
        {busy && <p role="status">Preparing exact source…</p>}
        {error && <p role="alert" className="text-sm text-amber-500">{error}</p>}
        {family && family.models.length > 1 && <label className="block text-sm">Consumed conformation<select aria-label={`${field} model`} className={input} value={savedReference?.modelNumber ?? ''} onChange={event => {
            const number = Number(event.target.value); const selected = family.models.find(item => item.number === number);
            if (selected) void choose({ name: `model-${number}.${family.format}`, file: new File([selected.content], `model-${number}.${family.format}`), derivedFrom: family.source, modelNumber: number }, family);
        }}><option value="" disabled>Select exact model to materialize</option>{family.models.map(item => <option key={item.number} value={item.number}>Model {item.number}</option>)}</select></label>}
        {document && document.format !== 'fasta' && <>
            <EpitopeMolstarViewer key={documentId} expandRevision={inspectionRevision} pdbData={document.content} format={document.format} documentId={documentId} sourceLabel={savedReference?.name || label} defaultFullView={inspectionRevision > 0} selectedResidueRefs={refs} onResidueRefClick={ref => {
                const same = (other: ResidueRef) => other.documentId === ref.documentId && other.authAsymId === ref.authAsymId && other.authSeqId === ref.authSeqId && other.insertionCode === ref.insertionCode;
                selectResidues(refs.some(same) ? refs.filter(other => !same(other)) : [...refs, ref]);
            }} />
            <EpitopeSelector chains={chains} selectedResidues={new Set(refs.map(ref => `${ref.authAsymId}${ref.authSeqId}${ref.insertionCode || ''}`))} onSelectionChange={keys => selectResidues(chains.flatMap(chain => chain.residues.filter(residue => keys.has(`${chain.id}${residue.resNum}${residue.iCode || ''}`)).map(residue => ({ documentId, authAsymId: chain.id, authSeqId: residue.resNum, insertionCode: residue.iCode }))))} selectedLabel="Inspection selection (author numbering)" />
            {isTarget && model === 'ppiflow' ? <button type="button" className={button} onClick={() => {
                try { onPatch({ specified_hotspots: ppiflowHotspotsFromSelection(document, documentId, refs, String(values[mode === 'protein_binder' ? 'target_chain' : 'antigen_chain'] || ''), mode) }); setError(''); }
                catch (error) { setError(errorText(error)); }
            }}>Use inspected residues as native PPIFlow hotspots</button> : <p className="text-xs text-[var(--text-secondary)]">Inspection uses author numbering. It does not change native masks. BoltzGen chain-local positions must be entered explicitly: this parser does not provide a verified native position map.</p>}
        </>}
    </section>;
}
