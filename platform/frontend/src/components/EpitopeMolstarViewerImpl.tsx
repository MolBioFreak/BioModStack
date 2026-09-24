import { useEffect, useMemo, useState } from 'react';

import { StructureWorkbench } from '../structureViewer/StructureWorkbench';
import type { ResidueRef } from '../structureViewer/contracts/structureIdentity.js';

export interface EpitopeMolstarViewerProps {
    structureUrl?: string;
    pdbData?: string;
    format?: 'cif' | 'pdb';
    height?: number | string;
    backgroundColor?: string;
    /** Exact document identity; defaults to the legacy primary document. */
    documentId?: string;
    sourceLabel?: string;
    /** Start expanded; the same shared runtime remains mounted on collapse. */
    defaultFullView?: boolean;
    /** Explicit inspection opens tools without reconstructing the runtime. */
    expandRevision?: number;
    /** Canonical viewer identity. Prefer this over selectedResidues. */
    selectedResidueRefs?: readonly ResidueRef[];
    /** Deprecated backend/UI compatibility keys. Parsed only at this boundary. */
    selectedResidues?: ReadonlySet<string>;
    onResidueRefClick?: (residue: ResidueRef) => void;
    /** Deprecated backend/UI compatibility callback. */
    onResidueClick?: (residueKey: string) => void;
}

const parseCompatibilityKey = (key: string): ResidueRef | null => {
    const normalized = key.trim();
    const explicit = /^([^:]+):(-?\d+)([A-Za-z]?)$/.exec(normalized);
    const compact = /^([A-Za-z])(-?\d+)([A-Za-z]?)$/.exec(normalized);
    const match = explicit ?? compact;
    if (!match) return null;
    const authSeqId = Number(match[2]);
    if (!Number.isSafeInteger(authSeqId)) return null;
    return {
        documentId: 'primary',
        authAsymId: match[1],
        authSeqId,
        insertionCode: match[3] || undefined,
    };
};

const compatibilityKey = (residue: ResidueRef): string | null => {
    const chain = residue.authAsymId ?? residue.labelAsymId;
    const number = residue.authSeqId ?? residue.labelSeqId;
    if (!chain || number === undefined) return null;
    return chain.length === 1
        ? `${chain}${number}${residue.insertionCode ?? ''}`
        : `${chain}:${number}${residue.insertionCode ?? ''}`;
};

export default function EpitopeMolstarViewer({
    structureUrl,
    pdbData,
    format = 'pdb',
    height = 400,
    backgroundColor = '#0f172a',
    documentId = 'primary',
    sourceLabel = 'Structure',
    defaultFullView = false,
    expandRevision = 0,
    selectedResidueRefs = [],
    selectedResidues = new Set<string>(),
    onResidueRefClick,
    onResidueClick,
}: EpitopeMolstarViewerProps) {
    const [fullView, setFullView] = useState(defaultFullView);
    useEffect(() => { if (expandRevision > 0) setFullView(true); }, [expandRevision]);

    const canonicalSelections = useMemo(() => {
        const byKey = new Map<string, ResidueRef>();
        for (const residue of selectedResidueRefs) byKey.set(JSON.stringify(residue), residue);
        for (const key of selectedResidues) {
            const residue = parseCompatibilityKey(key);
            if (residue && !selectedResidueRefs.some(ref => ref.documentId === documentId && compatibilityKey(ref) === compatibilityKey(residue))) {
                const scoped = { ...residue, documentId };
                byKey.set(JSON.stringify(scoped), scoped);
            }
        }
        return [...byKey.values()];
    }, [selectedResidueRefs, selectedResidues, documentId]);

    const heightStyle = typeof height === 'number' ? `${height}px` : height;
    if (!pdbData && !structureUrl) {
        return <div className="w-full flex items-center justify-center text-slate-500 bg-slate-900 rounded-lg border border-dashed border-slate-700" style={{ height: heightStyle }}>
            <div className="text-center"><div className="text-4xl mb-2">🧬</div><div className="text-sm">Upload a PDB to view 3D structure</div></div>
        </div>;
    }

    return <div className="w-full min-w-0 rounded-lg overflow-hidden border border-[var(--border-color)]">
        <div className="flex flex-wrap items-center justify-between gap-2 bg-[var(--bg-secondary)] p-2 text-xs text-[var(--text-primary)]">
            <span>{sourceLabel} · {format === 'cif' ? 'mmCIF' : 'PDB'} · {canonicalSelections.length} selected</span>
            <button type="button" className="rounded border border-[var(--border-color)] px-3 py-1 hover:text-accent" aria-expanded={fullView} onClick={() => setFullView(value => !value)}>{fullView ? 'Close full viewer' : 'Open full viewer'}</button>
        </div>
        <div style={{ height: fullView ? 'min(80vh, 850px)' : heightStyle }}>
        <StructureWorkbench
            mode="standard"
            structureDocumentId={documentId}
            structureUrl={structureUrl}
            structureData={pdbData}
            format={format}
            height="100%"
            backgroundColor={backgroundColor}
            alphafoldView={false}
            // This is a runtime-construction option: keep it stable on expansion.
            hideControls={false}
            workbenchCollapsed={!fullView}
            showSequenceTrack={fullView}
            residueSelections={canonicalSelections}
            onResidueClick={(click) => {
                const residue: ResidueRef = {
                    documentId: click.documentId,
                    labelAsymId: click.labelAsymId,
                    authAsymId: click.authAsymId,
                    labelSeqId: click.labelSeqId,
                    authSeqId: click.authSeqId,
                    insertionCode: click.insertionCode,
                };
                onResidueRefClick?.(residue);
                const key = compatibilityKey(residue);
                if (key) onResidueClick?.(key);
            }}
        />
        </div>
    </div>;
}

export { EpitopeMolstarViewer };
