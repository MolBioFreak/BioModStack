import type { BC2Request } from '../components/BindCraft2Settings';
import { materializeStructureTarget } from './api';
import { parsePDB, type Chain, type Residue } from '../utils/pdbUtils';

export interface BC2InitialSources {
    target?: { file?: File; path?: string; url?: string; name: string; chains?: string; hotspots?: string; modelPdb?: string };
    scaffold?: { file?: File; path?: string; url?: string; pdbContent?: string; name: string };
}
export type BC2Target = Record<string, unknown>;
export type BC2Source = { file?: File; path?: string; url?: string; name: string };
export type BC2Model = { number: number; chains: Chain[]; content: string };
export type BC2Document = { content: string; format: 'pdb' | 'cif' | 'fasta'; models: BC2Model[]; records?: Record<string, string> };
export const sourceDownloadUrl = (path: string) => `/api/files/download/${encodeURIComponent(path)}`;
export const bc2Targets = (value: BC2Request): BC2Target[] => Array.isArray(value.targets) ? value.targets as BC2Target[] : [];
export const textValue = (value: unknown): string => typeof value === 'string' ? value : '';
export const hasInitialTargetSlot = (value: BC2Request) => !Object.hasOwn(value, 'targets') && !value.target;
export const hasInitialScaffoldSlot = (value: BC2Request) => !Object.hasOwn(value, 'binder_scaffold');

/** Path is authoritative when available. A preview URL must not shadow its bytes. */
export async function readBC2Source(source: BC2Source, signal?: AbortSignal): Promise<string> {
    if (source.file) return source.file.text();
    const url = source.path ? sourceDownloadUrl(source.path) : source.url;
    if (!url) throw new Error('Choose a structure or FASTA source.');
    const response = await fetch(url, { signal });
    if (!response.ok) throw new Error(`Could not read ${source.name} (HTTP ${response.status}). Try selecting the source again.`);
    return response.text();
}

export async function acquireBC2Source(source: BC2Source): Promise<{ path: string; document: BC2Document }> {
    const content = await readBC2Source(source);
    const document = await parseBC2Document(content, source.file?.name || source.path || source.name);
    // Fetch URL-only selections once; upload and preview exactly those same bytes.
    const extension = document.format === 'fasta' ? 'fasta' : document.format;
    const originalName = source.file?.name || source.path || source.name;
    const compatibleSuffix = document.format === 'fasta' ? /\.(fa|fasta)$/i : document.format === 'cif' ? /\.cif$/i : /\.pdb$/i;
    // Alias extensions (.mmcif/.faa) get a fresh governed copy, not a guessed path.
    // Content is unchanged, and the emitted suffix matches the native reader.
    const path = source.path && !source.file && compatibleSuffix.test(source.path) ? source.path : await materializeStructureTarget({
        name: source.name,
        file: source.file && compatibleSuffix.test(originalName) ? source.file : new File([content], `${source.name.replace(/\.(pdb|cif|mmcif|fa|fasta|faa)$/i, '')}.${extension}`),
    }, 'inputs');
    return { path, document };
}

const aminoAcids: Record<string, string> = Object.fromEntries('ALA:A ARG:R ASN:N ASP:D CYS:C GLN:Q GLU:E GLY:G HIS:H ILE:I LEU:L LYS:K MET:M PHE:F PRO:P SER:S THR:T TRP:W TYR:Y VAL:V MSE:M'.split(' ').map(pair => pair.split(':')));

/** Preserve native first-appearance chain order and blank PDB chain IDs. The
 * legacy parser's A fallback and alphabetical chain sort are display conveniences
 * and must not decide the first chain for unqualified native residue spans. */
function pdbChains(content: string): Chain[] {
    const chains = new Map<string, Chain>();
    const seen = new Set<string>();
    for (const line of content.split(/\r?\n/)) {
        if (!/^(ATOM  |HETATM)/.test(line)) continue;
        const chainId = line.slice(21, 22).trim();
        const resNum = Number.parseInt(line.slice(22, 26), 10);
        const iCode = line.slice(26, 27).trim() || undefined;
        const resName = line.slice(17, 20).trim();
        const aa = aminoAcids[resName];
        const key = JSON.stringify([chainId, resNum, iCode]);
        if (!aa || !Number.isFinite(resNum) || seen.has(key)) continue;
        seen.add(key);
        if (!chains.has(chainId)) chains.set(chainId, { id: chainId, residues: [], sequence: '', length: 0 });
        const chain = chains.get(chainId)!;
        chain.residues.push({ chainId, resNum, iCode, resName, aa });
        chain.sequence += aa; chain.length++;
    }
    return [...chains.values()];
}

/** Unlike the legacy mmCIF → PDB parser, never truncate author chain IDs. */
export async function parseBC2Document(content: string, name: string, chainSubset?: readonly string[]): Promise<BC2Document> {
    if (/\.(fa|fasta|faa)$/i.test(name) || content.trimStart().startsWith('>')) {
        const records: Record<string, string> = Object.create(null);
        let id = '';
        for (const line of content.split(/\r?\n/).map(row => row.trim()).filter(Boolean)) {
            if (line.startsWith('>')) { id = line.slice(1).trim().split(/\s+/)[0]; records[id] ??= ''; }
            else { records[id] = (records[id] || '') + line; }
        }
        return { content, format: 'fasta', models: [], records };
    }
    if (!/\.(cif|mmcif)$/i.test(name) && !content.includes('_atom_site.')) {
        const source = chainSubset ? content.split(/\r?\n/).filter(line => !/^(ATOM  |HETATM|ANISOU|TER)/.test(line) || chainSubset.includes(line.slice(21, 22).trim())).join('\n') : content;
        const parsed = parsePDB(source);
        return { content, format: 'pdb', models: parsed.models.map(model => ({ number: model.modelNumber, content: model.content, chains: pdbChains(model.content) })) };
    }
    const [{ CIF }, { CifWriter }] = await Promise.all([import('molstar/lib/mol-io/reader/cif'), import('molstar/lib/mol-io/writer/cif')]);
    const parsed = await CIF.parseText(content).run();
    if (parsed.isError) throw new Error(`Could not read mmCIF: ${parsed.message}`);
    const block = parsed.result.blocks[0];
    const atoms = block?.categories.atom_site;
    if (!atoms) return { content, format: 'cif', models: [] };
    const get = (field: string, row: number) => { const column = atoms.getField(field); return column && column.valueKind(row) === 0 ? column.str(row) : ''; };
    const rowsByModel = new Map<number, number[]>();
    for (let row = 0; row < atoms.rowCount; row++) {
        if (chainSubset && !chainSubset.includes(get('auth_asym_id', row) || get('label_asym_id', row))) continue;
        const number = Number(get('pdbx_PDB_model_num', row) || 1);
        if (!rowsByModel.has(number)) rowsByModel.set(number, []);
        rowsByModel.get(number)!.push(row);
    }
    const models: BC2Model[] = [];
    for (const [number, rows] of rowsByModel) {
        const chains = new Map<string, Chain>();
        const seen = new Set<string>();
        for (const row of rows) {
            const id = get('auth_asym_id', row) || get('label_asym_id', row);
            const resNum = Number(get('auth_seq_id', row) || get('label_seq_id', row));
            const iCode = get('pdbx_PDB_ins_code', row) || undefined;
            const resName = get('auth_comp_id', row) || get('label_comp_id', row);
            const aa = aminoAcids[resName];
            if (!aa || !Number.isFinite(resNum)) continue;
            const key = JSON.stringify([id, resNum, iCode]);
            if (seen.has(key)) continue;
            seen.add(key);
            if (!chains.has(id)) chains.set(id, { id, residues: [], sequence: '', length: 0 });
            const chain = chains.get(id)!;
            chain.residues.push({ chainId: id, resNum, iCode, resName, aa });
            chain.sequence += aa; chain.length++;
        }
        const encoder = CifWriter.createEncoder({ binary: false });
        encoder.startDataBlock(block.header);
        for (const categoryName of block.categoryNames) {
            const category = block.categories[categoryName];
            const indices = categoryName === 'atom_site' ? rows : Array.from({ length: category.rowCount }, (_, i) => i);
            encoder.writeCategory({ name: categoryName, instance: () => ({
                fields: category.fieldNames.map(fieldName => {
                    const field = category.getField(fieldName)!;
                    return CifWriter.Field.str<number>(fieldName, i => field.str(indices[i]), { valueKind: i => field.valueKind(indices[i]) });
                }), source: [{ rowCount: indices.length }],
            }) });
        }
        models.push({ number, chains: [...chains.values()], content: String(encoder.getData()) });
    }
    return { content, format: 'cif', models };
}

export async function sliceBC2Structure(document: BC2Document, chainIds: readonly string[]): Promise<string> {
    const parsed = await parseBC2Document(document.models[0]?.content || document.content, `source.${document.format}`, chainIds);
    if (!parsed.models[0]) throw new Error('Select at least one chain present in this structure.');
    return parsed.models[0].content;
}

export function fastaChains(document: BC2Document, requestedChain: string): Chain[] {
    const records = document.records || {};
    const keys = Object.keys(records);
    const id = requestedChain || 'A';
    const sequence = records[id] ?? (keys.length === 1 ? records[keys[0]] : undefined);
    if (sequence === undefined) return [];
    return [{ id, sequence, length: sequence.length, residues: Array.from(sequence, (aa, i) => ({ chainId: id, resNum: i + 1, aa, resName: aa })) }];
}
export const residueKey = (residue: Residue) => `${residue.chainId}${residue.resNum}${residue.iCode || ''}`;
export function selectedChains(specification: string, chains: Chain[]): string[] {
    if (!specification.trim()) return chains.map(chain => chain.id);
    return specification.split(',').map(name => name.trim()).filter(Boolean).flatMap(id => chains.some(chain => chain.id === id) || id.length === 1 ? [id] : [...id]);
}

// Pinned native _EDIT_RE addresses chain letters + residue number, not insertion codes.
// Preserve untouched expressions (including unknown future syntax); edit only spans
// touched by an explicit visual gesture. No renumbering and no inferred interface mask.
function span(token: string, firstChain: string) {
    const match = /^(?:([A-Za-z]+))?(\d+)(?:-(\d+))?$/.exec(token.trim());
    if (!match) return null;
    const start = Number(match[2]); const end = Number(match[3] || match[2]);
    return { chain: match[1] || firstChain, start: Math.min(start, end), end: Math.max(start, end) };
}
export function visualResidues(specification: string, chains: Chain[], firstChain: string): Set<string> {
    const spans = specification.split(',').map(token => span(token, firstChain));
    return new Set(chains.flatMap(chain => chain.residues.filter(residue => spans.some(range => range && range.chain === residue.chainId && residue.resNum >= range.start && residue.resNum <= range.end)).map(residueKey)));
}
export function editVisualResidues(specification: string, before: Set<string>, after: Set<string>, chains: Chain[], firstChain: string): string {
    const residues = chains.flatMap(chain => chain.residues);
    let tokens = specification ? specification.split(',') : [];
    const changed = new Set([...before, ...after].filter(key => before.has(key) !== after.has(key)));
    for (const key of changed) {
        const residue = residues.find(item => residueKey(item) === key);
        if (!residue) continue;
        if (!/^[A-Za-z]+$/.test(residue.chainId) || residue.resNum < 0) throw new Error('This residue has no native chain-letter / positive-number span. Edit the native residue expression directly.');
        const native = `${residue.chainId}${residue.resNum}`;
        if (after.has(key)) {
            if (!tokens.some(token => { const range = span(token, firstChain); return range && range.chain === residue.chainId && range.start <= residue.resNum && range.end >= residue.resNum; })) tokens.push(native);
        } else {
            tokens = tokens.flatMap(token => {
                const range = span(token, firstChain);
                if (!range || range.chain !== residue.chainId || residue.resNum < range.start || residue.resNum > range.end) return [token];
                const part = (start: number, end: number) => `${range.chain}${start}${start === end ? '' : `-${end}`}`;
                return [...(residue.resNum > range.start ? [part(range.start, residue.resNum - 1)] : []), ...(residue.resNum < range.end ? [part(residue.resNum + 1, range.end)] : [])];
            });
        }
    }
    return tokens.join(',');
}

/** Native source replacement intentionally detaches old source-coordinate selections. */
export function replaceTargetSource(target: BC2Target, path: string, name: string): BC2Target {
    if (target.target_path === path) return target;
    const next: BC2Target = { ...target, target_path: path, name: textValue(target.name) || name };
    delete next.chains; delete next.hotspots; delete next.coldspots;
    return next;
}
