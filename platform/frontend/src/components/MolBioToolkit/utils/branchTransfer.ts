import type { MolBioBranchBundle, MolBioBranchImportResult } from '../../../lib/api';

export const isBranchBundle = (value: unknown): value is MolBioBranchBundle =>
    typeof value === 'object' && value !== null && (value as { format?: unknown }).format === 'molbio-branch';

export function downloadBranchBundle(bundle: MolBioBranchBundle, name: string): void {
    const url = URL.createObjectURL(new Blob([JSON.stringify(bundle, null, 2)], { type: 'application/json' }));
    const link = document.createElement('a');
    link.href = url;
    link.download = `${name.replace(/[^a-z0-9]/gi, '_')}.molbio-branch.json`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
}

export function branchImportMessage(result: MolBioBranchImportResult): string {
    if (result.already_imported) return 'Branch already imported - nothing new created.';
    const { documents, revisions, operations } = result.created;
    return `Branch imported: ${documents} documents, ${revisions} revisions, ${operations} operations created.`;
}

/** The first selected revision in the receiving store, as {sequenceId, revisionId}. */
export function firstImportedRevision(bundle: MolBioBranchBundle, result: MolBioBranchImportResult) {
    const oldRevisionId = bundle.selected[0];
    const oldDocumentId = bundle.revisions.find((revision) => revision.id === oldRevisionId)?.document_id;
    const revisionId = oldRevisionId ? result.id_map.revisions[oldRevisionId] : undefined;
    const sequenceId = oldDocumentId ? result.id_map.documents[oldDocumentId] : undefined;
    return sequenceId && revisionId ? { sequenceId, revisionId } : null;
}
