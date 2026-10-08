import { ExecutionTargetPicker } from './ExecutionTargetPicker';
import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';

import {
    completeCurrentLaunchContext,
    listShapeGeometries,
    fetchShapeSequenceSettings,
    submitShapeBlueprint,
    uploadShapeGeometry,
    type ShapeGeometrySummary,
    type ShapeLaunchRequest,
    type ShapeSequenceEngine,
    type ShapeSequenceSettings,
} from '../lib/api';
import { buildShapeLaunchRequest } from '../lib/shapeBlueprintLaunch';
import CanonicalMeshPreview from './CanonicalMeshPreview';
import MolstarViewer from './MolstarViewer';
import { ParamField } from './ModelParameterField';

const shortHash = (value: string) => `${value.slice(0, 12)}…${value.slice(-8)}`;
const boundedInteger = (value: string, fallback: number, minimum: number, maximum: number) => {
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) return fallback;
    return Math.min(maximum, Math.max(minimum, Math.trunc(parsed)));
};
const formatAngstrom = (value: number) => Math.abs(value) >= 1_000_000
    ? `${value.toExponential(3)} Å`
    : `${value.toLocaleString(undefined, { maximumFractionDigits: 3 })} Å`;
const requestError = (cause: unknown, fallback: string) => {
    if (cause && typeof cause === 'object' && 'response' in cause) {
        const response = (cause as { response?: { data?: { detail?: string | { message?: string } } } }).response;
        const detail = response?.data?.detail;
        if (typeof detail === 'string') return detail;
        if (detail?.message) return detail.message;
    }
    return cause instanceof Error ? cause.message : fallback;
};

const SHAPE_CLIENT_REQUEST_KEY = 'bms.shape-blueprint.client-request-id';

const getShapeClientRequestId = () => {
    const existing = sessionStorage.getItem(SHAPE_CLIENT_REQUEST_KEY);
    if (existing) return existing;
    const created = crypto.randomUUID();
    sessionStorage.setItem(SHAPE_CLIENT_REQUEST_KEY, created);
    return created;
};

export default function ShapeBlueprintTemplate({ initialValues = {} }: { initialValues?: Record<string, unknown> }) {
    const saved = <T,>(key: string, fallback: T): T => (initialValues[`shape_${key}`] ?? initialValues[key] ?? fallback) as T;
    let initialLengthPolicy: ShapeLaunchRequest['length_policy'];
    let hydrationError: string | null = null;
    try {
        const value = saved<string | ShapeLaunchRequest['length_policy']>('length_policy', undefined);
        initialLengthPolicy = typeof value === 'string' ? JSON.parse(value) : value;
        if (value !== undefined && (!initialLengthPolicy
            || !['fixed', 'uniform_integer_range', 'deterministic_range'].includes(initialLengthPolicy.mode)
            || !Number.isInteger(initialLengthPolicy.min) || !Number.isInteger(initialLengthPolicy.max))) {
            throw new Error('Invalid saved length policy');
        }
    } catch { hydrationError = 'Saved length policy is malformed; reopen the original request before cloning.'; }
    let initialSequenceSettings: ShapeSequenceSettings = {};
    try {
        const raw = saved<unknown>('sequence_settings', saved<unknown>('requested_sequence_settings', {}));
        const value: unknown = typeof raw === 'string' ? JSON.parse(raw) : raw;
        if (!value || typeof value !== 'object' || Array.isArray(value)
            || Object.values(value).some((item) => !['number', 'string', 'boolean'].includes(typeof item)
                || (typeof item === 'number' && !Number.isFinite(item)))) {
            throw new Error('Invalid saved sequence settings');
        }
        initialSequenceSettings = value as ShapeSequenceSettings;
    } catch { hydrationError = 'Saved sequence settings are malformed; reopen the original request before cloning.'; }
    const initialValidators = saved<string | NonNullable<ShapeLaunchRequest['validator_suite']>>('validator_suite', ['boltz2', 'esmfold2', 'protenix_v2']);
    const navigate = useNavigate();
    const queryClient = useQueryClient();
    const [selectedId, setSelectedId] = useState(saved('geometry_id', ''));
    const [clientRequestId] = useState(getShapeClientRequestId);
    const [file, setFile] = useState<File | null>(null);
    const [unit, setUnit] = useState('angstrom');
    const [name, setName] = useState(saved('name', 'Shape Blueprint design'));
    const [targetLength, setTargetLength] = useState(saved('target_length', initialLengthPolicy?.min ?? 120));
    const [lengthMode, setLengthMode] = useState<NonNullable<ShapeLaunchRequest['length_policy']>['mode']>(initialLengthPolicy?.mode ?? 'fixed');
    const [minimumLength, setMinimumLength] = useState(initialLengthPolicy?.min ?? 350);
    const [maximumLength, setMaximumLength] = useState(initialLengthPolicy?.max ?? 450);
    const [numBackbones, setNumBackbones] = useState(saved('num_backbones', 1));
    const [sequencesPerBackbone, setSequencesPerBackbone] = useState(saved('sequences_per_backbone', 1));
    const [sequencePolicy, setSequencePolicy] = useState<'auto' | 'skip' | 'external'>(saved('sequence_policy', 'auto'));
    const [sequenceEngine, setSequenceEngine] = useState<'proteinmpnn' | 'fampnn'>(saved('sequence_engine', 'proteinmpnn'));
    // Keep operator/saved values separate from contextual initial values. A count
    // refresh may update an unset batch size, but never an explicit one.
    const [sequenceSettingsByEngine, setSequenceSettingsByEngine] = useState<Partial<Record<ShapeSequenceEngine, ShapeSequenceSettings>>>(() => ({
        [sequencePolicy === 'external' ? sequenceEngine : 'proteinmpnn']: initialSequenceSettings,
    }));
    const [seed, setSeed] = useState(saved('seed', 0));
    const [guidanceProfile, setGuidanceProfile] = useState<ShapeLaunchRequest['guidance_profile']>(saved('guidance_profile', 'rfd3_ca_shape_transfer_control_v1'));
    const [validatorSuite, setValidatorSuite] = useState<NonNullable<ShapeLaunchRequest['validator_suite']>>(
        typeof initialValidators === 'string' ? initialValidators.split(',').filter(Boolean) as NonNullable<ShapeLaunchRequest['validator_suite']> : initialValidators,
    );
    const [error, setError] = useState<string | null>(null);
    const [reviewMode, setReviewMode] = useState<'surface' | 'points'>('surface');

    const sequenceEnabled = sequencePolicy !== 'skip' && sequencesPerBackbone > 0;
    const effectiveSequenceEngine = sequencePolicy === 'external' ? sequenceEngine : 'proteinmpnn';
    const sequenceSettingsQuery = useQuery({
        queryKey: ['shape-sequence-settings', effectiveSequenceEngine, sequencesPerBackbone],
        queryFn: () => fetchShapeSequenceSettings(effectiveSequenceEngine, sequencesPerBackbone).then((response) => response.data),
        enabled: sequenceEnabled,
    });
    const sequenceDefinition = sequenceSettingsQuery.data?.engine === effectiveSequenceEngine ? sequenceSettingsQuery.data : undefined;
    const explicitSequenceSettings = sequenceSettingsByEngine[effectiveSequenceEngine] ?? {};
    const sequenceSettings = { ...sequenceDefinition?.initial_values, ...explicitSequenceSettings };
    const unsupportedSequenceKeys = sequenceDefinition
        ? Object.keys(explicitSequenceSettings).filter((key) => !sequenceDefinition.params.some((param) => param.name === key))
        : [];
    const sequenceSettingsError = sequenceEnabled
        ? sequenceSettingsQuery.isError ? requestError(sequenceSettingsQuery.error, 'Sequence settings are unavailable.')
            : unsupportedSequenceKeys.length ? `Saved settings are unsupported by this engine: ${unsupportedSequenceKeys.join(', ')}. Reopen the original request; no settings were discarded.`
                : !sequenceDefinition ? 'Loading native sequence settings…' : null
        : null;
    const updateSequenceSetting = (key: string, value: number | string | boolean) => {
        setSequenceSettingsByEngine((current) => ({
            ...current,
            [effectiveSequenceEngine]: { ...current[effectiveSequenceEngine], [key]: value },
        }));
    };
    const unavailableSequenceInput = () => setError('This native sequence lane does not accept external file or sequence-library settings.');

    const geometriesQuery = useQuery({
        queryKey: ['shape-geometries'],
        queryFn: () => listShapeGeometries().then((response) => response.data.geometries),
    });
    const geometries = useMemo(() => geometriesQuery.data ?? [], [geometriesQuery.data]);
    const selected = useMemo<ShapeGeometrySummary | undefined>(
        () => selectedId ? geometries.find((geometry) => geometry.geometry_id === selectedId) : geometries[0],
        [geometries, selectedId],
    );
    const selectedMaxDimension = selected ? Math.max(...selected.dimensions_angstrom) : null;
    const hasHashBoundSurface = Boolean(selected?.preview_obj_sha256);
    const effectiveReviewMode = reviewMode === 'surface' && hasHashBoundSurface ? 'surface' : 'points';
    const invalidLengthPolicy = lengthMode !== 'fixed' && minimumLength > maximumLength;

    const upload = useMutation({
        mutationFn: () => {
            if (!file) throw new Error('Choose a closed triangular OBJ or STL first.');
            return uploadShapeGeometry(file, unit);
        },
        onSuccess: async (response) => {
            setSelectedId(response.data.geometry_id);
            setFile(null);
            setError(null);
            await queryClient.invalidateQueries({ queryKey: ['shape-geometries'] });
        },
        onError: (cause: unknown) => setError(requestError(cause, 'Geometry admission failed.')),
    });

    const launch = useMutation({
        mutationFn: () => {
            if (hydrationError) throw new Error(hydrationError);
            if (sequenceSettingsError) throw new Error(sequenceSettingsError);
            if (!selected) throw new Error('Select or upload canonical geometry first.');
            if (selected.geometry_id === saved('geometry_id', '') && (
                (saved('geometry_sha256', '') && saved('geometry_sha256', '') !== selected.geometry_sha256)
                || (saved('point_pool_sha256', '') && saved('point_pool_sha256', '') !== selected.point_pool_sha256)
            )) throw new Error('Saved geometry identity differs from the admitted geometry; select a new source explicitly.');
            return submitShapeBlueprint(buildShapeLaunchRequest(selected, {
                client_request_id: clientRequestId,
                name: name.trim() || 'Shape Blueprint design',
                target_length: lengthMode === 'fixed' ? targetLength : undefined,
                length_policy: {
                    ...(lengthMode === initialLengthPolicy?.mode ? initialLengthPolicy : {}),
                    mode: lengthMode,
                    min: lengthMode === 'fixed' ? targetLength : minimumLength,
                    max: lengthMode === 'fixed' ? targetLength : maximumLength,
                },
                num_backbones: numBackbones,
                sequences_per_backbone: sequencePolicy === 'skip' ? 0 : sequencesPerBackbone,
                sequence_policy: sequencePolicy,
                sequence_engine: sequencePolicy === 'external' ? sequenceEngine : undefined,
                sequence_settings: sequenceEnabled ? sequenceSettings : {},
                seed,
                guidance_profile: guidanceProfile,
                validator_suite: validatorSuite,
            }));
        },
        onSuccess: async (response) => {
            sessionStorage.removeItem(SHAPE_CLIENT_REQUEST_KEY);
            navigate(await completeCurrentLaunchContext(response.data) ?? `/designs/${response.data.job_id}`);
        },
        onError: (cause: unknown) => setError(requestError(cause, 'Shape request failed.')),
    });

    return (
        <div className="mx-auto max-w-7xl space-y-5 p-4 sm:p-6">
            <ExecutionTargetPicker />
            <header className="rounded-2xl border border-cyan-500/20 bg-slate-950/80 p-5">
                <p className="text-xs font-semibold uppercase tracking-[0.2em] text-cyan-300">Shape Blueprint</p>
                <h1 className="mt-1 text-2xl font-semibold text-white">Canonical shape-guided protein design</h1>
                <p className="mt-2 max-w-4xl text-sm leading-6 text-slate-300">
                    Immutable CAD mesh → canonical surface, points, and signed-distance field → bounded Shape request → artifact-bound result review.
                </p>
            </header>

            {error && <div className="rounded-xl border border-red-500/40 bg-red-500/10 p-3 text-sm text-red-200">{error}</div>}
            {hydrationError && <p role="alert">{hydrationError}</p>}
            {selectedId && !selected && <p role="alert">Saved geometry {selectedId} is unavailable. Select an admitted geometry explicitly; no replacement was chosen.</p>}

            <div className="grid gap-5 lg:grid-cols-[390px_minmax(0,1fr)]">
                <section className="space-y-4 rounded-2xl border border-slate-800 bg-slate-900/80 p-4">
                    <div>
                        <h2 className="font-semibold text-white">1. Admit or select geometry</h2>
                        <p className="mt-1 text-xs text-slate-400">Upload one closed triangular OBJ or 3D-print STL (ASCII or binary). Admission rejects holes, non-manifold topology, self-intersections, and disconnected bodies.</p>
                    </div>
                    <input type="file" accept=".obj,.stl" onChange={(event) => {
                        const selectedFile = event.target.files?.[0] ?? null;
                        setFile(selectedFile);
                    }} className="block w-full text-xs text-slate-300 file:mr-3 file:rounded-lg file:border-0 file:bg-cyan-600 file:px-3 file:py-2 file:text-white" />
                    {file?.name.toLowerCase().endsWith('.stl') && <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-2 text-xs leading-5 text-amber-100"><strong>Confirm source units.</strong> STL files do not encode units. For protein-scale shape borrowing, the default treats each STL coordinate unit as 1 Å; literal millimeter scaling is usually far too large.</div>}
                    <div className="grid grid-cols-[1fr_auto] gap-2">
                        <select value={unit} onChange={(event) => setUnit(event.target.value)} className="rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white">
                            <option value="angstrom">Ångström (1 mesh unit = 1 Å)</option><option value="nanometer">Nanometer</option><option value="micrometer">Micrometer</option><option value="millimeter">Millimeter</option><option value="centimeter">Centimeter</option><option value="meter">Meter</option><option value="inch">Inch</option><option value="foot">Foot</option>
                        </select>
                        <button type="button" disabled={!file || upload.isPending} onClick={() => upload.mutate()} className="rounded-lg bg-cyan-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-40">{upload.isPending ? 'Validating mesh…' : 'Admit mesh'}</button>
                    </div>
                    <select value={selected?.geometry_id ?? ''} onChange={(event) => setSelectedId(event.target.value)} className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white">
                        {!geometries.length && <option value="">No admitted geometry</option>}
                        {geometries.map((geometry) => <option key={geometry.geometry_id} value={geometry.geometry_id}>{geometry.source_format.toUpperCase()} · {geometry.geometry_id}</option>)}
                    </select>

                    <div className="border-t border-slate-800 pt-4">
                        <h2 className="font-semibold text-white">2. Launch settings</h2>
                        <label className="mt-3 block text-xs text-slate-400">Guidance profile<select value={guidanceProfile} onChange={(event) => setGuidanceProfile(event.target.value as ShapeLaunchRequest['guidance_profile'])} className="mt-1 w-full bg-slate-950 p-2"><option value="rfd3_ca_shape_transfer_control_v1">RFD3 Cα shape-transfer control v1</option><option value="rfd3_unguided_control_v1">RFD3 unguided control v1</option></select></label>
                        <fieldset className="mt-3 text-xs text-slate-300"><legend>Native validators (ESMFold2 baseline required)</legend>{(['esmfold2', 'boltz2', 'protenix_v2'] as const).map((validator) => <label key={validator} className="mr-3"><input type="checkbox" checked={validatorSuite.includes(validator)} disabled={validator === 'esmfold2'} onChange={(event) => setValidatorSuite((current) => event.target.checked ? [...current, validator] : current.filter((value) => value !== validator))} /> {validator}</label>)}</fieldset>
                        <div className="mt-3 rounded-lg border border-cyan-500/30 bg-cyan-500/10 p-3 text-xs leading-5 text-cyan-100">
                            {guidanceProfile === 'rfd3_ca_shape_transfer_control_v1' ? <><strong>RFD3 Cα shape-transfer control v1.</strong> Uses the source controller's 0.75 shape weight, guide scale 2, constant schedule, and 800 active interior targets through the reviewed native RFD3 <code>delta_L</code> transfer. It is not yet a promoted protein-validity profile.</> : <><strong>RFD3 unguided control v1.</strong> Native unguided control; no shape-guidance objective is applied.</>}
                        </div>
                        <label className="mt-3 block text-xs text-slate-400">Job name<input value={name} onChange={(event) => setName(event.target.value)} className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white" /></label>
                        <div className="mt-3 grid grid-cols-2 gap-3">
                            <label className="text-xs text-slate-400">Length policy<select value={lengthMode} onChange={(event) => setLengthMode(event.target.value as 'fixed' | 'deterministic_range')} className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white"><option value="fixed">Fixed length</option><option value="deterministic_range">Deterministic range</option><option value="uniform_integer_range">Uniform integer range</option></select></label>
                            {lengthMode === 'fixed' ? <label className="text-xs text-slate-400">Target length<input type="number" min={40} max={600} value={targetLength} onChange={(event) => setTargetLength(boundedInteger(event.target.value, 120, 40, 600))} className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white" /></label> : <><label className="text-xs text-slate-400">Minimum length<input type="number" min={40} max={600} value={minimumLength} onChange={(event) => setMinimumLength(boundedInteger(event.target.value, 350, 40, 600))} className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white" /></label><label className="text-xs text-slate-400">Maximum length<input type="number" min={40} max={600} value={maximumLength} onChange={(event) => setMaximumLength(boundedInteger(event.target.value, 450, 40, 600))} className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white" /></label></>}
                            <label className="text-xs text-slate-400">RFD3 total candidates<input type="number" min={1} max={200} value={numBackbones} onChange={(event) => setNumBackbones(boundedInteger(event.target.value, 1, 1, 200))} className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white" /></label>
                            <label className="text-xs text-slate-400">Sequence policy<select value={sequencePolicy} onChange={(event) => setSequencePolicy(event.target.value as 'auto' | 'skip' | 'external')} className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white"><option value="auto">Auto · ProteinMPNN when needed</option><option value="skip">Skip sequence design</option><option value="external">Explicit engine</option></select></label>
                            {sequencePolicy === 'external' && <label className="text-xs text-slate-400">Sequence engine<select value={sequenceEngine} onChange={(event) => setSequenceEngine(event.target.value as 'proteinmpnn' | 'fampnn')} className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white"><option value="proteinmpnn">ProteinMPNN</option><option value="fampnn">FAMPNN</option></select></label>}
                            <label className="text-xs text-slate-400">Sequences / admitted backbone<input type="number" min={sequencePolicy === 'skip' ? 0 : 1} max={8} disabled={sequencePolicy === 'skip'} value={sequencePolicy === 'skip' ? 0 : sequencesPerBackbone} onChange={(event) => setSequencesPerBackbone(boundedInteger(event.target.value, 1, 1, 8))} className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white disabled:opacity-50" /></label>
                            <label className="text-xs text-slate-400">Deterministic seed<input type="number" min={0} max={2147483647} value={seed} onChange={(event) => setSeed(boundedInteger(event.target.value, 0, 0, 2147483647))} className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white" /></label>
                        </div>
                    </div>
                    {sequenceEnabled && <section className="space-y-3 border-t border-slate-800 pt-4">
                        <h3 className="font-semibold text-white">Native {effectiveSequenceEngine === 'fampnn' ? 'FAMPNN' : 'ProteinMPNN'} settings</h3>
                        {sequenceDefinition && <>
                            <p className="text-xs text-slate-400">Global model {sequenceDefinition.model_version} · schema <span title={sequenceDefinition.schema_sha256}>{shortHash(sequenceDefinition.schema_sha256)}</span>. Initial values are editable; saved and edited values take precedence.</p>
                            {Object.keys(sequenceDefinition.contextual_defaults).length > 0 && <p className="text-xs text-cyan-200">{sequenceDefinition.contextual_default_reason} {Object.entries(sequenceDefinition.contextual_defaults).map(([key, value]) => `${key}=${String(value)}`).join(' · ')}</p>}
                            {sequenceDefinition.params.map((param) => <ParamField
                                key={`${effectiveSequenceEngine}:${param.name}`}
                                param={param}
                                params={sequenceSettings}
                                updateParam={updateSequenceSetting}
                                setShowFileBrowser={unavailableSequenceInput}
                                setActiveSequenceField={unavailableSequenceInput}
                                setShowSequenceManager={unavailableSequenceInput}
                                ligandPresets={[]}
                            />)}
                        </>}
                        {sequenceSettingsError && <p role="alert" className="text-xs text-amber-200">{sequenceSettingsError}</p>}
                    </section>}
                    {invalidLengthPolicy && <p className="text-xs text-amber-200">Minimum length must not exceed maximum length.</p>}
                    <button type="button" disabled={!selected || launch.isPending || invalidLengthPolicy || Boolean(hydrationError) || Boolean(sequenceSettingsError)} onClick={() => launch.mutate()} className="w-full rounded-xl bg-emerald-600 px-4 py-3 font-semibold text-white hover:bg-emerald-500 disabled:opacity-40">{launch.isPending ? 'Staging immutable request…' : 'Launch Shape Blueprint'}</button>
                </section>

                <section className="overflow-hidden rounded-2xl border border-slate-800 bg-slate-900/80">
                    <div className="border-b border-slate-800 p-4">
                        <div className="flex flex-wrap items-start justify-between gap-3">
                            <div><h2 className="font-semibold text-white">Canonical geometry review</h2><p className="mt-1 text-xs text-slate-400">{hasHashBoundSurface ? 'Review the exact hash-bound server-canonicalized surface or point pool—not the raw upload or a browser reconstruction.' : 'This legacy surface is not hash-bound; exact review is limited to the canonical point pool.'}</p></div>
                            <div className="flex rounded-lg border border-slate-700 p-1 text-xs">
                                <button type="button" disabled={!hasHashBoundSurface} onClick={() => setReviewMode('surface')} className={`rounded px-3 py-1 disabled:cursor-not-allowed disabled:opacity-40 ${effectiveReviewMode === 'surface' ? 'bg-cyan-600 text-white' : 'text-slate-300'}`}>Surface</button>
                                <button type="button" onClick={() => setReviewMode('points')} className={`rounded px-3 py-1 ${effectiveReviewMode === 'points' ? 'bg-cyan-600 text-white' : 'text-slate-300'}`}>Points</button>
                            </div>
                        </div>
                    </div>
                    {selected ? (effectiveReviewMode === 'surface'
                        ? <CanonicalMeshPreview url={`/api/shape-blueprint/geometries/${selected.geometry_id}/preview.obj`} height={430} label="Canonical Shape surface" />
                        : <MolstarViewer structureUrl={`/api/shape-blueprint/geometries/${selected.geometry_id}/points.cif`} format="cif" height={430} label="Canonical Shape point pool" />)
                        : <div className="flex h-[430px] items-center justify-center text-sm text-slate-500">Select geometry to preview</div>}
                    {selected && <div className="grid gap-2 border-t border-slate-800 p-4 text-xs text-slate-300 sm:grid-cols-2">
                        <div>Source <span className="font-semibold text-white">{selected.source_format.toUpperCase()}</span> · {selected.source_parser.replaceAll('_', ' ')}</div>
                        <div>Units <span className="font-semibold text-white">{selected.source_unit}</span> · {selected.angstrom_per_unit.toExponential(3)} Å/unit</div>
                        <div className="sm:col-span-2">Dimensions <span className="font-mono text-cyan-200">{selected.dimensions_angstrom.map(formatAngstrom).join(' × ')}</span></div>
                        <div>Source bytes <span className="font-mono text-cyan-200" title={selected.source_sha256}>{shortHash(selected.source_sha256)}</span></div>
                        <div>Geometry <span className="font-mono text-cyan-200" title={selected.geometry_sha256}>{shortHash(selected.geometry_sha256)}</span></div>
                        <div>Manifest <span className="font-mono text-cyan-200" title={selected.manifest_sha256}>{shortHash(selected.manifest_sha256)}</span></div>
                        {selected.preview_obj_sha256 && <div>Surface <span className="font-mono text-cyan-200" title={selected.preview_obj_sha256}>{shortHash(selected.preview_obj_sha256)}</span></div>}
                        <div>Points <span className="font-mono text-cyan-200" title={selected.point_pool_sha256}>{shortHash(selected.point_pool_sha256)}</span></div>
                        <div>SDF <span className="font-mono text-cyan-200" title={selected.sdf_sha256}>{shortHash(selected.sdf_sha256)}</span></div>
                        <div>Convention <span className="text-emerald-300">{selected.sdf_sign}</span> • {selected.sdf_grid_shape.join('×')}</div>
                        <div>{selected.vertex_count.toLocaleString()} vertices • {selected.face_count.toLocaleString()} faces</div>
                        <div>{selected.point_count.toLocaleString()} deterministic points</div>
                    </div>}
                    {selectedMaxDimension !== null && (selectedMaxDimension > 1_000 || selectedMaxDimension < 5) && <div className="border-t border-amber-500/20 bg-amber-500/10 p-3 text-xs leading-5 text-amber-100"><strong>Check mesh scale:</strong> longest dimension is {formatAngstrom(selectedMaxDimension)}. This is unusual for a protein-scale blueprint; confirm the source unit and re-admit if needed.</div>}
                </section>
            </div>
        </div>
    );
}
