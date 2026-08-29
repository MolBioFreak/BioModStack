import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { completeCurrentLaunchContext, submitJob, type Job } from '../lib/api';
import { ModelDocumentationLinks } from './ModelDocumentationLinks';
import { ProteinLocalRedesignTemplate } from './ProteinLocalRedesignTemplate';
import ShapeBlueprintTemplate from './ShapeBlueprintTemplate';
import { DE_NOVO_MODIFICATION_MODE_CARDS, type ModificationMode } from './proteinModificationModes';

interface ProteinModificationTemplateProps {
    onBack: () => void;
    initialValues?: Record<string, unknown>;
    requiredPinnedGpu?: number | null;
}

type DeNovoBackend = 'disco' | 'laproteina';

const DE_NOVO_TASK_OPTIONS: Record<DeNovoBackend, Array<{ value: string; label: string }>> = {
    disco: [
        { value: 'unconditional', label: 'Unconditional' },
        { value: 'ligand_conditioned', label: 'Ligand conditioned' },
        { value: 'dna_conditioned', label: 'DNA conditioned' },
        { value: 'rna_conditioned', label: 'RNA conditioned' },
        { value: 'custom_json', label: 'Custom native JSON' },
    ],
    laproteina: [
        { value: 'unconditional', label: 'Unconditional' },
        { value: 'motif_scaffolding', label: 'Motif scaffolding' },
    ],
};

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

export function ProteinModificationTemplate({
    onBack,
    initialValues,
    requiredPinnedGpu = null,
}: ProteinModificationTemplateProps) {
    const navigate = useNavigate();
    const queryClient = useQueryClient();
    const initialMode = initialValues?.modification_mode;
    const normalizedInitialMode: ModificationMode | null = initialMode === 'rfd3_local_redesign' || initialMode === 'region_redesign'
        ? 'rfd3_iteration'
        : initialMode === 'de_novo_design' || initialMode === 'rfd3_iteration' || initialMode === 'shape_blueprint'
            ? initialMode
            : null;
    const [mode, setMode] = useState<ModificationMode | null>(
        normalizedInitialMode,
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
    const [motifTaskName, setMotifTaskName] = useState(initialString(initialValues, 'laproteina_motif_task_name', ''));
    const [motifPdb, setMotifPdb] = useState(initialString(initialValues, 'laproteina_motif_pdb', ''));
    const [motifContig, setMotifContig] = useState(initialString(initialValues, 'laproteina_contig_string', ''));
    const [motifSegmentOrder, setMotifSegmentOrder] = useState(initialString(initialValues, 'laproteina_segment_order', ''));
    const [motifAtomSelectionMode, setMotifAtomSelectionMode] = useState(initialString(initialValues, 'laproteina_atom_selection_mode', 'all_atom'));
    const [motifMinLength, setMotifMinLength] = useState(initialString(initialValues, 'laproteina_motif_min_length', ''));
    const [motifMaxLength, setMotifMaxLength] = useState(initialString(initialValues, 'laproteina_motif_max_length', ''));
    const [discoExperiment, setDiscoExperiment] = useState(initialString(initialValues, 'disco_experiment', 'designable'));
    const [discoEffort, setDiscoEffort] = useState(initialString(initialValues, 'disco_effort', 'fast'));
    const [discoInferenceSeeds, setDiscoInferenceSeeds] = useState(initialNumber(initialValues, 'disco_num_inference_seeds', 8));
    const [discoSeeds, setDiscoSeeds] = useState(initialString(initialValues, 'disco_seeds', ''));
    const [discoInputJson, setDiscoInputJson] = useState(initialString(initialValues, 'disco_input_json_path', ''));
    const [ligandSdf, setLigandSdf] = useState(initialString(initialValues, 'disco_ligand_sdf', ''));
    const [ligandName, setLigandName] = useState(initialString(initialValues, 'disco_ligand_name', ''));
    const [nucleicSequence, setNucleicSequence] = useState(initialString(initialValues, 'disco_na_sequence', ''));
    const [error, setError] = useState<string | null>(null);

    const submitMutation = useMutation({
        mutationFn: async (payload: Partial<Job>) => submitJob(payload),
        onSuccess: async (response) => {
            queryClient.invalidateQueries({ queryKey: ['jobs'] });
            navigate(await completeCurrentLaunchContext(response.data) ?? '/');
        },
        onError: (err: Error) => setError(err.message || 'Failed to submit protein modification job'),
    });

    if (mode === 'rfd3_iteration') {
        const reopenValidatedPipeline = initialMode === 'region_redesign';
        return (
            <ProteinLocalRedesignTemplate
                onBack={() => setMode(null)}
                initialValues={initialValues}
                submissionModelId={reopenValidatedPipeline ? 'protein_modification_experimental' : 'protein_local_redesign'}
                requiredPinnedGpu={requiredPinnedGpu}
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
                    Create a new protein, iterate an existing complex in one RFD3 workbench, or use a shape blueprint.
                </p>
                <div className="grid gap-4 md:grid-cols-3">
                    {DE_NOVO_MODIFICATION_MODE_CARDS.map((card) => (
                        <button
                            key={card.id}
                            onClick={() => setMode(card.id)}
                            className={`rounded-xl border p-5 text-left ${card.cardClassName}`}
                        >
                            <div className={`font-semibold ${card.labelClassName}`}>{card.label}</div>
                            <div className="mt-2 text-sm text-slate-400">{card.description}</div>
                        </button>
                    ))}
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
        if (!DE_NOVO_TASK_OPTIONS[backend].some((option) => option.value === designTask)) {
            setError(`The ${backend === 'disco' ? 'DISCO' : 'La-Proteina'} backend does not support the selected design task.`);
            return;
        }
        if (backend === 'laproteina' && designTask === 'motif_scaffolding' && !(motifTaskName.trim() || (motifPdb.trim() && motifContig.trim()))) {
            setError('La-Proteina motif scaffolding requires an upstream motif task or a motif PDB with a contig string.');
            return;
        }
        if (backend === 'disco' && designTask === 'custom_json' && !discoInputJson.trim()) {
            setError('DISCO custom native JSON design requires an input JSON file.');
            return;
        }
        if (backend === 'disco' && designTask === 'ligand_conditioned' && !ligandSdf.trim()) {
            setError('DISCO ligand-conditioned design requires a ligand SDF.');
            return;
        }
        if (backend === 'disco' && (designTask === 'dna_conditioned' || designTask === 'rna_conditioned') && !nucleicSequence.trim()) {
            setError('DISCO DNA/RNA-conditioned design requires a nucleic-acid sequence.');
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
                    laproteina_motif_task_name: motifTaskName || undefined,
                    laproteina_motif_pdb: motifPdb || undefined,
                    laproteina_contig_string: motifContig || undefined,
                    laproteina_segment_order: motifSegmentOrder || undefined,
                    laproteina_atom_selection_mode: motifAtomSelectionMode,
                    laproteina_motif_min_length: motifMinLength || undefined,
                    laproteina_motif_max_length: motifMaxLength || undefined,
                } : {
                    disco_experiment: discoExperiment,
                    disco_effort: discoEffort,
                    disco_num_inference_seeds: discoInferenceSeeds,
                    disco_seeds: discoSeeds || undefined,
                    disco_input_json_path: discoInputJson || undefined,
                    disco_ligand_sdf: ligandSdf || undefined,
                    disco_ligand_name: ligandName || undefined,
                    disco_na_sequence: nucleicSequence || undefined,
                }),
            },
        });
    };

    return (
        <div className="space-y-6 text-slate-100" data-bms-de-novo-form="complete">
            <div className="flex flex-wrap items-start justify-between gap-4">
                <div className="flex items-center gap-3">
                    <button onClick={() => setMode(null)} className="rounded-lg border border-slate-700 px-3 py-1.5 text-sm text-slate-300">Modes</button>
                    <div>
                        <div className="text-xs font-semibold uppercase tracking-[0.18em] text-cyan-300">De Novo Design · Experimental</div>
                        <h2 className="text-2xl font-semibold">Generate New Protein Candidates</h2>
                    </div>
                </div>
                <div className="rounded-lg border border-cyan-500/30 bg-cyan-500/10 px-4 py-3 text-sm text-cyan-100">
                    {backend === 'disco' ? 'DISCO native generation' : 'La-Proteina native generation'} · {designTask.replaceAll('_', ' ')}
                </div>
            </div>

            <div className="grid gap-4 rounded-xl border border-slate-800 bg-slate-900/50 p-5 md:grid-cols-2 xl:grid-cols-3">
                <label className={labelClass}>Job name
                    <input className={fieldClass} value={jobName} onChange={(event) => setJobName(event.target.value)} />
                </label>
                <label className={labelClass}>Backend
                    <select className={fieldClass} value={backend} onChange={(event) => {
                        setBackend(event.target.value as DeNovoBackend);
                        setDesignTask('unconditional');
                    }}>
                        <option value="disco">DISCO</option>
                        <option value="laproteina">La-Proteina</option>
                    </select>
                </label>
                <label className={labelClass}>Design task
                    <select className={fieldClass} value={designTask} onChange={(event) => setDesignTask(event.target.value)}>
                        {DE_NOVO_TASK_OPTIONS[backend].map((option) => (
                            <option key={option.value} value={option.value}>{option.label}</option>
                        ))}
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
                                <option value="motif_idx_tip">Motif indexed tip-atoms</option>
                                <option value="motif_uidx_aa">Motif unindexed all-atom</option>
                                <option value="motif_uidx_tip">Motif unindexed tip-atoms</option>
                            </select>
                        </label>
                        <label className={labelClass}>Samples per length
                            <input className={fieldClass} type="number" min={1} max={512} value={laproteinaSamples} onChange={(event) => setLaproteinaSamples(Number(event.target.value))} />
                        </label>
                        <label className={labelClass}>Sampling steps
                            <input className={fieldClass} type="number" min={50} max={2000} value={laproteinaSteps} onChange={(event) => setLaproteinaSteps(Number(event.target.value))} />
                        </label>
                        {designTask === 'motif_scaffolding' && (
                            <>
                                <label className={labelClass}>Upstream motif task
                                    <input className={fieldClass} value={motifTaskName} onChange={(event) => setMotifTaskName(event.target.value)} placeholder="Optional La-Proteina motif_dict key" />
                                </label>
                                <label className={labelClass}>Motif PDB path
                                    <input className={fieldClass} value={motifPdb} onChange={(event) => setMotifPdb(event.target.value)} />
                                </label>
                                <label className={labelClass}>Motif contig
                                    <input className={fieldClass} value={motifContig} onChange={(event) => setMotifContig(event.target.value)} />
                                </label>
                                <label className={labelClass}>Segment order
                                    <input className={fieldClass} value={motifSegmentOrder} onChange={(event) => setMotifSegmentOrder(event.target.value)} placeholder="A;B;C" />
                                </label>
                                <label className={labelClass}>Atom selection
                                    <select className={fieldClass} value={motifAtomSelectionMode} onChange={(event) => setMotifAtomSelectionMode(event.target.value)}>
                                        <option value="all_atom">All atom</option>
                                        <option value="tip_atoms">Tip atoms</option>
                                        <option value="backbone">Backbone</option>
                                        <option value="sidechain">Sidechain</option>
                                        <option value="ca_only">Cα only</option>
                                        <option value="random">Random</option>
                                    </select>
                                </label>
                                <label className={labelClass}>Motif minimum length
                                    <input className={fieldClass} type="number" min={1} max={2000} value={motifMinLength} onChange={(event) => setMotifMinLength(event.target.value)} />
                                </label>
                                <label className={labelClass}>Motif maximum length
                                    <input className={fieldClass} type="number" min={1} max={2000} value={motifMaxLength} onChange={(event) => setMotifMaxLength(event.target.value)} />
                                </label>
                            </>
                        )}
                    </>
                ) : (
                    <>
                        <label className={labelClass}>DISCO experiment
                            <select className={fieldClass} value={discoExperiment} onChange={(event) => setDiscoExperiment(event.target.value)}>
                                <option value="designable">Designable</option>
                                <option value="diverse">Diverse</option>
                            </select>
                        </label>
                        <label className={labelClass}>Inference effort
                            <select className={fieldClass} value={discoEffort} onChange={(event) => setDiscoEffort(event.target.value)}>
                                <option value="fast">Fast</option>
                                <option value="max">Max</option>
                            </select>
                        </label>
                        <label className={labelClass}>Inference seed count
                            <input className={fieldClass} type="number" min={1} max={512} value={discoInferenceSeeds} onChange={(event) => setDiscoInferenceSeeds(Number(event.target.value))} />
                        </label>
                        <label className={labelClass}>Exact seeds
                            <input className={fieldClass} value={discoSeeds} onChange={(event) => setDiscoSeeds(event.target.value)} placeholder="Optional comma-separated integers" />
                        </label>
                        {designTask === 'custom_json' && (
                            <label className={labelClass}>Native input JSON path
                                <input className={fieldClass} value={discoInputJson} onChange={(event) => setDiscoInputJson(event.target.value)} />
                            </label>
                        )}
                        {designTask === 'ligand_conditioned' && (
                            <>
                                <label className={labelClass}>Ligand SDF path
                                    <input className={fieldClass} value={ligandSdf} onChange={(event) => setLigandSdf(event.target.value)} />
                                </label>
                                <label className={labelClass}>Ligand name
                                    <input className={fieldClass} value={ligandName} onChange={(event) => setLigandName(event.target.value)} />
                                </label>
                            </>
                        )}
                        {(designTask === 'dna_conditioned' || designTask === 'rna_conditioned') && (
                            <label className={labelClass}>DNA/RNA sequence
                                <textarea className={fieldClass} rows={3} value={nucleicSequence} onChange={(event) => setNucleicSequence(event.target.value)} />
                            </label>
                        )}
                    </>
                )}
            </div>

            <ModelDocumentationLinks
                topics={backend === 'laproteina' ? ['laproteina'] : ['disco']}
                summary="The form exposes the supported native request contract for the selected generator."
                compact
            />

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
