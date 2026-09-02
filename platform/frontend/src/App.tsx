import { lazy, Suspense } from 'react';
import { Navigate, Routes, Route, useLocation, useParams } from 'react-router-dom';
import { HotkeysProvider } from '@blueprintjs/core';
import { Layout } from './components/Layout';
import { GlobalExperimentProvider } from './components/experiments/GlobalExperimentContext';
import NgsMolBioProjectHub from './components/molbio-ngs/NgsMolBioProjectHub';
import { useResolvedBmsFeatures } from './runtime/installFeatures';

const Dashboard = lazy(() => import('./components/Dashboard').then((module) => ({ default: module.Dashboard })));
const ProjectManager = lazy(() => import('./pages/ProjectManager').then((module) => ({ default: module.ProjectManager })));
const JobSubmission = lazy(() => import('./components/JobSubmission').then((module) => ({ default: module.JobSubmission })));
const ResultsViewer = lazy(() => import('./components/ResultsViewer').then((module) => ({ default: module.ResultsViewer })));
const JobDetailPage = lazy(() => import('./components/JobDetailPage').then((module) => ({ default: module.JobDetailPage })));
const MolBioToolkitV2 = lazy(() => import('./components/MolBioToolkit/indexV2').then((module) => ({ default: module.MolBioToolkitV2 })));
const NGSToolkit = lazy(() => import('./components/NGSToolkit').then((module) => ({ default: module.NGSToolkit })));
const BioXpCockpit = lazy(() => import('./components/BioXpCockpit').then((module) => ({ default: module.BioXpCockpit })));
const StatsToolkitLauncher = lazy(() => import('./components/StatsToolkitLauncher').then((module) => ({ default: module.StatsToolkitLauncher })));

function RouteLoadingFallback() {
  return (
    <div className="flex min-h-[24rem] items-center justify-center px-6 text-sm text-content-secondary">
      Loading BioModStack workspace…
    </div>
  );
}

function HistoricalMolBioReopenRoute() {
  const location = useLocation();
  const query = new URLSearchParams(location.search);
  const sequenceId = query.get('sequence_id')?.trim();
  const revisionId = query.get('revision_id')?.trim();
  const primerId = query.get('primer_id')?.trim();
  const pcrExperimentId = query.get('experiment_id')?.trim();

  if (sequenceId && revisionId) {
    if (!query.has('molbio_sequence_id')) query.set('molbio_sequence_id', sequenceId);
    if (!query.has('molbio_revision_id')) query.set('molbio_revision_id', revisionId);
  }
  if (pcrExperimentId && revisionId) {
    if (!query.has('pcr_experiment_id')) query.set('pcr_experiment_id', pcrExperimentId);
    if (!query.has('pcr_revision_id')) query.set('pcr_revision_id', revisionId);
  }
  if (primerId && revisionId && !query.has('primer_revision_id')) {
    query.set('primer_revision_id', revisionId);
  }

  const search = query.toString();
  return <Navigate replace to={`/designer${search ? `?${search}` : ''}`} />;
}

function HistoricalDomainExperimentReopenRoute() {
  const location = useLocation();
  const { domainExperimentId } = useParams<{ domainExperimentId: string }>();
  const query = new URLSearchParams(location.search);
  if (domainExperimentId) query.set('domain_experiment_id', domainExperimentId);
  const search = query.toString();
  return <Navigate replace to={`/ngs${search ? `?${search}` : ''}`} />;
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
            <Route path="/projects" element={<ProjectManager />} />
            <Route path="/projects/:projectId" element={<ProjectManager />} />
            <Route path="/projects/:projectId/experiments/:experimentId" element={<ProjectManager />} />
            <Route path="/projects/:projectId/experiments/:experimentId/domains/:domainId" element={<ProjectManager />} />
            <Route path="/submit" element={<JobSubmission />} />
            <Route path="/results" element={<ResultsViewer />} />
            <Route path="/designs" element={<ResultsViewer />} />
            <Route path="/designs/:jobId" element={<ResultsViewer />} />
            <Route path="/jobs" element={<Navigate replace to="/designs" />} />
            <Route path="/jobs/:jobId" element={<JobDetailPage />} />
            <Route path="/molbio" element={<HistoricalMolBioReopenRoute />} />
            <Route
              path="/molbio-ngs/domain-experiments/:domainExperimentId"
              element={<HistoricalDomainExperimentReopenRoute />}
            />
            {/* Molecular Biology Toolkit - Seqviz-based sequence editor */}
            <Route
              path="/designer"
              element={(
                  <div className="w-full max-w-none">
                    <NgsMolBioProjectHub />
                    <MolBioToolkitV2 />
                  </div>
                )}
            />
            {/* NGS Data Visualization Toolkit - Nanopore-focused orchestration surface */}
            <Route
              path="/ngs"
              element={(
                  <div className="w-full max-w-none">
                    <NGSToolkit />
                  </div>
                )}
            />
            {/* Isolated Stats Toolkit rendered inside the BioModStack workspace. */}
            <Route path="/stats" element={<StatsToolkitLauncher />} />
            {/* Historical analytics route now resolves to Dashboard telemetry. */}
            <Route path="/infra" element={<Navigate replace to="/" />} />
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
