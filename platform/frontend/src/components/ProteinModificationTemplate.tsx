import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { submitJob, type Job } from '../lib/api';
import { ModelDocumentationLinks } from './ModelDocumentationLinks';
import { ProteinLocalRedesignTemplate } from './ProteinLocalRedesignTemplate';
import ShapeBlueprintTemplate from './ShapeBlueprintTemplate';

interface ProteinModificationTemplateProps {
    onBack: () => void;
    initialValues?: Record<string, unknown>;
}

type ModificationMode = 'de_novo_design' | 'region_redesign' | 'shape_blueprint';
type DeNovoBackend = 'disco' | 'laproteina';

const fieldClass = 'w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100';
const labelClass = 'space-y-1 text-sm font-medium text-slate-200';

const initialString = (values: Record<string, unknown> | undefined, key: string, fallback: string): string => {
    const value = values?.[key];
    return typeof value === 'string' && value.trim() ? value : fallback;
};

const initialNumber = (values: Record<string, unknown> | undefined, key: string, fallback: number): number => {
    const value = Number(values?.[key]);
    return Number.isFinite(value) ? value : fallback;
};

export function ProteinModificationTemplate({ onBack, initialValues }: ProteinModificationTemplateProps) {
    const navigate = useNavigate();
    const queryClient = useQueryClient();
    const initialMode = initialValues?.modification_mode;
    const [mode, setMode] = useState<ModificationMode | null>(
        initialMode === 'de_novo_design' || initialMode === 'region_redesign' || initialMode === 'shape_blueprint' ? initialMode : null,
    );
    const [jobName, setJobName] = useState(initialString(initialValues, 'job_name', 'protein_modification'));
    const [backend, setBackend] = useState<DeNovoBackend>(
        initialValues?.backend === 'laproteina' ? 'laproteina' : 'disco',
    );
    const [designTask, setDesignTask] = useState(initialString(initialValues, 'design_task', 'unconditional'));
    const [numDesigns, setNumDesigns] = useState(initialNumber(initialValues, 'num_designs', 8));
    const [targetLengths, setTargetLengths] = useState(initialString(initialValues, 'target_lengths', '100,200'));
    const [laproteinaPreset, setLaproteinaPreset] = useState(initialString(initialValues, 'laproteina_preset', 'ucond_tri'));
    const [laproteinaSamples, setLaproteinaSamples] = useState(initialNumber(initialValues, 'laproteina_samples_per_length', 8));
    const [laproteinaSteps, setLaproteinaSteps] = useState(initialNumber(initialValues, 'laproteina_num_steps', 400));
    const [motifPdb, setMotifPdb] = useState(initialString(initialValues, 'laproteina_motif_pdb', ''));
    const [motifContig, setMotifContig] = useState(initialString(initialValues, 'laproteina_contig_string', ''));
    const [discoExperiment, setDiscoExperiment] = useState(initialString(initialValues, 'disco_experiment', 'designable'));
    const [discoEffort, setDiscoEffort] = useState(initialString(initialValues, 'disco_effort', 'fast'));
    const [discoInputJson, setDiscoInputJson] = useState(initialString(initialValues, 'disco_input_json_path', ''));
    const [ligandSdf, setLigandSdf] = useState(initialString(initialValues, 'disco_ligand_sdf', ''));
    const [ligandName, setLigandName] = useState(initialString(initialValues, 'disco_ligand_name', ''));
    const [nucleicSequence, setNucleicSequence] = useState(initialString(initialValues, 'disco_na_sequence', ''));
    const [error, setError] = useState<string | null>(null);

    const submitMutation = useMutation({
        mutationFn: async (payload: Partial<Job>) => submitJob(payload),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['jobs'] });
            navigate('/');
        },
        onError: (err: Error) => setError(err.message || 'Failed to submit protein modification job'),
    });

    if (mode === 'region_redesign') {
        return (
            <ProteinLocalRedesignTemplate
                onBack={() => setMode(null)}
                initialValues={initialValues}
                submissionModelId="protein_modification_experimental"
                submissionMode="region_redesign"
            />
        );
    }

    if (mode === 'shape_blueprint') {
        return (
            <div className="space-y-3">
                <button onClick={() => setMode(null)} className="ml-4 mt-3 rounded-lg border border-slate-700 px-3 py-1.5 text-sm text-slate-300 sm:ml-6">Back to modification modes</button>
                <ShapeBlueprintTemplate />
            </div>
        );
    }

    if (mode === null) {
        return (
            <div className="space-y-6 text-slate-100">
                <div className="flex items-center gap-3">
                    <button onClick={onBack} className="rounded-lg border border-slate-700 px-3 py-1.5 text-sm text-slate-300">Back</button>
                    <div>
                        <div className="text-xs font-semibold uppercase tracking-[0.18em] text-orange-300">Experimental</div>
                        <h2 className="text-2xl font-semibold">De Novo Design</h2>
                    </div>
                </div>
                <p className="max-w-3xl text-sm text-slate-400">
                    Choose whether to create a new protein candidate or modify selected regions of an existing structure. These modes share one product boundary but retain distinct scientific contracts.
                </p>
                <div className="grid gap-4 md:grid-cols-3">
                    <button
                        onClick={() => setMode('de_novo_design')}
                        className="rounded-xl border border-cyan-500/40 bg-cyan-500/10 p-5 text-left hover:border-cyan-300"
                    >
                        <div className="font-semibold text-cyan-100">De Novo Design</div>
                        <div className="mt-2 text-sm text-slate-400">Generate new candidates with DISCO or La-Proteina.</div>
                    </button>
                    <button
                        onClick={() => setMode('region_redesign')}
                        className="rounded-xl border border-emerald-500/40 bg-emerald-500/10 p-5 text-left hover:border-emerald-300"
                    >
                        <div className="font-semibold text-emerald-100">Region Redesign</div>
                        <div className="mt-2 text-sm text-slate-400">Select and remodel regions while preserving structural context.</div>
                    </button>
                    <button
                        onClick={() => setMode('shape_blueprint')}
                        className="rounded-xl border border-violet-500/40 bg-violet-500/10 p-5 text-left hover:border-violet-300"
                    >
                        <div className="font-semibold text-violet-100">Shape Blueprint</div>
                        <div className="mt-2 text-sm text-slate-400">Immutable geometry → RFD3 Cα shape-transfer → conditional sequence design → validator review.</div>
                    </button>
                </div>
                <ModelDocumentationLinks
                    topics={['laproteina', 'disco', 'rfdiffusion', 'fampnn', 'proteinmpnn', 'boltz2']}
                    summary="Method references are grouped under this experimental parent; each mode keeps its native runtime contract."
                    compact
                />
            </div>
        );
    }

    const submitDeNovo = async () => {
        setError(null);
        if (!jobName.trim()) {
            setError('Job name is required.');
            return;
        }
        if (numDesigns < 1 || !targetLengths.trim()) {
            setError('Provide at least one design and one target length.');
            return;
        }

        await submitMutation.mutateAsync({
            name: jobName.trim(),
            model_id: 'protein_modification_experimental',
            mode: 'de_novo_design',
            params: {
                modification_mode: 'de_novo_design',
                backend,
                design_task: designTask,
                num_designs: numDesigns,
                target_lengths: targetLengths,
                ...(backend === 'laproteina' ? {
                    laproteina_preset: laproteinaPreset,
                    laproteina_samples_per_length: laproteinaSamples,
                    laproteina_num_steps: laproteinaSteps,
                    laproteina_motif_pdb: motifPdb || undefined,
                    laproteina_contig_string: motifContig || undefined,
                } : {
                    disco_experiment: discoExperiment,
                    disco_effort: discoEffort,
                    disco_input_json_path: discoInputJson || undefined,
                    disco_ligand_sdf: ligandSdf || undefined,
                    disco_ligand_name: ligandName || undefined,
                    disco_na_sequence: nucleicSequence || undefined,
                }),
            },
        });
    };

    return (
        <div className="space-y-6 text-slate-100">
            <div className="flex items-center gap-3">
                <button onClick={() => setMode(null)} className="rounded-lg border border-slate-700 px-3 py-1.5 text-sm text-slate-300">Modes</button>
                <div>
                    <div className="text-xs font-semibold uppercase tracking-[0.18em] text-cyan-300">De Novo Design · Experimental</div>
                    <h2 className="text-2xl font-semibold">Generate New Protein Candidates</h2>
                </div>
            </div>

            <div className="grid gap-4 rounded-xl border border-slate-800 bg-slate-900/50 p-5 md:grid-cols-2">
                <label className={labelClass}>Job name
                    <input className={fieldClass} value={jobName} onChange={(event) => setJobName(event.target.value)} />
                </label>
                <label className={labelClass}>Backend
                    <select className={fieldClass} value={backend} onChange={(event) => setBackend(event.target.value as DeNovoBackend)}>
                        <option value="disco">DISCO</option>
                        <option value="laproteina">La-Proteina</option>
                    </select>
                </label>
                <label className={labelClass}>Design task
                    <select className={fieldClass} value={designTask} onChange={(event) => setDesignTask(event.target.value)}>
                        <option value="unconditional">Unconditional</option>
                        <option value="motif_scaffolding">Motif scaffolding</option>
                        <option value="ligand_conditioned">Ligand conditioned</option>
                        <option value="dna_conditioned">DNA conditioned</option>
                        <option value="rna_conditioned">RNA conditioned</option>
                        <option value="custom_json">Custom native JSON</option>
                    </select>
                </label>
                <label className={labelClass}>Number of designs
                    <input className={fieldClass} type="number" min={1} max={512} value={numDesigns} onChange={(event) => setNumDesigns(Number(event.target.value))} />
                </label>
                <label className={labelClass}>Target lengths
                    <input className={fieldClass} value={targetLengths} onChange={(event) => setTargetLengths(event.target.value)} placeholder="100,150,200" />
                </label>

                {backend === 'laproteina' ? (
                    <>
                        <label className={labelClass}>Preset
                            <select className={fieldClass} value={laproteinaPreset} onChange={(event) => setLaproteinaPreset(event.target.value)}>
                                <option value="ucond_tri">Unconditional (triangular)</option>
                                <option value="ucond_notri">Unconditional</option>
                                <option value="ucond_notri_long">Unconditional long</option>
                                <option value="motif_idx_aa">Motif indexed all-atom</option>
                                <option value="motif_uidx_aa">Motif unindexed all-atom</option>
                            </select>
                        </label>
                        <label className={labelClass}>Samples per length
                            <input className={fieldClass} type="number" min={1} max={512} value={laproteinaSamples} onChange={(event) => setLaproteinaSamples(Number(event.target.value))} />
                        </label>
                        <label className={labelClass}>Sampling steps
                            <input className={fieldClass} type="number" min={50} max={2000} value={laproteinaSteps} onChange={(event) => setLaproteinaSteps(Number(event.target.value))} />
                        </label>
                        <label className={labelClass}>Motif PDB path
                            <input className={fieldClass} value={motifPdb} onChange={(event) => setMotifPdb(event.target.value)} />
                        </label>
                        <label className={labelClass}>Motif contig
                            <input className={fieldClass} value={motifContig} onChange={(event) => setMotifContig(event.target.value)} />
                        </label>
                    </>
                ) : (
                    <>
                        <label className={labelClass}>DISCO experiment
                            <input className={fieldClass} value={discoExperiment} onChange={(event) => setDiscoExperiment(event.target.value)} />
                        </label>
                        <label className={labelClass}>Inference effort
                            <select className={fieldClass} value={discoEffort} onChange={(event) => setDiscoEffort(event.target.value)}>
                                <option value="fast">Fast</option>
                                <option value="medium">Medium</option>
                                <option value="max">Max</option>
                            </select>
                        </label>
                        <label className={labelClass}>Native input JSON path
                            <input className={fieldClass} value={discoInputJson} onChange={(event) => setDiscoInputJson(event.target.value)} />
                        </label>
                        <label className={labelClass}>Ligand SDF path
                            <input className={fieldClass} value={ligandSdf} onChange={(event) => setLigandSdf(event.target.value)} />
                        </label>
                        <label className={labelClass}>Ligand name
                            <input className={fieldClass} value={ligandName} onChange={(event) => setLigandName(event.target.value)} />
                        </label>
                        <label className={labelClass}>DNA/RNA sequence
                            <input className={fieldClass} value={nucleicSequence} onChange={(event) => setNucleicSequence(event.target.value)} />
                        </label>
                    </>
                )}
            </div>

            {error && <div className="rounded-lg border border-red-500/40 bg-red-500/10 p-3 text-sm text-red-200">{error}</div>}
            <button
                onClick={submitDeNovo}
                disabled={submitMutation.isPending}
                className="rounded-lg bg-cyan-500 px-5 py-2.5 font-semibold text-slate-950 disabled:opacity-50"
            >
                {submitMutation.isPending ? 'Submitting…' : 'Launch De Novo Design'}
            </button>
        </div>
    );
}
