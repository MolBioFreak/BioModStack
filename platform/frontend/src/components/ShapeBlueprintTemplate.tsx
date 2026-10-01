import { ExecutionTargetPicker } from './ExecutionTargetPicker';
import { NativeSettingsDisclosure } from './NativeSettingsDisclosure';
import { DE_NOVO_PRELOAD_SELECTION } from './dashboard/IndependentProvisionPanel';
import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';

import {
    completeCurrentLaunchContext,
    listShapeGeometries,
    fetchShapeSequenceSettings,
    fetchShapeSettings,
    type ShapePredictor,
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
import { ShapeNativeSettings } from './ShapeNativeSettings';
import { ProteinDesignSections, ProteinDesignPanel, ProteinDesignRun } from './ProteinDesignWorkflow';

const shortHash = (value: string) => `${value.slice(0, 12)}…${value.slice(-8)}`;
const geometryLabel = (geometry: ShapeGeometrySummary) => {
    const filename = (geometry as ShapeGeometrySummary & { original_filename?: string | null }).original_filename;
    return filename || `${geometry.source_format.toUpperCase()} · ${shortHash(geometry.geometry_id)}`;
};
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

interface ShapeBlueprintTemplateProps {
    initialValues?: Record<string, unknown>;
    embedded?: boolean;
    runDetails?: React.ReactNode;
    launchContextId?: string | null;
    onDraftChange?: (draft: Record<string, unknown>) => void;
}

export default function ShapeBlueprintTemplate({ initialValues = {}, embedded = false, onDraftChange, runDetails, launchContextId }: ShapeBlueprintTemplateProps) {
    const saved = <T,>(key: string, fallback: T): T => (Object.hasOwn(initialValues, `shape_${key}`) ? initialValues[`shape_${key}`] : Object.hasOwn(initialValues, key) ? initialValues[key] : fallback) as T;
    const [section, setSection] = useState(saved('section', 'Geometry'));
    const [sourceView, setSourceView] = useState(saved('source_view', 'library'));
    const [numericDrafts, setNumericDrafts] = useState<Record<string, string>>(saved('numeric_drafts', {}));
    const numericValue = (key: string, value: number) => Object.hasOwn(numericDrafts, key) ? numericDrafts[key] : value;
    // Invalid editing text stays explicit at the request boundary: the native
    // request owner validates it, rather than silently launching a prior value.
    const requestedInteger = (key: string, value: number) => numericValue(key, value) as number;
    const editInteger = (key: string, text: string, update: (value: number) => void, fallback: number, min: number, max: number) => {
        setNumericDrafts(current => { const next = { ...current }; if (text === '') next[key] = ''; else delete next[key]; return next; });
        if (text !== '') update(boundedInteger(text, fallback, min, max));
    };
    let initialLengthPolicy: ShapeLaunchRequest['length_policy'];
    let hydrationError: string | null = null;
    const savedObject = <T extends object,>(key: string, fallback: T): T => {
        const raw = saved<unknown>(key, fallback);
        try {
            const value = typeof raw === 'string' ? JSON.parse(raw) : raw;
            if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Expected an object');
            return value as T;
        } catch { hydrationError = `Saved ${key.replaceAll('_', ' ')} are malformed; reopen the original request before cloning.`; return fallback; }
    };
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
        if (!value || typeof value !== 'object' || Array.isArray(value)) {
            throw new Error('Invalid saved sequence settings');
        }
        initialSequenceSettings = value as ShapeSequenceSettings;
    } catch { hydrationError = 'Saved sequence settings are malformed; reopen the original request before cloning.'; }
    const initialValidators = saved<string | NonNullable<ShapeLaunchRequest['validator_suite']>>('validator_suite', ['boltz2', 'esmfold2', 'protenix_v2']);
    const navigate = useNavigate();
    const queryClient = useQueryClient();
    const [selectedId, setSelectedId] = useState(saved('geometry_id', ''));
    const [clientIdentity, setClientIdentity] = useState(() => ({
        id: getShapeClientRequestId(), signature: sessionStorage.getItem(`${SHAPE_CLIENT_REQUEST_KEY}.signature`),
    }));
    const [file, setFile] = useState<File | null>(null);
    const [unit, setUnit] = useState(saved('source_unit', 'angstrom'));
    const [name, setName] = useState(saved('name', 'Shape Blueprint design'));
    const [targetLength, setTargetLength] = useState(saved('target_length', initialLengthPolicy?.min ?? 120));
    const [lengthMode, setLengthMode] = useState<NonNullable<ShapeLaunchRequest['length_policy']>['mode']>(initialLengthPolicy?.mode ?? 'fixed');
    const [minimumLength, setMinimumLength] = useState(saved('minimum_length', initialLengthPolicy?.min ?? 350));
    const [maximumLength, setMaximumLength] = useState(saved('maximum_length', initialLengthPolicy?.max ?? 450));
    const [numBackbones, setNumBackbones] = useState(saved('num_backbones', 1));
    const [sequencesPerBackbone, setSequencesPerBackbone] = useState(saved('sequences_per_backbone', 1));
    const [sequencePolicy, setSequencePolicy] = useState<'auto' | 'skip' | 'external'>(saved('sequence_policy', 'auto'));
    const [sequenceEngine, setSequenceEngine] = useState<ShapeSequenceEngine>(saved('sequence_engine', 'proteinmpnn'));
    // Keep operator/saved values separate from contextual initial values. A count
    // refresh may update an unset batch size, but never an explicit one.
    const [sequenceSettingsByEngine, setSequenceSettingsByEngine] = useState<Partial<Record<ShapeSequenceEngine, ShapeSequenceSettings>>>(() => ({
        ...savedObject<Partial<Record<ShapeSequenceEngine, ShapeSequenceSettings>>>('sequence_settings_by_engine', {
            [sequencePolicy === 'external' ? sequenceEngine : 'proteinmpnn']: initialSequenceSettings,
        }),
    }));
    const [sequenceEditedKeys, setSequenceEditedKeys] = useState<Partial<Record<ShapeSequenceEngine, string[]>>>(() => saved('sequence_edited_keys', Object.fromEntries(Object.entries(sequenceSettingsByEngine).map(([id, values]) => [id, Object.keys(values ?? {})]))));
    const [rfd3Settings, setRfd3Settings] = useState<Record<string, unknown>>(savedObject('rfd3_settings', savedObject('requested_rfd3_settings', {})));
    const [inputSettingsByEngine, setInputSettingsByEngine] = useState<Partial<Record<ShapeSequenceEngine, Record<string, unknown>>>>(savedObject('sequence_input_settings_by_engine', { [sequenceEngine]: savedObject('sequence_input_settings', savedObject('requested_sequence_input_settings', {})) }));
    const [validatorSettingsByEngine, setValidatorSettingsByEngine] = useState<Partial<Record<ShapePredictor, Record<string, unknown>>>>(savedObject('validator_settings_by_engine', savedObject('validator_settings', savedObject('requested_validator_settings', {}))));
    const settingsQuery = useQuery({ queryKey: ['shape-settings'], queryFn: () => fetchShapeSettings().then(response => response.data), retry: false });
    const effectiveRfd3Settings = { ...settingsQuery.data?.rfd3.initial_values, ...rfd3Settings };
    const [seed, setSeed] = useState(saved('seed', 0));
    const effectiveValidatorSettings = Object.fromEntries((['esmfold2', 'boltz2', 'protenix_v2'] as const).map(id => {
        const definition = settingsQuery.data?.validators[id];
        const derivedSeed = id === 'esmfold2' && Object.hasOwn(definition?.contextual_defaults ?? {}, 'seed') ? { seed }
            : id === 'protenix_v2' && Object.hasOwn(definition?.contextual_defaults ?? {}, 'protenix_seeds') ? { protenix_seeds: String(seed) } : {};
        return [id, { ...definition?.initial_values, ...derivedSeed, ...validatorSettingsByEngine[id] }];
    }));
    const [guidanceProfile, setGuidanceProfile] = useState<ShapeLaunchRequest['guidance_profile']>(saved('guidance_profile', 'rfd3_ca_shape_transfer_control_v1'));
    const [validatorSuite, setValidatorSuite] = useState<NonNullable<ShapeLaunchRequest['validator_suite']>>(
        typeof initialValidators === 'string' ? initialValidators.split(',').filter(Boolean) as NonNullable<ShapeLaunchRequest['validator_suite']> : initialValidators,
    );
    const [error, setError] = useState<string | null>(null);
    const [reviewMode, setReviewMode] = useState<'surface' | 'points'>(saved('review_mode', 'surface'));

    const sequenceEnabled = sequencePolicy !== 'skip' && sequencesPerBackbone > 0;
    const effectiveSequenceEngine = sequencePolicy === 'external' ? sequenceEngine : 'proteinmpnn';
    const sequenceSettingsQuery = useQuery({
        queryKey: ['shape-sequence-settings', effectiveSequenceEngine, sequencesPerBackbone],
        queryFn: () => fetchShapeSequenceSettings(effectiveSequenceEngine, sequencesPerBackbone).then((response) => response.data),
        retry: false,
    });
    const sequenceDefinition = sequenceSettingsQuery.data?.engine === effectiveSequenceEngine ? sequenceSettingsQuery.data : undefined;
    const explicitSequenceSettings = sequenceSettingsByEngine[effectiveSequenceEngine] ?? {};
    const sequenceSettings = { ...explicitSequenceSettings, ...sequenceDefinition?.initial_values, ...Object.fromEntries(Object.entries(explicitSequenceSettings).filter(([key]) => sequenceEditedKeys[effectiveSequenceEngine]?.includes(key))) };
    const unsupportedSequenceKeys = sequenceDefinition
        ? Object.keys(explicitSequenceSettings).filter((key) => !sequenceDefinition.params.some((param) => param.name === key))
        : [];
    const sequenceSettingsError = sequenceEnabled
        ? sequenceSettingsQuery.isError ? requestError(sequenceSettingsQuery.error, 'Sequence settings are unavailable.')
            : unsupportedSequenceKeys.length ? `Saved settings are unsupported by this engine: ${unsupportedSequenceKeys.join(', ')}. Reopen the original request; no settings were discarded.`
                : !sequenceDefinition ? 'Loading native sequence settings…' : null
        : null;
    const updateSequenceSettings = (patch: Record<string, unknown>) => {
        setSequenceEditedKeys(current => ({ ...current, [effectiveSequenceEngine]: [...new Set([...(current[effectiveSequenceEngine] ?? []), ...Object.keys(patch)])] }));
        setSequenceSettingsByEngine((current) => ({
            ...current,
            [effectiveSequenceEngine]: { ...current[effectiveSequenceEngine], ...patch },
        }));
    };
    const inputDefaults = Object.fromEntries(Object.entries((sequenceDefinition?.input_settings_schema?.properties ?? {}) as Record<string, Record<string, unknown>>).filter(([, field]) => Object.hasOwn(field, 'default')).map(([key, field]) => [key, field.default]));
    const sequenceInputSettings = { ...inputDefaults, ...inputSettingsByEngine[effectiveSequenceEngine] };
    const rememberSequence = () => {
        setSequenceSettingsByEngine(current => ({ ...current, [effectiveSequenceEngine]: sequenceSettings }));
        setInputSettingsByEngine(current => ({ ...current, [effectiveSequenceEngine]: sequenceInputSettings }));
    };

    const geometriesQuery = useQuery({
        queryKey: ['shape-geometries'],
        queryFn: () => listShapeGeometries().then((response) => response.data.geometries),
    });
    const geometries = useMemo(() => geometriesQuery.data ?? [], [geometriesQuery.data]);
    const selected = useMemo<ShapeGeometrySummary | undefined>(
        () => selectedId ? geometries.find((geometry) => geometry.geometry_id === selectedId) : geometries[0],
        [geometries, selectedId],
    );
    // Resolve the existing first-library-item default once. A reordered refresh
    // must not silently change a geometry the operator is already inspecting.
    useEffect(() => { if (!selectedId && selected) setSelectedId(selected.geometry_id); }, [selectedId, selected]);
    const selectedMaxDimension = selected ? Math.max(...selected.dimensions_angstrom) : null;
    const hasHashBoundSurface = Boolean(selected?.preview_obj_sha256);
    const effectiveReviewMode = reviewMode === 'surface' && hasHashBoundSurface ? 'surface' : 'points';
    const invalidLengthPolicy = lengthMode !== 'fixed' && minimumLength > maximumLength;

    const launchSettings: Parameters<typeof buildShapeLaunchRequest>[1] = {
        client_request_id: clientIdentity.id,
        name: name.trim() || 'Shape Blueprint design',
        target_length: lengthMode === 'fixed' ? requestedInteger('target_length', targetLength) : undefined,
        length_policy: {
            ...(lengthMode === initialLengthPolicy?.mode ? initialLengthPolicy : {}),
            mode: lengthMode,
            min: lengthMode === 'fixed' ? requestedInteger('target_length', targetLength) : requestedInteger('minimum_length', minimumLength),
            max: lengthMode === 'fixed' ? requestedInteger('target_length', targetLength) : requestedInteger('maximum_length', maximumLength),
        },
        num_backbones: requestedInteger('num_backbones', numBackbones),
        sequences_per_backbone: sequencePolicy === 'skip' ? 0 : requestedInteger('sequences_per_backbone', sequencesPerBackbone),
        sequence_policy: sequencePolicy,
        sequence_engine: sequencePolicy === 'external' ? sequenceEngine : undefined,
        sequence_settings: sequenceEnabled ? sequenceSettings : {},
        sequence_input_settings: sequenceEnabled && effectiveSequenceEngine === 'caliby_experimental' ? sequenceInputSettings : {},
        rfd3_settings: effectiveRfd3Settings,
        validator_settings: Object.fromEntries(validatorSuite.map(id => [id, effectiveValidatorSettings[id]])),
        launch_context_id: launchContextId ?? null,
        seed: requestedInteger('seed', seed),
        guidance_profile: guidanceProfile,
        validator_suite: validatorSuite,
    };
    const retainedRequest = saved<ShapeLaunchRequest | undefined>('submitted_request', undefined);
    const configuredRequest: ShapeLaunchRequest | null = selected ? buildShapeLaunchRequest(selected, launchSettings)
        : retainedRequest?.geometry_id === selectedId ? {
            ...launchSettings,
            geometry_id: retainedRequest.geometry_id,
            expected_geometry_sha256: retainedRequest.expected_geometry_sha256,
            expected_geometry_manifest_sha256: retainedRequest.expected_geometry_manifest_sha256,
            expected_point_pool_sha256: retainedRequest.expected_point_pool_sha256,
        } : null;
    // Project preparation consumes the same active serializer. During a cold
    // library read use only the saved geometry identity, never stale settings.
    // Destination is independently issued by the Project owner.
    const shapeSubmittedRequest = configuredRequest ? Object.fromEntries(Object.entries(configuredRequest).filter(([key]) => key !== 'launch_context_id')) : undefined;

    // Drafts retain inactive controls too; launch still uses the native request owner.
    // Pending local uploads are not persisted: only admitted geometry is reload-safe.
    const draftJson = JSON.stringify({
        shape_submitted_request: shapeSubmittedRequest,
        shape_geometry_id: selected?.geometry_id ?? selectedId,
        shape_geometry_sha256: selected?.geometry_sha256 ?? saved('geometry_sha256', ''),
        shape_point_pool_sha256: selected?.point_pool_sha256 ?? saved('point_pool_sha256', ''),
        shape_name: name, shape_source_unit: unit, shape_review_mode: reviewMode, shape_section: section, shape_source_view: sourceView,
        shape_numeric_drafts: numericDrafts,
        shape_rfd3_settings: effectiveRfd3Settings,
        shape_sequence_input_settings_by_engine: { ...inputSettingsByEngine, [effectiveSequenceEngine]: sequenceInputSettings },
        shape_sequence_input_settings: sequenceInputSettings,
        shape_validator_settings_by_engine: effectiveValidatorSettings,
        shape_target_length: targetLength, shape_minimum_length: minimumLength, shape_maximum_length: maximumLength,
        shape_length_policy: {
            ...(lengthMode === initialLengthPolicy?.mode ? initialLengthPolicy : {}),
            mode: lengthMode, min: lengthMode === 'fixed' ? targetLength : minimumLength,
            max: lengthMode === 'fixed' ? targetLength : maximumLength,
        },
        shape_num_backbones: numBackbones, shape_sequences_per_backbone: sequencesPerBackbone,
        shape_sequence_policy: sequencePolicy, shape_sequence_engine: sequenceEngine,
        shape_sequence_edited_keys: sequenceEditedKeys,
        shape_sequence_settings: sequenceSettings,
        shape_sequence_settings_by_engine: { ...sequenceSettingsByEngine, [effectiveSequenceEngine]: sequenceSettings },
        shape_seed: seed, shape_guidance_profile: guidanceProfile, shape_validator_suite: validatorSuite,
    });
    useEffect(() => { onDraftChange?.(JSON.parse(draftJson)); }, [draftJson, onDraftChange]);

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

    // Loading native defaults must not consume a retained retry identity.
    const requestSignature = configuredRequest && !sequenceSettingsError && !hydrationError
        ? JSON.stringify({ ...configuredRequest, client_request_id: undefined }) : null;
    if (requestSignature && requestSignature !== clientIdentity.signature) {
        setClientIdentity({ signature: requestSignature, id: clientIdentity.signature ? crypto.randomUUID() : clientIdentity.id });
    }
    useEffect(() => {
        sessionStorage.setItem(SHAPE_CLIENT_REQUEST_KEY, clientIdentity.id);
        if (clientIdentity.signature) sessionStorage.setItem(`${SHAPE_CLIENT_REQUEST_KEY}.signature`, clientIdentity.signature);
    }, [clientIdentity]);

    const launch = useMutation({
        mutationFn: () => {
            if (hydrationError) throw new Error(hydrationError);
            if (sequenceSettingsError) throw new Error(sequenceSettingsError);
            if (!selected) throw new Error('Select or upload canonical geometry first.');
            if (selected.geometry_id === saved('geometry_id', '') && (
                (saved('geometry_sha256', '') && saved('geometry_sha256', '') !== selected.geometry_sha256)
                || (saved('point_pool_sha256', '') && saved('point_pool_sha256', '') !== selected.point_pool_sha256)
            )) throw new Error('Saved geometry identity differs from the admitted geometry; select a new source explicitly.');
            return submitShapeBlueprint(configuredRequest!);
        },
        onSuccess: async (response) => {
            sessionStorage.removeItem(SHAPE_CLIENT_REQUEST_KEY);
            sessionStorage.removeItem(`${SHAPE_CLIENT_REQUEST_KEY}.signature`);
            navigate(await completeCurrentLaunchContext(response.data) ?? `/designs/${response.data.job_id}`);
        },
        onError: (cause: unknown) => setError(requestError(cause, 'Shape request failed.')),
    });

    return (
        <div className="mx-auto max-w-7xl space-y-5 text-[var(--text-primary)]">
            {!embedded && <header>
                <h1 className="text-2xl font-semibold">Shape Blueprint</h1>
                <p className="mt-2 text-sm text-[var(--text-secondary)]">Design protein candidates around a canonical geometry.</p>
            </header>}
            <ProteinDesignSections label="Shape sections" sections={['Geometry', 'RFD3', 'Sequence design', 'Prediction', 'Run']} active={section} onChange={setSection} />
            {error && <p role="alert">{error}</p>}
            {hydrationError && <p role="alert">{hydrationError}</p>}
            {selectedId && !selected && geometriesQuery.isSuccess && <p role="alert">Saved geometry {selectedId} is unavailable. Select an admitted geometry explicitly; no replacement was chosen.</p>}
            <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
                <ProteinDesignPanel title={section === 'RFD3' ? 'RFD3 generation' : section === 'Run' ? 'Run configuration' : section}>
                    <div hidden={section !== 'Geometry'} className="space-y-4">
                    <div>
                        <h2 className="font-semibold text-[var(--text-primary)]">Select or upload geometry</h2>
                        <p className="mt-1 text-xs text-[var(--text-secondary)]">Upload one closed triangular OBJ or 3D-print STL (ASCII or binary). Use a single closed mesh.</p>
                    </div>
                    <ProteinDesignSections label="Geometry source" sections={['Saved geometry', 'Upload OBJ / STL']} active={sourceView === 'upload' ? 'Upload OBJ / STL' : 'Saved geometry'} onChange={value => setSourceView(value === 'Upload OBJ / STL' ? 'upload' : 'library')} />
                    <div hidden={sourceView !== 'upload'} className="space-y-3">
                    <input aria-label="Geometry file" type="file" accept=".obj,.stl" onChange={(event) => {
                        const selectedFile = event.target.files?.[0] ?? null;
                        setFile(selectedFile);
                    }} className="block w-full text-xs text-[var(--text-secondary)] file:mr-3 file:rounded-lg file:border-0 file:bg-cyan-600 file:px-3 file:py-2 file:text-white" />
                    {file?.name.toLowerCase().endsWith('.stl') && <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-2 text-xs leading-5 text-[var(--text-secondary)]"><strong>Confirm source units.</strong> STL files do not encode units. For protein-scale shape borrowing, the default treats each STL coordinate unit as 1 Å; literal millimeter scaling is usually far too large.</div>}
                    <div className="grid grid-cols-[1fr_auto] gap-2">
                        <select aria-label="Source units" value={unit} onChange={(event) => setUnit(event.target.value)} className="rounded-lg border border-[var(--border-primary)] bg-[var(--bg-tertiary)] px-3 py-2 text-sm text-[var(--text-primary)]">
                            <option value="angstrom">Ångström (1 mesh unit = 1 Å)</option><option value="nanometer">Nanometer</option><option value="micrometer">Micrometer</option><option value="millimeter">Millimeter</option><option value="centimeter">Centimeter</option><option value="meter">Meter</option><option value="inch">Inch</option><option value="foot">Foot</option>
                        </select>
                        <button type="button" disabled={!file || upload.isPending} onClick={() => upload.mutate()} className="rounded-lg bg-cyan-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-40">{upload.isPending ? 'Validating mesh…' : 'Admit mesh'}</button>
                    </div>
                    </div>
                    <div hidden={sourceView !== 'library'} className="space-y-3">
                    {geometriesQuery.isPending && <p role="status">Loading saved geometry…</p>}
                    {geometriesQuery.isError && <p role="status">Geometry list could not refresh. Your selection and settings are retained. <button type="button" onClick={() => void geometriesQuery.refetch()}>Retry geometry list</button></p>}
                    {geometriesQuery.isSuccess && !geometries.length && <p>No saved geometry yet. Upload an OBJ or STL to begin.</p>}
                    <select aria-label="Geometry" value={selected?.geometry_id ?? ''} onChange={(event) => setSelectedId(event.target.value)} className="w-full rounded-lg border border-[var(--border-primary)] bg-[var(--bg-tertiary)] px-3 py-2 text-sm text-[var(--text-primary)]">
                        {!selected && <option value="">{selectedId ? `Saved selection · ${shortHash(selectedId)}` : geometriesQuery.isSuccess ? 'Choose geometry' : 'Geometry list unavailable'}</option>}
                        {geometries.map((geometry) => <option key={geometry.geometry_id} value={geometry.geometry_id}>{geometryLabel(geometry)} · {geometry.dimensions_angstrom.map(formatAngstrom).join(' × ')}</option>)}
                    </select>
                    </div>
                    </div>
                    <div hidden={section !== 'RFD3'}>
                        {settingsQuery.isError && <p role="status">Native settings could not refresh. Saved values are retained. <button type="button" onClick={() => void settingsQuery.refetch()}>Retry native settings</button></p>}
                        <label className="mt-3 block text-xs text-[var(--text-secondary)]">Guidance profile<select value={guidanceProfile} onChange={(event) => setGuidanceProfile(event.target.value as ShapeLaunchRequest['guidance_profile'])} className="mt-1 w-full bg-[var(--bg-tertiary)] p-2"><option value="rfd3_ca_shape_transfer_control_v1">RFD3 Cα shape-transfer control v1</option><option value="rfd3_unguided_control_v1">RFD3 unguided control v1</option></select></label>
                        <details className="mt-3 rounded-lg border border-[var(--border-primary)] p-3 text-sm"><summary>Profile-defined guidance · read-only</summary>
                            {guidanceProfile === 'rfd3_ca_shape_transfer_control_v1' ? <dl className="mt-3 grid grid-cols-2 gap-2"><dt>Shape weight</dt><dd>0.75</dd><dt>Guide scale</dt><dd>2</dd><dt>Schedule</dt><dd>Constant</dd><dt>Interior targets</dt><dd>800</dd></dl> : <p className="mt-3">Unguided control · no shape-guidance objective.</p>}
                            <p className="mt-2 text-xs text-[var(--text-secondary)]">Fixed by the selected existing profile. Geometry guidance does not establish folding or function.</p>
                        </details>
                        <div className="mt-3 grid grid-cols-2 gap-3">
                            <label className="text-xs text-[var(--text-secondary)]">Length policy<select value={lengthMode} onChange={(event) => setLengthMode(event.target.value as 'fixed' | 'deterministic_range')} className="mt-1 w-full rounded-lg border border-[var(--border-primary)] bg-[var(--bg-tertiary)] px-3 py-2 text-sm text-[var(--text-primary)]"><option value="fixed">Fixed length</option><option value="deterministic_range">Deterministic range</option><option value="uniform_integer_range">Uniform integer range</option></select></label>
                            {lengthMode === 'fixed' ? <label className="text-xs text-[var(--text-secondary)]">Target length<input type="number" min={40} max={600} value={numericValue('target_length', targetLength)} onChange={event => editInteger('target_length', event.target.value, setTargetLength, 120, 40, 600)} className="mt-1 w-full rounded-lg border border-[var(--border-primary)] bg-[var(--bg-tertiary)] px-3 py-2 text-sm text-[var(--text-primary)]" /></label> : <><label className="text-xs text-[var(--text-secondary)]">Minimum length<input type="number" min={40} max={600} value={numericValue('minimum_length', minimumLength)} onChange={event => editInteger('minimum_length', event.target.value, setMinimumLength, 350, 40, 600)} className="mt-1 w-full rounded-lg border border-[var(--border-primary)] bg-[var(--bg-tertiary)] px-3 py-2 text-sm text-[var(--text-primary)]" /></label><label className="text-xs text-[var(--text-secondary)]">Maximum length<input type="number" min={40} max={600} value={numericValue('maximum_length', maximumLength)} onChange={event => editInteger('maximum_length', event.target.value, setMaximumLength, 450, 40, 600)} className="mt-1 w-full rounded-lg border border-[var(--border-primary)] bg-[var(--bg-tertiary)] px-3 py-2 text-sm text-[var(--text-primary)]" /></label></>}
                            <label className="text-xs text-[var(--text-secondary)]">RFD3 total candidates<input type="number" min={1} max={200} value={numericValue('num_backbones', numBackbones)} onChange={event => editInteger('num_backbones', event.target.value, setNumBackbones, 1, 1, 200)} className="mt-1 w-full rounded-lg border border-[var(--border-primary)] bg-[var(--bg-tertiary)] px-3 py-2 text-sm text-[var(--text-primary)]" /></label>
                            <label className="text-xs text-[var(--text-secondary)]">Deterministic seed<input type="number" min={0} max={2147483647} value={numericValue('seed', seed)} onChange={event => editInteger('seed', event.target.value, setSeed, 0, 0, 2147483647)} className="mt-1 w-full rounded-lg border border-[var(--border-primary)] bg-[var(--bg-tertiary)] px-3 py-2 text-sm text-[var(--text-primary)]" /></label>
                        </div>
                        <div className="mt-4"><ShapeNativeSettings definition={settingsQuery.data?.rfd3} values={effectiveRfd3Settings} onPatch={patch => setRfd3Settings(current => ({ ...current, ...patch }))} /></div>
                    </div>
                    <div hidden={section !== 'Sequence design'} className="space-y-4">
                        <p className="text-sm text-[var(--text-secondary)]">Design sequences for generated backbones. Settings are retained separately for each designer.</p>
                            <label className="text-xs text-[var(--text-secondary)]">Sequence policy<select value={sequencePolicy} onChange={(event) => { rememberSequence(); setSequencePolicy(event.target.value as 'auto' | 'skip' | 'external'); }} className="mt-1 w-full rounded-lg border border-[var(--border-primary)] bg-[var(--bg-tertiary)] px-3 py-2 text-sm text-[var(--text-primary)]"><option value="auto">Auto · ProteinMPNN when needed</option><option value="skip">Skip sequence design</option><option value="external">Explicit engine</option></select></label>
                            {sequencePolicy === 'external' && <label className="text-xs text-[var(--text-secondary)]">Sequence engine<select value={sequenceEngine} onChange={(event) => { rememberSequence(); setSequenceEngine(event.target.value as ShapeSequenceEngine); }} className="mt-1 w-full rounded-lg border border-[var(--border-primary)] bg-[var(--bg-tertiary)] px-3 py-2 text-sm text-[var(--text-primary)]"><option value="proteinmpnn">ProteinMPNN</option><option value="fampnn">FA-MPNN</option><option value="caliby_experimental">Caliby</option></select></label>}
                            <label className="text-xs text-[var(--text-secondary)]">Sequences / admitted backbone<input type="number" min={sequencePolicy === 'skip' ? 0 : 1} max={8} disabled={sequencePolicy === 'skip'} value={sequencePolicy === 'skip' ? 0 : numericValue('sequences_per_backbone', sequencesPerBackbone)} onChange={event => editInteger('sequences_per_backbone', event.target.value, setSequencesPerBackbone, 1, 1, 8)} className="mt-1 w-full rounded-lg border border-[var(--border-primary)] bg-[var(--bg-tertiary)] px-3 py-2 text-sm text-[var(--text-primary)] disabled:opacity-50" /></label>
                    <details open={sequenceEnabled} className="space-y-3" key={sequenceEnabled ? 'active' : 'retained'}>
                        <summary>{sequenceEnabled ? 'Native designer settings' : 'Retained designer settings · stage skipped'}</summary>
                        {sequenceDefinition && <>
                            {Object.keys(sequenceDefinition.contextual_defaults).length > 0 && <p className="text-xs text-[var(--text-secondary)]">{sequenceDefinition.contextual_default_reason}</p>}
                            <ShapeNativeSettings definition={sequenceDefinition} values={sequenceSettings} onPatch={updateSequenceSettings} />
                            {sequenceDefinition.input_settings_schema && <details className="rounded-lg border border-[var(--border-primary)] p-3"><summary>Generated-state constraints</summary>
                                <p className="my-2 text-sm text-[var(--text-secondary)]">Each generated backbone supplies one single-state ensemble. Native author-residue constraints are preserved unchanged.</p>
                                <ShapeNativeSettings inputSchema={sequenceDefinition.input_settings_schema} values={sequenceInputSettings}
                                    onPatch={patch => setInputSettingsByEngine(current => ({ ...current, [effectiveSequenceEngine]: { ...current[effectiveSequenceEngine], ...patch } }))} />
                            </details>}
                        </>}
                        {sequenceSettingsError && <p role="status" className="text-sm text-[var(--text-secondary)]">{sequenceSettingsError} <button type="button" onClick={() => void sequenceSettingsQuery.refetch()}>Retry designer settings</button></p>}
                    </details>
                    </div>
                    <div hidden={section !== 'Prediction'} className="space-y-4">
                        <p className="text-sm text-[var(--text-secondary)]">Native predictions are computational evidence, not experimental validation.</p>
                        {settingsQuery.isError && <p role="status">Prediction settings could not refresh. Retained values are unchanged. <button type="button" onClick={() => void settingsQuery.refetch()}>Retry prediction settings</button></p>}
                        {(['esmfold2', 'boltz2', 'protenix_v2'] as const).map(id => <section key={id} aria-label={`${id} prediction`} className="min-w-0 space-y-3 rounded-lg border border-[var(--border-primary)] p-3">
                            <label className="flex gap-2 text-sm font-medium"><input type="checkbox" aria-label={`Include ${id}`} checked={validatorSuite.includes(id)} disabled={id === 'esmfold2'} onChange={event => setValidatorSuite(current => event.target.checked ? [...current, id] : current.filter(value => value !== id))} />{id === 'esmfold2' ? 'ESMFold2 · existing baseline' : id === 'boltz2' ? 'Boltz2' : 'Protenix V2'}</label>
                            <NativeSettingsDisclosure open={validatorSuite.includes(id) || undefined} summary={validatorSuite.includes(id) ? 'Native settings' : 'Retained native settings'}>{() => <div className="mt-3">
                                <ShapeNativeSettings definition={settingsQuery.data?.validators[id]} values={effectiveValidatorSettings[id]} onPatch={patch => setValidatorSettingsByEngine(current => ({ ...current, [id]: { ...current[id], ...patch } }))} />
                            </div>}</NativeSettingsDisclosure>
                        </section>)}
                    </div>
                    <div hidden={section !== 'Run'} className="space-y-4">
                        <dl className="space-y-2 text-sm"><div><dt>Geometry</dt><dd>{selected ? geometryLabel(selected) : 'Not selected'}</dd></div>
                            <div><dt>Generation</dt><dd>RFD3 · {numBackbones} backbones</dd></div><div><dt>Sequence design</dt><dd>{sequenceEnabled ? `${effectiveSequenceEngine} · ${sequencesPerBackbone} per backbone` : 'Skipped · settings retained'}</dd></div>
                            <div><dt>Destination</dt><dd>{launchContextId ? 'Current Project launch context' : 'Standalone'}</dd></div></dl>
                        {runDetails}
                        <ProteinDesignRun jobName={name} onJobNameChange={setName} pending={launch.isPending} disabled={!selected || invalidLengthPolicy || Boolean(hydrationError) || Boolean(sequenceSettingsError)} onSubmit={() => launch.mutate()} submitLabel="Launch Shape Blueprint">
                            <ExecutionTargetPicker workflowRequest={configuredRequest && !hydrationError && !sequenceSettingsError && !invalidLengthPolicy ? { workflow_type: 'shape_blueprint', request: configuredRequest } : null} preloadSelection={DE_NOVO_PRELOAD_SELECTION} />
                            {invalidLengthPolicy && <p role="status">Minimum length must not exceed maximum length.</p>}
                        </ProteinDesignRun>
                    </div>
                </ProteinDesignPanel>
                <section className={`min-w-0 overflow-hidden rounded-2xl border border-[var(--border-primary)] bg-[var(--bg-secondary)] ${section === 'Geometry' ? 'order-first lg:order-none' : ''}`}>
                    <div className="border-b border-[var(--border-primary)] p-4">
                        <div className="flex flex-wrap items-start justify-between gap-3">
                            <div><h2 className="font-semibold text-[var(--text-primary)]">Geometry preview</h2><p className="mt-1 text-xs text-[var(--text-secondary)]">{hasHashBoundSurface ? 'Review the canonical surface or point pool.' : 'Legacy surface is not hash-bound; review the canonical point pool.'}</p></div>
                            <div className="flex rounded-lg border border-[var(--border-primary)] p-1 text-xs">
                                <button type="button" disabled={!hasHashBoundSurface} onClick={() => setReviewMode('surface')} className={`rounded px-3 py-1 disabled:cursor-not-allowed disabled:opacity-40 ${effectiveReviewMode === 'surface' ? 'bg-cyan-600 text-white' : 'text-[var(--text-secondary)]'}`}>Surface</button>
                                <button type="button" onClick={() => setReviewMode('points')} className={`rounded px-3 py-1 ${effectiveReviewMode === 'points' ? 'bg-cyan-600 text-white' : 'text-[var(--text-secondary)]'}`}>Points</button>
                            </div>
                        </div>
                    </div>
                    {selected ? (effectiveReviewMode === 'surface'
                        ? <CanonicalMeshPreview url={`/api/shape-blueprint/geometries/${selected.geometry_id}/preview.obj`} height={430} label="Canonical Shape surface" />
                        : <MolstarViewer structureUrl={`/api/shape-blueprint/geometries/${selected.geometry_id}/points.cif`} format="cif" height={430} label="Canonical Shape point pool" />)
                        : <div className="flex h-[430px] items-center justify-center text-sm text-[var(--text-secondary)]">Select geometry to preview</div>}
                    {selected && <div className="grid gap-2 border-t border-[var(--border-primary)] p-4 text-xs text-[var(--text-secondary)] sm:grid-cols-2">
                        <div>Source <span className="font-semibold text-[var(--text-primary)]">{geometryLabel(selected)}</span> · {selected.source_parser.replaceAll('_', ' ')}</div>
                        <div>Units <span className="font-semibold text-[var(--text-primary)]">{selected.source_unit}</span> · {selected.angstrom_per_unit.toExponential(3)} Å/unit</div>
                        <div className="sm:col-span-2">Dimensions <span className="font-mono text-[var(--text-secondary)]">{selected.dimensions_angstrom.map(formatAngstrom).join(' × ')}</span></div>
                        <details className="sm:col-span-2"><summary>Geometry details</summary>
                        <div>Source bytes <span className="font-mono text-[var(--text-secondary)]" title={selected.source_sha256}>{shortHash(selected.source_sha256)}</span></div>
                        <div>Geometry <span className="font-mono text-[var(--text-secondary)]" title={selected.geometry_sha256}>{shortHash(selected.geometry_sha256)}</span></div>
                        <div>Manifest <span className="font-mono text-[var(--text-secondary)]" title={selected.manifest_sha256}>{shortHash(selected.manifest_sha256)}</span></div>
                        {selected.preview_obj_sha256 && <div>Surface <span className="font-mono text-[var(--text-secondary)]" title={selected.preview_obj_sha256}>{shortHash(selected.preview_obj_sha256)}</span></div>}
                        <div>Points <span className="font-mono text-[var(--text-secondary)]" title={selected.point_pool_sha256}>{shortHash(selected.point_pool_sha256)}</span></div>
                        <div>SDF <span className="font-mono text-[var(--text-secondary)]" title={selected.sdf_sha256}>{shortHash(selected.sdf_sha256)}</span></div>
                        <div>Convention <span className="text-[var(--text-secondary)]">{selected.sdf_sign}</span> • {selected.sdf_grid_shape.join('×')}</div>
                        <div>{selected.vertex_count.toLocaleString()} vertices • {selected.face_count.toLocaleString()} faces</div>
                        <div>{selected.point_count.toLocaleString()} deterministic points</div>
                        </details>
                    </div>}
                    {selectedMaxDimension !== null && (selectedMaxDimension > 1_000 || selectedMaxDimension < 5) && <div className="border-t border-amber-500/20 bg-amber-500/10 p-3 text-xs leading-5 text-[var(--text-secondary)]"><strong>Check mesh scale:</strong> longest dimension is {formatAngstrom(selectedMaxDimension)}. This is unusual for a protein-scale blueprint; confirm the source unit and re-admit if needed.</div>}
                </section>
            </div>
        </div>
    );
}
