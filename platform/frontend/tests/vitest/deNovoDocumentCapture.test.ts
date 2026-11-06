import { createHash, webcrypto } from 'node:crypto';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

const upload = vi.hoisted(() => vi.fn());
vi.mock('../../src/lib/api', () => ({ uploadImmutableFile: upload }));
import { adoptAnnotatedCif, DocumentCaptureRegistry, type CapturedAnnotatedDocument } from '../../src/lib/deNovoDocumentCapture';

beforeEach(() => vi.stubGlobal('crypto', webcrypto));
afterEach(() => { vi.unstubAllGlobals(); vi.clearAllMocks(); });
const retained = (path = 'inputs/original.cif'): CapturedAnnotatedDocument => ({
    format: 'cif', source: { path, expected_sha256: 'a'.repeat(64) }, sha256: 'a'.repeat(64), size: 123,
});
const blobBytes = (blob: Blob): Promise<ArrayBuffer> => new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error);
    reader.onload = () => resolve(reader.result as ArrayBuffer);
    reader.readAsArrayBuffer(blob);
});

it('uploads one exact frozen CIF and retains ancestry/MODEL outside the scientific source selector', async () => {
    const cif = 'data_edited\r\n_custom.note "α exact bytes"\r\n';
    const expected = createHash('sha256').update(cif).digest('hex');
    upload.mockImplementation(async (directory: string, file: File, sha: string) => {
        expect(directory).toBe('inputs/annotated_structures');
        expect(file.name).toBe(`edited-${expected}.cif`);
        expect(file.type).toBe('chemical/x-mmcif');
        expect(Array.from(new Uint8Array(await blobBytes(file)))).toEqual(Array.from(new TextEncoder().encode(cif)));
        expect(sha).toBe(expected);
        return { data: { path: `${directory}/${file.name}`, sha256: sha, size: file.size, existing: false } };
    });
    const original = { path: 'inputs/original.cif', model_number: 7, expected_sha256: 'b'.repeat(64) };
    const provenance = { revision: 3, recipe: { strands: [], unknown: null } };
    const result = await adoptAnnotatedCif(cif, { originalSource: original, modelNumber: 7, provenance });
    expect(upload).toHaveBeenCalledTimes(1);
    expect(result.source).toEqual({ path: `inputs/annotated_structures/edited-${expected}.cif`,
        expected_sha256: expected, model_number: 7, output_format: 'native' });
    expect(result.original_source).toEqual(original);
    expect(result.provenance).toEqual(provenance);
    expect(result.source).not.toHaveProperty('provenance');
    provenance.revision = 4;
    expect(result.provenance?.revision).toBe(3);
    expect(JSON.parse(JSON.stringify(result))).toEqual(result);
});

it('does not claim capture when persistence failed or returned different bytes', async () => {
    upload.mockRejectedValueOnce(new Error('disk unavailable'));
    await expect(adoptAnnotatedCif('data_x\n')).rejects.toThrow('disk unavailable');
    upload.mockResolvedValueOnce({ data: { path: 'inputs/wrong.cif', sha256: '0'.repeat(64), size: 7 } });
    await expect(adoptAnnotatedCif('data_x\n')).rejects.toThrow('does not match');
});

it('source-free/manual authoring does not require an editor registration', async () => {
    expect(await new DocumentCaptureRegistry().capture()).toEqual({});
    expect(upload).not.toHaveBeenCalled();
});

it('captures each registered live document exactly once per action', async () => {
    const registry = new DocumentCaptureRegistry();
    const first = vi.fn(async () => retained('inputs/first.cif'));
    const second = vi.fn(async () => retained('inputs/second.cif'));
    registry.register('first', { generation: () => 0, capture: first });
    registry.register('second', { generation: () => 0, capture: second });
    const captured = await registry.capture();
    expect(captured.first.source.path).toBe('inputs/first.cif');
    expect(captured.second.source.path).toBe('inputs/second.cif');
    expect(first).toHaveBeenCalledTimes(1);
    expect(second).toHaveBeenCalledTimes(1);
});

it('does not clear a successor registration when the old component unmounts', async () => {
    const registry = new DocumentCaptureRegistry();
    const removeOld = registry.register('source', { generation: () => 0, capture: async () => retained('inputs/old.cif') });
    registry.register('source', { generation: () => 0, capture: async () => retained('inputs/current.cif') });
    removeOld();
    expect((await registry.capture()).source.source.path).toBe('inputs/current.cif');
});

it('rejects a stale action after A→B→A changes and allows a fresh capture', async () => {
    const registry = new DocumentCaptureRegistry();
    let generation = 0;
    let finish!: (value: CapturedAnnotatedDocument) => void;
    const capture = vi.fn(() => new Promise<CapturedAnnotatedDocument>(resolve => { finish = resolve; }));
    registry.register('source', { generation: () => generation, capture });
    const pending = registry.capture();
    generation = 1; generation = 2;
    finish(retained());
    await expect(pending).rejects.toThrow('document changed');
    const current = registry.capture();
    finish(retained('inputs/current.cif'));
    expect((await current).source.source.path).toBe('inputs/current.cif');
});

it('dirty notifications invalidate an in-flight capture and notify only mounted inspection subscribers', async () => {
    const registry = new DocumentCaptureRegistry(); const notify = vi.fn();
    const unsubscribe = registry.subscribe(notify);
    let finish!: (value: CapturedAnnotatedDocument) => void;
    registry.register('source', { generation: () => 0, capture: () => new Promise(resolve => { finish = resolve; }) });
    const before = registry.getSnapshot(); const pending = registry.capture();
    registry.changed(); registry.changed();
    expect(registry.getSnapshot()).toBe(before + 2);
    finish(retained()); await expect(pending).rejects.toThrow('document changed');
    expect(notify).toHaveBeenCalledTimes(3); unsubscribe(); registry.changed();
    expect(notify).toHaveBeenCalledTimes(3);
});

it('removing an editor during capture cannot save its late export', async () => {
    const registry = new DocumentCaptureRegistry();
    let finish!: (value: CapturedAnnotatedDocument) => void;
    const remove = registry.register('source', { generation: () => 0,
        capture: () => new Promise(resolve => { finish = resolve; }) });
    const pending = registry.capture();
    remove(); finish(retained());
    await expect(pending).rejects.toThrow('document changed');
    expect(await registry.capture()).toEqual({});
});
