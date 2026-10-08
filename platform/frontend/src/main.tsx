import './polyfills'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ThemeProvider } from './components/ThemeProvider'
import './index.css'
import App from './App.tsx'
import { signalCordovaAppReady } from './runtime/cordovaShell'
import { getRouterBasename, isAppPath } from './runtime/navigation'
import { buildIdentity } from './lib/buildIdentity'

console.info('[BioModStack build]', buildIdentity)

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 1000 * 60, // 1 minute
      // Large result payloads must not remain in an inactive renderer indefinitely.
      gcTime: 1000 * 60 * 10,
      retry: 1,
      retryDelay: (attempt) => Math.min(30_000, 1000 * 2 ** attempt),
      refetchOnWindowFocus: false,
      refetchOnReconnect: true,
      // Preserve polling ownership while hidden; TanStack resumes it on focus.
      refetchIntervalInBackground: false,
    },
  },
})

const routerBasename = getRouterBasename({ envBaseUrl: import.meta.env.BASE_URL })
const isDesigner = typeof window !== 'undefined' && isAppPath(window.location.pathname, '/designer', routerBasename)
const AppTree = isDesigner ? (
  <App />
) : (
  <StrictMode>
    <App />
  </StrictMode>
)

createRoot(document.getElementById('root')!).render(
  <QueryClientProvider client={queryClient}>
    <ThemeProvider>
      <BrowserRouter basename={routerBasename}>
        {AppTree}
      </BrowserRouter>
    </ThemeProvider>
  </QueryClientProvider>,
)

if (typeof window !== 'undefined') {
  window.requestAnimationFrame(() => {
    signalCordovaAppReady(window)
  })
}
