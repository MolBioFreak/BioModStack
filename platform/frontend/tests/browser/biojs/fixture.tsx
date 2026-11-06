import React from 'react'
import { createRoot } from 'react-dom/client'
import Biojs from '../../../src/components/alignments/BiojsViewer'
const doc = {resultId:'r',rows:[{rowId:'one',label:'first',aligned:'ACGT'},{rowId:'two',label:'second',aligned:'A-GT'}]}
function App() { const [selection,setSelection] = React.useState(); return <><output id="selected">{JSON.stringify(selection ?? null)}</output><Biojs document={doc} selection={selection} onSelect={setSelection}/></> }
createRoot(document.getElementById('root')!).render(<App />)
