import React from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';
import { GlobalExperimentProvider } from '../src/components/experiments/GlobalExperimentContext';
import { ThemeProvider } from '../src/components/ThemeProvider';
import { NGSToolkit } from '../src/components/NGSToolkit';
import '../src/index.css';

// Real receiving workspace and one cache; API requests are not intercepted.
const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
createRoot(document.getElementById('root')!).render(
    <QueryClientProvider client={client}><BrowserRouter><GlobalExperimentProvider>
        <ThemeProvider><NGSToolkit /></ThemeProvider>
    </GlobalExperimentProvider></BrowserRouter></QueryClientProvider>,
);
