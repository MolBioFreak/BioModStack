import type {
    AssemblyFragmentInput,
    AssemblyOperationResponse,
    AssemblyProduct,
    GoldenGateAssemblyOptionsResponse,
    GoldenGateAssemblyRequest,
    GoldenGateCatalogAuthority,
} from './api';

const SHA256 = /^[0-9a-f]{64}$/;

function isRecord(value: unknown): value is Record<string, unknown> {
    return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function requireGoldenGateAuthority(value: unknown): GoldenGateCatalogAuthority {
    if (!isRecord(value) || Object.keys(value).sort().join(',') !== 'catalog_id,catalog_sha256,enzyme_id') {
        throw new Error('Golden Gate catalog authority is missing or invalid.');
    }
    if (
        typeof value.enzyme_id !== 'string' || value.enzyme_id.length === 0
        || typeof value.catalog_id !== 'string' || value.catalog_id.length === 0
        || typeof value.catalog_sha256 !== 'string' || !SHA256.test(value.catalog_sha256)
    ) {
        throw new Error('Golden Gate catalog authority is missing or invalid.');
    }
    return value as unknown as GoldenGateCatalogAuthority;
}

export function requireGoldenGateAssemblyResponse(value: unknown): AssemblyOperationResponse {
    if (!isRecord(value) || !isRecord(value.product) || value.product.mode !== 'golden_gate') {
        throw new Error('Golden Gate assembly response is invalid.');
    }
    const authority = requireGoldenGateAuthority(value.product.golden_gate_authority);
    if (value.saved_sequence !== undefined && value.saved_sequence !== null) {
        if (!isRecord(value.saved_sequence) || !isRecord(value.saved_sequence.operation_params)) {
            throw new Error('Saved Golden Gate catalog authority is missing or invalid.');
        }
        const params = value.saved_sequence.operation_params;
        if (
            params.enzyme_id !== authority.enzyme_id
            || params.catalog_id !== authority.catalog_id
            || params.catalog_sha256 !== authority.catalog_sha256
        ) {
            throw new Error('Saved Golden Gate catalog authority does not match the simulation.');
        }
    }
    return value as unknown as AssemblyOperationResponse;
}

export function buildAssemblyReloadOperationParams(product: AssemblyProduct): Record<string, unknown> {
    const base = {
        mode: product.mode,
        fragment_count: product.fragments.length,
        warnings: product.warnings,
        validation_notes: product.validation_notes,
    };
    if (product.mode === 'golden_gate') {
        return { ...base, ...requireGoldenGateAuthority(product.golden_gate_authority) };
    }
    if (product.golden_gate_authority !== null) {
        throw new Error('Non-Golden-Gate products cannot carry Golden Gate catalog authority.');
    }
    return base;
}

export function buildGoldenGateAssemblyRequest({
    fragments,
    circular,
    selectedEnzymeId,
    options,
    newName,
    saveDescription,
}: {
    fragments: AssemblyFragmentInput[];
    circular: boolean;
    selectedEnzymeId: string;
    options: GoldenGateAssemblyOptionsResponse | null;
    newName?: string;
    saveDescription?: string;
}): GoldenGateAssemblyRequest {
    const enzymeId = selectedEnzymeId.trim();
    const catalogId = options?.catalog?.catalog_id;
    const catalogSha256 = options?.catalog?.catalog_sha256;
    if (
        !enzymeId
        || !options?.enzymes.some((enzyme) => enzyme.enzyme_id === enzymeId)
        || typeof catalogId !== 'string' || catalogId.length === 0
        || typeof catalogSha256 !== 'string' || !SHA256.test(catalogSha256)
    ) {
        throw new Error('Select a currently loaded Golden Gate compatible catalog enzyme.');
    }
    return {
        fragments,
        circular,
        enzyme_id: enzymeId,
        catalog_id: catalogId,
        expected_catalog_sha256: catalogSha256,
        new_name: newName || undefined,
        save_description: saveDescription || undefined,
    };
}
