/**
 * qPCR Data Processor Page
 */

import { useState } from 'react';
import { RawDataImport } from './RawDataImport';
import { DeltaCqPanel, DeltaDeltaCqPanel } from './DeltaCqPanels';
import { StandardCurvePanel, QuantificationPanel, AnovaDunnettPanel } from './QuantificationPanels';

type AnalysisType = 'import' | 'deltacq' | 'deltadeltacq' | 'stdcurve' | 'quantify' | 'anova';

export function QpcrPage() {
    const [activeAnalysis, setActiveAnalysis] = useState<AnalysisType>('import');

    const analysisOptions: { id: AnalysisType; label: string }[] = [
        { id: 'import', label: 'Raw Data Import' },
        { id: 'deltacq', label: 'ΔCq Analysis' },
        { id: 'deltadeltacq', label: 'ΔΔCq & Fold Change' },
        { id: 'stdcurve', label: 'Standard Curve' },
        { id: 'quantify', label: 'Absolute Quantification' },
        { id: 'anova', label: 'ANOVA + Dunnett' },
    ];

    return (
        <div className="p-6">
            <div className="mb-6">
                <h2 className="text-2xl font-bold text-text-primary">qPCR Data Processor</h2>
                <p className="text-text-secondary">Import raw instrument data, view amplification curves, and perform relative/absolute quantification</p>
            </div>

            {/* Analysis Type Selector */}
            <div className="mb-6 flex flex-wrap gap-2">
                {analysisOptions.map(option => (
                    <button
                        key={option.id}
                        onClick={() => setActiveAnalysis(option.id)}
                        className={`px-4 py-2 text-sm font-medium transition-colors ${activeAnalysis === option.id
                                ? 'bg-accent-primary text-white'
                                : 'bg-bg-secondary text-text-secondary hover:text-text-primary border border-border-primary'
                            }`}
                    >
                        {option.label}
                    </button>
                ))}
            </div>

            {/* Active Analysis Panel */}
            <div className="border border-border-primary bg-bg-secondary p-4">
                {activeAnalysis === 'import' && <RawDataImport />}
                {activeAnalysis === 'deltacq' && <DeltaCqPanel />}
                {activeAnalysis === 'deltadeltacq' && <DeltaDeltaCqPanel />}
                {activeAnalysis === 'stdcurve' && <StandardCurvePanel />}
                {activeAnalysis === 'quantify' && <QuantificationPanel />}
                {activeAnalysis === 'anova' && <AnovaDunnettPanel />}
            </div>
        </div>
    );
}

export { RawDataImport } from './RawDataImport';
export { DeltaCqPanel, DeltaDeltaCqPanel } from './DeltaCqPanels';
export { StandardCurvePanel, QuantificationPanel, AnovaDunnettPanel } from './QuantificationPanels';
