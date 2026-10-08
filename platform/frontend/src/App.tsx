import { Routes, Route } from 'react-router-dom';
import { HotkeysProvider } from '@blueprintjs/core';
import { Layout } from './components/Layout';
import { Dashboard } from './components/Dashboard';
import { JobSubmission } from './components/JobSubmission';
import { ResultsViewer } from './components/ResultsViewer';
import { JobDetailPage } from './components/JobDetailPage';
import { MolBioToolkitV2 } from './components/MolBioToolkit/indexV2';
import { NGSToolkit } from './components/NGSToolkit';

function App() {
  return (
    <HotkeysProvider>
      <Layout>
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
        </Routes>
      </Layout>
    </HotkeysProvider>
  );
}

export default App
