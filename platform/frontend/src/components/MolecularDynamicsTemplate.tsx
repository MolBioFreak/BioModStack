import { useEffect, useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';

import { api, submitJob, uploadFile } from '../lib/api';
import {
    buildMolecularDynamicsJobSpec,
    estimateMolecularDynamicsScope,
    validateMolecularDynamicsChemistryProfile,
    validateMolecularDynamicsForm,
    type MolecularDynamicsChemistryProfileInventory,
    type MolecularDynamicsForm,
} from './molecularDynamicsUiState';

interface MolecularDynamicsTemplateProps {
    onBack: () => void;
    initialValues?: Record<string, unknown>;
}

const DEFAULT_FORM: MolecularDynamicsForm = {
    jobName: 'molecular_dynamics',
    engine: 'gromacs',
    inputMode: 'structure',
    structurePath: '',
    coordinatesPath: '',
    topologyPath: '',
    replicas: 1,
    productionNs: 0.001,
    randomSeed: 20260717,
    forceField: 'amber99sb-ildn',
    waterModel: 'tip3p',
    paddingNm: 1,
    saltMolar: 0.15,
    temperatureK: 300,
    pressureBar: 1,
    minimizationSteps: 50000,
    nvtPs: 100,
    nptPs: 100,
    timestepFs: 2,
    trajectoryIntervalPs: 1,
    energyIntervalPs: 0.2,
    checkpointIntervalMinutes: 15,
    ntomp: 8,
};

const panelClass = 'rounded-2xl border border-slate-700/80 bg-slate-900/55 p-5 shadow-lg';
const inputClass = 'mt-1 w-full rounded-lg border border-slate-700 bg-slate-950/80 px-3 py-2 text-sm text-slate-100 outline-none transition focus:border-cyan-500';
const labelClass = 'block text-xs font-semibold uppercase tracking-[0.12em] text-slate-400';

function NumberField({
    label,
    value,
    onChange,
    min,
    max,
    step = 1,
    unit,
    description,
}: {
    label: string;
    value: number;
    onChange: (value: number) => void;
    min?: number;
    max?: number;
    step?: number;
    unit?: string;
    description?: string;
}) {
    return (
        <label className={labelClass}>
            {label}{unit ? ` (${unit})` : ''}
            <input
                className={inputClass}
                type="number"
                value={value}
                min={min}
                max={max}
                step={step}
                onChange={(event) => onChange(Number(event.target.value))}
            />
            {description && <span className="mt-1 block normal-case tracking-normal text-[11px] font-normal text-slate-500">{description}</span>}
        </label>
    );
}

function SectionTitle({ children, note }: { children: string; note?: string }) {
    return (
        <div className="mb-4">
            <h2 className="text-base font-semibold text-slate-100">{children}</h2>
            {note && <p className="mt-1 text-xs text-slate-500">{note}</p>}
        </div>
    );
}

export function MolecularDynamicsTemplate({ onBack, initialValues }: MolecularDynamicsTemplateProps) {
    const navigate = useNavigate();
    const queryClient = useQueryClient();
    const chemistryCatalogQuery = useQuery({
        queryKey: ['molecular-dynamics', 'chemistry-profiles'],
        queryFn: async () => (
            await api.get<MolecularDynamicsChemistryProfileInventory>(
                '/api/molecular-dynamics/chemistry-profiles',
                { timeout: 10_000 },
            )
        ).data,
        staleTime: 30_000,
        retry: 1,
    });
    const initialSpec = initialValues?.md_job_spec as Partial<Record<string, unknown>> | undefined;
    const [form, setForm] = useState<MolecularDynamicsForm>({
        ...DEFAULT_FORM,
        jobName: String(initialValues?.name || initialValues?.job_name || DEFAULT_FORM.jobName),
        engine: initialSpec?.engine === 'openmm' ? 'openmm' : DEFAULT_FORM.engine,
        replicas: Number(initialSpec?.replicas || DEFAULT_FORM.replicas),
    });
    const [structureFile, setStructureFile] = useState<File | null>(null);
    const [showAdvanced, setShowAdvanced] = useState(false);
    const [isSubmitting, setIsSubmitting] = useState(false);
    const [submitError, setSubmitError] = useState('');
    const initialPreparation = initialSpec?.preparation as Partial<Record<string, unknown>> | undefined;
    const [selectedProfileId, setSelectedProfileId] = useState(
        String(initialPreparation?.chemistry_profile_id || ''),
    );
    const [selectedProfileDigest, setSelectedProfileDigest] = useState(
        String(initialPreparation?.chemistry_profile_sha256 || ''),
    );

    const update = <K extends keyof MolecularDynamicsForm>(key: K, value: MolecularDynamicsForm[K]) => {
        setForm((current) => ({ ...current, [key]: value }));
    };
    const chemistryProfiles = chemistryCatalogQuery.data?.profiles ?? [];
    const selectedChemistryProfile = chemistryProfiles.find((profile) => profile.id === selectedProfileId);
    const profileDigestIsStale = Boolean(
        selectedChemistryProfile && selectedProfileDigest !== selectedChemistryProfile.profile_sha256,
    );
    const launchConstraints = form.inputMode === 'structure' ? selectedChemistryProfile?.launch_constraints : null;
    useEffect(() => {
        if (selectedProfileId || chemistryProfiles.length === 0) return;
        const firstSelectable = chemistryProfiles.find((profile) => profile.states.selectable);
        if (firstSelectable) {
            setSelectedProfileId(firstSelectable.id);
            setSelectedProfileDigest(firstSelectable.profile_sha256);
        }
    }, [chemistryProfiles, selectedProfileId]);
    const errors = useMemo(() => {
        const chemistryErrors = form.inputMode === 'prepared'
            ? []
            : chemistryCatalogQuery.isPending
                ? ['Loading the deployed chemistry profile catalog.']
                : chemistryCatalogQuery.isError
                    ? ['The deployed chemistry profile catalog is unavailable; launch is blocked.']
                    : validateMolecularDynamicsChemistryProfile(
                        selectedChemistryProfile,
                        form.engine,
                        true,
                        selectedProfileDigest,
                    );
        return [...validateMolecularDynamicsForm(form, selectedChemistryProfile), ...chemistryErrors];
    }, [chemistryCatalogQuery.isError, chemistryCatalogQuery.isPending, form, selectedChemistryProfile, selectedProfileDigest]);
    const scope = useMemo(() => estimateMolecularDynamicsScope(form), [form]);

    const launch = async () => {
        setSubmitError('');
        setIsSubmitting(true);
        try {
            let structurePath = form.structurePath.trim();
            if (form.inputMode === 'structure' && structureFile) {
                const response = await uploadFile('inputs/molecular_dynamics', structureFile);
                structurePath = String(response.data?.path || '').trim();
                if (!structurePath) throw new Error('Structure upload succeeded but returned no runtime path.');
            }
            const launchForm = { ...form, structurePath };
            const launchErrors = [
                ...validateMolecularDynamicsForm(launchForm, selectedChemistryProfile),
                ...(launchForm.inputMode === 'prepared' ? [] : validateMolecularDynamicsChemistryProfile(
                    selectedChemistryProfile,
                    launchForm.engine,
                    true,
                    selectedProfileDigest,
                )),
            ];
            if (launchErrors.length) throw new Error(launchErrors.join(' '));
            if (launchForm.inputMode === 'structure' && !selectedChemistryProfile) {
                throw new Error('The selected chemistry profile is stale; reload the deployed catalog.');
            }
            const mdJobSpec = buildMolecularDynamicsJobSpec(
                launchForm,
                selectedChemistryProfile,
                chemistryCatalogQuery.data?.catalog_digest,
            );
            await submitJob({
                name: launchForm.jobName.trim(),
                model_id: 'molecular_dynamics',
                mode: 'simulate',
                params: { md_job_spec: mdJobSpec },
            });
            await queryClient.invalidateQueries({ queryKey: ['jobs'] });
            navigate('/');
        } catch (error) {
            setSubmitError(error instanceof Error ? error.message : 'Molecular Dynamics launch failed.');
        } finally {
            setIsSubmitting(false);
        }
    };

    const ready = errors.length === 0 && (form.inputMode !== 'structure' || Boolean(structureFile || form.structurePath.trim()));

    return (
        <div className="mx-auto max-w-7xl space-y-5" data-bms-md-launcher="true">
            <header className="flex flex-wrap items-start justify-between gap-4">
                <div>
                    <button type="button" onClick={onBack} className="mb-3 rounded-lg border border-slate-700 px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-800">
                        ← Back to workflows
                    </button>
                    <div className="flex items-center gap-3">
                        <h1 className="text-2xl font-bold text-slate-100">Molecular Dynamics</h1>
                        <span className="rounded-full border border-orange-400/30 bg-orange-500/10 px-2 py-1 text-[10px] font-semibold uppercase tracking-wider text-orange-300">Experimental alpha</span>
                    </div>
                    <p className="mt-2 max-w-3xl text-sm text-slate-400">
                        Automatic preparation has accepted 1AKI protein and 1LMB protein-DNA short-GPU lanes. It is not validated for long-timescale production science.
                    </p>
                </div>
                <div className="rounded-xl border border-cyan-500/20 bg-cyan-500/5 px-4 py-3 text-xs text-cyan-100">
                    <div className="font-semibold">Deployed runtime truth</div>
                    <div className="mt-1 text-cyan-200/70">GROMACS 2025.3 · catalog-probed assets · scoped launch gates</div>
                </div>
            </header>

            <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_21rem]">
                <div className="space-y-5">
                    <section className={panelClass}>
                        <SectionTitle children="Starting system" note="Use a PDB for BMS preparation, or provide an already prepared coordinate/topology pair." />
                        <label className={labelClass}>Job name
                            <input className={inputClass} value={form.jobName} onChange={(event) => update('jobName', event.target.value)} />
                        </label>
                        <div className="mt-4 grid grid-cols-2 gap-2">
                            {(['structure', 'prepared'] as const).map((mode) => (
                                <button
                                    key={mode}
                                    type="button"
                                    disabled={mode === 'prepared' && form.engine !== 'openmm'}
                                    onClick={() => update('inputMode', mode)}
                                    className={`rounded-lg border px-3 py-2 text-sm ${form.inputMode === mode ? 'border-cyan-500 bg-cyan-500/10 text-cyan-200' : 'border-slate-700 text-slate-400 hover:bg-slate-800'}`}
                                >
                                    {mode === 'structure' ? 'Prepare from PDB' : 'Use prepared system'}
                                </button>
                            ))}
                        </div>
                        {form.inputMode === 'structure' ? (
                            <div className="mt-4 grid gap-4 md:grid-cols-2">
                                <label className={labelClass}>Upload PDB
                                    <input className={`${inputClass} file:mr-3 file:rounded file:border-0 file:bg-cyan-500/15 file:px-2 file:py-1 file:text-cyan-200`} type="file" accept=".pdb" onChange={(event) => setStructureFile(event.target.files?.[0] || null)} />
                                </label>
                                <label className={labelClass}>Or server path
                                    <input className={inputClass} value={form.structurePath} placeholder="inputs/molecular_dynamics/system.pdb" onChange={(event) => update('structurePath', event.target.value)} />
                                </label>
                            </div>
                        ) : (
                            <div className="mt-4 grid gap-4 md:grid-cols-2">
                                <label className={labelClass}>Coordinates path (.gro)
                                    <input className={inputClass} value={form.coordinatesPath} onChange={(event) => update('coordinatesPath', event.target.value)} />
                                </label>
                                <label className={labelClass}>Topology path (.top)
                                    <input className={inputClass} value={form.topologyPath} onChange={(event) => update('topologyPath', event.target.value)} />
                                </label>
                                <p className="md:col-span-2 text-xs text-amber-300/80">Prepared systems are supported only by OpenMM. Declared topology includes are snapshotted into the verified job closure.</p>
                            </div>
                        )}
                    </section>

                    <section className={panelClass}>
                        <SectionTitle children="Engine & replicas" note="Replicas are independent scheduler-visible child jobs with deterministic seeds." />
                        <div className="grid gap-4 md:grid-cols-3">
                            <label className={labelClass}>Engine
                                <select className={inputClass} value={form.engine} onChange={(event) => update('engine', event.target.value as MolecularDynamicsForm['engine'])}>
                                    <option value="gromacs" disabled={form.inputMode === 'prepared'}>GROMACS 2025.3 (automatic preparation only)</option>
                                    <option value="openmm">OpenMM 8.5.2 (prepared systems only)</option>
                                </select>
                            </label>
                            <NumberField label="Independent replicas" value={form.replicas} min={launchConstraints?.replicas ?? 1} max={launchConstraints?.replicas ?? 16} onChange={(value) => update('replicas', value)} />
                            <NumberField label="Base random seed" value={form.randomSeed} min={1} step={1} onChange={(value) => update('randomSeed', value)} description="Replica i uses base seed + i." />
                        </div>
                        {form.engine === 'openmm' && (
                            <div className="mt-4 rounded-lg border border-amber-400/25 bg-amber-500/8 px-3 py-2 text-xs text-amber-200">
                                OpenMM is exact and fail-closed: prepared GROMACS coordinates/topology, production stage only, CUDA required, no fallback to GROMACS or CPU.
                            </div>
                        )}
                    </section>

                    <section className={panelClass}>
                        <SectionTitle children="Preparation & ensemble" note="Automatic chemistry is locked to a versioned deployed profile; candidates remain visible but cannot launch." />
                        <div className="grid gap-4 md:grid-cols-3">
                            {form.inputMode === 'structure' ? (
                                <label className={`${labelClass} md:col-span-2`}>Chemistry profile
                                    <select
                                        className={inputClass}
                                        value={profileDigestIsStale ? '' : selectedProfileId}
                                        onChange={(event) => {
                                            const profile = chemistryProfiles.find((item) => item.id === event.target.value);
                                            setSelectedProfileId(event.target.value);
                                            setSelectedProfileDigest(profile?.profile_sha256 ?? '');
                                        }}
                                        disabled={chemistryCatalogQuery.isPending || chemistryCatalogQuery.isError}
                                    >
                                        <option value="">Select a deployed profile</option>
                                        {chemistryProfiles.map((profile) => (
                                            <option key={profile.id} value={profile.id} disabled={!profile.states.selectable}>
                                                {profile.display_name} — {profile.states.selectable ? 'selectable' : 'candidate (not launchable)'}
                                            </option>
                                        ))}
                                    </select>
                                </label>
                            ) : (
                                <div className="md:col-span-2 rounded-lg border border-amber-400/25 bg-amber-500/8 p-3 text-xs text-amber-200">
                                    Prepared systems carry chemistry_assurance: external_unreviewed and no catalog profile claim.
                                </div>
                            )}
                            <NumberField label="Salt" unit="M" value={form.saltMolar} min={launchConstraints?.salt_molar ?? 0} max={launchConstraints?.salt_molar ?? 2} step={0.01} onChange={(value) => update('saltMolar', value)} />
                            {form.inputMode === 'structure' && selectedChemistryProfile && (
                                <div className="md:col-span-3 rounded-lg border border-slate-700 bg-slate-950/60 p-3 text-xs text-slate-300">
                                    <div className="flex flex-wrap items-center gap-2">
                                        <span className={selectedChemistryProfile.states.selectable ? 'text-cyan-300' : 'text-amber-300'}>
                                            {selectedChemistryProfile.states.selectable ? 'Selectable' : 'Candidate — not selectable'}
                                        </span>
                                        <span>· {selectedChemistryProfile.assurance}</span>
                                        {selectedChemistryProfile.legacy && <span>· legacy</span>}
                                        <span>· scope: {selectedChemistryProfile.scientific_validation.scope.launch_scope}</span>
                                    </div>
                                    <div className="mt-2 text-slate-400">
                                        Locked v1 chemistry: {selectedChemistryProfile.v1_preparation.force_field} + {selectedChemistryProfile.v1_preparation.water_model}
                                    </div>
                                    <div className="mt-1 text-slate-500">{selectedChemistryProfile.availability_explanation}</div>
                                </div>
                            )}
                            {form.inputMode === 'structure' && chemistryProfiles.some((profile) => !profile.states.selectable) && (
                                <details className="md:col-span-3 rounded-lg border border-slate-800 bg-slate-950/30 p-3 text-xs text-slate-400">
                                    <summary className="cursor-pointer text-slate-300">Candidate inventory (visible for status, never launchable)</summary>
                                    <ul className="mt-2 space-y-2">
                                        {chemistryProfiles.filter((profile) => !profile.states.selectable).map((profile) => (
                                            <li key={profile.id}>
                                                <span className="font-medium text-slate-300">{profile.display_name}:</span> {profile.availability_explanation}
                                            </li>
                                        ))}
                                    </ul>
                                </details>
                            )}
                            {form.inputMode === 'prepared' && (
                                <p className="md:col-span-3 text-xs text-amber-300/80">
                                    Prepared topology chemistry is external and unreviewed in v1. No smoke profile ID, digest, or scope is attached or implied.
                                </p>
                            )}
                            <NumberField label="Box padding" unit="nm" value={form.paddingNm} min={launchConstraints?.padding_nm ?? 0.5} max={launchConstraints?.padding_nm ?? 5} step={0.1} onChange={(value) => update('paddingNm', value)} />
                            <NumberField label="Temperature" unit="K" value={form.temperatureK} min={launchConstraints?.temperature_k ?? 1} max={launchConstraints?.temperature_k ?? 500} onChange={(value) => update('temperatureK', value)} />
                            <NumberField label="Pressure" unit="bar" value={form.pressureBar} min={launchConstraints?.pressure_bar ?? 0.1} max={launchConstraints?.pressure_bar ?? 100} step={0.1} onChange={(value) => update('pressureBar', value)} />
                            <NumberField label="Minimization" unit="steps" value={form.minimizationSteps} min={1} max={launchConstraints?.max_minimization_steps} step={1000} onChange={(value) => update('minimizationSteps', value)} />
                            <NumberField label="NVT equilibration" unit="ps" value={form.nvtPs} min={1} max={launchConstraints ? (launchConstraints.max_nvt_steps * launchConstraints.timestep_fs) / 1000 : undefined} step={10} onChange={(value) => update('nvtPs', value)} />
                            <NumberField label="NPT equilibration" unit="ps" value={form.nptPs} min={1} max={launchConstraints ? (launchConstraints.max_npt_steps * launchConstraints.timestep_fs) / 1000 : undefined} step={10} onChange={(value) => update('nptPs', value)} />
                        </div>
                    </section>

                    <section className={panelClass}>
                        <SectionTitle children="Production & output cadence" note="Phase 1 uses a validated fixed 2 fs timestep." />
                        <div className="grid gap-4 md:grid-cols-3">
                            <NumberField label="Production per replica" unit="ns" value={form.productionNs} min={0.001} max={launchConstraints ? (launchConstraints.max_production_steps * launchConstraints.timestep_fs) / 1_000_000 : 10_000} step={0.001} onChange={(value) => update('productionNs', value)} />
                            <NumberField label="Trajectory interval" unit="ps" value={form.trajectoryIntervalPs} min={0.002} max={form.productionNs * 1000} step={0.1} onChange={(value) => update('trajectoryIntervalPs', value)} />
                            <NumberField label="Energy/log interval" unit="ps" value={form.energyIntervalPs} min={0.002} max={form.productionNs * 1000} step={0.1} onChange={(value) => update('energyIntervalPs', value)} />
                            <NumberField label="Checkpoint interval" unit="minutes" value={form.checkpointIntervalMinutes} min={0.1} step={1} onChange={(value) => update('checkpointIntervalMinutes', value)} />
                        </div>
                    </section>

                    <section className={`${panelClass} p-0`}>
                        <button type="button" onClick={() => setShowAdvanced((value) => !value)} className="flex w-full items-center justify-between p-5 text-left text-sm font-semibold text-slate-300">
                            <span>Advanced runtime controls</span><span>{showAdvanced ? '▲' : '▼'}</span>
                        </button>
                        {showAdvanced && (
                            <div className="grid gap-4 border-t border-slate-800 p-5 md:grid-cols-3">
                                <NumberField label="CPU threads per replica" value={form.ntomp} min={1} max={64} onChange={(value) => update('ntomp', value)} />
                                <NumberField label="Timestep" unit="fs" value={form.timestepFs} min={2} max={2} onChange={(value) => update('timestepFs', value)} description="Fixed by the validated phase-1 contract." />
                            </div>
                        )}
                    </section>
                </div>

                <aside className="space-y-4 lg:sticky lg:top-5 lg:self-start">
                    <section className={panelClass}>
                        <h2 className="text-sm font-semibold text-slate-200">Launch summary</h2>
                        <dl className="mt-4 space-y-3 text-xs">
                            <div className="flex justify-between gap-3"><dt className="text-slate-500">Engine</dt><dd className="text-slate-200">{form.engine === 'gromacs' ? 'GROMACS 2025.3' : 'OpenMM 8.5.2'}</dd></div>
                            <div className="flex justify-between gap-3"><dt className="text-slate-500">GPU children</dt><dd className="text-slate-200">{form.replicas}</dd></div>
                            <div className="flex justify-between gap-3"><dt className="text-slate-500">Per replica</dt><dd className="text-slate-200">{form.productionNs} ns</dd></div>
                            <div className="flex justify-between gap-3"><dt className="text-slate-500">Aggregate simulation</dt><dd className="font-semibold text-cyan-300">{scope.aggregateSimulationNs.toLocaleString()} ns</dd></div>
                            <div className="flex justify-between gap-3"><dt className="text-slate-500">Steps / replica</dt><dd className="text-slate-200">{scope.productionStepsPerReplica.toLocaleString()}</dd></div>
                            <div className="flex justify-between gap-3"><dt className="text-slate-500">Frames / replica</dt><dd className="text-slate-200">{scope.trajectoryFramesPerReplica.toLocaleString()}</dd></div>
                            <div className="flex justify-between gap-3"><dt className="text-slate-500">Total frames</dt><dd className="text-slate-200">{scope.totalTrajectoryFrames.toLocaleString()}</dd></div>
                        </dl>
                        <p className="mt-4 border-t border-slate-800 pt-3 text-[11px] text-slate-500">No runtime estimate is shown until a matching system/GPU benchmark exists.</p>
                    </section>

                    {errors.length > 0 && (
                        <section className="rounded-xl border border-red-500/30 bg-red-500/8 p-4">
                            <h2 className="text-xs font-semibold uppercase tracking-wider text-red-300">Resolve before launch</h2>
                            <ul className="mt-2 list-disc space-y-1 pl-4 text-xs text-red-200/80">{errors.map((error) => <li key={error}>{error}</li>)}</ul>
                        </section>
                    )}
                    {submitError && <div className="rounded-xl border border-red-500/30 bg-red-500/8 p-3 text-xs text-red-200">{submitError}</div>}
                    <button
                        type="button"
                        disabled={!ready || isSubmitting}
                        onClick={launch}
                        className="w-full rounded-xl bg-cyan-500 px-4 py-3 text-sm font-bold text-slate-950 transition hover:bg-cyan-400 disabled:cursor-not-allowed disabled:bg-slate-700 disabled:text-slate-500"
                    >
                        {isSubmitting ? 'Materializing MD job…' : `Launch ${form.replicas} replica${form.replicas === 1 ? '' : 's'}`}
                    </button>
                    <p className="text-center text-[11px] text-slate-600">Launch creates one CPU coordinator and independently scheduled one-GPU replica jobs.</p>
                </aside>
            </div>
        </div>
    );
}
