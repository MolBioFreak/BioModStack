import { useEffect, useState } from 'react';
import { isAxiosError } from 'axios';
import { downloadDesignPdb, fetchDesignById, fetchModelById } from '../lib/api';
import { submitBinderSelected, type BinderOperation } from '../lib/binderContinuation';
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
}

/** Reuse global model parameter metadata and native FrustraMPNN settings. */
export default function BinderSelectedControls({ sourceJobId, selectedDesignIds, onOpenJob, onStartMD }: Props) {
    const [operation, setOperation] = useState<BinderOperation>('refine');
    const [model, setModel] = useState<UntypedApiValue | null>(null);
    const [settings, setSettings] = useState<Record<string, Record<string, UntypedApiValue>>>({});
    const [frustra, setFrustra] = useState(() => hydrateFrustraMpnnSettings(undefined));
    const [inspectionSource, setInspectionSource] = useState<File | null>(null);
    const [inspectionError, setInspectionError] = useState<string | null>(null);
    const firstSelectedId = selectedDesignIds[0];
    useEffect(() => {
        const controller = new AbortController();
        setInspectionSource(null); setInspectionError(null);
        if (operation !== 'frustrampnn' || !firstSelectedId) return;
        void fetchDesignById(firstSelectedId, sourceJobId).then(async ({ data }) => {
            const response = await fetch(downloadDesignPdb(firstSelectedId), { credentials: 'same-origin', signal: controller.signal });
            if (!response.ok) throw new Error(`Selected source inspection failed (${response.status})`);
            const bytes = await response.arrayBuffer();
            const suffix = /\.(cif|mmcif)$/i.test(data.pdb_path ?? '') ? '.cif' : '.pdb';
            if (!controller.signal.aborted) setInspectionSource(new File([bytes], `${firstSelectedId}${suffix}`));
        }).catch(reason => { if (!controller.signal.aborted) setInspectionError(String(reason)); });
        return () => controller.abort();
    }, [operation, firstSelectedId, sourceJobId]);
    const [target, setTarget] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [busy, setBusy] = useState(false);
    const [children, setChildren] = useState<Array<{ id: string; name: string }>>([]);
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
    const fields = operation === 'frustrampnn' ? [] : modelFields(model, operation);
    const run = async () => {
        setBusy(true); setError(null); setChildren([]);
        try {
            const result = await submitBinderSelected({ source_job_id: sourceJobId,
                design_ids: [...selectedDesignIds], operation,
                ...(operation === 'frustrampnn' ? { frustrampnn_settings: frustra }
                    : { params: { ...params }, execution_target_id: target }),
            });
            setChildren(result.launched_jobs);
        } catch (reason) {
            setError(isAxiosError(reason) ? JSON.stringify(reason.response?.data?.detail ?? reason.message)
                : reason instanceof Error ? reason.message : String(reason));
        } finally { setBusy(false); }
    };
    return <section aria-label="Selected binder continuation" className="mb-4 rounded-lg border border-slate-700 p-4 text-sm">
        <h3 className="font-semibold">Continue selected candidates</h3>
        <p>{selectedDesignIds.length} selected across all pages. Each action starts a separate model-owned round from these exact states; no diagnostic is required.</p>
        <details className="mt-2"><summary>Selected Design identities</summary>
            <ul>{selectedDesignIds.map(id => <li key={id}>{id} <button type="button" onClick={() => onStartMD(id)}>Use as GROMACS MD starting structure</button></li>)}</ul>
        </details>
        <label>Operation <select aria-label="Binder continuation operation" value={operation} onChange={e => setOperation(e.target.value as BinderOperation)}>
            <option value="refine">Independent repack, anchors and native antibody/nanobody flow</option>
            <option value="fampnn">FA-MPNN binder sequence redesign</option>
            <option value="proteinmpnn">ProteinMPNN sequence design</option>
            <option value="caliby">Caliby</option>
            <option value="predict_boltz2">Boltz-2 protein complex prediction</option>
            <option value="predict_protenix">Protenix protein complex prediction</option>
            <option value="frustrampnn">FrustraMPNN landscape</option>
        </select></label>
        {operation === 'frustrampnn' ? <>
            <FrustraMpnnSettingsPanel value={frustra} onChange={setFrustra}
                governedSource={inspectionSource ? { kind: 'upload', file: inspectionSource } : null} />
            {inspectionError && <p role="status">Source inspection unavailable: {inspectionError}. The model owner resolves the selected inputs at submission.</p>}
        </> : <>
            {!model && !error && <p role="status">Loading model settings…</p>}
            <div className="mt-3 grid grid-cols-1 gap-3 md:grid-cols-2">{fields.map((param: UntypedApiValue) => <ParamField
                key={param.name} param={param} params={params}
                updateParam={(key, value) => setSettings(previous => ({ ...previous, [operation]: { ...previous[operation], [key]: value } }))}
                setShowFileBrowser={() => {}} setActiveSequenceField={() => {}} setShowSequenceManager={() => {}} ligandPresets={[]} />)}</div>
            <ExecutionTargetPicker value={target} onChange={setTarget} disabled={busy} />
        </>}
        <button type="button" disabled={busy || !selectedDesignIds.length || (operation !== 'frustrampnn' && !model)} onClick={() => void run()}>Run selected operation</button>
        {busy && <p role="status">Submitting selected operation…</p>}
        {error && <p role="alert">{error}</p>}
        {children.map(child => <p role="status" key={child.id}>Queued {child.name} ({child.id}). <button type="button" onClick={() => onOpenJob(child.id)}>Open child Job</button></p>)}
    </section>;
}
