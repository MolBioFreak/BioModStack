import { Routes, Route } from 'react-router-dom';
import { Layout } from './components/Layout';
import { Dashboard } from './components/Dashboard';
import { JobSubmission } from './components/JobSubmission';
import { ResultsViewer } from './components/ResultsViewer';
import { JobDetailPage } from './components/JobDetailPage';
import { SequenceDesigner } from './components/SequenceDesigner';

function App() {
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/submit" element={<JobSubmission />} />
        <Route path="/results" element={<ResultsViewer />} />
        <Route path="/designs" element={<ResultsViewer />} />
        <Route path="/designs/:jobId" element={<ResultsViewer />} />
        <Route path="/jobs/:jobId" element={<JobDetailPage />} />
        <Route path="/designer" element={<SequenceDesigner />} />
      </Routes>
    </Layout>
  );
}

export default App

