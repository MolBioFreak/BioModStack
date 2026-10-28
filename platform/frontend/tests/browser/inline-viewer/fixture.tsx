import React from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import AlignmentPage from '../../../src/components/alignments/AlignmentPage'
import { api } from '../../../src/lib/api'
api.get = (async (url: string) => ({data: url.includes('/documents/') ? {result_id:'r', rows:[{row_id:'one',label:'duplicate',aligned:'ACGT'},{row_id:'two',label:'duplicate',aligned:'A-GT'},{row_id:'three',label:'</script><img src=x onerror="parent.pwned=1">\n>forged',aligned:'AC-T'}]} : {artifacts:[{id:'a',role:'alignment'}]}})) as typeof api.get
createRoot(document.getElementById('root')!).render(<QueryClientProvider client={new QueryClient()}><MemoryRouter initialEntries={['/alignments?job_id=synthetic']}><AlignmentPage /></MemoryRouter></QueryClientProvider>)
