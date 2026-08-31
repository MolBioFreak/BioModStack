import { useEffect, useMemo, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';

import { api, assertLocalOnlySubmission, completeCurrentLaunchContext, submitJob } from '../lib/api';
import { ModelDocumentationLinks } from './ModelDocumentationLinks';
import { Gen2StartingStructure } from './Gen2StartingStructure';
import { Gen2StructureSourceSelector, type Gen2StructureSourceTab } from './Gen2StructureSourceSelector';
import { Gen2StructureReviewPanel } from './Gen2StructureReviewPanel';
import { Gen2PredictionReturnBridge, type Gen2PredictionCandidate, type Gen2PredictionPage } from './Gen2PredictionReturnBridge';
import { Gen2WorkflowControl } from './Gen2WorkflowControl';
import {
    applyMolecularDynamicsProfileDefaults,
    buildMolecularDynamicsJobSpec,
    buildMolecularDynamicsLaunchIntent,
    buildMolecularDynamicsPredictionHandoff,
    buildMolecularDynamicsPredictionRoute,
    estimateMolecularDynamicsScope,
    molecularDynamicsRequestedSettings,
    parseMolecularDynamicsChemistryProfileInventory,
    parseMolecularDynamicsHandoffUserSequence,
    parseMolecularDynamicsHandoffUserSequencePage,
    parseMolecularDynamicsLaunchPreview,
    parseMolecularDynamicsPredictionSourceCandidates,
    parseMolecularDynamicsServerFilePage,
    parseMolecularDynamicsStartingStructureInspection,
    resolveMolecularDynamicsCloneSource,
    storeMolecularDynamicsDraft,
    validateMolecularDynamicsChemistryProfile,
    validateMolecularDynamicsForm,
    type MolecularDynamicsChemistryProfile,
    type MolecularDynamicsForm,
    type MolecularDynamicsLaunchIntent,
    type MolecularDynamicsLaunchPreview,
    type MolecularDynamicsServerFilePage,
    type MolecularDynamicsStartingStructureInspection,
    type MolecularDynamicsStartingStructureRef,
} from './molecularDynamicsUiState';

interface MolecularDynamicsTemplateProps {
    onBack: () => void;
    initialValues?: Record<string, unknown>;
    launchContextId?: string | null;
    onOpenStructurePrediction?: (initialValues: Record<string, unknown>) => void;
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
    forceField: 'profile-owned',
    waterModel: 'profile-owned',
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
    neutralize: true,
};

const panelClass = 'rounded-2xl border border-slate-700/80 bg-slate-900/55 p-5 shadow-lg';
const inputClass = 'mt-1 w-full rounded-lg border border-slate-700 bg-slate-950/80 px-3 py-2 text-sm text-slate-100 outline-none transition focus:border-cyan-500 disabled:cursor-not-allowed disabled:opacity-65';
const labelClass = 'block text-xs font-semibold uppercase tracking-[0.12em] text-slate-400';

type SourceMode = 'fixture' | 'rcsb' | 'upload' | 'prediction' | 'design' | 'prior_md_input' | 'server_file';

const ACCEPTED_PREDICTION_PRODUCERS = new Set([
    'esmfold2:predict',
    'boltz2:predict',
    'boltz2:complex',
    'rf3:predict',
    'rf3:complex',
    'protenix:predict',
    'protenix:complex',
]);
const MAX_PREDICTION_CANDIDATE_PAGES = 64;

const displayError = (error: unknown, fallback: string): string => {
    if (error && typeof error === 'object') {
        const response = (error as { response?: { data?: { detail?: unknown } } }).response;
        const detail = response?.data?.detail;
        if (detail && typeof detail === 'object' && 'message' in detail && typeof detail.message === 'string') {
            return detail.message;
        }
        if (typeof detail === 'string') return detail;
    }
    return error instanceof Error ? error.message : fallback;
};

const initialForm = (initialValues?: Record<string, unknown>): MolecularDynamicsForm => {
    const initialSpec = initialValues?.md_job_spec as Record<string, unknown> | undefined;
    const intent = initialValues?.intent as Record<string, unknown> | undefined;
    const requested = intent?.requested_settings as Record<string, unknown> | undefined;
    const stages = initialSpec?.stages as Record<string, Record<string, unknown>> | undefined;
    const preparation = initialSpec?.preparation as Record<string, unknown> | undefined;
    const execution = initialSpec?.execution as Record<string, unknown> | undefined;
    const production = stages?.production;
    const nvt = stages?.nvt;
    const npt = stages?.npt;
    const timestep = Number(production?.timestep_fs ?? DEFAULT_FORM.timestepFs);
    const finite = (value: unknown, fallback: number): number => {
        const parsed = Number(value);
        return Number.isFinite(parsed) ? parsed : fallback;
    };
    const result: MolecularDynamicsForm = {
        ...DEFAULT_FORM,
        jobName: String(initialValues?.name || initialValues?.job_name || DEFAULT_FORM.jobName),
        replicas: finite(requested?.replicas ?? initialSpec?.replicas, DEFAULT_FORM.replicas),
        randomSeed: finite(requested?.random_seed ?? initialSpec?.random_seed, DEFAULT_FORM.randomSeed),
        paddingNm: finite(requested?.padding_nm ?? preparation?.padding_nm, DEFAULT_FORM.paddingNm),
        saltMolar: finite(requested?.salt_molar ?? preparation?.salt_molar, DEFAULT_FORM.saltMolar),
        neutralize: requested?.neutralize !== undefined ? requested.neutralize !== false : preparation?.neutralize !== false,
        temperatureK: finite(requested?.temperature_k ?? production?.temperature_k, DEFAULT_FORM.temperatureK),
        pressureBar: finite(requested?.pressure_bar ?? production?.pressure_bar, DEFAULT_FORM.pressureBar),
        timestepFs: finite(requested?.timestep_fs, timestep),
        minimizationSteps: finite(requested?.minimization_steps ?? stages?.minimization?.steps, DEFAULT_FORM.minimizationSteps),
        nvtPs: finite(requested?.nvt_ps, finite(nvt?.steps, (DEFAULT_FORM.nvtPs * 1000) / timestep) * timestep / 1000),
        nptPs: finite(requested?.npt_ps, finite(npt?.steps, (DEFAULT_FORM.nptPs * 1000) / timestep) * timestep / 1000),
        productionNs: finite(requested?.production_ns, finite(production?.steps, (DEFAULT_FORM.productionNs * 1_000_000) / timestep) * timestep / 1_000_000),
        trajectoryIntervalPs: finite(requested?.trajectory_interval_ps, finite(production?.trajectory_interval_steps, (DEFAULT_FORM.trajectoryIntervalPs * 1000) / timestep) * timestep / 1000),
        energyIntervalPs: finite(requested?.energy_interval_ps, finite(production?.energy_interval_steps, (DEFAULT_FORM.energyIntervalPs * 1000) / timestep) * timestep / 1000),
        checkpointIntervalMinutes: finite(requested?.checkpoint_interval_minutes ?? production?.checkpoint_interval_minutes, DEFAULT_FORM.checkpointIntervalMinutes),
        ntomp: finite(requested?.ntomp ?? execution?.ntomp, DEFAULT_FORM.ntomp),
    };
    const draft = initialValues?.md_form;
    if (draft && typeof draft === 'object' && !Array.isArray(draft)) {
        const record = draft as Record<string, unknown>;
        (Object.keys(result) as Array<keyof MolecularDynamicsForm>).forEach((key) => {
            const value = record[key];
            if (typeof value === typeof result[key]) (result as unknown as Record<string, unknown>)[key] = value;
        });
        result.structurePath = '';
        result.coordinatesPath = '';
        result.topologyPath = '';
    }
    return result;
};

function SectionTitle({ children, note }: { children: string; note?: string }) {
    return <div className="mb-4"><h2 className="text-base font-semibold text-slate-100">{children}</h2>{note && <p className="mt-1 text-xs text-slate-500">{note}</p>}</div>;
}

function NumberField({
    label, value, onChange, min, max, step = 1, unit, description, fixed = false, slider = false, setting,
}: {
    label: string;
    value: number;
    onChange: (value: number) => void;
    min?: number;
    max?: number;
    step?: number;
    unit?: string;
    description?: string;
    fixed?: boolean;
    slider?: boolean;
    setting?: string;
}) {
    const number = (
        <input
            className={inputClass}
            type="number"
            value={value}
            min={min}
            max={max}
            step={step}
            disabled={fixed}
            data-md-setting={setting}
            onChange={(event) => onChange(Number(event.target.value))}
        />
    );
    return (
        <label className={labelClass}>
            <span className="flex items-center justify-between gap-2"><span>{label}{unit ? ` (${unit})` : ''}</span>{fixed && <span className="normal-case tracking-normal text-cyan-300">Fixed by profile</span>}</span>
            {slider && min !== undefined && max !== undefined ? (
                <div className="grid grid-cols-[minmax(0,1fr)_7rem] items-center gap-3">
                    <input
                        className="mt-2 w-full accent-cyan-400"
                        type="range"
                        value={value}
                        min={min}
                        max={max}
                        step={step}
                        disabled={fixed}
                        aria-label={`${label} slider`}
                        onChange={(event) => onChange(Number(event.target.value))}
                    />
                    {number}
                </div>
            ) : number}
            {description && <span className="mt-1 block normal-case tracking-normal text-[11px] font-normal text-slate-500">{description}</span>}
        </label>
    );
}

export function MolecularDynamicsTemplate({
    onBack,
    initialValues,
    launchContextId = null,
}: MolecularDynamicsTemplateProps) {
    const navigate = useNavigate();
    const queryClient = useQueryClient();
    const chemistryCatalogQuery = useQuery({
        queryKey: ['molecular-dynamics', 'chemistry-profiles'],
        queryFn: async () => parseMolecularDynamicsChemistryProfileInventory(
            (await api.get<unknown>('/api/molecular-dynamics/chemistry-profiles', { timeout: 10_000 })).data,
        ),
        staleTime: 30_000,
        retry: (failureCount, error) => !String(error).includes('Invalid chemistry profile inventory response') && failureCount < 1,
    });
    const [form, setForm] = useState<MolecularDynamicsForm>(() => initialForm(initialValues));
    const initialSpec = initialValues?.md_job_spec as Record<string, unknown> | undefined;
    const initialChemistry = initialSpec?.chemistry as Record<string, unknown> | undefined;
    const initialPreparation = initialSpec?.preparation as Record<string, unknown> | undefined;
    const initialIntent = initialValues?.intent as Record<string, unknown> | undefined;
    const routedPredictionJobId = typeof initialValues?.source_prediction_job_id === 'string'
        ? initialValues.source_prediction_job_id
        : '';
    const routedDesignId = typeof initialValues?.source_design_id === 'string'
        ? initialValues.source_design_id
        : '';
    const isResultsViewerDesignHandoff = Boolean(routedPredictionJobId && routedDesignId);
    const cloneSource = useMemo(
        () => isResultsViewerDesignHandoff ? null : resolveMolecularDynamicsCloneSource(initialValues || {}),
        [initialValues, isResultsViewerDesignHandoff],
    );
    const [selectedProfileId, setSelectedProfileId] = useState(String(initialIntent?.chemistry_profile_id || initialChemistry?.profile_id || initialPreparation?.chemistry_profile_id || ''));
    const [selectedProfileDigest, setSelectedProfileDigest] = useState(String(initialIntent?.chemistry_profile_sha256 || initialChemistry?.profile_sha256 || initialPreparation?.chemistry_profile_sha256 || ''));
    const returnedPredictionJobId = cloneSource?.kind === 'design' ? '' : routedPredictionJobId;
    const [sourceMode, setSourceMode] = useState<SourceMode>(cloneSource?.kind === 'design' ? 'design' : returnedPredictionJobId ? 'prediction' : 'rcsb');
    const [inspection, setInspection] = useState<MolecularDynamicsStartingStructureInspection | null>(null);
    const [admissionAuthority, setAdmissionAuthority] = useState<{
        sourceSha256: string;
        profileId: string;
        profileDigest: string;
    } | null>(null);
    const inspectionRequestGenerationRef = useRef(0);
    const activeInspectionShaRef = useRef<string | null>(null);
    const [viewerState, setViewerState] = useState<'idle' | 'loading' | 'loaded' | 'failed'>('idle');
    const [viewerLoadedSha256, setViewerLoadedSha256] = useState<string | null>(null);
    const [viewerError, setViewerError] = useState('');
    const [promotedSha256, setPromotedSha256] = useState<string | null>(null);
    const [sourceBusy, setSourceBusy] = useState(false);
    const [sourceError, setSourceError] = useState('');
    const [rcsbId, setRcsbId] = useState('');
    const [uploadFile, setUploadFile] = useState<File | null>(null);
    const [predictionJobId, setPredictionJobId] = useState(returnedPredictionJobId);
    const loadedReturnedPredictionRef = useRef<string | null>(null);
    const [predictionPage, setPredictionPage] = useState<Gen2PredictionPage | null>(null);
    const [selectedPredictionCandidate, setSelectedPredictionCandidate] = useState<Gen2PredictionCandidate | null>(null);
    const [sequence, setSequence] = useState('');
    const [sequenceName, setSequenceName] = useState('MD candidate');
    const [sequenceSource, setSequenceSource] = useState<'new' | 'saved'>('new');
    const [savedSequenceId, setSavedSequenceId] = useState('');
    const [designId, setDesignId] = useState(cloneSource?.kind === 'design' ? cloneSource.id : '');
    const [priorMdJobId, setPriorMdJobId] = useState(cloneSource?.kind === 'prior_md_input' ? cloneSource.id : '');
    const [serverSearch, setServerSearch] = useState('');
    const [preview, setPreview] = useState<MolecularDynamicsLaunchPreview | null>(null);
    const [previewAuthorityIdentity, setPreviewAuthorityIdentity] = useState<string | null>(null);
    const [previewRequestAuthorityIdentity, setPreviewRequestAuthorityIdentity] = useState<string | null>(null);
    const previewRequestGenerationRef = useRef(0);
    const [isPreviewing, setIsPreviewing] = useState(false);
    const [isSubmitting, setIsSubmitting] = useState(false);
    const [submitError, setSubmitError] = useState('');
    const [showAdvanced, setShowAdvanced] = useState(false);

    const chemistryProfiles = useMemo(
        () => chemistryCatalogQuery.data?.profiles ?? [],
        [chemistryCatalogQuery.data?.profiles],
    );
    const selectedProfile = chemistryProfiles.find((profile) => profile.id === selectedProfileId);
    const profileDigestIsStale = Boolean(selectedProfile && selectedProfile.profile_sha256 !== selectedProfileDigest);
    const constraints = selectedProfile?.launch_constraints;
    const currentPreviewAuthorityIdentity = useMemo(() => JSON.stringify({
        source_ref: inspection?.source_ref ?? null,
        expected_source_sha256: inspection?.identity.sha256 ?? null,
        admission: admissionAuthority,
        inspection_admission: inspection?.admission ?? null,
        viewer_state: viewerState,
        viewer_loaded_sha256: viewerLoadedSha256,
        promoted_sha256: promotedSha256,
        chemistry_profile_id: selectedProfileId,
        chemistry_profile_sha256: selectedProfile?.profile_sha256 ?? null,
        selected_profile_digest: selectedProfileDigest,
        catalog_digest: chemistryCatalogQuery.data?.catalog_digest ?? null,
        name: form.jobName,
        requested_settings: molecularDynamicsRequestedSettings(form),
        launch_context_id: launchContextId,
    }), [
        admissionAuthority,
        chemistryCatalogQuery.data?.catalog_digest,
        form,
        inspection,
        launchContextId,
        promotedSha256,
        selectedProfile,
        selectedProfileDigest,
        selectedProfileId,
        viewerLoadedSha256,
        viewerState,
    ]);
    const currentPreviewAuthorityIdentityRef = useRef(currentPreviewAuthorityIdentity);
    currentPreviewAuthorityIdentityRef.current = currentPreviewAuthorityIdentity;

    const serverFilesQuery = useQuery({
        queryKey: ['molecular-dynamics', 'starting-structures', 'server-files', serverSearch],
        queryFn: async (): Promise<MolecularDynamicsServerFilePage> => parseMolecularDynamicsServerFilePage(
            (await api.get<unknown>('/api/molecular-dynamics/starting-structures/server-files', { params: { search: serverSearch, limit: 24 } })).data,
        ),
        enabled: sourceMode === 'server_file',
        retry: false,
    });
    const savedSequencesQuery = useQuery({
        queryKey: ['molecular-dynamics', 'user-sequences'],
        queryFn: async () => parseMolecularDynamicsHandoffUserSequencePage(
            (await api.get<unknown>('/api/user-sequences', { params: { limit: 100, offset: 0 } })).data,
        ),
        enabled: sourceMode === 'prediction',
        retry: false,
    });

    const invalidatePreview = () => {
        previewRequestGenerationRef.current += 1;
        setPreview(null);
        setPreviewAuthorityIdentity(null);
        setPreviewRequestAuthorityIdentity(null);
        setIsPreviewing(false);
        setSubmitError('');
    };
    const update = <K extends keyof MolecularDynamicsForm>(key: K, value: MolecularDynamicsForm[K]) => {
        invalidatePreview();
        setForm((current) => ({ ...current, [key]: value }));
    };

    useEffect(() => {
        previewRequestGenerationRef.current += 1;
        setPreview(null);
        setPreviewAuthorityIdentity(null);
        setPreviewRequestAuthorityIdentity(null);
        setIsPreviewing(false);
        setSubmitError('');
    }, [currentPreviewAuthorityIdentity]);

    useEffect(() => {
        if (!selectedProfile || profileDigestIsStale) return;
        setForm((current) => applyMolecularDynamicsProfileDefaults(current, selectedProfile));
    }, [profileDigestIsStale, selectedProfile]);

    const inspectSource = async (
        sourceRef: MolecularDynamicsStartingStructureRef,
        chemistryProfile: MolecularDynamicsChemistryProfile | undefined = selectedProfile,
    ) => {
        const requestGeneration = ++inspectionRequestGenerationRef.current;
        const previousInspection = inspection;
        const sameSource = previousInspection?.source_ref.kind === sourceRef.kind
            && previousInspection.source_ref.id === sourceRef.id;
        setSourceBusy(true);
        setSourceError('');
        setAdmissionAuthority(null);
        activeInspectionShaRef.current = null;
        if (!sameSource) {
            setInspection(null);
            setViewerState('loading');
            setViewerLoadedSha256(null);
            setViewerError('');
            setPromotedSha256(null);
        }
        invalidatePreview();
        try {
            const result = await api.post<unknown>(
                '/api/molecular-dynamics/starting-structures/inspect',
                { source_ref: sourceRef, chemistry_profile_id: chemistryProfile?.id ?? null },
            );
            if (requestGeneration !== inspectionRequestGenerationRef.current) return;
            const parsed = parseMolecularDynamicsStartingStructureInspection(result.data, sourceRef, chemistryProfile?.id);
            if (previousInspection && previousInspection.identity.sha256 !== parsed.identity.sha256) {
                setViewerState('loading');
                setViewerLoadedSha256(null);
                setViewerError('');
                setPromotedSha256(null);
            }
            activeInspectionShaRef.current = parsed.identity.sha256;
            setInspection(parsed);
            setAdmissionAuthority(parsed.admission.state === 'admitted' && chemistryProfile
                ? {
                    sourceSha256: parsed.identity.sha256,
                    profileId: chemistryProfile.id,
                    profileDigest: chemistryProfile.profile_sha256,
                }
                : null);
        } catch (error) {
            if (requestGeneration === inspectionRequestGenerationRef.current) {
                setSourceError(displayError(error, 'Starting-structure inspection failed.'));
            }
        } finally {
            if (requestGeneration === inspectionRequestGenerationRef.current) {
                setSourceBusy(false);
            }
        }
    };

    const selectProfile = (profile: MolecularDynamicsChemistryProfile) => {
        setSelectedProfileId(profile.id);
        setSelectedProfileDigest(profile.profile_sha256);
        setForm((current) => applyMolecularDynamicsProfileDefaults(current, profile));
        invalidatePreview();
        if (inspection && promotedSha256 === inspection.identity.sha256) {
            void inspectSource(inspection.source_ref, profile);
        }
    };

    useEffect(() => {
        if (!cloneSource || !chemistryCatalogQuery.data || inspection || sourceBusy) return;
        setSourceMode(cloneSource.kind === 'design' ? 'design' : 'prior_md_input');
        void inspectSource(cloneSource, selectedProfile);
        // The clone source is immutable and should be admitted exactly once after catalog hydration.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [chemistryCatalogQuery.data, cloneSource, selectedProfile]);

    const uploadAndInspect = async () => {
        if (!uploadFile) return;
        const requestGeneration = ++inspectionRequestGenerationRef.current;
        setSourceBusy(true);
        setSourceError('');
        setInspection(null);
        setAdmissionAuthority(null);
        activeInspectionShaRef.current = null;
        setViewerState('loading');
        setViewerLoadedSha256(null);
        setViewerError('');
        setPromotedSha256(null);
        invalidatePreview();
        const body = new FormData();
        body.append('file', uploadFile);
        try {
            const uploaded = await api.post<unknown>('/api/molecular-dynamics/starting-structures/upload', body);
            if (requestGeneration !== inspectionRequestGenerationRef.current) return;
            const uploadedInspection = parseMolecularDynamicsStartingStructureInspection(uploaded.data);
            const inspected = await api.post<unknown>(
                '/api/molecular-dynamics/starting-structures/inspect',
                { source_ref: uploadedInspection.source_ref, chemistry_profile_id: selectedProfile?.id ?? null },
            );
            if (requestGeneration !== inspectionRequestGenerationRef.current) return;
            const parsed = parseMolecularDynamicsStartingStructureInspection(inspected.data, uploadedInspection.source_ref, selectedProfile?.id);
            activeInspectionShaRef.current = parsed.identity.sha256;
            setInspection(parsed);
            setAdmissionAuthority(parsed.admission.state === 'admitted' && selectedProfile
                ? {
                    sourceSha256: parsed.identity.sha256,
                    profileId: selectedProfile.id,
                    profileDigest: selectedProfile.profile_sha256,
                }
                : null);
        } catch (error) {
            if (requestGeneration === inspectionRequestGenerationRef.current) {
                setSourceError(displayError(error, 'Starting-structure upload failed.'));
            }
        } finally {
            if (requestGeneration === inspectionRequestGenerationRef.current) {
                setSourceBusy(false);
            }
        }
    };

    const loadPredictionCandidates = async (targetDesignId?: string) => {
        const normalizedJobId = predictionJobId.trim();
        setSourceBusy(true);
        setSourceError('');
        setPredictionPage(null);
        setSelectedPredictionCandidate(null);
        try {
            let cursor: string | null = null;
            let expectedJobProjection: string | null = null;
            const seenCursors = new Set<string>();
            for (let pageNumber = 0; pageNumber < MAX_PREDICTION_CANDIDATE_PAGES; pageNumber += 1) {
                const cursorKey = cursor ?? '__first_page__';
                if (seenCursors.has(cursorKey)) {
                    throw new Error('Prediction candidate pagination repeated a cursor; the ResultsViewer handoff is blocked.');
                }
                seenCursors.add(cursorKey);
                const result = await api.get<unknown>(
                    `/api/molecular-dynamics/prediction-jobs/${normalizedJobId}/source-candidates`,
                    { params: cursor ? { limit: 24, cursor } : { limit: 24 } },
                );
                const page = parseMolecularDynamicsPredictionSourceCandidates(result.data, normalizedJobId);
                if (!targetDesignId) {
                    setPredictionPage(page);
                    return;
                }
                if (page.job.status !== 'completed') {
                    throw new Error('The supplied Structure Prediction Job is not completed; the ResultsViewer Design handoff is blocked.');
                }
                if (!ACCEPTED_PREDICTION_PRODUCERS.has(`${page.job.model_id}:${page.job.mode}`)) {
                    throw new Error('The supplied Job is not an accepted Structure Prediction producer.');
                }
                const jobProjection = JSON.stringify(page.job);
                if (expectedJobProjection !== null && jobProjection !== expectedJobProjection) {
                    throw new Error('The prediction Job projection changed during candidate pagination.');
                }
                expectedJobProjection = jobProjection;
                const exactCandidate = page.candidates.find((candidate) => candidate.source_ref.id === targetDesignId);
                if (exactCandidate) {
                    if (!exactCandidate.eligible) {
                        throw new Error('The supplied Design is not eligible as a Molecular Dynamics starting structure.');
                    }
                    setPredictionPage({ ...page, candidates: [exactCandidate], next_cursor: null });
                    setSelectedPredictionCandidate(exactCandidate);
                    await inspectSource(exactCandidate.source_ref);
                    return;
                }
                if (!page.next_cursor) {
                    throw new Error('The supplied Design does not belong to the supplied prediction Job.');
                }
                cursor = page.next_cursor;
            }
            throw new Error('Prediction candidate pagination exceeded the bounded ResultsViewer handoff limit.');
        } catch (error) {
            setPredictionPage(null);
            setSelectedPredictionCandidate(null);
            setSourceError(displayError(error, 'Prediction candidates could not be loaded.'));
        } finally {
            setSourceBusy(false);
        }
    };

    useEffect(() => {
        if (!returnedPredictionJobId) return;
        const routeGeneration = `${returnedPredictionJobId}:${routedDesignId}`;
        if (loadedReturnedPredictionRef.current === routeGeneration) return;
        loadedReturnedPredictionRef.current = routeGeneration;
        setSourceMode('prediction');
        void loadPredictionCandidates(routedDesignId || undefined);
        // The returned Job and optional exact Design IDs are immutable for this mounted route generation.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [returnedPredictionJobId, routedDesignId]);

    const openPrediction = async () => {
        try {
            let sequenceId = savedSequenceId;
            if (sequenceSource === 'saved') {
                const selected = savedSequencesQuery.data?.find((candidate) => candidate.id === savedSequenceId);
                if (!selected) throw new Error('Select one available saved sequence before opening Structure Prediction.');
                sequenceId = selected.id;
            } else {
                const values = buildMolecularDynamicsPredictionHandoff(sequence, sequenceName);
                const saved = await api.post<unknown>('/api/user-sequences', {
                    name: values.sequence_name,
                    sequence: values.sequence,
                });
                sequenceId = parseMolecularDynamicsHandoffUserSequence(saved.data).id;
            }
            const draftId = crypto.randomUUID();
            storeMolecularDynamicsDraft(sessionStorage, draftId, {
                form: {
                    ...form,
                    structurePath: undefined,
                    coordinatesPath: undefined,
                    topologyPath: undefined,
                },
                selectedProfileId,
                selectedProfileDigest,
            });
            navigate(buildMolecularDynamicsPredictionRoute(sequenceId, draftId));
        } catch (error) {
            setSourceError(displayError(error, 'Structure Prediction handoff failed.'));
        }
    };

    const formErrors = useMemo(() => {
        if (form.inputMode === 'prepared') return validateMolecularDynamicsForm(form);
        const chemistryErrors = chemistryCatalogQuery.isPending
            ? ['Loading the deployed chemistry profile catalog.']
            : chemistryCatalogQuery.isError
                ? ['The deployed chemistry profile catalog is unavailable; launch is blocked.']
                : !selectedProfileId
                    ? ['Select a chemistry profile after promoting a starting structure.']
                    : validateMolecularDynamicsChemistryProfile(selectedProfile, 'gromacs', true, selectedProfileDigest);
        const typedForm = { ...form, engine: 'gromacs' as const, structurePath: inspection ? 'typed-source' : '' };
        return [...validateMolecularDynamicsForm(typedForm, selectedProfile), ...chemistryErrors];
    }, [chemistryCatalogQuery.isError, chemistryCatalogQuery.isPending, form, inspection, selectedProfile, selectedProfileDigest, selectedProfileId]);
    const scope = useMemo(() => estimateMolecularDynamicsScope(form), [form]);

    const typedReady = form.inputMode === 'structure'
        && formErrors.length === 0
        && !sourceBusy
        && inspection?.admission.state === 'admitted'
        && inspection.admission.profile_id === selectedProfileId
        && admissionAuthority?.sourceSha256 === inspection.identity.sha256
        && admissionAuthority.profileId === selectedProfileId
        && admissionAuthority.profileDigest === selectedProfileDigest
        && selectedProfile?.id === selectedProfileId
        && selectedProfile.profile_sha256 === selectedProfileDigest
        && viewerState === 'loaded'
        && viewerLoadedSha256 === inspection?.identity.sha256
        && promotedSha256 === inspection?.identity.sha256
        && !profileDigestIsStale;
    const previewIsCurrent = Boolean(
        typedReady
        && preview
        && previewAuthorityIdentity === currentPreviewAuthorityIdentity,
    );
    const previewRequestIsCurrent = isPreviewing
        && previewRequestAuthorityIdentity === currentPreviewAuthorityIdentity;

    const intent = (): MolecularDynamicsLaunchIntent => {
        if (!inspection || !selectedProfile || !chemistryCatalogQuery.data) throw new Error('Inspect and admit one starting structure before preview.');
        return buildMolecularDynamicsLaunchIntent({
            form: { ...form, engine: 'gromacs', inputMode: 'structure' },
            source: inspection,
            profile: selectedProfile,
            catalogDigest: chemistryCatalogQuery.data.catalog_digest,
            launchContextId,
        });
    };

    const previewLaunch = async () => {
        const requestAuthorityIdentity = currentPreviewAuthorityIdentity;
        const requestGeneration = ++previewRequestGenerationRef.current;
        setPreview(null);
        setPreviewAuthorityIdentity(null);
        setPreviewRequestAuthorityIdentity(requestAuthorityIdentity);
        setSubmitError('');
        setIsPreviewing(true);
        try {
            const launchIntent = intent();
            const result = await api.post<unknown>('/api/molecular-dynamics/launch-preview', {
                schema_version: 'bms.md.launch-preview-request.v1',
                intent: launchIntent,
            });
            if (requestGeneration !== previewRequestGenerationRef.current
                || requestAuthorityIdentity !== currentPreviewAuthorityIdentityRef.current) return;
            setPreview(parseMolecularDynamicsLaunchPreview(result.data, launchIntent));
            setPreviewAuthorityIdentity(requestAuthorityIdentity);
        } catch (error) {
            if (requestGeneration === previewRequestGenerationRef.current
                && requestAuthorityIdentity === currentPreviewAuthorityIdentityRef.current) {
                setPreview(null);
                setPreviewAuthorityIdentity(null);
                setSubmitError(displayError(error, 'Molecular Dynamics preview failed.'));
            }
        } finally {
            if (requestGeneration === previewRequestGenerationRef.current
                && requestAuthorityIdentity === currentPreviewAuthorityIdentityRef.current) {
                setIsPreviewing(false);
                setPreviewRequestAuthorityIdentity(null);
            }
        }
    };

    const launchTyped = async () => {
        if (!preview || !previewIsCurrent) return;
        setSubmitError('');
        setIsSubmitting(true);
        try {
            assertLocalOnlySubmission('Molecular Dynamics');
            const result = await api.post('/api/molecular-dynamics/launch', {
                schema_version: 'bms.md.launch-request.v1',
                intent: intent(),
                preview_digest: preview.preview_digest,
            });
            await queryClient.invalidateQueries({ queryKey: ['jobs'] });
            navigate(await completeCurrentLaunchContext(result.data) ?? '/');
        } catch (error) {
            setSubmitError(displayError(error, 'Molecular Dynamics launch failed.'));
        } finally {
            setIsSubmitting(false);
        }
    };

    const launchPreparedCompatibility = async () => {
        setSubmitError('');
        setIsSubmitting(true);
        try {
            const spec = buildMolecularDynamicsJobSpec(form);
            const result = await submitJob({ name: form.jobName.trim(), model_id: 'molecular_dynamics', mode: 'simulate', params: { md_job_spec: spec } });
            await queryClient.invalidateQueries({ queryKey: ['jobs'] });
            navigate(await completeCurrentLaunchContext(result.data) ?? '/');
        } catch (error) {
            setSubmitError(displayError(error, 'Prepared-system compatibility launch failed.'));
        } finally {
            setIsSubmitting(false);
        }
    };

    const activeInspectionSha256 = inspection?.identity.sha256 ?? null;
    const handleViewerLoadState = useMemo(() => {
        const generationSha256 = activeInspectionSha256;
        return (state: 'loading' | 'loaded' | 'failed', errorMessage?: string) => {
            if (!generationSha256 || activeInspectionShaRef.current !== generationSha256) return;
            setViewerState(state);
            setViewerLoadedSha256(state === 'loaded' ? generationSha256 : null);
            setViewerError(state === 'failed' ? (errorMessage ?? 'Mol* could not display this structure.') : '');
            if (state !== 'loaded') setPromotedSha256(null);
        };
    }, [activeInspectionSha256]);
    const viewerCurrent = viewerState === 'loaded' && viewerLoadedSha256 === activeInspectionSha256;
    const activeSourceTab: Gen2StructureSourceTab = sourceMode === 'rcsb'
        ? 'rcsb'
        : sourceMode === 'upload'
            ? 'upload'
            : sourceMode === 'fixture'
                ? 'samples'
                : 'runs';
    const changeSourceTab = (tab: Gen2StructureSourceTab) => {
        invalidatePreview();
        setSourceMode(tab === 'samples' ? 'fixture' : tab === 'runs' ? 'prediction' : tab);
        setSourceError('');
    };

    return (
        <div className="mx-auto w-full space-y-5" data-bms-md-launcher="gen2">
            <header className="flex flex-wrap items-start justify-between gap-4">
                <div>
                    <button type="button" onClick={onBack} className="mb-3 rounded-lg border border-slate-700 px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-800">← Back to workflows</button>
                    <div className="flex items-center gap-3"><h1 className="text-2xl font-bold text-slate-100">Molecular Dynamics</h1><span className="rounded-full border border-orange-400/30 bg-orange-500/10 px-2 py-1 text-[10px] font-semibold uppercase tracking-wider text-orange-300">Experimental alpha</span></div>
                    <p className="mt-2 max-w-3xl text-sm text-slate-400">Choose immutable starting coordinates first, prove exact-byte profile admission, then preview the server-compiled GROMACS request. A static prediction is a starting hypothesis—not molecular dynamics.</p>
                </div>
                <ModelDocumentationLinks topics={['gromacs', 'openmm']} title="MD references" compact summary="Product scope, engine references, and chemistry limits remain visible before launch." />
            </header>
            <Gen2StartingStructure returned={Boolean(returnedPredictionJobId && predictionPage)} />

            <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_22rem]">
                <div className="space-y-5">
                    <section className={panelClass}>
                        <SectionTitle children="Choose a starting structure" note="Discovery, immutable inspection, exact profile admission, and Job materialization remain separate." />
                        <label className={labelClass}>Job name<input className={inputClass} value={form.jobName} onChange={(event) => update('jobName', event.target.value)} /></label>
                        <div className="mt-4"><Gen2StructureSourceSelector active={activeSourceTab} onChange={changeSourceTab} /></div>
                        {sourceMode !== 'prediction' && <button type="button" onClick={() => setSourceMode('prediction')} className="mt-4 w-full rounded-xl border border-violet-500/30 bg-violet-500/10 p-4 text-left"><span className="block text-sm font-semibold text-violet-100">Predict structure from sequence</span><span className="mt-1 block text-xs text-violet-200/70">Sequence is a prerequisite path and opens the complete Structure Prediction workflow.</span></button>}

                        <div className="mt-4 grid gap-4 xl:grid-cols-2">
                            <div className="rounded-xl border border-slate-800 bg-slate-950/45 p-4">
                                {sourceMode === 'fixture' && <div><h3 className="text-sm font-semibold text-slate-200">Verified managed fixture</h3><p className="mt-2 text-xs text-slate-500">RCSB 1AKI exact product bytes. Admission remains profile-specific and limited to the profile's declared validation scope.</p><button type="button" disabled={sourceBusy} onClick={() => void inspectSource({ kind: 'managed_fixture', id: '1aki-admitted-v1' })} className="mt-4 rounded-lg bg-cyan-500 px-3 py-2 text-sm font-semibold text-slate-950 disabled:opacity-50">Use verified 1AKI fixture</button></div>}
                                {sourceMode === 'rcsb' && <div><label className={labelClass}>RCSB accession<input className={inputClass} value={rcsbId} maxLength={4} placeholder="1AKI" onChange={(event) => setRcsbId(event.target.value.toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 4))} /></label><button type="button" disabled={sourceBusy || rcsbId.length !== 4} onClick={() => void inspectSource({ kind: 'rcsb', id: rcsbId })} className="mt-3 rounded-lg bg-cyan-500 px-3 py-2 text-sm font-semibold text-slate-950 disabled:opacity-50">Retrieve and inspect</button></div>}
                                {sourceMode === 'upload' && <div><label className={labelClass}>PDB or mmCIF file<input className={`${inputClass} file:mr-3 file:rounded file:border-0 file:bg-cyan-500/15 file:px-2 file:py-1 file:text-cyan-200`} type="file" accept=".pdb,.cif,.mmcif" onChange={(event) => setUploadFile(event.target.files?.[0] || null)} /></label><button type="button" disabled={sourceBusy || !uploadFile} onClick={() => void uploadAndInspect()} className="mt-3 rounded-lg bg-cyan-500 px-3 py-2 text-sm font-semibold text-slate-950 disabled:opacity-50">Upload immutable bytes</button></div>}
                                {sourceMode === 'prediction' && <div className="space-y-4">
                                    <div>
                                        <h3 className="text-sm font-semibold text-slate-200">Predict structure from sequence</h3>
                                        <p className="mt-1 text-xs text-slate-500">Open the canonical Structure Prediction workflow; predictor choice and model-native settings remain there.</p>
                                        <div className="mt-3 flex gap-2">
                                            <button type="button" aria-pressed={sequenceSource === 'new'} onClick={() => setSequenceSource('new')} className="rounded-lg border border-slate-700 px-3 py-2 text-xs text-slate-200">Enter new sequence</button>
                                            <button type="button" aria-pressed={sequenceSource === 'saved'} onClick={() => setSequenceSource('saved')} className="rounded-lg border border-slate-700 px-3 py-2 text-xs text-slate-200">Use saved sequence</button>
                                        </div>
                                        {sequenceSource === 'new' ? <>
                                            <label className={`${labelClass} mt-3`}>Sequence name<input className={inputClass} value={sequenceName} onChange={(event) => setSequenceName(event.target.value)} /></label>
                                            <label className={`${labelClass} mt-3`}>Protein sequence<textarea className={`${inputClass} min-h-24 font-mono`} value={sequence} onChange={(event) => setSequence(event.target.value)} /></label>
                                        </> : <label className={`${labelClass} mt-3`}>Saved sequence<select data-md-saved-sequence className={inputClass} value={savedSequenceId} disabled={savedSequencesQuery.isPending || savedSequencesQuery.isError} onChange={(event) => setSavedSequenceId(event.target.value)}><option value="">Select a saved sequence</option>{savedSequencesQuery.data?.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.length} aa</option>)}</select></label>}
                                        <button type="button" onClick={() => void openPrediction()} className="mt-3 rounded-lg border border-cyan-500/40 px-3 py-2 text-sm text-cyan-200">Open Structure Prediction</button>
                                    </div>
                                    <div className="border-t border-slate-800 pt-4"><label className={labelClass}>Completed prediction Job ID<input className={inputClass} value={predictionJobId} onChange={(event) => setPredictionJobId(event.target.value)} /></label><button type="button" disabled={sourceBusy || !predictionJobId.trim()} onClick={() => void loadPredictionCandidates()} className="mt-3 rounded-lg bg-cyan-500 px-3 py-2 text-sm font-semibold text-slate-950 disabled:opacity-50">Load prediction candidates</button></div>
                                </div>}
                                {sourceMode === 'design' && <div><label className={labelClass}>Completed Design ID<input className={inputClass} value={designId} onChange={(event) => setDesignId(event.target.value)} /></label><button type="button" disabled={sourceBusy || !designId.trim()} onClick={() => void inspectSource({ kind: 'design', id: designId.trim() })} className="mt-3 rounded-lg bg-cyan-500 px-3 py-2 text-sm font-semibold text-slate-950 disabled:opacity-50">Inspect Design</button></div>}
                                {sourceMode === 'prior_md_input' && <div><label className={labelClass}>Prior MD Job ID<input className={inputClass} value={priorMdJobId} onChange={(event) => setPriorMdJobId(event.target.value)} /></label><p className="mt-2 text-xs text-slate-500">Reopen the prior Job-owned immutable input; browser-visible host paths are never reused.</p><button type="button" disabled={sourceBusy || !priorMdJobId.trim()} onClick={() => void inspectSource({ kind: 'prior_md_input', id: priorMdJobId.trim() })} className="mt-3 rounded-lg bg-cyan-500 px-3 py-2 text-sm font-semibold text-slate-950 disabled:opacity-50">Inspect prior MD input</button></div>}
                                {sourceMode === 'server_file' && <div><label className={labelClass}>Search governed server files<input className={inputClass} value={serverSearch} onChange={(event) => setServerSearch(event.target.value)} /></label>{serverFilesQuery.isError && <p className="mt-3 text-xs text-amber-300">The policy-controlled server-file browser is unavailable.</p>}<div className="mt-3 max-h-64 space-y-2 overflow-auto">{serverFilesQuery.data?.items.map((item) => <button key={item.id} type="button" onClick={() => void inspectSource({ kind: 'server_file', id: item.id })} className="block w-full rounded border border-slate-700 p-2 text-left text-xs text-slate-300"><span className="font-semibold">{item.label}</span><span className="ml-2 text-slate-500">{item.format.toUpperCase()} · {item.bytes.toLocaleString()} bytes</span></button>)}</div></div>}
                                {sourceMode === 'prediction' && predictionPage && <Gen2PredictionReturnBridge
                                    page={predictionPage}
                                    selectedId={selectedPredictionCandidate?.source_ref.id ?? null}
                                    onSelect={(candidate) => {
                                        setSelectedPredictionCandidate(candidate);
                                        void inspectSource(candidate.source_ref);
                                    }}
                                    onRunAnother={() => { setPredictionPage(null); setPredictionJobId(''); setSelectedPredictionCandidate(null); }}
                                />}
                                {sourceBusy && <p className="mt-3 text-xs text-cyan-300">Inspecting immutable starting-structure bytes…</p>}
                                {sourceError && <p role="alert" className="mt-3 rounded border border-red-500/30 bg-red-500/10 p-2 text-xs text-red-200">{sourceError}</p>}
                            </div>

                            <Gen2StructureReviewPanel
                                inspection={inspection}
                                viewerState={viewerState}
                                viewerError={viewerError}
                                viewerCurrent={viewerCurrent}
                                promoted={promotedSha256 === inspection?.identity.sha256}
                                actionLabel={selectedPredictionCandidate && inspection?.source_ref.id === selectedPredictionCandidate.source_ref.id ? 'Use selected Design for MD' : 'Use this structure'}
                                onLoadStateChange={handleViewerLoadState}
                                onPromote={() => { if (inspection) setPromotedSha256(inspection.identity.sha256); }}
                            />
                        </div>
                        <label className={`${labelClass} mt-4`}>Chemistry profile
                            <select data-md-chemistry-profile className={inputClass} value={profileDigestIsStale ? '' : selectedProfileId} disabled={chemistryCatalogQuery.isPending || chemistryCatalogQuery.isError || promotedSha256 !== inspection?.identity.sha256} onChange={(event) => {
                                const profile = chemistryProfiles.find((candidate) => candidate.id === event.target.value);
                                if (profile) selectProfile(profile);
                            }}>
                                <option value="">Select a deployed profile after structure review</option>
                                {chemistryProfiles.map((profile) => <option key={profile.id} value={profile.id} disabled={!profile.states.selectable}>{profile.display_name} — {profile.states.selectable ? 'selectable' : 'candidate only'}</option>)}
                            </select>
                        </label>
                    </section>

                    <section className={panelClass}>
                        <SectionTitle children="Typed scientific controls" note="Every operator-owned value is sent through bms.md.launch-intent.v1. Profile-owned values are visible and read-only." />
                        <div className="grid gap-4 md:grid-cols-3">
                            <Gen2WorkflowControl label="Independent replicas" value={form.replicas} min={constraints?.replicas ?? 1} max={constraints?.replicas ?? 8} fixed={Boolean(constraints)} onChange={(value) => update('replicas', value)} description="Fixed by the selected validated profile." />
                            <NumberField label="Base random seed" value={form.randomSeed} min={1} max={2147483647} step={1} setting="random_seed" onChange={(value) => update('randomSeed', value)} description="Replica i derives a deterministic independent seed." />
                            <NumberField label="Production per replica" unit="ns" value={form.productionNs} min={0.001} max={constraints ? constraints.max_production_steps * constraints.timestep_fs / 1_000_000 : 100} step={0.001} slider onChange={(value) => update('productionNs', value)} description="Recommended default: 0.001 ns for bounded launch acceptance; scientific campaigns require separate qualification." />
                            <NumberField label="Box padding" unit="nm" value={form.paddingNm} min={constraints?.padding_nm ?? 0.5} max={constraints?.padding_nm ?? 5} step={0.1} fixed={Boolean(constraints)} onChange={(value) => update('paddingNm', value)} />
                            <NumberField label="Salt" unit="M" value={form.saltMolar} min={constraints?.salt_molar ?? 0} max={constraints?.salt_molar ?? 2} step={0.01} fixed={Boolean(constraints)} onChange={(value) => update('saltMolar', value)} />
                            <label className={labelClass}>Neutralize system<span className="mt-3 flex items-center gap-2 normal-case tracking-normal text-slate-300"><input type="checkbox" checked={form.neutralize !== false} onChange={(event) => update('neutralize', event.target.checked)} /> Add counterions to neutralize net charge</span><span className="mt-1 block normal-case tracking-normal text-[11px] font-normal text-slate-500">Requested explicitly and persisted in the effective preparation contract.</span></label>
                            <NumberField label="Temperature" unit="K" value={form.temperatureK} min={constraints?.temperature_k ?? 1} max={constraints?.temperature_k ?? 500} fixed={Boolean(constraints)} onChange={(value) => update('temperatureK', value)} />
                            <NumberField label="Pressure" unit="bar" value={form.pressureBar} min={constraints?.pressure_bar ?? 0.1} max={constraints?.pressure_bar ?? 100} step={0.1} fixed={Boolean(constraints)} onChange={(value) => update('pressureBar', value)} />
                            <NumberField label="Timestep" unit="fs" value={form.timestepFs} min={constraints?.timestep_fs ?? 0.5} max={constraints?.timestep_fs ?? 4} step={0.5} fixed={Boolean(constraints)} onChange={(value) => update('timestepFs', value)} />
                            <NumberField label="Minimization" unit="steps" value={form.minimizationSteps} min={1} max={constraints?.max_minimization_steps ?? 5_000_000} step={1000} slider onChange={(value) => update('minimizationSteps', value)} />
                            <NumberField label="NVT equilibration" unit="ps" value={form.nvtPs} min={0.002} max={constraints ? constraints.max_nvt_steps * form.timestepFs / 1000 : 10_000} step={1} slider onChange={(value) => update('nvtPs', value)} />
                            <NumberField label="NPT equilibration" unit="ps" value={form.nptPs} min={0.002} max={constraints ? constraints.max_npt_steps * form.timestepFs / 1000 : 10_000} step={1} slider onChange={(value) => update('nptPs', value)} />
                            <NumberField label="Trajectory interval" unit="ps" value={form.trajectoryIntervalPs} min={0.002} max={form.productionNs * 1000} step={0.002} slider onChange={(value) => update('trajectoryIntervalPs', value)} />
                            <NumberField label="Energy/log interval" unit="ps" value={form.energyIntervalPs} min={0.002} max={form.productionNs * 1000} step={0.002} slider onChange={(value) => update('energyIntervalPs', value)} />
                            <NumberField label="Checkpoint interval" unit="minutes" value={form.checkpointIntervalMinutes} min={1} max={1440} step={1} slider onChange={(value) => update('checkpointIntervalMinutes', value)} />
                            <NumberField label="CPU threads per replica" value={form.ntomp} min={1} max={128} step={1} onChange={(value) => update('ntomp', value)} description="Provider-neutral CPU request; GPU placement remains scheduler-owned." />
                        </div>
                        {selectedProfile && <div className="mt-4 rounded-lg border border-slate-700 bg-slate-950/50 p-3 text-xs text-slate-400">Engine <span className="text-slate-200">GROMACS</span> · profile chemistry <span className="text-slate-200">{constraints?.force_field} + {constraints?.water_model}</span> · scope <span className="text-slate-200">{selectedProfile.scientific_validation.scope.launch_scope}</span></div>}
                    </section>

                    <section className={`${panelClass} p-0`}>
                        <button type="button" onClick={() => setShowAdvanced((value) => !value)} className="flex w-full items-center justify-between p-5 text-left text-sm font-semibold text-slate-300"><span>Advanced prepared-system compatibility</span><span>{showAdvanced ? '▲' : '▼'}</span></button>
                        {showAdvanced && <div className="space-y-4 border-t border-slate-800 p-5"><p className="text-xs text-amber-200">Prepared coordinates/topology remain a compatibility lane for the pinned OpenMM adapter. New automatic-preparation launches use the typed workflow above.</p><div className="grid grid-cols-2 gap-2">{(['structure', 'prepared'] as const).map((mode) => <button key={mode} type="button" disabled={mode === 'prepared' && form.engine !== 'openmm'} onClick={() => update('inputMode', mode)} className="rounded border border-slate-700 px-3 py-2 text-xs text-slate-300">{mode === 'structure' ? 'Typed starting structure' : 'Use prepared system'}</button>)}</div><label className={labelClass}>Compatibility engine<select className={inputClass} value={form.engine} onChange={(event) => { const engine = event.target.value as MolecularDynamicsForm['engine']; update('engine', engine); if (engine === 'openmm') update('inputMode', 'prepared'); }}><option value="gromacs" disabled={form.inputMode === 'prepared'}>GROMACS 2025.3</option><option value="openmm">OpenMM 8.5.2</option></select></label>{form.inputMode === 'prepared' && <div className="grid gap-4 md:grid-cols-2"><label className={labelClass}>Coordinates path (.gro)<input className={inputClass} value={form.coordinatesPath} onChange={(event) => update('coordinatesPath', event.target.value)} /></label><label className={labelClass}>Topology path (.top)<input className={inputClass} value={form.topologyPath} onChange={(event) => update('topologyPath', event.target.value)} /></label><p className="md:col-span-2 text-xs text-amber-300/80">Prepared systems are supported only by OpenMM. Declared topology includes are snapshotted into the verified Job closure.</p><button type="button" disabled={formErrors.length > 0 || isSubmitting} onClick={() => void launchPreparedCompatibility()} className="md:col-span-2 rounded-lg border border-amber-400/40 px-3 py-2 text-sm text-amber-200 disabled:opacity-50">Launch prepared-system compatibility job</button></div>}</div>}
                    </section>
                </div>

                <aside className="space-y-4 lg:sticky lg:top-5 lg:self-start">
                    <section className={panelClass}>
                        <h2 className="text-sm font-semibold text-slate-200">Launch summary</h2>
                        <dl className="mt-4 space-y-3 text-xs"><div className="flex justify-between gap-3"><dt className="text-slate-500">Source</dt><dd className="max-w-40 truncate text-right text-slate-200">{inspection?.identity.label ?? 'Not inspected'}</dd></div><div className="flex justify-between gap-3"><dt className="text-slate-500">Profile admission</dt><dd className={inspection?.admission.state === 'admitted' ? 'text-cyan-300' : 'text-amber-300'}>{inspection?.admission.state ?? 'pending'}</dd></div><div className="flex justify-between gap-3"><dt className="text-slate-500">Engine</dt><dd className="text-slate-200">GROMACS 2025.3</dd></div><div className="flex justify-between gap-3"><dt className="text-slate-500">GPU children</dt><dd className="text-slate-200">{form.replicas}</dd></div><div className="flex justify-between gap-3"><dt className="text-slate-500">Aggregate simulation</dt><dd className="font-semibold text-cyan-300">{scope.aggregateSimulationNs.toLocaleString()} ns</dd></div><div className="flex justify-between gap-3"><dt className="text-slate-500">Steps / replica</dt><dd className="text-slate-200">{scope.productionStepsPerReplica.toLocaleString()}</dd></div><div className="flex justify-between gap-3"><dt className="text-slate-500">Total frames</dt><dd className="text-slate-200">{scope.totalTrajectoryFrames.toLocaleString()}</dd></div></dl>
                    </section>
                    {form.inputMode === 'structure' && formErrors.length > 0 && <section className="rounded-xl border border-red-500/30 bg-red-500/8 p-4"><h2 className="text-xs font-semibold uppercase tracking-wider text-red-300">Resolve before preview</h2><ul className="mt-2 list-disc space-y-1 pl-4 text-xs text-red-200/80">{formErrors.map((error) => <li key={error}>{error}</li>)}</ul></section>}
                    {submitError && <div role="alert" className="rounded-xl border border-red-500/30 bg-red-500/8 p-3 text-xs text-red-200">{submitError}</div>}
                    {previewIsCurrent && preview && <section className="rounded-xl border border-cyan-500/30 bg-cyan-500/8 p-4 text-xs text-cyan-100"><div className="font-semibold">Effective request digest</div><div className="mt-2 break-all font-mono text-[10px]">{preview.preview_digest}</div>{preview.blockers.map((blocker) => <div key={blocker.code} className="mt-2 text-red-200">{blocker.message}</div>)}<details className="mt-3"><summary className="cursor-pointer text-cyan-200">Effective JSON</summary><pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap rounded bg-slate-950 p-2 text-[10px] text-slate-300">{JSON.stringify(preview.effective_request, null, 2)}</pre></details></section>}
                    {form.inputMode === 'structure' && <><button type="button" disabled={!typedReady || previewRequestIsCurrent || isSubmitting} onClick={() => void previewLaunch()} className="w-full rounded-xl border border-cyan-500/50 px-4 py-3 text-sm font-bold text-cyan-200 disabled:cursor-not-allowed disabled:border-slate-700 disabled:text-slate-500">{previewRequestIsCurrent ? 'Compiling preview…' : 'Preview effective request'}</button><button type="button" disabled={!previewIsCurrent || (preview?.blockers.length ?? 0) > 0 || isSubmitting} onClick={() => void launchTyped()} className="w-full rounded-xl bg-cyan-500 px-4 py-3 text-sm font-bold text-slate-950 disabled:cursor-not-allowed disabled:bg-slate-700 disabled:text-slate-500">{isSubmitting ? 'Materializing MD job…' : 'Launch typed MD job'}</button></>}
                    <p className="text-center text-[11px] text-slate-600">Launch creates one canonical scheduler-visible Job. Server-owned runtime paths, GPU placement, and materialization never enter browser state.</p>
                </aside>
            </div>
        </div>
    );
}
