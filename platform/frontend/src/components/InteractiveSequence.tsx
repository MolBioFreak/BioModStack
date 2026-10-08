
import { useState } from 'react';
import type { Mutation } from '../utils/mutationUtils';

const AMINO_ACIDS = [
    { code: 'A', name: 'Ala', color: '#C8C8C8' }, // Tiny
    { code: 'G', name: 'Gly', color: '#C8C8C8' },
    { code: 'S', name: 'Ser', color: '#FA9600' }, // Polar
    { code: 'T', name: 'Thr', color: '#FA9600' },
    { code: 'C', name: 'Cys', color: '#E6E600' },
    { code: 'P', name: 'Pro', color: '#DC9682' },
    { code: 'V', name: 'Val', color: '#0F820F' }, // Hydrophobic
    { code: 'L', name: 'Leu', color: '#0F820F' },
    { code: 'I', name: 'Ile', color: '#0F820F' },
    { code: 'M', name: 'Met', color: '#0F820F' },
    { code: 'F', name: 'Phe', color: '#3232AA' }, // Aromatic
    { code: 'Y', name: 'Tyr', color: '#3232AA' },
    { code: 'W', name: 'Trp', color: '#3232AA' },
    { code: 'D', name: 'Asp', color: '#E60A0A' }, // Acidic
    { code: 'E', name: 'Glu', color: '#E60A0A' },
    { code: 'N', name: 'Asn', color: '#00DCDC' }, // Basic/Amide
    { code: 'Q', name: 'Gln', color: '#00DCDC' },
    { code: 'H', name: 'His', color: '#8282D2' },
    { code: 'K', name: 'Lys', color: '#145AFF' },
    { code: 'R', name: 'Arg', color: '#145AFF' },
];

interface InteractiveSequenceProps {
    sequence: string;
    mutations: Mutation[];
    onMutationAdd: (position: number, toAA: string) => void;
    onMutationRemove: (position: number) => void;
}

export function InteractiveSequence({ sequence, mutations, onMutationAdd, onMutationRemove }: InteractiveSequenceProps) {
    const [selectedPos, setSelectedPos] = useState<number | null>(null);

    // Create a map for fast lookup of mutations
    const mutationMap = new Map<number, string>();
    mutations.forEach(m => mutationMap.set(m.position, m.to));

    // Handle AA selection
    const handleSelectAA = (aaCode: string) => {
        if (selectedPos !== null) {
            onMutationAdd(selectedPos, aaCode);
            setSelectedPos(null);
        }
    };

    return (
        <div className="relative">
            {/* Sequence Grid */}
            <div className="flex flex-wrap gap-1 font-mono text-sm leading-none bg-slate-900/50 p-4 rounded-lg border border-slate-800 max-h-[400px] overflow-y-auto">
                {sequence.split('').map((aa, idx) => {
                    const pos = idx + 1;
                    const mutatedAA = mutationMap.get(pos);
                    const isMutated = !!mutatedAA;
                    const isSelected = selectedPos === pos;

                    return (
                        <div key={pos} className="relative group">
                            {/* Position Marker (every 10) */}
                            {pos % 10 === 0 && (
                                <div className="absolute -top-4 left-1/2 -translate-x-1/2 text-[9px] text-slate-600 select-none">
                                    {pos}
                                </div>
                            )}

                            <button
                                onClick={() => setSelectedPos(isSelected ? null : pos)}
                                className={`w-8 h-8 flex items-center justify-center rounded transition-all border ${isSelected
                                    ? 'bg-blue-600 border-blue-400 text-white z-10 scale-110 shadow-lg'
                                    : isMutated
                                        ? 'bg-purple-900/40 border-purple-500 text-purple-300'
                                        : 'bg-slate-800 border-transparent text-slate-400 hover:bg-slate-700 hover:border-slate-600'
                                    }`}
                                title={`Pos ${pos}: ${aa} ${isMutated ? `→ ${mutatedAA}` : ''}`}
                            >
                                {isMutated ? mutatedAA : aa}
                            </button>

                            {/* Original AA indicator if mutated */}
                            {isMutated && (
                                <div className="absolute -bottom-2 -right-1 text-[8px] bg-slate-900 text-slate-500 px-0.5 rounded border border-slate-800">
                                    {aa}
                                </div>
                            )}
                        </div>
                    );
                })}
            </div>

            {/* Mutation Picker Popover */}
            {selectedPos !== null && (
                <div className="absolute top-0 left-0 right-0 z-20 mt-2 p-4 bg-slate-800 border border-slate-600 rounded-xl shadow-2xl animate-in zoom-in-95 duration-200">
                    <div className="flex justify-between items-center mb-3">
                        <h4 className="text-sm font-semibold text-slate-200">
                            Mutate Pos {selectedPos} <span className="text-slate-500">({sequence[selectedPos - 1]})</span>
                        </h4>
                        <button
                            onClick={() => setSelectedPos(null)}
                            className="text-slate-400 hover:text-white"
                        >✕</button>
                    </div>

                    <div className="grid grid-cols-5 sm:grid-cols-10 gap-2 mb-3">
                        {AMINO_ACIDS.map((aa) => {
                            const isOriginal = sequence[selectedPos - 1] === aa.code;
                            const isCurrent = mutationMap.get(selectedPos) === aa.code;

                            return (
                                <button
                                    key={aa.code}
                                    onClick={() => handleSelectAA(aa.code)}
                                    disabled={isOriginal}
                                    className={`flex flex-col items-center justify-center p-2 rounded border transition-all ${isCurrent
                                        ? 'bg-purple-600 border-purple-400 text-white'
                                        : isOriginal
                                            ? 'bg-slate-700/50 border-transparent text-slate-500 opacity-50 cursor-not-allowed'
                                            : 'bg-slate-700/50 border-slate-600 hover:bg-slate-600 hover:border-slate-500 text-slate-200'
                                        }`}
                                >
                                    <span className="font-bold text-lg">{aa.code}</span>
                                    <span className="text-[9px] uppercase opacity-70">{aa.name}</span>
                                </button>
                            );
                        })}
                    </div>

                    {mutationMap.has(selectedPos) && (
                        <button
                            onClick={() => {
                                onMutationRemove(selectedPos);
                                setSelectedPos(null);
                            }}
                            className="w-full py-2 bg-red-500/10 hover:bg-red-500/20 text-red-400 text-xs rounded border border-red-500/20 transition-colors"
                        >
                            Revert to Wild Type ({sequence[selectedPos - 1]})
                        </button>
                    )}
                </div>
            )}
        </div>
    );
}
