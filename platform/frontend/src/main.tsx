import './polyfills'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ThemeProvider } from './components/ThemeProvider'
import './index.css'
import App from './App.tsx'
import { getRouterBasename, isAppPath } from './runtime/navigation'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 1000 * 60, // 1 minute
      retry: 1,
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
