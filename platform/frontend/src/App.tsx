import { lazy, Suspense } from 'react';
import { Routes, Route } from 'react-router-dom';
import { HotkeysProvider } from '@blueprintjs/core';
import { Layout } from './components/Layout';

const Dashboard = lazy(() => import('./components/Dashboard').then((module) => ({ default: module.Dashboard })));
const JobSubmission = lazy(() => import('./components/JobSubmission').then((module) => ({ default: module.JobSubmission })));
const ResultsViewer = lazy(() => import('./components/ResultsViewer').then((module) => ({ default: module.ResultsViewer })));
const JobDetailPage = lazy(() => import('./components/JobDetailPage').then((module) => ({ default: module.JobDetailPage })));
const MolBioToolkitV2 = lazy(() => import('./components/MolBioToolkit/indexV2').then((module) => ({ default: module.MolBioToolkitV2 })));
const NGSToolkit = lazy(() => import('./components/NGSToolkit').then((module) => ({ default: module.NGSToolkit })));
const BioXpCockpit = lazy(() => import('./components/BioXpCockpit').then((module) => ({ default: module.BioXpCockpit })));
const InfraMonitorPage = lazy(() => import('./components/InfraMonitorPage').then((module) => ({ default: module.InfraMonitorPage })));
const AssayAnalytics = lazy(() => import('./components/AssayAnalytics').then((module) => ({ default: module.AssayAnalytics })));

function RouteLoadingFallback() {
  return (
    <div className="flex min-h-[24rem] items-center justify-center px-6 text-sm text-content-secondary">
      Loading BioModStack workspace…
    </div>
  );
}

function App() {
  return (
    <HotkeysProvider>
      <Layout>
        <Suspense fallback={<RouteLoadingFallback />}>
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/submit" element={<JobSubmission />} />
            <Route path="/results" element={<ResultsViewer />} />
            <Route path="/designs" element={<ResultsViewer />} />
            <Route path="/designs/:jobId" element={<ResultsViewer />} />
            <Route path="/jobs/:jobId" element={<JobDetailPage />} />
            {/* Molecular Biology Toolkit - Seqviz-based sequence editor */}
            <Route path="/designer" element={<MolBioToolkitV2 />} />
            {/* NGS Data Visualization Toolkit - Nanopore-focused orchestration surface */}
            <Route path="/ngs" element={<NGSToolkit />} />
            {/* Stats Toolkit - qPCR, chromatography/Empower, DOE/statistics */}
            <Route path="/assay" element={<AssayAnalytics />} />
            {/* Infra Monitor - native workstation telemetry surface */}
            <Route path="/infra" element={<InfraMonitorPage />} />
            {/* BioXP Handler Controls - OEM/liquid-handler-first robot-local runtime proxy */}
            <Route path="/bioxp" element={<BioXpCockpit />} />
          </Routes>
        </Suspense>
      </Layout>
    </HotkeysProvider>
  );
}

export default App
