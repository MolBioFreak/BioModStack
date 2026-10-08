/**
 * Statistical Toolkit Page
 */

import { useState } from 'react';
import { ControlChart } from './ControlChart';
import { ProcessCapability } from './ProcessCapability';
import { HypothesisTesting } from './HypothesisTesting';
import { RegressionAnalysis } from './RegressionAnalysis';
import { DoEPanel } from './DoEPanel';

type AnalysisType = 'spc' | 'capability' | 'doe' | 'hypothesis' | 'regression';

export function StatisticsPage() {
    const [activeAnalysis, setActiveAnalysis] = useState<AnalysisType>('spc');

    const analysisOptions: { id: AnalysisType; label: string }[] = [
        { id: 'spc', label: 'Control Chart (SPC)' },
        { id: 'capability', label: 'Process Capability' },
        { id: 'doe', label: 'Design of Experiments' },
        { id: 'hypothesis', label: 'Hypothesis Testing' },
        { id: 'regression', label: 'Regression Analysis' },
    ];

    return (
        <div className="p-6">
            <div className="mb-6">
                <h2 className="text-2xl font-bold text-text-primary">Statistical Toolkit</h2>
                <p className="text-text-secondary">Quality engineering and statistical analysis tools</p>
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
                {activeAnalysis === 'spc' && <ControlChart />}
                {activeAnalysis === 'capability' && <ProcessCapability />}
                {activeAnalysis === 'doe' && <DoEPanel />}
                {activeAnalysis === 'hypothesis' && <HypothesisTesting />}
                {activeAnalysis === 'regression' && <RegressionAnalysis />}
            </div>
        </div>
    );
}

export { ControlChart } from './ControlChart';
export { ProcessCapability } from './ProcessCapability';
export { HypothesisTesting } from './HypothesisTesting';
export { RegressionAnalysis } from './RegressionAnalysis';
export { DoEPanel } from './DoEPanel';
