import { useEffect, useMemo, useState } from 'react';

import MolstarViewer from './MolstarViewer';
import type { MolstarDirectResidueClick } from '../structureViewer/adapters/MolstarDirectAdapter';

interface Selection {
    chain_id?: string;
    start_residue_number?: number;
    end_residue_number?: number;
    color?: { r: number; g: number; b: number };
}

export interface EpitopeMolstarViewerProps {
    structureUrl?: string;
    pdbData?: string;
    format?: 'cif' | 'pdb';
    height?: number | string;
    backgroundColor?: string;
    selectedResidues: Set<string>;
    onResidueClick?: (residueKey: string) => void;
}

const parseResidueKey = (key: string): { chainId: string; residueNumber: number } | null => {
    const compact = key.trim().match(/^([A-Za-z])(-?\d+)([A-Za-z]?)$/);
    if (!compact) return null;
    return { chainId: compact[1], residueNumber: Number(compact[2]) };
};

const legacyResidueKey = (residue: MolstarDirectResidueClick): string => (
    `${residue.authAsymId}${residue.authSeqId}${residue.insertionCode || ''}`
);

export default function EpitopeMolstarViewer({
    structureUrl,
    pdbData,
    format = 'pdb',
    height = 400,
    backgroundColor = '#0f172a',
    selectedResidues,
    onResidueClick,
}: EpitopeMolstarViewerProps) {
    const [blobUrl, setBlobUrl] = useState<string | null>(null);

    useEffect(() => {
        if (!pdbData) {
            setBlobUrl(null);
            return undefined;
        }
        const url = URL.createObjectURL(new Blob([pdbData], { type: 'chemical/x-pdb' }));
        setBlobUrl(url);
        return () => URL.revokeObjectURL(url);
    }, [pdbData]);

    const effectiveUrl = blobUrl ?? structureUrl;
    const selections = useMemo<Selection[]>(() => {
        const result: Selection[] = [];
        for (const key of selectedResidues) {
            const parsed = parseResidueKey(key);
            if (!parsed) continue;
            result.push({
                chain_id: parsed.chainId,
                start_residue_number: parsed.residueNumber,
                end_residue_number: parsed.residueNumber,
                color: { r: 16, g: 185, b: 129 },
            });
        }
        return result;
    }, [selectedResidues]);

    const heightStyle = typeof height === 'number' ? `${height}px` : height;
    if (!effectiveUrl) {
        return (
            <div
                className="w-full flex items-center justify-center text-slate-500 bg-slate-900 rounded-lg border border-dashed border-slate-700"
                style={{ height: heightStyle }}
            >
                <div className="text-center">
                    <div className="text-4xl mb-2">🧬</div>
                    <div className="text-sm">Upload a PDB to view 3D structure</div>
                </div>
            </div>
        );
    }

    return (
        <div
            className="w-full rounded-lg overflow-hidden relative border border-slate-700"
            style={{ height: heightStyle }}
        >
            <MolstarViewer
                structureUrl={effectiveUrl}
                format={format}
                height="100%"
                backgroundColor={backgroundColor}
                alphafoldView={false}
                hideControls
                selections={selections}
                onResidueClick={onResidueClick
                    ? (residue) => onResidueClick(legacyResidueKey(residue))
                    : undefined}
            />
            <div className="absolute top-2 left-2 z-20 px-2 py-1 bg-slate-800/90 text-slate-300 text-xs rounded flex items-center gap-2 pointer-events-none">
                <span className="text-blue-400">🔍</span>
                3D Preview - Click residues here or use the 2D grid below
            </div>
            {selectedResidues.size > 0 && (
                <div className="absolute top-2 right-2 z-20 px-2 py-1 bg-emerald-600/90 text-white text-xs rounded pointer-events-none">
                    {selectedResidues.size} selected
                </div>
            )}
        </div>
    );
}

export { EpitopeMolstarViewer };
