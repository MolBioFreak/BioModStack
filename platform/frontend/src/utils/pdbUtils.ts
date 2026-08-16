/**
 * PDB Parsing Utilities
 * Extract chain information, sequences, and model-aware structure slices from PDB content.
 */

const AA3TO1: Record<string, string> = {
    ALA: 'A', ARG: 'R', ASN: 'N', ASP: 'D', CYS: 'C',
    GLN: 'Q', GLU: 'E', GLY: 'G', HIS: 'H', ILE: 'I',
    LEU: 'L', LYS: 'K', MET: 'M', PHE: 'F', PRO: 'P',
    SER: 'S', THR: 'T', TRP: 'W', TYR: 'Y', VAL: 'V',
    MSE: 'M', HYP: 'P', PYL: 'O', SEC: 'U',
};

const HEADER_PREFIXES = [
    'HEADER',
    'TITLE ',
    'COMPND',
    'SOURCE',
    'KEYWDS',
    'EXPDTA',
    'AUTHOR',
    'REMARK',
    'CRYST1',
    'ORIGX1',
    'ORIGX2',
    'ORIGX3',
    'SCALE1',
    'SCALE2',
    'SCALE3',
    'MTRIX1',
    'MTRIX2',
    'MTRIX3',
] as const;

const ATOMISH_PREFIXES = ['ATOM  ', 'HETATM', 'ANISOU'] as const;
const BODY_PREFIXES = [...ATOMISH_PREFIXES, 'TER'] as const;

export interface Residue {
    chainId: string;
    resNum: number;
    iCode?: string;
    resName: string;
    aa: string;
}

export interface Chain {
    id: string;
    sequence: string;
    residues: Residue[];
    length: number;
}

export interface PDBModel {
    index: number;
    modelNumber: number;
    label: string;
    chains: Chain[];
    content: string;
}

export interface ParsedPDB {
    chains: Chain[];
    models: PDBModel[];
    title?: string;
}

function parseChainsFromLines(lines: string[]): Chain[] {
    const chainMap = new Map<string, Residue[]>();
    const seenResidues = new Set<string>();

    for (const line of lines) {
        if (!line.startsWith('ATOM  ') && !line.startsWith('HETATM')) continue;

        const resName = line.slice(17, 20).trim();
        const chainId = line.slice(21, 22).trim() || 'A';
        const resNum = parseInt(line.slice(22, 26).trim(), 10);
        const iCode = line.slice(26, 27).trim() || undefined;
        const aa = AA3TO1[resName];

        if (!aa || Number.isNaN(resNum)) continue;

        const key = `${chainId}:${resNum}${iCode || ''}`;
        if (seenResidues.has(key)) continue;
        seenResidues.add(key);

        if (!chainMap.has(chainId)) {
            chainMap.set(chainId, []);
        }
        chainMap.get(chainId)!.push({
            chainId,
            resNum,
            iCode,
            resName,
            aa,
        });
    }

    const chains: Chain[] = [];
    for (const [id, residues] of chainMap.entries()) {
        residues.sort((a, b) => {
            if (a.resNum !== b.resNum) return a.resNum - b.resNum;
            return (a.iCode || '').localeCompare(b.iCode || '');
        });
        chains.push({
            id,
            sequence: residues.map((r) => r.aa).join(''),
            residues,
            length: residues.length,
        });
    }

    chains.sort((a, b) => a.id.localeCompare(b.id));
    return chains;
}

function buildModelContent(headerLines: string[], bodyLines: string[]): string {
    const merged = [...headerLines, ...bodyLines];
    if (merged.length === 0 || merged[merged.length - 1].trim() !== 'END') {
        merged.push('END');
    }
    return `${merged.join('\n')}\n`;
}

function createModel(index: number, modelNumber: number, headerLines: string[], bodyLines: string[]): PDBModel | null {
    if (bodyLines.length === 0) return null;
    return {
        index,
        modelNumber,
        label: `Model ${modelNumber}`,
        chains: parseChainsFromLines(bodyLines),
        content: buildModelContent(headerLines, bodyLines),
    };
}

export function parsePDB(pdbContent: string): ParsedPDB {
    const lines = pdbContent.split(/\r?\n/);
    const headerLines: string[] = [];
    const defaultBodyLines: string[] = [];
    const models: PDBModel[] = [];
    let title = '';

    let currentModelLines: string[] = [];
    let currentModelNumber: number | null = null;
    let insideModel = false;
    let modelIndex = 0;

    const pushCurrentModel = () => {
        if (!insideModel || currentModelNumber == null) return;
        const model = createModel(modelIndex, currentModelNumber, headerLines, currentModelLines);
        if (model) {
            models.push(model);
        }
        currentModelLines = [];
        currentModelNumber = null;
        insideModel = false;
    };

    for (const rawLine of lines) {
        const line = rawLine.replace(/\r$/, '');

        if (!line) continue;

        if (line.startsWith('TITLE')) {
            title += `${line.slice(10).trim()} `;
        }

        if (line.startsWith('MODEL')) {
            pushCurrentModel();
            insideModel = true;
            modelIndex += 1;
            const parsedNumber = parseInt(line.slice(10).trim(), 10);
            currentModelNumber = Number.isFinite(parsedNumber) ? parsedNumber : modelIndex;
            currentModelLines = [];
            continue;
        }

        if (line.startsWith('ENDMDL')) {
            pushCurrentModel();
            continue;
        }

        if (HEADER_PREFIXES.some((prefix) => line.startsWith(prefix))) {
            if (!insideModel) {
                headerLines.push(line);
            }
            continue;
        }

        if (BODY_PREFIXES.some((prefix) => line.startsWith(prefix))) {
            if (insideModel) {
                currentModelLines.push(line);
            } else {
                defaultBodyLines.push(line);
            }
        }
    }

    pushCurrentModel();

    if (models.length === 0) {
        const fallbackModel = createModel(1, 1, headerLines, defaultBodyLines);
        if (fallbackModel) {
            models.push(fallbackModel);
        }
    }

    return {
        chains: models[0]?.chains ?? [],
        models,
        title: title.trim() || undefined,
    };
}

function tokenizeMmCifRow(line: string): string[] {
    return line.match(/'(?:[^']|'')*'|"(?:[^"]|"")*"|\S+/g)?.map((token) => {
        if ((token.startsWith("'") && token.endsWith("'")) || (token.startsWith('"') && token.endsWith('"'))) {
            return token.slice(1, -1);
        }
        return token;
    }) ?? [];
}

function mmCifValue(value: string | undefined): string | undefined {
    if (!value || value === '.' || value === '?') return undefined;
    return value;
}

function mmCifAtomLine(record: Record<string, string>, serialFallback: number): { model: number; line: string } | null {
    const group = mmCifValue(record.group_PDB) || 'ATOM';
    const atomName = mmCifValue(record.label_atom_id) || mmCifValue(record.auth_atom_id);
    const residueName = mmCifValue(record.label_comp_id) || mmCifValue(record.auth_comp_id);
    const chainId = mmCifValue(record.auth_asym_id) || mmCifValue(record.label_asym_id) || 'A';
    const residueNumber = mmCifValue(record.auth_seq_id) || mmCifValue(record.label_seq_id);
    const x = mmCifValue(record.Cartn_x);
    const y = mmCifValue(record.Cartn_y);
    const z = mmCifValue(record.Cartn_z);
    if (!atomName || !residueName || !residueNumber || !x || !y || !z) return null;
    const serial = Number.parseInt(mmCifValue(record.id) || '', 10) || serialFallback;
    const model = Number.parseInt(mmCifValue(record.pdbx_PDB_model_num) || '1', 10) || 1;
    const insertionCode = mmCifValue(record.pdbx_PDB_ins_code) || ' ';
    const occupancy = Number(mmCifValue(record.occupancy) || '1').toFixed(2);
    const bFactor = Number(mmCifValue(record.B_iso_or_equiv) || '0').toFixed(2);
    const element = (mmCifValue(record.type_symbol) || atomName.slice(0, 1)).toUpperCase().slice(0, 2);
    const recordName = group === 'HETATM' ? 'HETATM' : 'ATOM  ';
    const atomLabel = atomName.length < 4 ? ` ${atomName}`.padEnd(4, ' ') : atomName.slice(0, 4);
    const chainColumn = chainId.slice(-1);
    const residueColumn = Number.parseInt(residueNumber, 10);
    if (!Number.isFinite(residueColumn)) return null;
    const line = `${recordName}${String(serial).padStart(5, ' ')} ${atomLabel} ${residueName.padStart(3, ' ').slice(-3)} ${chainColumn}${String(residueColumn).padStart(4, ' ')}${insertionCode.slice(0, 1).padEnd(1, ' ')}   ${Number(x).toFixed(3).padStart(8, ' ')}${Number(y).toFixed(3).padStart(8, ' ')}${Number(z).toFixed(3).padStart(8, ' ')}${occupancy.padStart(6, ' ')}${bFactor.padStart(6, ' ')}          ${element.padStart(2, ' ')}  `;
    return { model, line };
}

export function parseMmCIF(cifContent: string): ParsedPDB {
    const lines = cifContent.split(/\r?\n/);
    for (let start = 0; start < lines.length; start += 1) {
        if (lines[start].trim() !== 'loop_') continue;
        const headers: string[] = [];
        let cursor = start + 1;
        while (cursor < lines.length && lines[cursor].trim().startsWith('_')) {
            headers.push(lines[cursor].trim());
            cursor += 1;
        }
        if (!headers.some((header) => header.startsWith('_atom_site.'))) continue;
        const columnNames = headers.map((header) => header.slice('_atom_site.'.length).split(/[. ]/, 1)[0]);
        const modelLines = new Map<number, string[]>();
        let serialFallback = 1;
        let pending: string[] = [];
        for (; cursor < lines.length; cursor += 1) {
            const raw = lines[cursor].trim();
            if (!raw || raw === '#' || raw === 'loop_' || raw.startsWith('data_') || raw.startsWith('_')) {
                if (pending.length) break;
                if (raw === '#' || raw === 'loop_' || raw.startsWith('data_') || raw.startsWith('_')) break;
                continue;
            }
            pending = [...pending, ...tokenizeMmCifRow(raw)];
            while (pending.length >= columnNames.length) {
                const values = pending.splice(0, columnNames.length);
                const record: Record<string, string> = {};
                columnNames.forEach((name, index) => { record[name] = values[index]; });
                const atom = mmCifAtomLine(record, serialFallback);
                serialFallback += 1;
                if (!atom) continue;
                const block = modelLines.get(atom.model) || [];
                block.push(atom.line);
                modelLines.set(atom.model, block);
            }
        }
        if (!modelLines.size) return { chains: [], models: [] };
        const orderedModels = Array.from(modelLines.keys()).sort((a, b) => a - b);
        const pdbLines: string[] = [];
        if (orderedModels.length === 1) {
            pdbLines.push(...(modelLines.get(orderedModels[0]) || []), 'END');
        } else {
            orderedModels.forEach((model) => {
                pdbLines.push(`MODEL     ${String(model).padStart(4, ' ')}`);
                pdbLines.push(...(modelLines.get(model) || []), 'ENDMDL');
            });
            pdbLines.push('END');
        }
        return parsePDB(pdbLines.join('\\n'));
    }
    return { chains: [], models: [] };
}

export async function parseStructureFile(file: File): Promise<ParsedPDB> {
    const content = await file.text();
    if (/\\.(?:cif|mmcif)$/i.test(file.name) || content.includes('_atom_site.')) {
        return parseMmCIF(content);
    }
    return parsePDB(content);
}

export async function parsePDBFile(file: File): Promise<ParsedPDB> {
    const content = await file.text();
    return parsePDB(content);
}

export function getModelByNumber(parsed: ParsedPDB, modelNumber?: number | null): PDBModel | null {
    if (!parsed.models.length) return null;
    if (modelNumber == null) return parsed.models[0];
    return parsed.models.find((model) => model.modelNumber === modelNumber) ?? parsed.models[0];
}

export function formatSelectedResidues(residues: Residue[]): string {
    return residues.map((r) => `${r.chainId}${r.resNum}${r.iCode || ''}`).join(',');
}

export function parseResidueString(str: string): Array<{ chain: string; resNum: number; iCode?: string }> {
    const results: Array<{ chain: string; resNum: number; iCode?: string }> = [];
    const parts = str.split(',').map((s) => s.trim()).filter(Boolean);

    for (const part of parts) {
        const match = part.match(/^([A-Z])(-?\d+)([A-Z]?)$/i);
        if (!match) continue;
        results.push({
            chain: match[1].toUpperCase(),
            resNum: parseInt(match[2], 10),
            iCode: match[3] || undefined,
        });
    }

    return results;
}
