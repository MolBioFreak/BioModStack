import { useMemo } from 'react';

import { StructureWorkbench } from '../structureViewer/StructureWorkbench';
import type { ResidueRef } from '../structureViewer/contracts/structureIdentity.js';

export interface EpitopeMolstarViewerProps {
    structureUrl?: string;
    pdbData?: string;
    format?: 'cif' | 'pdb';
    height?: number | string;
    backgroundColor?: string;
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
    selectedResidueRefs = [],
    selectedResidues = new Set<string>(),
    onResidueRefClick,
    onResidueClick,
}: EpitopeMolstarViewerProps) {

    const canonicalSelections = useMemo(() => {
        const byKey = new Map<string, ResidueRef>();
        for (const residue of selectedResidueRefs) byKey.set(compatibilityKey(residue) ?? JSON.stringify(residue), residue);
        for (const key of selectedResidues) {
            const residue = parseCompatibilityKey(key);
            if (residue) byKey.set(compatibilityKey(residue)!, residue);
        }
        return [...byKey.values()];
    }, [selectedResidueRefs, selectedResidues]);

    const heightStyle = typeof height === 'number' ? `${height}px` : height;
    if (!pdbData && !structureUrl) {
        return <div className="w-full flex items-center justify-center text-slate-500 bg-slate-900 rounded-lg border border-dashed border-slate-700" style={{ height: heightStyle }}>
            <div className="text-center"><div className="text-4xl mb-2">🧬</div><div className="text-sm">Upload a PDB to view 3D structure</div></div>
        </div>;
    }

    return <div className="w-full rounded-lg overflow-hidden relative border border-slate-700" style={{ height: heightStyle }}>
        <StructureWorkbench
            mode="compact"
            structureUrl={structureUrl}
            structureData={pdbData}
            format={format}
            height="100%"
            backgroundColor={backgroundColor}
            alphafoldView={false}
            hideControls
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
        <div className="absolute top-2 left-2 z-20 px-2 py-1 bg-slate-800/90 text-slate-300 text-xs rounded flex items-center gap-2 pointer-events-none"><span className="text-blue-400">🔍</span>3D Preview - Click residues here or use the 2D grid below</div>
        {canonicalSelections.length > 0 && <div className="absolute top-2 right-2 z-20 px-2 py-1 bg-emerald-600/90 text-white text-xs rounded pointer-events-none">{canonicalSelections.length} selected</div>}
    </div>;
}

export { EpitopeMolstarViewer };
