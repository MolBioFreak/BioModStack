import { lazy, Suspense } from 'react';
import { Navigate, Routes, Route } from 'react-router-dom';
import { HotkeysProvider } from '@blueprintjs/core';
import { Layout } from './components/Layout';
import { GlobalExperimentProvider } from './components/experiments/GlobalExperimentContext';
import DomainExperimentWorkspace from './components/molbio-ngs/DomainExperimentWorkspace';
import { useResolvedBmsFeatures } from './runtime/installFeatures';

const Dashboard = lazy(() => import('./components/Dashboard').then((module) => ({ default: module.Dashboard })));
const JobSubmission = lazy(() => import('./components/JobSubmission').then((module) => ({ default: module.JobSubmission })));
const ResultsViewer = lazy(() => import('./components/ResultsViewer').then((module) => ({ default: module.ResultsViewer })));
const JobDetailPage = lazy(() => import('./components/JobDetailPage').then((module) => ({ default: module.JobDetailPage })));
const MolBioToolkitV2 = lazy(() => import('./components/MolBioToolkit/indexV2').then((module) => ({ default: module.MolBioToolkitV2 })));
const NGSToolkit = lazy(() => import('./components/NGSToolkit').then((module) => ({ default: module.NGSToolkit })));
const BioXpCockpit = lazy(() => import('./components/BioXpCockpit').then((module) => ({ default: module.BioXpCockpit })));
const InfraMonitorPage = lazy(() => import('./components/InfraMonitorPage').then((module) => ({ default: module.InfraMonitorPage })));
const StatsToolkitLauncher = lazy(() => import('./components/StatsToolkitLauncher').then((module) => ({ default: module.StatsToolkitLauncher })));

function RouteLoadingFallback() {
  return (
    <div className="flex min-h-[24rem] items-center justify-center px-6 text-sm text-content-secondary">
      Loading BioModStack workspace…
    </div>
  );
}

function App() {
  const { features: bmsFeatures, resolved: bmsFeaturesResolved } = useResolvedBmsFeatures();

  return (
    <HotkeysProvider>
      <GlobalExperimentProvider>
        <Layout>
          <Suspense fallback={<RouteLoadingFallback />}>
            <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/submit" element={<JobSubmission />} />
            <Route path="/results" element={<ResultsViewer />} />
            <Route path="/designs" element={<ResultsViewer />} />
            <Route path="/designs/:jobId" element={<ResultsViewer />} />
            <Route path="/jobs" element={<Navigate replace to="/designs" />} />
            <Route path="/jobs/:jobId" element={<JobDetailPage />} />
            {/* Molecular Biology Toolkit - Seqviz-based sequence editor */}
            <Route
              path="/designer"
              element={(
                <div className="w-full max-w-none">
                  <DomainExperimentWorkspace />
                  <MolBioToolkitV2 />
                </div>
              )}
            />
            {/* NGS Data Visualization Toolkit - Nanopore-focused orchestration surface */}
            <Route
              path="/ngs"
              element={(
                <div className="w-full max-w-none">
                  <DomainExperimentWorkspace />
                  <NGSToolkit />
                </div>
              )}
            />
            {/* Isolated Stats Toolkit rendered inside the BioModStack workspace. */}
            <Route path="/stats" element={<StatsToolkitLauncher />} />
            {/* Infra Monitor - native workstation telemetry surface */}
            <Route path="/infra" element={<InfraMonitorPage />} />
            {/* BioXP Handler Controls */}
            <Route
              path="/bioxp"
              element={!bmsFeaturesResolved
                ? <RouteLoadingFallback />
                : bmsFeatures.bioxp
                  ? <BioXpCockpit />
                  : <Navigate replace to="/" />}
            />
            </Routes>
          </Suspense>
        </Layout>
      </GlobalExperimentProvider>
    </HotkeysProvider>
  );
}

export default App
