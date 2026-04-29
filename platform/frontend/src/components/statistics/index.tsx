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
import { ControlChart } from './ControlChart';
import { ProcessCapability } from './ProcessCapability';
import { HypothesisTesting } from './HypothesisTesting';
import { RegressionAnalysis } from './RegressionAnalysis';
import { DoEPanel } from './DoEPanel';

type AnalysisType = 'spc' | 'capability' | 'doe' | 'hypothesis' | 'regression';

const analysisOptions: Array<AssayNavItem<AnalysisType>> = [
    {
        id: 'spc',
        label: 'Control Chart (SPC)',
        status: 'Run-chart QC',
        description: 'Create I-MR or X̄-R charts from real process measurements with centerline and control-limit Plotly overlays.',
    },
    {
        id: 'capability',
        label: 'Process Capability',
        status: 'Cp / Cpk / Pp / Ppk',
        description: 'Evaluate specification fit, centering, within/overall sigma, and capability visualization from pasted values.',
    },
    {
        id: 'doe',
        label: 'Design of Experiments',
        status: 'pyDOE3 + RSM',
        description: 'Generate factorial/response-surface designs and analyze explicit DOE matrices with Plotly contour/surface views.',
    },
    {
        id: 'hypothesis',
        label: 'Hypothesis Testing',
        status: 'Classical tests',
        description: 'Run one-sample, two-sample, paired t-tests, and ANOVA against real numeric groups.',
    },
    {
        id: 'regression',
        label: 'Regression Analysis',
        status: 'statsmodels OLS',
        description: 'Fit simple linear models with coefficients, diagnostics, residual statistics, and Plotly scatter/fitted traces.',
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
        value: 'Empty workbench until pasted values or generated DOE output exists',
        tone: 'warning' as const,
    },
];

export function StatisticsPage() {
    const [activeAnalysis, setActiveAnalysis] = useState<AnalysisType>('spc');

    return (
        <div className="space-y-6 p-6">
            <AssayWorkbenchIntro
                eyebrow="BMS DOE + Statistics Workbench"
                title="JMP-like core statistics with explicit real inputs"
                description="Generate DOE layouts, fit RSM/regression models, run SPC/capability, and perform classical tests without loading built-in assay rows. Plotly renders the active charts; backend routes report the statistical engine where available."
            >
                <AssayStatusStrip items={statusItems} />
            </AssayWorkbenchIntro>

            <AssaySubnavGrid
                items={analysisOptions}
                activeId={activeAnalysis}
                onChange={setActiveAnalysis}
                columnsClass="sm:grid-cols-2 xl:grid-cols-5"
            />

            <AssayPanel className="p-4">
                {activeAnalysis === 'spc' && <ControlChart />}
                {activeAnalysis === 'capability' && <ProcessCapability />}
                {activeAnalysis === 'doe' && <DoEPanel />}
                {activeAnalysis === 'hypothesis' && <HypothesisTesting />}
                {activeAnalysis === 'regression' && <RegressionAnalysis />}
            </AssayPanel>
        </div>
    );
}
