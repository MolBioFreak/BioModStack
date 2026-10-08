/**
 * MolBioToolkit - Molecular Biology Toolkit
 *
 * OVE-based sequence editor with persistence + cloning/mutagenesis workflows.
 */

import { useState, useCallback, useMemo, useEffect, memo } from 'react';

// Import OVE from workspace package
import { Editor, updateEditor } from '@biomodstack/ove';
import { Provider } from 'react-redux';
import { store } from './store';
import { anyToJson, jsonToGenbank } from '@teselagen/bio-parsers';
// Import styles directly
import '@biomodstack/ove/style.css';
import './ove-theme.css';

// Types for sequence data
interface SequenceFeature {
    id: string;
    name: string;
    type?: string;
    start: number;
    end: number;
    strand?: number;
    color?: string;
    notes?: Record<string, unknown> | null;
}

interface PrimerFeature {
    id: string;
    name: string;
    sequence?: string;
    start: number;
    end: number;
    strand?: number;
    tm?: number;
    gc_percent?: number;
}

interface SequenceData {
    name: string;
    circular: boolean;
    sequence: string;
    features: SequenceFeature[];
    primers?: PrimerFeature[];
}

interface SequenceListItem {
    id: string;
    name: string;
    description?: string | null;
    sequence_type: string;
    is_circular: boolean;
    length: number;
    gc_content?: number | null;
    feature_count: number;
    created_at: string;
}

interface DigestFragment {
    sequence: string;
    start: number;
    end: number;
}

const SAMPLE_SEQUENCE: SequenceData = {
    name: "Sample Plasmid",
    circular: true,
    sequence: "GAATTCGAGCTCGGTACCCGGGGATCCTCTAGAGTCGACCTGCAGGCATGCAAGCTTGGCGTAATCATGGTCATAGCTGTTTCCTGTGTGAAATTGTTATCCGCTCACAATTCCACACAACATACGAGCCGGAAGCATAAAGTGTAAAGCCTGGGGTGCCTAATGAGTGAGCTAACTCACATTAATTGCGTTGCGCTCACTGCCCGCTTTCCAGTCGGGAAACCTGTCGTGCCAGCTGCATTAATGAATCGGCCAACGCGCGGGGAGAGGCGGTTTGCGTATTGGGCGCTCTTCCGCTTCCTCGCTCACTGACTCGCTGCGCTCGGTCGTTCGGCTGCGGCGAGCGGTATCAGCTCACTCAAAGGCGGTAATACGGTTATCCACAGAATCAGGGGATAACGCAGGAAAGAACATGTGAGCAAAAGGCCAGCAAAAGGCCAGGAACCGTAAAAAGGCCGCGTTGCTGGCGTTTTTCCATAGGCTCCGCCCCCCTGACGAGCATCACAAAAATCGACGCTCAAGTCAGAGGTGGCGAAACCCGACAGGACTATAAAGATACCAGGCGTTTCCCCCTGGAAGCTCCCTCGTGCGCTCTCCTGTTCCGACCCTGCCGCTTACCGGATACCTGTCCGCCTTTCTCCCTTCGGGAAGCGTGGCGCTTTCTCATAGCTCACGCTGTAGGTATCTCAGTTCGGTGTAGGTCGTTCGCTCCAAGCTGGGCTGTGTGCACGAACCCCCCGTTCAGCCCGACCGCTGCGCCTTATCCGGTAACTATCGTCTTGAGTCCAACCCGGTAAGACACGACTTATCGCCACTGGCAGCAGCCACTGGTAACAGGATTAGCAGAGCGAGGTATGTAGGCGGTGCTACAGAGTTCTTGAAGTGGTGGCCTAACTACGGCTACACTAGAAGGACAGTATTTGGTATCTGCGCTCTGCTGAAGCCAGTTACCTTCGGAAAAAGAGTTGGTAGCTCTTGATCCGGCAAACAAACCACCGCTGGTAGCGGTGGTTTTTTTGTTTGCAAGCAGCAGATTACGCGCAGAAAAAAAGGATCTCAAGAAGATCCTTTGATCTTTTCTACGGGGTCTGACGCTCAGTGGAACGAAAACTCACGTTAAGGGATTTTGGTCATGAGATTATCAAAAAGGATCTTCACCTAGATCCTTTTAAATTAAAAATGAAGTTTTAAATCAATCTAAAGTATATATGAGTAAACTTGGTCTGACAGTTACCAATGCTTAATCAGTGAGGCACCTATCTCAGCGATCTGTCTATTTCGTTCATCCATAGTTGCCTGACTCCCCGTCGTGTAGATAACTACGATACGGGAGGGCTTACCATCTGGCCCCAGTGCTGCAATGATACCGCGAGACCCACGCTCACCGGCTCCAGATTTATCAGCAATAAACCAGCCAGCCGGAAGGGCCGAGCGCAGAAGTGGTCCTGCAACTTTATCCGCCTCCATCCAGTCTATTAATTGTTGCCGGGAAGCTAGAGTAAGTAGTTCGCCAGTTAATAGTTTGCGCAACGTTGTTGCCATTGCTACAGGCATCGTGGTGTCACGCTCGTCGTTTGGTATGGCTTCATTCAGCTCCGGTTCCCAACGATCAAGGCGAGTTACATGATCCCCCATGTTGTGCAAAAAAGCGGTTAGCTCCTTCGGTCCTCCGATCGTTGTCAGAAGTAAGTTGGCCGCAGTGTTATCACTCATGGTTATGGCAGCACTGCATAATTCTCTTACTGTCATGCCATCCGTAAGATGCTTTTCTGTGACTGGTGAGTACTCAACCAAGTCATTCTGAGAATAGTGTATGCGGCGACCGAGTTGCTCTTGCCCGGCGTCAACACGGGATAATACCGCGCCACATAGCAGAACTTTAAAAGTGCTCATCATTGGAAAACGTTCTTCGGGGCGAAAACTCTCAAGGATCTTACCGCTGTTGAGATCCAGTTCGATGTAACCCACTCGTGCACCCAACTGATCTTCAGCATCTTTTACTTTCACCAGCGTTTCTGGGTGAGCAAAAACAGGAAGGCAAAATGCCGCAAAAAAGGGAATAAGGGCGACACGGAAATGTTGAATACTCATACTCTTCCTTTTTCAATATTATTGAAGCATTTATCAGGGTTATTGTCTCATGAGCGGATACATATTTGAATGTATTTAGAAAAATAAACAAATAGGGGTTCCGCGCACATTTCCCCGAAAAGTGCCACCTGACGTCTAAGAAACCATTATTATCATGACATTAACCTATAAAAATAGGCGTATCACGAGGCCCTTTCGTCTTCAA",
    features: [
        { id: "f1", name: "lac promoter", type: "promoter", start: 0, end: 50, strand: 1, color: "#31B440" },
        { id: "f2", name: "MCS", type: "misc_feature", start: 51, end: 100, strand: 1, color: "#C6C9D1" },
        { id: "f3", name: "lacZ alpha", type: "CDS", start: 101, end: 400, strand: 1, color: "#EF6500" },
        { id: "f4", name: "ori", type: "rep_origin", start: 800, end: 1200, strand: 1, color: "#FFCC00" },
        { id: "f5", name: "AmpR", type: "CDS", start: 1500, end: 2200, strand: -1, color: "#F74F4F" },
    ],
    primers: []
};

interface OVEWrapperProps {
    sequenceData: SequenceData;
    onSave?: (data: unknown) => void;
}

const EDITOR_NAME = 'MolBioToolkitEditor';

function reverseComplement(seq: string): string {
    const map: Record<string, string> = { A: 'T', T: 'A', C: 'G', G: 'C', N: 'N' };
    return seq.toUpperCase().split('').reverse().map(c => map[c] || 'N').join('');
}

function calcGcPercent(seq: string): number {
    if (!seq.length) return 0;
    const gc = seq.split('').filter(c => c === 'G' || c === 'C').length;
    return Math.round((gc / seq.length) * 1000) / 10;
}

function calcTmWallace(seq: string): number {
    const s = seq.toUpperCase();
    const a = (s.match(/A/g) || []).length;
    const t = (s.match(/T/g) || []).length;
    const g = (s.match(/G/g) || []).length;
    const c = (s.match(/C/g) || []).length;
    return 2 * (a + t) + 4 * (g + c);
}

function detectSequenceType(seq: string): 'dna' | 'rna' {
    const hasU = /U/i.test(seq);
    const hasT = /T/i.test(seq);
    if (hasU && !hasT) return 'rna';
    return 'dna';
}

const OVEWrapper = memo(function OVEWrapper({ sequenceData, onSave }: OVEWrapperProps) {
    const oveSequenceData = useMemo(() => ({
        name: sequenceData.name,
        circular: sequenceData.circular,
        sequence: sequenceData.sequence,
        features: sequenceData.features.map(f => ({
            id: f.id,
            name: f.name,
            type: f.type || 'misc_feature',
            start: f.start,
            end: f.end,
            strand: f.strand || 1,
            forward: (f.strand || 1) === 1,
            color: f.color,
            notes: f.notes || undefined
        })),
        primers: (sequenceData.primers || []).map(p => ({
            id: p.id,
            name: p.name,
            type: 'primer_bind',
            start: p.start,
            end: p.end,
            strand: p.strand || 1,
            forward: (p.strand || 1) === 1,
            sequence: p.sequence
        }))
    }), [sequenceData]);

    useEffect(() => {
        updateEditor(store, EDITOR_NAME, {
            readOnly: false,
            sequenceData: oveSequenceData,
            panelsShown: [
                [
                    { active: true, id: "circular", name: "Circular Map" },
                    { id: "digestTool", name: "Digest" },
                    { id: "pcrTool", name: "PCR" }
                ],
                [
                    { id: "sequence", name: "Sequence Map", active: true },
                    { id: "rail", name: "Linear Map" },
                    { id: "properties", name: "Properties" }
                ]
            ],
            annotationsToSupport: {
                features: true,
                translations: true,
                parts: true,
                orfs: true,
                cutsites: true,
                primers: true,
                warnings: true,
                lineageAnnotations: true,
                assemblyPieces: true
            },
            annotationVisibility: {
                features: true,
                parts: true,
                primers: true,
                cutsites: true,
                orfs: false,
                orfTranslations: false,
                translations: true,
                axis: true,
                axisNumbers: true,
                reverseSequence: true,
                dnaColors: false,
                sequence: true,
                caret: true
            }
        });
    }, [oveSequenceData]);

    return (
        <div className="ove-editor-container w-full h-full">
            <Provider store={store}>
                <Editor
                    editorName={EDITOR_NAME}
                    showMenuBar={true}
                    onSave={onSave}
                    PropertiesProps={{
                        propertiesList: [
                            "general",
                            "features",
                            "parts",
                            "primers",
                            "translations",
                            "cutsites",
                            "orfs",
                            "genbank"
                        ]
                    }}
                    ToolBarProps={{
                        toolList: [
                            "saveTool",
                            "downloadTool",
                            "importTool",
                            "undoTool",
                            "redoTool",
                            "cutsiteTool",
                            "featureTool",
                            "partTool",
                            "primerTool",
                            "oligoTool",
                            "orfTool",
                            "editTool",
                            "findTool",
                            "alignmentTool",
                            "visibilityTool"
                        ]
                    }}
                />
            </Provider>
        </div>
    );
});

export function MolBioToolkit() {
    const [sequenceData, setSequenceData] = useState<SequenceData>(SAMPLE_SEQUENCE);
    const [selectedSequenceId, setSelectedSequenceId] = useState<string | null>(null);
    const [sequenceList, setSequenceList] = useState<SequenceListItem[]>([]);
    const [sequenceSearch, setSequenceSearch] = useState('');
    const [isLoading, setIsLoading] = useState(false);
    const [showImportModal, setShowImportModal] = useState(false);
    const [saveMessage, setSaveMessage] = useState('');
    const [operationError, setOperationError] = useState('');

    // Operation state
    const [digestEnzymes, setDigestEnzymes] = useState('EcoRI:GAATTC');
    const [digestFragments, setDigestFragments] = useState<DigestFragment[]>([]);
    const [selectedFragments, setSelectedFragments] = useState<number[]>([]);

    const [pcrFwd, setPcrFwd] = useState('');
    const [pcrRev, setPcrRev] = useState('');
    const [pcrName, setPcrName] = useState('');

    const [mutationsInput, setMutationsInput] = useState('A123G');
    const [mutName, setMutName] = useState('');

    const [gibsonOverlap, setGibsonOverlap] = useState(20);
    const [gibsonName, setGibsonName] = useState('');

    const [goldenEnzymes, setGoldenEnzymes] = useState('BsaI:GGTCTC');
    const [goldenName, _setGoldenName] = useState('');

    const [msaJobId, setMsaJobId] = useState<string | null>(null);
    const [msaStatus, setMsaStatus] = useState<string>('');
    const [msaManifest, setMsaManifest] = useState<string>('');

    const refreshSequenceList = useCallback(async () => {
        try {
            const res = await fetch('/api/sequences?limit=500&offset=0');
            if (!res.ok) return;
            const data = await res.json();
            setSequenceList(Array.isArray(data) ? data : []);
        } catch (err) {
            console.error('Failed to load sequences', err);
        }
    }, []);

    useEffect(() => {
        refreshSequenceList();
    }, [refreshSequenceList]);

    const filteredSequences = useMemo(() => {
        if (!sequenceSearch.trim()) return sequenceList;
        const q = sequenceSearch.toLowerCase();
        return sequenceList.filter(s => s.name.toLowerCase().includes(q));
    }, [sequenceList, sequenceSearch]);

    const getEditorSequenceData = useCallback(() => {
        const state = store.getState() as any;
        const editor = state?.VectorEditor?.[EDITOR_NAME];
        return editor?.sequenceData || null;
    }, []);

    const normalizePrimers = useCallback((primers: any[], sequence: string): PrimerFeature[] => {
        return (primers || []).map((p: any, idx: number) => {
            const start = p.start ?? p.startIndex ?? 0;
            const end = p.end ?? p.endIndex ?? start;
            const forward = p.forward !== undefined ? p.forward : (p.strand || 1) === 1;
            let primerSeq = p.sequence || '';
            if (!primerSeq && end > start && sequence.length >= end) {
                const slice = sequence.slice(start, end);
                primerSeq = forward ? slice : reverseComplement(slice);
            }
            const gc = primerSeq ? calcGcPercent(primerSeq) : undefined;
            const tm = primerSeq ? calcTmWallace(primerSeq) : undefined;
            return {
                id: p.id || `primer_${idx}`,
                name: p.name || `Primer ${idx + 1}`,
                sequence: primerSeq || undefined,
                start,
                end,
                strand: forward ? 1 : -1,
                tm,
                gc_percent: gc
            };
        });
    }, []);

    const buildSavePayload = useCallback(() => {
        const editorData = getEditorSequenceData();
        const base = editorData || sequenceData;
        const seq = base.sequence || '';
        const features = (base.features || []).map((f: any, idx: number) => ({
            id: f.id || `feat_${idx}`,
            name: f.name || 'Feature',
            type: f.type || 'misc_feature',
            start: f.start,
            end: f.end,
            strand: f.strand || (f.forward ? 1 : -1),
            color: f.color,
            notes: f.notes || null
        }));
        const primers = normalizePrimers(base.primers || [], seq);

        return {
            name: base.name || sequenceData.name,
            description: '',
            sequence: seq,
            sequence_type: detectSequenceType(seq),
            is_circular: !!base.circular,
            features,
            primers
        };
    }, [getEditorSequenceData, sequenceData, normalizePrimers]);

    const loadSequenceById = useCallback(async (id: string) => {
        setIsLoading(true);
        try {
            const res = await fetch(`/api/sequences/${id}`);
            if (!res.ok) throw new Error('Failed to load sequence');
            const seq = await res.json();
            setSelectedSequenceId(seq.id);
            setSequenceData({
                name: seq.name,
                circular: seq.is_circular,
                sequence: (seq.sequence || '').toUpperCase(),
                features: seq.features || [],
                primers: seq.primers || []
            });
            setDigestFragments([]);
            setSelectedFragments([]);
        } catch (err) {
            console.error(err);
            setOperationError('Failed to load sequence');
        } finally {
            setIsLoading(false);
        }
    }, []);

    const handleSaveSequence = useCallback(async () => {
        setSaveMessage('');
        setOperationError('');
        try {
            const payload = buildSavePayload();
            const res = await fetch(selectedSequenceId ? `/api/sequences/${selectedSequenceId}` : '/api/sequences', {
                method: selectedSequenceId ? 'PUT' : 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            if (!res.ok) {
                const err = await res.json().catch(() => ({}));
                throw new Error(err.detail || 'Save failed');
            }
            const saved = await res.json();
            setSelectedSequenceId(saved.id);
            setSequenceData({
                name: saved.name,
                circular: saved.is_circular,
                sequence: saved.sequence,
                features: saved.features || [],
                primers: saved.primers || []
            });
            setSaveMessage('Saved');
            await refreshSequenceList();
        } catch (err: any) {
            console.error(err);
            setOperationError(err.message || 'Save failed');
        }
    }, [buildSavePayload, selectedSequenceId, refreshSequenceList]);

    const handleFileImport = useCallback(async (file: File) => {
        setIsLoading(true);
        try {
            const text = await file.text();
            const results = await anyToJson(text, {
                fileName: file.name,
                parseOptions: { inclusive1BasedStart: false, jsonType: 'json' }
            });
            const parsedSeq = Array.isArray(results) ? results[0] : results;
            if (parsedSeq && parsedSeq.parsedSequence) {
                const seq = parsedSeq.parsedSequence;
                setSelectedSequenceId(null);
                setSequenceData({
                    name: seq.name || file.name.replace(/\.[^/.]+$/, ''),
                    circular: seq.circular ?? true,
                    sequence: (seq.sequence || '').toUpperCase(),
                    features: (seq.features || []).map((f: any) => ({
                        id: f.id || Math.random().toString(36).slice(2),
                        name: f.name || 'Untitled Feature',
                        type: f.type || 'misc_feature',
                        start: f.start,
                        end: f.end,
                        strand: f.strand,
                        color: f.color
                    })),
                    primers: (seq.primers || []).map((p: any) => ({
                        id: p.id || Math.random().toString(36).slice(2),
                        name: p.name || 'Untitled Primer',
                        sequence: p.sequence,
                        start: p.start,
                        end: p.end,
                        strand: p.strand,
                        tm: p.tm,
                        gc_percent: p.gc_percent
                    }))
                });
            } else {
                alert('Could not parse sequence from file.');
            }
            setShowImportModal(false);
        } catch (error) {
            console.error('Failed to import file:', error);
            alert('Error parsing file. See console for details.');
        } finally {
            setIsLoading(false);
        }
    }, []);

    const handleDrop = useCallback((e: React.DragEvent) => {
        e.preventDefault();
        const file = e.dataTransfer.files[0];
        if (file) handleFileImport(file);
    }, [handleFileImport]);

    const handleDragOver = useCallback((e: React.DragEvent) => {
        e.preventDefault();
    }, []);

    const stats = useMemo(() => {
        const seq = sequenceData.sequence;
        const gc = seq.replace(/[^GCgc]/g, '').length;
        return {
            length: seq.length,
            gcContent: seq.length ? ((gc / seq.length) * 100).toFixed(1) : '0.0',
        };
    }, [sequenceData.sequence]);

    const parseEnzymes = useCallback((input: string) => {
        return input
            .split(',')
            .map(s => s.trim())
            .filter(Boolean)
            .map((entry) => {
                const [name, site] = entry.includes(':') ? entry.split(':') : [entry, entry];
                return { name: name.trim(), site: (site || '').trim() };
            });
    }, []);

    const runDigest = useCallback(async () => {
        setOperationError('');
        try {
            const enzymes = parseEnzymes(digestEnzymes);
            const res = await fetch('/api/molbio/digest', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    sequence_id: selectedSequenceId,
                    sequence: selectedSequenceId ? undefined : sequenceData.sequence,
                    is_circular: sequenceData.circular,
                    enzymes,
                    save: false
                })
            });
            if (!res.ok) throw new Error('Digest failed');
            const data = await res.json();
            setDigestFragments(data.fragments || []);
            setSelectedFragments([]);
        } catch (err: any) {
            setOperationError(err.message || 'Digest failed');
        }
    }, [digestEnzymes, parseEnzymes, selectedSequenceId, sequenceData]);

    const runLigation = useCallback(async () => {
        setOperationError('');
        try {
            if (selectedFragments.length === 0) {
                throw new Error('Select fragments to ligate');
            }
            const fragments = selectedFragments.map(i => digestFragments[i]?.sequence).filter(Boolean) as string[];
            const res = await fetch('/api/molbio/ligate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    fragments,
                    circular: true,
                    parent_id: selectedSequenceId,
                    save: true,
                    new_name: sequenceData.name + '_ligated'
                })
            });
            if (!res.ok) throw new Error('Ligation failed');
            const data = await res.json();
            if (data.sequence?.id) {
                await loadSequenceById(data.sequence.id);
                await refreshSequenceList();
            }
        } catch (err: any) {
            setOperationError(err.message || 'Ligation failed');
        }
    }, [selectedFragments, digestFragments, selectedSequenceId, sequenceData.name, loadSequenceById, refreshSequenceList]);

    const runPCR = useCallback(async () => {
        setOperationError('');
        try {
            const res = await fetch('/api/molbio/pcr', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    sequence_id: selectedSequenceId,
                    sequence: selectedSequenceId ? undefined : sequenceData.sequence,
                    primer_fwd: pcrFwd,
                    primer_rev: pcrRev,
                    save: true,
                    new_name: pcrName || (sequenceData.name + '_PCR')
                })
            });
            if (!res.ok) throw new Error('PCR failed');
            const data = await res.json();
            if (data.sequence?.id) {
                await loadSequenceById(data.sequence.id);
                await refreshSequenceList();
            }
        } catch (err: any) {
            setOperationError(err.message || 'PCR failed');
        }
    }, [selectedSequenceId, sequenceData, pcrFwd, pcrRev, pcrName, loadSequenceById, refreshSequenceList]);

    const parseMutations = useCallback((input: string) => {
        return input.split(',').map(s => s.trim()).filter(Boolean).map((token) => {
            const match = token.match(/^([ACGT])?(\d+)([ACGT])$/i);
            if (!match) throw new Error(`Invalid mutation: ${token}`);
            return {
                pos: parseInt(match[2], 10),
                from: match[1]?.toUpperCase(),
                to: match[3].toUpperCase()
            };
        });
    }, []);

    const runMutagenesis = useCallback(async () => {
        setOperationError('');
        try {
            const mutations = parseMutations(mutationsInput);
            const res = await fetch('/api/molbio/mutagenesis', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    sequence_id: selectedSequenceId,
                    sequence: selectedSequenceId ? undefined : sequenceData.sequence,
                    mutations,
                    save: true,
                    new_name: mutName || (sequenceData.name + '_mut')
                })
            });
            if (!res.ok) throw new Error('Mutagenesis failed');
            const data = await res.json();
            if (data.sequence?.id) {
                await loadSequenceById(data.sequence.id);
                await refreshSequenceList();
            }
        } catch (err: any) {
            setOperationError(err.message || 'Mutagenesis failed');
        }
    }, [mutationsInput, mutName, parseMutations, selectedSequenceId, sequenceData, loadSequenceById, refreshSequenceList]);

    const runGibson = useCallback(async () => {
        setOperationError('');
        try {
            if (selectedFragments.length === 0) throw new Error('Select fragments for Gibson');
            const fragments = selectedFragments.map(i => digestFragments[i]?.sequence).filter(Boolean) as string[];
            const res = await fetch('/api/molbio/gibson', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    fragments,
                    overlap_length: gibsonOverlap,
                    circular: true,
                    parent_id: selectedSequenceId,
                    save: true,
                    new_name: gibsonName || (sequenceData.name + '_gibson')
                })
            });
            if (!res.ok) throw new Error('Gibson assembly failed');
            const data = await res.json();
            if (data.sequence?.id) {
                await loadSequenceById(data.sequence.id);
                await refreshSequenceList();
            }
        } catch (err: any) {
            setOperationError(err.message || 'Gibson failed');
        }
    }, [selectedFragments, digestFragments, gibsonOverlap, gibsonName, selectedSequenceId, sequenceData.name, loadSequenceById, refreshSequenceList]);

    const runGoldenGate = useCallback(async () => {
        setOperationError('');
        try {
            if (selectedFragments.length === 0) throw new Error('Select fragments for Golden Gate');
            const fragments = selectedFragments.map(i => digestFragments[i]?.sequence).filter(Boolean) as string[];
            const enzymes = parseEnzymes(goldenEnzymes);
            const res = await fetch('/api/molbio/golden-gate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    fragments,
                    enzymes,
                    circular: true,
                    parent_id: selectedSequenceId,
                    save: true,
                    new_name: goldenName || (sequenceData.name + '_goldengate')
                })
            });
            if (!res.ok) throw new Error('Golden Gate assembly failed');
            const data = await res.json();
            if (data.sequence?.id) {
                await loadSequenceById(data.sequence.id);
                await refreshSequenceList();
            }
        } catch (err: any) {
            setOperationError(err.message || 'Golden Gate failed');
        }
    }, [selectedFragments, digestFragments, goldenEnzymes, parseEnzymes, goldenName, selectedSequenceId, sequenceData.name, loadSequenceById, refreshSequenceList]);

    const runMSA = useCallback(async () => {
        setOperationError('');
        try {
            const editorData = getEditorSequenceData();
            const seq = editorData?.sequence || sequenceData.sequence;
            const res = await fetch('/api/msa', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    name: sequenceData.name,
                    sequence_id: selectedSequenceId,
                    sequence: selectedSequenceId ? undefined : seq
                })
            });
            if (!res.ok) throw new Error('MSA job failed to start');
            const data = await res.json();
            setMsaJobId(data.job_id);
            setMsaStatus('queued');
        } catch (err: any) {
            setOperationError(err.message || 'MSA failed');
        }
    }, [sequenceData.name, selectedSequenceId, getEditorSequenceData]);

    useEffect(() => {
        if (!msaJobId) return;
        let cancelled = false;
        const timer = setInterval(async () => {
            try {
                const res = await fetch(`/api/jobs/${msaJobId}`);
                if (!res.ok) return;
                const data = await res.json();
                if (cancelled) return;
                setMsaStatus(data.status || 'queued');
                if (data.msa_manifest_path) {
                    setMsaManifest(data.msa_manifest_path);
                }
                if (['completed', 'failed', 'cancelled'].includes(data.status)) {
                    clearInterval(timer);
                }
            } catch (err) {
                // ignore
            }
        }, 5000);
        return () => {
            cancelled = true;
            clearInterval(timer);
        };
    }, [msaJobId]);

    return (
        <div
            className="h-screen flex flex-col bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900"
            onDrop={handleDrop}
            onDragOver={handleDragOver}
        >
            <div className="flex-shrink-0 h-14 border-b border-slate-700/50 bg-slate-900/80 backdrop-blur-sm">
                <div className="h-full px-4 flex items-center justify-between">
                    <div className="flex items-center gap-3">
                        <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-emerald-500 to-teal-500 flex items-center justify-center shadow-lg shadow-emerald-500/20">
                            <span className="text-white font-bold text-xs">SEQ</span>
                        </div>
                        <div className="flex items-center gap-2">
                            <span className="text-white font-semibold">{sequenceData.name}</span>
                            <span className="text-slate-500">•</span>
                            <span className="text-slate-400 text-sm">{stats.length.toLocaleString()} bp</span>
                            <span className="text-slate-500">•</span>
                            <span className="text-slate-400 text-sm">GC: {stats.gcContent}%</span>
                            <span className="text-slate-500">•</span>
                            <span className={`text-xs px-2 py-0.5 rounded-full ${sequenceData.circular
                                ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/30'
                                : 'bg-blue-500/20 text-blue-300 border border-blue-500/30'
                                }`}>
                                {sequenceData.circular ? 'Circular' : 'Linear'}
                            </span>
                        </div>
                    </div>

                    <div className="flex items-center gap-2">
                        {saveMessage && <span className="text-emerald-300 text-xs">{saveMessage}</span>}
                        <button
                            onClick={() => setShowImportModal(true)}
                            className="px-3 py-1.5 bg-slate-700/80 hover:bg-slate-600 text-white rounded-lg text-sm font-medium transition-all border border-slate-600/50 hover:border-slate-500"
                        >
                            Import
                        </button>
                        <button
                            onClick={handleSaveSequence}
                            className="px-3 py-1.5 bg-emerald-600 hover:bg-emerald-500 text-white rounded-lg text-sm font-medium transition-all shadow-lg shadow-emerald-600/20"
                        >
                            Save
                        </button>
                        <button
                            onClick={() => {
                                const state = store.getState() as any;
                                const editorState = state.VectorEditor[EDITOR_NAME];
                                if (!editorState || !editorState.sequenceData) {
                                    alert('No sequence data to export');
                                    return;
                                }
                                try {
                                    const genbankString = jsonToGenbank(editorState.sequenceData);
                                    const blob = new Blob([genbankString], { type: 'text/plain' });
                                    const url = URL.createObjectURL(blob);
                                    const link = document.createElement('a');
                                    link.href = url;
                                    link.download = `${editorState.sequenceData.name || 'sequence'}.gb`;
                                    document.body.appendChild(link);
                                    link.click();
                                    document.body.removeChild(link);
                                    URL.revokeObjectURL(url);
                                } catch (e) {
                                    console.error('Failed to generate GenBank file:', e);
                                    alert('Failed to export file');
                                }
                            }}
                            className="px-3 py-1.5 bg-slate-600 hover:bg-slate-500 text-white rounded-lg text-sm font-medium"
                        >
                            Export .gb
                        </button>
                    </div>
                </div>
            </div>

            <div className="flex-1 min-h-0 flex">
                {/* Left: Sequence Library */}
                <div className="w-72 border-r border-slate-700/50 bg-slate-900/70 p-3 overflow-y-auto">
                    <div className="text-slate-200 font-semibold mb-2">Sequence Library</div>
                    <input
                        type="text"
                        value={sequenceSearch}
                        onChange={(e) => setSequenceSearch(e.target.value)}
                        placeholder="Search..."
                        className="w-full bg-slate-800 border border-slate-700 rounded-md px-3 py-2 text-sm text-white"
                    />
                    <div className="mt-3 space-y-2">
                        {filteredSequences.map((seq) => (
                            <button
                                key={seq.id}
                                onClick={() => loadSequenceById(seq.id)}
                                className={`w-full text-left px-3 py-2 rounded-md border ${selectedSequenceId === seq.id
                                    ? 'bg-emerald-600/20 border-emerald-500/40 text-emerald-100'
                                    : 'bg-slate-800/60 border-slate-700 text-slate-200 hover:bg-slate-700/70'
                                    }`}
                            >
                                <div className="font-medium text-sm">{seq.name}</div>
                                <div className="text-xs text-slate-400">{seq.length} bp • {seq.is_circular ? 'Circular' : 'Linear'}</div>
                            </button>
                        ))}
                    </div>
                </div>

                {/* Center: Editor */}
                <div className="flex-1 min-h-0 overflow-hidden">
                    <OVEWrapper
                        sequenceData={{
                            name: sequenceData.name,
                            circular: sequenceData.circular,
                            sequence: sequenceData.sequence,
                            features: sequenceData.features,
                            primers: sequenceData.primers
                        }}
                        onSave={() => null}
                    />
                </div>

                {/* Right: Operations */}
                <div className="w-96 border-l border-slate-700/50 bg-slate-900/70 p-4 overflow-y-auto">
                    <div className="text-slate-200 font-semibold mb-3">Operations</div>
                    {operationError && <div className="text-red-300 text-xs mb-2">{operationError}</div>}

                    <div className="mb-4">
                        <div className="text-sm font-medium text-slate-300">Digest</div>
                        <input
                            value={digestEnzymes}
                            onChange={(e) => setDigestEnzymes(e.target.value)}
                            placeholder="EcoRI:GAATTC,BamHI:GGATCC"
                            className="mt-2 w-full bg-slate-800 border border-slate-700 rounded-md px-3 py-2 text-sm text-white"
                        />
                        <button
                            onClick={runDigest}
                            className="mt-2 px-3 py-1.5 bg-slate-700 hover:bg-slate-600 text-white rounded-md text-sm"
                        >
                            Run Digest
                        </button>
                        {digestFragments.length > 0 && (
                            <div className="mt-2">
                                <div className="text-xs text-slate-400 mb-1">Fragments</div>
                                <div className="space-y-1 max-h-36 overflow-y-auto">
                                    {digestFragments.map((frag, idx) => (
                                        <label key={idx} className="flex items-center gap-2 text-xs text-slate-300">
                                            <input
                                                type="checkbox"
                                                checked={selectedFragments.includes(idx)}
                                                onChange={(e) => {
                                                    setSelectedFragments((prev) =>
                                                        e.target.checked
                                                            ? [...prev, idx]
                                                            : prev.filter(i => i !== idx)
                                                    );
                                                }}
                                            />
                                            <span>Fragment {idx + 1} • {frag.sequence.length} bp</span>
                                        </label>
                                    ))}
                                </div>
                                <div className="flex gap-2 mt-2">
                                    <button
                                        onClick={runLigation}
                                        className="px-3 py-1.5 bg-emerald-600 hover:bg-emerald-500 text-white rounded-md text-sm"
                                    >
                                        Ligate Selected
                                    </button>
                                    <button
                                        onClick={runGibson}
                                        className="px-3 py-1.5 bg-slate-700 hover:bg-slate-600 text-white rounded-md text-sm"
                                    >
                                        Gibson
                                    </button>
                                </div>
                                <div className="flex gap-2 mt-2">
                                    <input
                                        type="number"
                                        value={gibsonOverlap}
                                        onChange={(e) => setGibsonOverlap(parseInt(e.target.value, 10))}
                                        className="w-24 bg-slate-800 border border-slate-700 rounded-md px-2 py-1 text-xs text-white"
                                    />
                                    <input
                                        value={gibsonName}
                                        onChange={(e) => setGibsonName(e.target.value)}
                                        placeholder="Gibson name"
                                        className="flex-1 bg-slate-800 border border-slate-700 rounded-md px-2 py-1 text-xs text-white"
                                    />
                                </div>
                                <div className="flex gap-2 mt-2">
                                    <button
                                        onClick={runGoldenGate}
                                        className="px-3 py-1.5 bg-slate-700 hover:bg-slate-600 text-white rounded-md text-sm"
                                    >
                                        Golden Gate
                                    </button>
                                    <input
                                        value={goldenEnzymes}
                                        onChange={(e) => setGoldenEnzymes(e.target.value)}
                                        className="flex-1 bg-slate-800 border border-slate-700 rounded-md px-2 py-1 text-xs text-white"
                                    />
                                </div>
                            </div>
                        )}
                    </div>

                    <div className="mb-4">
                        <div className="text-sm font-medium text-slate-300">PCR</div>
                        <input
                            value={pcrFwd}
                            onChange={(e) => setPcrFwd(e.target.value)}
                            placeholder="Forward primer"
                            className="mt-2 w-full bg-slate-800 border border-slate-700 rounded-md px-3 py-2 text-sm text-white"
                        />
                        <input
                            value={pcrRev}
                            onChange={(e) => setPcrRev(e.target.value)}
                            placeholder="Reverse primer"
                            className="mt-2 w-full bg-slate-800 border border-slate-700 rounded-md px-3 py-2 text-sm text-white"
                        />
                        <input
                            value={pcrName}
                            onChange={(e) => setPcrName(e.target.value)}
                            placeholder="Output name"
                            className="mt-2 w-full bg-slate-800 border border-slate-700 rounded-md px-3 py-2 text-sm text-white"
                        />
                        <button
                            onClick={runPCR}
                            className="mt-2 px-3 py-1.5 bg-slate-700 hover:bg-slate-600 text-white rounded-md text-sm"
                        >
                            Run PCR
                        </button>
                    </div>

                    <div className="mb-4">
                        <div className="text-sm font-medium text-slate-300">Mutagenesis</div>
                        <input
                            value={mutationsInput}
                            onChange={(e) => setMutationsInput(e.target.value)}
                            placeholder="A123G, C200T"
                            className="mt-2 w-full bg-slate-800 border border-slate-700 rounded-md px-3 py-2 text-sm text-white"
                        />
                        <input
                            value={mutName}
                            onChange={(e) => setMutName(e.target.value)}
                            placeholder="Output name"
                            className="mt-2 w-full bg-slate-800 border border-slate-700 rounded-md px-3 py-2 text-sm text-white"
                        />
                        <button
                            onClick={runMutagenesis}
                            className="mt-2 px-3 py-1.5 bg-slate-700 hover:bg-slate-600 text-white rounded-md text-sm"
                        >
                            Run Mutagenesis
                        </button>
                    </div>

                    <div className="mb-4">
                        <div className="text-sm font-medium text-slate-300">GPU MSA</div>
                        <button
                            onClick={runMSA}
                            className="mt-2 px-3 py-1.5 bg-blue-600 hover:bg-blue-500 text-white rounded-md text-sm"
                        >
                            Run MSA
                        </button>
                        {msaJobId && (
                            <div className="mt-2 text-xs text-slate-300">
                                Job: {msaJobId.slice(0, 8)} • {msaStatus}
                                {msaManifest && (
                                    <div className="text-slate-400 break-all">Manifest: {msaManifest}</div>
                                )}
                            </div>
                        )}
                    </div>
                </div>
            </div>

            {showImportModal && (
                <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-50">
                    <div className="bg-slate-800 rounded-2xl border border-slate-700 p-6 w-full max-w-md shadow-2xl">
                        <h2 className="text-xl font-bold text-white mb-4">Import Sequence</h2>
                        <div
                            className="border-2 border-dashed border-slate-600 rounded-xl p-8 text-center cursor-pointer hover:border-emerald-500/50 hover:bg-slate-700/30 transition-all"
                            onClick={() => document.getElementById('fileInput')?.click()}
                        >
                            <div className="w-12 h-12 mx-auto mb-3 rounded-full bg-slate-700 flex items-center justify-center">
                                <span className="text-2xl">📁</span>
                            </div>
                            <p className="text-white font-medium">Drop files here or click to browse</p>
                            <p className="text-sm text-slate-400 mt-1">
                                Supports GenBank, FASTA, SnapGene, SBOL
                            </p>
                        </div>
                        <input
                            id="fileInput"
                            type="file"
                            className="hidden"
                            accept=".gb,.gbk,.genbank,.fasta,.fa,.fna,.dna,.sbol,.txt"
                            onChange={(e) => {
                                const file = e.target.files?.[0];
                                if (file) handleFileImport(file);
                            }}
                        />
                        <div className="flex justify-end gap-3 mt-6">
                            <button
                                onClick={() => setShowImportModal(false)}
                                className="px-4 py-2 text-slate-400 hover:text-white transition-all"
                            >
                                Cancel
                            </button>
                        </div>
                    </div>
                </div>
            )}

            {isLoading && (
                <div className="fixed inset-0 bg-black/50 backdrop-blur-sm flex items-center justify-center z-50">
                    <div className="bg-slate-800 rounded-xl p-6 flex items-center gap-4">
                        <div className="w-8 h-8 border-4 border-emerald-500 border-t-transparent rounded-full animate-spin" />
                        <span className="text-white">Loading sequence...</span>
                    </div>
                </div>
            )}
        </div>
    );
}

export default MolBioToolkit;
