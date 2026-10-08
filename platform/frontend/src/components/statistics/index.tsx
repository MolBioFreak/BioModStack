/**
 * BMS DOE + Statistics Workbench Page
 */

import { useState } from 'react';
import {
    AssayPanel,
    AssayStatusStrip,
    AssaySubnavGrid,
    AssayWorkbenchIntro,
    type AssayNavItem,
} from '../assay/AssayWorkbenchPrimitives';
import { AnalyticalQcWorkbench } from '../assay/AnalyticalQcWorkbench';
import { ControlChart } from './ControlChart';
import { ProcessCapability } from './ProcessCapability';
import { HypothesisTesting } from './HypothesisTesting';
import { RegressionAnalysis } from './RegressionAnalysis';
import { DoEPanel } from './DoEPanel';

type AnalysisType = 'qc' | 'spc' | 'capability' | 'doe' | 'hypothesis' | 'regression';

const analysisOptions: Array<AssayNavItem<AnalysisType>> = [
    {
        id: 'qc',
        label: 'Manual Analytical QC',
        status: 'Clean + group data',
        description: 'Clean rows, exclude, bunch, group/run stats.',
    },
    {
        id: 'spc',
        label: 'Control Chart (SPC)',
        status: 'Run-chart QC',
        description: 'I-MR/X̄-R charts, centerline, limits.',
    },
    {
        id: 'capability',
        label: 'Process Capability',
        status: 'Cp / Cpk / Pp / Ppk',
        description: 'Cp/Cpk/Pp/Ppk from pasted values.',
    },
    {
        id: 'doe',
        label: 'Design of Experiments',
        status: 'pyDOE3 + RSM',
        description: 'Factorial/RSM design + contour/surface views.',
    },
    {
        id: 'hypothesis',
        label: 'Hypothesis Testing',
        status: 'Classical tests',
        description: 't-tests and ANOVA on named groups.',
    },
    {
        id: 'regression',
        label: 'Regression Analysis',
        status: 'statsmodels OLS',
        description: 'OLS coefficients, diagnostics, residuals.',
    },
];

const statusItems = [
    {
        title: 'Source of truth',
        value: '/api/assay-analytics DOE/statistics routes',
        tone: 'accent' as const,
    },
    {
        title: 'Core engines',
        value: 'pyDOE3, statsmodels, scipy, and Plotly surfaces',
    },
    {
        title: 'Data policy',
        value: 'Empty until pasted/generated data; no fabricated rows',
        tone: 'warning' as const,
    },
];

export function StatisticsPage() {
    const [activeAnalysis, setActiveAnalysis] = useState<AnalysisType>('qc');

    return (
        <div className="space-y-6 p-6">
            <AssayWorkbenchIntro
                eyebrow="BMS DOE + Statistics Workbench"
                title="JMP-like core statistics with explicit real inputs"
                description="DOE, RSM/regression, SPC, capability, tests, Plotly."
            >
                <AssayStatusStrip items={statusItems} />
            </AssayWorkbenchIntro>

            <AssaySubnavGrid
                items={analysisOptions}
                activeId={activeAnalysis}
                onChange={setActiveAnalysis}
                columnsClass="sm:grid-cols-2 xl:grid-cols-6"
            />

            <AssayPanel className="p-4">
                <div hidden={activeAnalysis !== 'qc'}>
                    <AnalyticalQcWorkbench />
                </div>
                <div hidden={activeAnalysis !== 'spc'}>
                    <ControlChart />
                </div>
                <div hidden={activeAnalysis !== 'capability'}>
                    <ProcessCapability />
                </div>
                <div hidden={activeAnalysis !== 'doe'}>
                    <DoEPanel />
                </div>
                <div hidden={activeAnalysis !== 'hypothesis'}>
                    <HypothesisTesting />
                </div>
                <div hidden={activeAnalysis !== 'regression'}>
                    <RegressionAnalysis />
                </div>
            </AssayPanel>
        </div>
    );
}
