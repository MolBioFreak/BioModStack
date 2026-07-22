export type AnnotationSourceProvider = 'ncbi' | 'addgene';

export interface AnnotationSourceStatus {
    ncbi: { available: boolean };
    addgene: { available: boolean };
}

export interface AnnotationSourceProvenance {
    provider: AnnotationSourceProvider;
    source_id: string;
    source_url: string;
    artifact_sha256: string;
    [key: string]: unknown;
}

interface AnnotationSourceArtifact {
    content: string;
    file_name: string;
    media_type: string;
    source: AnnotationSourceProvenance;
}

export interface RetrievedAnnotationSource {
    file: File;
    source: AnnotationSourceProvenance;
}

type FetchLike = (input: string | URL | Request, init?: RequestInit) => Promise<Response>;

const NCBI_ACCESSION_PATTERN = /^[A-Z][A-Z0-9_]*(?:\.[0-9]+)?$/;

export function assertAnnotationArtifactChecksum(
    source: AnnotationSourceProvenance,
    actualSha256: string,
): void {
    if (source.artifact_sha256.toLowerCase() !== actualSha256.toLowerCase()) {
        throw new Error('Retrieved annotation artifact checksum does not match the server provenance record.');
    }
}

export function normalizeNcbiAccession(accession: string): string {
    const normalized = accession.trim().toUpperCase();
    if (!normalized || normalized.length > 64 || !NCBI_ACCESSION_PATTERN.test(normalized)) {
        throw new Error('NCBI accession has an invalid format.');
    }
    return normalized;
}

export function normalizeAddgenePlasmidId(value: string): number {
    const normalized = value.trim();
    if (!/^[1-9][0-9]*$/.test(normalized)) {
        throw new Error('Addgene plasmid ID must be a positive integer.');
    }
    const plasmidId = Number(normalized);
    if (!Number.isSafeInteger(plasmidId) || plasmidId > 2_147_483_647) {
        throw new Error('Addgene plasmid ID must be a positive integer.');
    }
    return plasmidId;
}

function isRecord(value: unknown): value is Record<string, unknown> {
    return typeof value === 'object' && value !== null && !Array.isArray(value);
}

async function responseJson(response: Response): Promise<unknown> {
    try {
        return await response.json();
    } catch {
        throw new Error(`Annotation source API returned invalid JSON (HTTP ${response.status}).`);
    }
}

async function requireOkJson(response: Response): Promise<unknown> {
    const payload = await responseJson(response);
    if (!response.ok) {
        const detail = isRecord(payload) && typeof payload.detail === 'string'
            ? payload.detail
            : `Annotation source retrieval failed (HTTP ${response.status}).`;
        throw new Error(detail);
    }
    return payload;
}

function parseArtifact(payload: unknown): AnnotationSourceArtifact {
    if (!isRecord(payload) || !isRecord(payload.source)) {
        throw new Error('Server returned an invalid annotation artifact.');
    }
    const { content, file_name: fileName, media_type: mediaType, source } = payload;
    if (
        typeof content !== 'string'
        || typeof fileName !== 'string'
        || !/\.gb(?:k|ank)?$/i.test(fileName)
        || typeof mediaType !== 'string'
        || (source.provider !== 'ncbi' && source.provider !== 'addgene')
        || typeof source.source_id !== 'string'
        || typeof source.source_url !== 'string'
        || typeof source.artifact_sha256 !== 'string'
    ) {
        throw new Error('Server returned an invalid annotation artifact.');
    }
    return {
        content,
        file_name: fileName,
        media_type: mediaType,
        source: source as unknown as AnnotationSourceProvenance,
    };
}

async function retrieve(path: string, fetchImpl: FetchLike): Promise<RetrievedAnnotationSource> {
    const response = await fetchImpl(path, { method: 'GET', credentials: 'same-origin' });
    const artifact = parseArtifact(await requireOkJson(response));
    return {
        file: new File([artifact.content], artifact.file_name, { type: artifact.media_type }),
        source: artifact.source,
    };
}

export async function retrieveNcbiAnnotationSource(
    accession: string,
    fetchImpl: FetchLike = fetch,
): Promise<RetrievedAnnotationSource> {
    const normalized = normalizeNcbiAccession(accession);
    return retrieve(`/api/molbio/annotation-sources/ncbi/${normalized}`, fetchImpl);
}

export async function retrieveAddgeneAnnotationSource(
    plasmidId: string,
    fetchImpl: FetchLike = fetch,
): Promise<RetrievedAnnotationSource> {
    const normalized = normalizeAddgenePlasmidId(plasmidId);
    return retrieve(`/api/molbio/annotation-sources/addgene/${normalized}`, fetchImpl);
}

export async function fetchAnnotationSourceStatus(
    fetchImpl: FetchLike = fetch,
): Promise<AnnotationSourceStatus> {
    const response = await fetchImpl('/api/molbio/annotation-sources/status', {
        method: 'GET',
        credentials: 'same-origin',
    });
    const payload = await requireOkJson(response);
    if (
        !isRecord(payload)
        || !isRecord(payload.ncbi)
        || !isRecord(payload.addgene)
        || typeof payload.ncbi.available !== 'boolean'
        || typeof payload.addgene.available !== 'boolean'
    ) {
        throw new Error('Server returned invalid annotation-source status.');
    }
    return {
        ncbi: { available: payload.ncbi.available },
        addgene: { available: payload.addgene.available },
    };
}
