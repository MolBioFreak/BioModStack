import { useEffect, useState } from 'react';
import { useQueries } from '@tanstack/react-query';
import { isAxiosError } from 'axios';
import { downloadDesignPdb, fetchDesignById, fetchJobById, fetchModelById, fetchFiles } from '../lib/api';
import { fetchDiagnosticSelectionContext, type CandidateDocuments, type DiagnosticSelectionContext } from '../lib/binderDiagnosticSelection';
import { submitBinderSelected, readBinderCandidateDocuments, writeBinderCandidateDocuments, type BinderOperation } from '../lib/binderContinuation';
import { BinderWorkflowWorkspace } from './BinderWorkflowWorkspace';
import { SequenceManager } from './SequenceManager';
import { StructuralSourceFiles } from './StructuralSourceFiles';
import EpitopeMolstarViewer from './EpitopeMolstarViewer';
import { CDRRangeSelector, type CDRDefinition } from './CDRRangeSelector';
import { EpitopeSelector } from './EpitopeSelector';
import { parseBC2Document, type BC2Document } from '../lib/bindcraft2StructureInputs';
import { BindCraft2SettingsReadback } from './BindCraft2NativeResults';
import { ParamField } from './ModelParameterField';
import { ExecutionTargetPicker } from './ExecutionTargetPicker';
import { FrustraMpnnSettingsPanel } from './frustrampnn/FrustraMpnnSettingsPanel';
import { hydrateFrustraMpnnSettings } from './frustrampnn/frustraMpnnSettingsState';

const models = { refine: 'binder_refinement', caliby: 'caliby_binder', fampnn: 'fampnn', proteinmpnn: 'proteinmpnn', predict_boltz2: 'boltz2', predict_protenix: 'protenix' } as const;
const modes = { refine: 'refine', caliby: 'design', fampnn: 'binder_design', proteinmpnn: 'design', predict_boltz2: 'complex', predict_protenix: 'complex' } as const;
const systemInputs = new Set(['pdb_paths', 'source_identity_json', 'selected_input_dir', 'selected_input_manifest', 'input_pdb', 'sequence', 'sequence_name']);
const modelFields = (model: UntypedApiValue, operation: Exclude<BinderOperation, 'frustrampnn'>) => {
    const mode = model?.modes?.find((item: UntypedApiValue) => item.id === modes[operation]);
    return (model?.params ?? []).filter((p: UntypedApiValue) => !p.hidden && !systemInputs.has(p.name)
        && (!mode?.params?.length || mode.params.includes(p.name)));
};
interface Props {
    sourceJobId: string;
    selectedDesignIds: string[];
    onOpenJob: (id: string) => void;
    onStartMD: (designId: string) => void;
    candidateDocuments?: CandidateDocuments;
    launchContextId?: string | null;
    inspectDesignId?: string;
}

/** Reuse global model parameter metadata and native FrustraMPNN settings. */
export default function BinderSelectedControls({ sourceJobId, selectedDesignIds, onOpenJob, onStartMD, candidateDocuments, launchContextId, inspectDesignId }: Props) {
    const [section, setSection] = useState('sources');
    const [inspectId, setInspectId] = useState<string | null>(inspectDesignId ?? null);
    useEffect(() => { if (inspectDesignId) { setInspectId(inspectDesignId); setSection('sources'); } }, [inspectDesignId]);
    const [inspectionDocument, setInspectionDocument] = useState<BC2Document | null>(null);
    const [inspectionModel, setInspectionModel] = useState<number | null>(null);
    const [cdrChain, setCdrChain] = useState('');
    const [inspectionResidues, setInspectionResidues] = useState<Set<string>>(new Set());
    const [fileField, setFileField] = useState<string | null>(null);
    const [fileFolder, setFileFolder] = useState('/');
    const [fileEntries, setFileEntries] = useState<Array<{ path: string; name: string; is_directory: boolean }>>([]);
    const [sequenceField, setSequenceField] = useState('');
    const [showSequences, setShowSequences] = useState(false);
    const [documentSelections, setDocumentSelections] = useState<CandidateDocuments>(() => candidateDocuments ?? readBinderCandidateDocuments(sourceJobId));
    const [sourceRows, setSourceRows] = useState<Record<string, { owner: string; documents: DiagnosticSelectionContext['candidate_documents'][string] }>>({});
    const [sourceError, setSourceError] = useState<string | null>(null);
    const selectedKey = JSON.stringify(selectedDesignIds);
    const documentKey = JSON.stringify(candidateDocuments);
    useEffect(() => { setDocumentSelections(documentKey ? JSON.parse(documentKey) : readBinderCandidateDocuments(sourceJobId)); }, [sourceJobId, documentKey]);
    useEffect(() => {
        const sync = (event: Event) => { const detail = (event as CustomEvent).detail; if (detail.jobId === sourceJobId) setDocumentSelections(detail.documents); };
        window.addEventListener('bms:binder-documents', sync);
        return () => window.removeEventListener('bms:binder-documents', sync);
    }, [sourceJobId]);
    const chooseDocument = (id: string, value: string) => {
        const next = { ...documentSelections };
        const selector = JSON.parse(value);
        if (Object.keys(selector).length) next[id] = selector;
        else delete next[id];
        writeBinderCandidateDocuments(sourceJobId, next);
        setDocumentSelections(next);
    };
    useEffect(() => {
        let current = true;
        setSourceRows({}); setSourceError(null);
        const contexts = new Map<string, ReturnType<typeof fetchDiagnosticSelectionContext>>();
        void Promise.all((JSON.parse(selectedKey) as string[]).map(async id => {
            const { data } = await fetchDesignById(id);
            const owner = data.job_id;
            if (!owner) return [id, { owner: sourceJobId, documents: [] }] as const;
            if (!contexts.has(owner)) contexts.set(owner, fetchDiagnosticSelectionContext(owner));
            const context = await contexts.get(owner)!;
            return [id, { owner, documents: context.candidate_documents?.[id] ?? [] }] as const;
        })).then(rows => { if (current) setSourceRows(Object.fromEntries(rows)); })
            .catch(reason => { if (current) setSourceError(String(reason)); });
        return () => { current = false; };
    }, [sourceJobId, selectedKey]);
    const [operation, setOperation] = useState<BinderOperation>('refine');
    const [model, setModel] = useState<UntypedApiValue | null>(null);
    const [settings, setSettings] = useState<Record<string, Record<string, UntypedApiValue>>>({});
    const [frustra, setFrustra] = useState(() => hydrateFrustraMpnnSettings(undefined));
    const [inspectionSource, setInspectionSource] = useState<File | null>(null);
    const [inspectionError, setInspectionError] = useState<string | null>(null);
    const firstSelectedId = inspectId ?? selectedDesignIds[0];
    const inspectedDocument = documentSelections[firstSelectedId];
    const inspectedRow = sourceRows[firstSelectedId];
    useEffect(() => {
        const controller = new AbortController();
        setInspectionSource(null); setInspectionDocument(null); setInspectionModel(null); setCdrChain(''); setInspectionResidues(new Set()); setInspectionError(null);
        if ((!inspectId && operation !== 'frustrampnn') || !firstSelectedId) return;
        void fetchDesignById(firstSelectedId).then(async ({ data }) => {
            const explicit = inspectedDocument && (inspectedDocument.artifact_id !== undefined || inspectedDocument.target_state !== undefined);
            const matches = inspectedRow?.documents.filter(doc => (!inspectedDocument?.artifact_id || doc.artifact_id === inspectedDocument.artifact_id)
                && (inspectedDocument?.target_state === undefined || doc.target_state === inspectedDocument.target_state)) ?? [];
            const document = matches.length === 1 ? matches[0] as typeof matches[number] & { download_url?: string } : undefined;
            if (explicit && !document?.download_url) throw new Error('Selected document inspection is not available yet');
            const url = explicit ? document!.download_url! : downloadDesignPdb(firstSelectedId);
            const response = await fetch(url, { credentials: 'same-origin', signal: controller.signal });
            if (!response.ok) throw new Error(`Selected source inspection failed (${response.status})`);
            const bytes = await response.arrayBuffer();
            const suffix = /\.(cif|mmcif)$/i.test(explicit ? matches[0]?.logical_path ?? '' : data.pdb_path ?? '') ? '.cif' : '.pdb';
            const file = new File([bytes], `${firstSelectedId}${suffix}`);
            if (!controller.signal.aborted) setInspectionSource(file);
            const parsed = await parseBC2Document(new TextDecoder().decode(bytes), file.name);
            if (!controller.signal.aborted) setInspectionDocument(parsed);
        }).catch(reason => { if (!controller.signal.aborted) setInspectionError(String(reason)); });
        return () => controller.abort();
    }, [operation, inspectId, firstSelectedId, sourceJobId, inspectedDocument, inspectedRow]);
    const [target, setTarget] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [busy, setBusy] = useState(false);
    const [children, setChildren] = useState<Array<{ id: string; name: string }>>([]);
    const childQueries = useQueries({ queries: children.map(child => ({
        queryKey: ['binder-child-job', child.id], queryFn: () => fetchJobById(child.id).then(response => response.data),
        refetchInterval: (query: { state: { data?: { status?: string } } }) => !query.state.data?.status || ['queued', 'running'].includes(query.state.data.status) ? 3000 : false,
    })) });
    useEffect(() => {
        let current = true;
        setModel(null);
        setError(null);
        if (operation === 'frustrampnn') return;
        fetchModelById(models[operation]).then(({ data }) => {
            if (!current) return;
            setModel(data);
            const defaults = Object.fromEntries(modelFields(data, operation)
                .filter((p: UntypedApiValue) => p.default !== undefined)
                .map((p: UntypedApiValue) => [p.name, p.default]));
            setSettings(previous => ({ ...previous, [operation]: { ...defaults, ...previous[operation] } }));
        }).catch(reason => { if (current) setError(String(reason)); });
        return () => { current = false; };
    }, [operation]);
    const params = settings[operation] ?? {};
    const updateParam = (key: string, value: UntypedApiValue) => setSettings(previous => ({ ...previous, [operation]: { ...previous[operation], [key]: value } }));
    const browseFiles = async (path: string) => {
        try { const { data } = await fetchFiles(path); setFileFolder(path); setFileEntries(data.entries); }
        catch (reason) { setError(String(reason)); }
    };
    const fields = operation === 'frustrampnn' ? [] : modelFields(model, operation);
    const inspectedModel = inspectionDocument?.models.find(model => model.number === inspectionModel) ?? (inspectionDocument?.models.length === 1 ? inspectionDocument.models[0] : undefined);
    const run = async () => {
        setBusy(true); setError(null); setChildren([]);
        try {
            const result = await submitBinderSelected({ source_job_id: sourceJobId,
                design_ids: [...selectedDesignIds], operation,
                candidate_documents: Object.fromEntries(selectedDesignIds.filter(id => documentSelections[id]).map(id => [id, documentSelections[id]])),
                ...(launchContextId ? { launch_context_id: launchContextId } : {}),
                ...(operation === 'frustrampnn' ? { frustrampnn_settings: frustra }
                    : { params: { ...params }, execution_target_id: target }),
            });
            setChildren(result.launched_jobs);
        } catch (reason) {
            setError(isAxiosError(reason) ? JSON.stringify(reason.response?.data?.detail ?? reason.message)
                : reason instanceof Error ? reason.message : String(reason));
        } finally { setBusy(false); }
    };
    return <section aria-label="Selected binder continuation" className="mb-4 text-sm"><BinderWorkflowWorkspace
        title="Selected candidate workspace" sections={['Sources', 'Operations', 'Settings', 'Review'].map(label => ({ id: label.toLowerCase(), label }))}
        activeSection={section} onSectionChange={setSection}
        summary={<p>Destination: {launchContextId ? `explicit Project launch context ${launchContextId}` : 'standalone (no Project destination)'}. Source ancestry is unchanged.</p>}>
        <h3 className="font-semibold">Continue selected candidates</h3>
        <p>{selectedDesignIds.length} selected across all pages. Each action starts a separate model-owned round from these exact states; no diagnostic is required.</p>
        <div hidden={section !== 'sources'}><details open className="mt-2"><summary>Selected sources</summary>
            <ul>{selectedDesignIds.map(id => <li key={id}>{id} · Source Job {sourceRows[id]?.owner ?? sourceJobId}
                <label>Document / state <select aria-label={`Document for ${id}`} value={JSON.stringify(documentSelections[id] ?? {})}
                    onChange={event => chooseDocument(id, event.target.value)}>
                    <option value="{}">Primary document (native default)</option>
                    {sourceRows[id]?.documents.map(doc => {
                        const selector = { artifact_id: doc.artifact_id, ...(doc.target_state != null ? { target_state: doc.target_state } : {}) };
                        return <option key={JSON.stringify(selector)} value={JSON.stringify(selector)}>{doc.target_state ?? 'Unnamed state'} · {doc.logical_path ?? doc.artifact_id}</option>;
                    })}
                    {documentSelections[id] && !sourceRows[id]?.documents.some(doc => doc.artifact_id === documentSelections[id].artifact_id && doc.target_state === documentSelections[id].target_state)
                        && Object.keys(documentSelections[id]).length > 0 && <option value={JSON.stringify(documentSelections[id])}>{documentSelections[id].target_state ?? 'Selected state'} · {documentSelections[id].artifact_id ?? 'Producer-bound document'}</option>}
                </select></label>
                <button type="button" onClick={() => setInspectId(id)}>Inspect exact source {id}</button>
                <button type="button" onClick={() => onStartMD(id)}>Use as GROMACS MD starting structure</button></li>)}</ul>
            <p>GROMACS uses its separate Design starting-structure handoff; this document choice applies to the selected operation.</p>
            {sourceError && <p>Document inventory unavailable: {sourceError}. Primary selection and submitted exact identities remain available.</p>}
        </details>
        {inspectionDocument && <section aria-label="Exact selected source inspection">
            <p>Inspecting {firstSelectedId} · {inspectedDocument?.artifact_id ?? 'Design primary document'}. Inspection does not change the selected document.</p>
            {inspectionDocument.format !== 'fasta' && <EpitopeMolstarViewer pdbData={inspectedModel?.content ?? inspectionDocument.content} format={inspectionDocument.format} documentId={`${firstSelectedId}:${inspectedDocument?.artifact_id ?? 'primary'}:${inspectedModel?.number ?? 'all-models'}`} defaultFullView selectedResidues={inspectionResidues} onResidueClick={residue => setInspectionResidues(current => { const next = new Set(current); if (next.has(residue)) next.delete(residue); else next.add(residue); return next; })} />}
            {inspectionDocument.models.length > 1 && <label>Inspected model<select aria-label="Inspected model" value={inspectionModel ?? ''} onChange={event => { setInspectionModel(Number(event.target.value)); setInspectionResidues(new Set()); }}><option value="" disabled>Choose a model for residue inspection</option>{inspectionDocument.models.map(model => <option key={model.number} value={model.number}>{model.number}</option>)}</select></label>}
            <EpitopeSelector chains={inspectedModel?.chains ?? []} selectedResidues={inspectionResidues} onSelectionChange={setInspectionResidues} selectedLabel="Inspected author residues" />
            {fields.some((field: UntypedApiValue) => field.name === 'fixed_positions') && <button type="button" onClick={() => {
                const positions = (inspectedModel?.chains ?? []).flatMap(chain => chain.residues.filter(residue => inspectionResidues.has(`${chain.id}${residue.resNum}${residue.iCode || ''}`)).map(residue => `${chain.id}:${residue.resNum}${residue.iCode || ''}`));
                updateParam('fixed_positions', positions.join(','));
            }}>Use inspected author residues as fixed positions</button>}
            <p>Native masks remain explicit settings. No CDR, interface or chain-role inference is performed.</p>
        </section>}
        {inspectionError && operation !== 'frustrampnn' && <p>Source inspection unavailable: {inspectionError}</p>}
        </div>
        <div hidden={section !== 'operations'}><label>Operation <select aria-label="Binder continuation operation" value={operation} onChange={e => setOperation(e.target.value as BinderOperation)}>
            <option value="refine">Independent repack, anchors and native antibody/nanobody flow</option>
            <option value="fampnn">FA-MPNN binder sequence redesign</option>
            <option value="proteinmpnn">ProteinMPNN sequence design</option>
            <option value="caliby">Caliby</option>
            <option value="predict_boltz2">Boltz-2 protein complex prediction</option>
            <option value="predict_protenix">Protenix protein complex prediction</option>
            <option value="frustrampnn">FrustraMPNN landscape</option>
        </select></label><p>Choose one model-owned action. Repack, anchors, flow and redesign switches remain independently editable in Settings.</p></div>
        <div hidden={section !== 'settings'}>{operation === 'frustrampnn' ? <>
            <FrustraMpnnSettingsPanel value={frustra} onChange={setFrustra}
                governedSource={inspectionSource ? { kind: 'upload', file: inspectionSource } : null} />
            {inspectionError && <p role="status">Source inspection unavailable: {inspectionError}. The model owner resolves the selected inputs at submission.</p>}
        </> : <>
            {!model && !error && <p role="status">Loading model settings…</p>}
            <div className="mt-3 grid grid-cols-1 gap-3 md:grid-cols-2">{fields.map((param: UntypedApiValue) => param.name === 'manual_cdr_definitions' ? <section key={param.name} aria-label="Manual native CDR definitions">
                <h4>Manual native CDR definitions</h4><p>Assign author residues from the inspected source; no automatic region inference.</p>
                <label>CDR source chain<select aria-label="CDR source chain" value={cdrChain} onChange={event => setCdrChain(event.target.value)}><option value="">First inspected chain</option>{inspectedModel?.chains.map(chain => <option key={chain.id} value={chain.id}>{chain.id}</option>)}</select></label>
                <CDRRangeSelector activeChain={cdrChain || undefined} chains={inspectedModel?.chains ?? []} cdrDefinitions={(Array.isArray(params[param.name]) ? params[param.name] : []).map((row: UntypedApiValue) => ({ ...row, residues: new Set<string>(row.residues ?? []) }))}
                    onDefinitionsChange={(rows: CDRDefinition[]) => updateParam(param.name, rows.map(row => ({ ...row, residues: [...row.residues] })))} />
                {!inspectedModel && <button type="button" onClick={() => { setInspectId(firstSelectedId); setSection('sources'); }}>Inspect source to define regions</button>}
            </section> : param.name === 'cdr_positions_by_loop' ? <fieldset key={param.name}><legend>Native loop positions</legend><p>Explicit native numeric positions, independent of manual author-residue definitions.</p>
                {Object.entries(params[param.name] ?? {}).map(([loop, raw]) => <div key={loop}><span>{loop}</span>{(Array.isArray(raw) ? raw : []).map((position, index, positions) => <label key={index}>{loop} position {index + 1}<input aria-label={`${loop} position ${index + 1}`} type="number" step={1} value={position} onChange={event => { if (event.target.value !== '') updateParam(param.name, { ...params[param.name], [loop]: positions.map((value, i) => i === index ? Number(event.target.value) : value) }); }} /><button type="button" onClick={() => updateParam(param.name, { ...params[param.name], [loop]: positions.filter((_, i) => i !== index) })}>Remove {loop} position {index + 1}</button></label>)}
                    <button type="button" onClick={() => { const next = { ...params[param.name] }; delete next[loop]; updateParam(param.name, next); }}>Remove loop {loop}</button>
                </div>)}
                <form onSubmit={event => { event.preventDefault(); const data = new FormData(event.currentTarget); const loop = String(data.get('loop')); const position = Number(data.get('position')); updateParam(param.name, { ...params[param.name], [loop]: [...(params[param.name]?.[loop] ?? []), position] }); }}><label>Loop ID<input name="loop" aria-label="Native loop ID" required /></label><label>Position<input name="position" aria-label="Native loop position" type="number" step={1} required /></label><button type="submit">Add native loop position</button></form>
            </fieldset> : (['sequence', 'dna', 'rna'].includes(param.preset_type) || param.type === 'text' && param.ui_control !== 'textarea') ? <section key={param.name} aria-label={param.label || param.name}>
                <label>{param.label || param.name}<textarea aria-label={param.label || param.name} value={params[param.name] ?? ''} onChange={event => updateParam(param.name, event.target.value)} /></label>
                <p>{param.description}</p><button type="button" onClick={() => { setSequenceField(param.name); setShowSequences(true); }}>Choose saved sequence for {param.label || param.name}</button>
                {inspectedModel?.chains.map(chain => <button key={chain.id} type="button" onClick={() => updateParam(param.name, chain.sequence)}>Use inspected chain {chain.id} sequence for {param.label || param.name}</button>)}
                <p>Sequence selection changes only this native field; it does not assign chain roles or create coordinates.</p>
            </section> : <ParamField
                key={param.name} param={param} params={params}
                updateParam={updateParam}
                setShowFileBrowser={name => { setFileField(name); if (name) void browseFiles('/'); }} setActiveSequenceField={setSequenceField} setShowSequenceManager={setShowSequences} ligandPresets={[]} />)}</div>
            <ExecutionTargetPicker value={target} onChange={setTarget} disabled={busy} />
        </>}
        {fileField && <section aria-label="Native input file browser"><h4>Choose {fileField}</h4>
            <StructuralSourceFiles allowSequence onSelect={source => { updateParam(fileField, source.path); setFileField(null); }} />
            <p>{fileFolder}</p>{fields.find((field: UntypedApiValue) => field.name === fileField)?.type === 'directory' && <button type="button" onClick={() => { updateParam(fileField, fileFolder); setFileField(null); }}>Use this native input directory</button>}<button type="button" onClick={() => void browseFiles('/' + fileFolder.split('/').filter(Boolean).slice(0, -1).join('/'))}>Parent folder</button>
            {fileEntries.map(entry => <button key={entry.path} type="button" onClick={() => { if (entry.is_directory) void browseFiles(entry.path); else { updateParam(fileField, entry.path); setFileField(null); } }}>{entry.name}</button>)}
            <button type="button" onClick={() => setFileField(null)}>Close file browser</button></section>}
        {showSequences && <SequenceManager onClose={() => setShowSequences(false)} onSelect={sequence => { updateParam(sequenceField, sequence.sequence); setShowSequences(false); }} />}
        </div>
        <div hidden={section !== 'review'}><h4>Review selected request</h4>
            <p>Source Job {sourceJobId}; {selectedDesignIds.length} exact selected candidates. Operation: {operation}.</p>
            <BindCraft2SettingsReadback value={{ design_ids: selectedDesignIds, candidate_documents: Object.fromEntries(selectedDesignIds.filter(id => documentSelections[id]).map(id => [id, documentSelections[id]])), ...(operation === 'frustrampnn' ? { frustrampnn_settings: frustra } : { params, execution_target_id: target }) }} />
        </div>
        <button type="button" disabled={busy || !selectedDesignIds.length || (operation !== 'frustrampnn' && !model)} onClick={() => void run()}>Run selected operation</button>
        {busy && <p role="status">Submitting selected operation…</p>}
        {error && <p role="alert">{error}</p>}
        {children.map((child, index) => <p role="status" key={child.id}>{childQueries[index]?.data?.status ?? 'Queued'} {child.name} ({child.id}). <button type="button" onClick={() => onOpenJob(child.id)}>Open child Job</button></p>)}
    </BinderWorkflowWorkspace></section>;
}
