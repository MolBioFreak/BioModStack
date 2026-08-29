export const MD_HANDOFF_DRAFT_PREFIX = 'bms.md.gen2.draft.v1:';

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

const requireUuid = (value: string, label: string): string => {
    if (!UUID_RE.test(value)) throw new Error(`Invalid Molecular Dynamics handoff route: ${label} must be one UUID.`);
    return value;
};

export interface MolecularDynamicsHandoffRoute {
    sourceSequenceId: string | null;
    draftId: string | null;
    sourcePredictionJobId: string | null;
    sourceDesignId: string | null;
    returnTemplate: 'molecular_dynamics' | null;
}

export interface MolecularDynamicsHandoffUserSequence {
    id: string;
    name: string;
    sequence: string;
    description: string | null;
    length: number;
    organism: string | null;
    uniprot_id: string | null;
    ncbi_id: string | null;
    is_preset: boolean;
    created_at: string;
    updated_at: string | null;
}

export const parseMolecularDynamicsHandoffUserSequence = (
    value: unknown,
    expectedSequenceId?: string,
): MolecularDynamicsHandoffUserSequence => {
    if (expectedSequenceId) requireUuid(expectedSequenceId, 'source_sequence_id');
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('The selected saved sequence response is invalid.');
    const record = value as Record<string, unknown>;
    const expectedKeys = ['id', 'name', 'sequence', 'description', 'length', 'organism', 'uniprot_id', 'ncbi_id', 'is_preset', 'created_at', 'updated_at'].sort();
    const actualKeys = Object.keys(record).sort();
    if (actualKeys.length !== expectedKeys.length || actualKeys.some((key, index) => key !== expectedKeys[index])) {
        throw new Error('The selected saved sequence response is invalid.');
    }
    const nullableStrings = ['description', 'organism', 'uniprot_id', 'ncbi_id', 'updated_at'];
    if (typeof record.id !== 'string' || !UUID_RE.test(record.id) || (expectedSequenceId && record.id !== expectedSequenceId)
        || typeof record.name !== 'string' || !record.name.trim()
        || typeof record.sequence !== 'string' || !record.sequence.trim() || /\s/.test(record.sequence)
        || typeof record.length !== 'number' || !Number.isInteger(record.length) || record.length !== record.sequence.length
        || typeof record.is_preset !== 'boolean' || typeof record.created_at !== 'string' || !record.created_at
        || nullableStrings.some((key) => record[key] !== null && typeof record[key] !== 'string')) {
        throw new Error('The selected saved sequence response is invalid.');
    }
    return value as MolecularDynamicsHandoffUserSequence;
};

export const parseMolecularDynamicsHandoffUserSequencePage = (
    value: unknown,
): MolecularDynamicsHandoffUserSequence[] => {
    if (!Array.isArray(value)) throw new Error('The selected saved sequence list response is invalid.');
    return value.map((entry) => parseMolecularDynamicsHandoffUserSequence(entry));
};

export const buildMolecularDynamicsPredictionRoute = (sequenceId: string, draftId: string): string => {
    requireUuid(sequenceId, 'source_sequence_id');
    requireUuid(draftId, 'md_draft_id');
    return `/submit?template=structure_prediction&source_sequence_id=${sequenceId}&return_template=molecular_dynamics&md_draft_id=${draftId}`;
};

export const buildMolecularDynamicsPredictionReturnRoute = (draftId: string, jobResponse: unknown): string => {
    requireUuid(draftId, 'md_draft_id');
    if (!jobResponse || typeof jobResponse !== 'object' || Array.isArray(jobResponse)) {
        throw new Error('The canonical Structure Prediction Job response is invalid.');
    }
    const keys = Object.keys(jobResponse as Record<string, unknown>);
    const id = (jobResponse as Record<string, unknown>).id;
    if (!keys.includes('id') || typeof id !== 'string' || !UUID_RE.test(id)) {
        throw new Error('The canonical Structure Prediction Job response has no valid Job ID.');
    }
    return `/submit?template=molecular_dynamics&md_draft_id=${draftId}&source_prediction_job_id=${id}`;
};

export const buildResultsViewerMolecularDynamicsRoute = (jobId: string, designId: string): string => {
    requireUuid(jobId, 'source_prediction_job_id');
    requireUuid(designId, 'source_design_id');
    return `/submit?template=molecular_dynamics&source_prediction_job_id=${jobId}&source_design_id=${designId}`;
};

export const parseMolecularDynamicsHandoffRoute = (search: string): MolecularDynamicsHandoffRoute => {
    const query = search.startsWith('?') ? search.slice(1) : search;
    const params = new URLSearchParams(query);
    const controlled = ['source_sequence_id', 'return_template', 'md_draft_id', 'source_prediction_job_id', 'source_design_id'] as const;
    for (const key of params.keys()) {
        if (/^(source_|return_|md_)/.test(key) && !controlled.includes(key as typeof controlled[number])) {
            throw new Error(`Invalid Molecular Dynamics handoff route: unknown ${key}.`);
        }
    }
    for (const key of controlled) {
        if (params.getAll(key).length > 1) throw new Error(`Invalid Molecular Dynamics handoff route: duplicate ${key}.`);
    }
    const value = (key: typeof controlled[number]) => params.get(key);
    const sourceSequenceId = value('source_sequence_id');
    const draftId = value('md_draft_id');
    const sourcePredictionJobId = value('source_prediction_job_id');
    const sourceDesignId = value('source_design_id');
    const rawReturnTemplate = value('return_template');
    if (sourceSequenceId) requireUuid(sourceSequenceId, 'source_sequence_id');
    if (draftId) requireUuid(draftId, 'md_draft_id');
    if (sourcePredictionJobId) requireUuid(sourcePredictionJobId, 'source_prediction_job_id');
    if (sourceDesignId) requireUuid(sourceDesignId, 'source_design_id');
    if (rawReturnTemplate && rawReturnTemplate !== 'molecular_dynamics') {
        throw new Error('Invalid Molecular Dynamics handoff route: unknown return_template.');
    }
    if (sourceSequenceId && (!draftId || rawReturnTemplate !== 'molecular_dynamics')) {
        throw new Error('Invalid Molecular Dynamics handoff route: incomplete Structure Prediction return contract.');
    }
    if (rawReturnTemplate === 'molecular_dynamics' && (!sourceSequenceId || !draftId)) {
        throw new Error('Invalid Molecular Dynamics handoff route: incomplete Structure Prediction return contract.');
    }
    return {
        sourceSequenceId,
        draftId,
        sourcePredictionJobId,
        sourceDesignId,
        returnTemplate: rawReturnTemplate as 'molecular_dynamics' | null,
    };
};

export const buildMolecularDynamicsHandoffInitialValues = (
    route: MolecularDynamicsHandoffRoute,
    savedDraft: Record<string, unknown> | null,
): Record<string, unknown> => {
    const result: Record<string, unknown> = {};
    if (savedDraft?.form && typeof savedDraft.form === 'object' && !Array.isArray(savedDraft.form)) {
        const safeForm = { ...(savedDraft.form as Record<string, unknown>) };
        for (const forbidden of ['structurePath', 'coordinatesPath', 'topologyPath', 'structure_bytes', 'managed_path']) {
            delete safeForm[forbidden];
        }
        result.md_form = safeForm;
    }
    const selectedProfileId = savedDraft?.selectedProfileId;
    const selectedProfileDigest = savedDraft?.selectedProfileDigest;
    if (typeof selectedProfileId === 'string' && selectedProfileId.length > 0 && selectedProfileId.length <= 128
        && typeof selectedProfileDigest === 'string' && /^[0-9a-f]{64}$/.test(selectedProfileDigest)) {
        result.intent = {
            chemistry_profile_id: selectedProfileId,
            chemistry_profile_sha256: selectedProfileDigest,
        };
    }
    if (route.sourcePredictionJobId) result.source_prediction_job_id = route.sourcePredictionJobId;
    if (route.sourceDesignId) result.source_design_id = route.sourceDesignId;
    return result;
};

export const storeMolecularDynamicsDraft = (
    storage: Pick<Storage, 'setItem'>,
    draftId: string,
    draft: Record<string, unknown>,
): void => {
    requireUuid(draftId, 'md_draft_id');
    const safeDraft = { ...draft };
    for (const forbidden of ['structurePath', 'coordinatesPath', 'topologyPath', 'structure_bytes', 'managed_path']) {
        delete safeDraft[forbidden];
    }
    storage.setItem(`${MD_HANDOFF_DRAFT_PREFIX}${draftId}`, JSON.stringify(safeDraft));
};

export const loadMolecularDynamicsDraft = (
    storage: Pick<Storage, 'getItem'>,
    draftId: string,
): Record<string, unknown> | null => {
    requireUuid(draftId, 'md_draft_id');
    const raw = storage.getItem(`${MD_HANDOFF_DRAFT_PREFIX}${draftId}`);
    if (!raw) return null;
    try {
        const parsed: unknown = JSON.parse(raw);
        return parsed && typeof parsed === 'object' && !Array.isArray(parsed)
            ? parsed as Record<string, unknown>
            : null;
    } catch {
        return null;
    }
};
