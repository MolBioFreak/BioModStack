import type {
    AssemblyFragmentInput,
    GoldenGateAssemblyOptionsResponse,
    GoldenGateAssemblyRequest,
} from './api';

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
    if (!enzymeId || !options?.enzymes.some((enzyme) => enzyme.enzyme_id === enzymeId)) {
        throw new Error('Select a currently loaded Golden Gate compatible catalog enzyme.');
    }
    return {
        fragments,
        circular,
        enzyme_id: enzymeId,
        new_name: newName || undefined,
        save_description: saveDescription || undefined,
    };
}
