import { createContext, useContext } from 'react';
import { buildFileDownloadUrl, uploadImmutableFile, type StructureSourceSelection } from './api';
import type { MolecularConstructionDraft, MolecularGeometryResult } from './molecularConstruction';

/** Suite editor evidence, never native scientific parameters. */
export interface PreparedGeometryDocument {
    format: 'pdb';
    source: StructureSourceSelection;
    sha256: string;
    size: number;
    original_source?: StructureSourceSelection;
    construction: MolecularConstructionDraft;
    geometry: MolecularGeometryResult;
}
export interface PreparedGeometryReceiving {
    current?: MolecularConstructionDraft;
    generation: () => number;
    retain: (document: PreparedGeometryDocument) => void;
}
export const PreparedGeometryContext = createContext<PreparedGeometryReceiving | null>(null);
export const usePreparedGeometry = () => useContext(PreparedGeometryContext);
const digest = async (bytes: Uint8Array) => Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes as Uint8Array<ArrayBuffer>)), byte => byte.toString(16).padStart(2, '0')).join('');

/** Exact PDB custody only; graph bond/charge semantics remain in the companion. */
export async function uploadPreparedGeometry(construction: MolecularConstructionDraft, originalSource?: StructureSourceSelection): Promise<PreparedGeometryDocument> {
    const snapshot = structuredClone(construction);
    const geometry = snapshot.geometry?.result;
    if (!geometry || geometry.format !== 'pdb') throw Error('No current positioned PDB document. Existing sources are unchanged.');
    const bytes = new TextEncoder().encode(geometry.coordinate_text);
    const sha256 = await digest(bytes);
    if (sha256 !== geometry.coordinate_sha256 || bytes.byteLength !== geometry.coordinate_size_bytes) throw Error('Positioned PDB bytes do not match their geometry receipt.');
    // Reuse the existing PDB-only immutable namespace (its historical name is not an engine choice).
    const { data } = await uploadImmutableFile('inputs/protein_local_redesign', new File([bytes], `constructed-${sha256}.pdb`, { type: 'chemical/x-pdb' }), sha256);
    if (data.sha256 !== sha256 || data.size !== bytes.byteLength) throw Error('Immutable PDB upload receipt does not match the positioned bytes.');
    const response = await fetch(buildFileDownloadUrl(data.path));
    if (!response.ok) throw Error(`Immutable PDB readback HTTP ${response.status}. Existing sources are unchanged.`);
    const readback = new Uint8Array(await response.arrayBuffer());
    if (readback.byteLength !== bytes.byteLength || await digest(readback) !== sha256) throw Error('Immutable PDB readback does not match the positioned bytes.');
    return { format: 'pdb', source: { path: data.path, expected_sha256: sha256, output_format: 'native' }, sha256, size: bytes.byteLength,
        ...(originalSource ? { original_source: structuredClone(originalSource) } : {}), construction: snapshot, geometry: structuredClone(geometry) };
}
