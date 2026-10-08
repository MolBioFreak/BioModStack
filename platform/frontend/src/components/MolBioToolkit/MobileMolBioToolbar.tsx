import type { MolBioMobileSurface } from './utils/mobileLayout';

interface MobileMolBioToolbarProps {
    constructName: string;
    digestAvailable: boolean;
    qcAvailable: boolean;
    hasSequence: boolean;
    surface: MolBioMobileSurface;
    onBack: () => void;
    onOpenConstructs: () => void;
    onSurfaceChange: (surface: MolBioMobileSurface) => void;
}

const SURFACES: Array<{ id: MolBioMobileSurface; label: string }> = [
    { id: 'map', label: 'Map' },
    { id: 'sequence', label: 'Sequence' },
    { id: 'details', label: 'Details' },
    { id: 'digest', label: 'Digest' },
    { id: 'qc', label: 'QC' },
];

export function MobileMolBioToolbar({
    constructName,
    digestAvailable,
    qcAvailable,
    hasSequence,
    surface,
    onBack,
    onOpenConstructs,
    onSurfaceChange,
}: MobileMolBioToolbarProps) {
    return (
        <header
            data-molbio-mobile-toolbar="true"
            style={{ paddingTop: 'calc(env(safe-area-inset-top) + 0.75rem)' }}
            className="z-40 flex flex-shrink-0 items-center gap-2 overflow-x-auto border-b border-slate-700 bg-slate-950/95 pb-2 pt-[max(env(safe-area-inset-top),0.75rem)] pl-[max(env(safe-area-inset-left),0.75rem)] pr-[max(env(safe-area-inset-right),0.75rem)] shadow-lg overscroll-x-contain"
        >
            <button
                type="button"
                aria-label="Back in MolBio mobile viewer"
                onClick={onBack}
                className="min-h-12 min-w-12 flex-shrink-0 rounded-lg border border-slate-600 bg-slate-800 px-3 text-sm font-medium text-slate-100 transition-colors hover:bg-slate-700"
            >
                Back
            </button>
            <button
                type="button"
                onClick={onOpenConstructs}
                className="min-h-12 min-w-12 flex-shrink-0 rounded-lg border border-cyan-600/60 bg-cyan-950/60 px-3 text-sm font-medium text-cyan-100 transition-colors hover:bg-cyan-900/60"
            >
                Constructs
            </button>
            <div className="min-w-32 max-w-64 flex-shrink truncate px-1 text-sm font-semibold text-slate-100" title={constructName}>
                {constructName || 'Choose a construct'}
            </div>
            <nav className="flex flex-shrink-0 items-center gap-1" aria-label="Mobile MolBio views">
                {SURFACES.map((item) => (
                    <button
                        key={item.id}
                        type="button"
                        aria-pressed={surface === item.id}
                        disabled={
                            !hasSequence
                            || (item.id === 'digest' && !digestAvailable)
                            || (item.id === 'qc' && !qcAvailable)
                        }
                        title={item.id === 'digest' && !digestAvailable
                            ? 'Digest is available for the current construct only.'
                            : item.id === 'qc' && !qcAvailable
                                ? 'QC is available for the current construct only.'
                                : undefined}
                        onClick={() => onSurfaceChange(item.id)}
                        className={`min-h-12 min-w-12 rounded-lg px-3 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${
                            surface === item.id
                                ? 'bg-cyan-600 text-white'
                                : 'border border-slate-700 bg-slate-900 text-slate-300 hover:bg-slate-800'
                        }`}
                    >
                        {item.label}
                    </button>
                ))}
            </nav>
        </header>
    );
}
