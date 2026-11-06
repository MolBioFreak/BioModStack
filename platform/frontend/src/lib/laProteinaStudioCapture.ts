import { buildFileDownloadUrl, materializeExactStructure, materializeStructureTarget } from './api';
import type { CapturedAnnotatedDocument } from './deNovoDocumentCapture';
import { materializeLaProteinaMotifSelection } from './laProteinaMotifSelection';

/** CAD consumes a motif-only PDB, not Studio's CIF or its RFD4 conditions.
 * The explicit native contig selects existing ATOM residues; the established
 * motif-only writer retains their atoms and order without renumbering.
 */
export async function materializeLaProteinaStudioCapture(document: CapturedAnnotatedDocument, contig: string): Promise<string> {
    const receipt = await materializeExactStructure({ ...document.source, output_format: 'pdb' });
    if (receipt.format !== 'pdb') throw new Error('The edited document has no checked PDB representation. Manual motif inputs remain available.');
    const response = await fetch(buildFileDownloadUrl(receipt.path), { credentials: 'same-origin', cache: 'no-store' });
    if (!response.ok) throw new Error(`Motif derivative readback failed (${response.status})`);
    const bytes = await response.arrayBuffer();
    const sha256 = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)), byte => byte.toString(16).padStart(2, '0')).join('');
    if (sha256 !== receipt.sha256) throw new Error('The checked motif source changed during readback. Capture again; the editor is retained.');
    const pdb = new TextDecoder().decode(bytes);
    const available = new Set(pdb.split(/\r?\n/).filter(line => line.startsWith('ATOM  ')).map(line => `${line.slice(21, 22)}${Number(line.slice(22, 26))}${line.slice(26, 27).trim()}`));
    const blocks = contig.split('/').map(token => /^([ABCDEFGHJKLMNOPQRSTUVWXYZ])(\d+)(?:-(\d+))?$/.exec(token)).filter(match => match !== null);
    const selected = new Set([...available].filter(key => blocks.some(block => {
        const match = /^([A-Z])(\d+)$/.exec(key);
        return match && match[1] === block[1] && Number(match[2]) >= Number(block[2]) && Number(match[2]) <= Number(block[3] ?? block[2]);
    })));
    const motif = materializeLaProteinaMotifSelection(pdb, selected, contig);
    return materializeStructureTarget({ name: 'laproteina-studio-motif.pdb', file: new File([motif.pdb], 'laproteina-studio-motif.pdb', { type: 'chemical/x-pdb' }) }, 'inputs');
}
