import React from 'react'; import {createRoot} from 'react-dom/client'; import Av2Viewer from '../../src/components/alignments/Av2Viewer';
const doc = {resultId:'synthetic', rows:[{rowId:'r1',label:'one',aligned:'AC-GT'},{rowId:'r2',label:'two',aligned:'ACAGT'},{rowId:'r3',label:'three',aligned:'ACAGT'}]};
createRoot(document.getElementById('root')!).render(<Av2Viewer document={doc} onSelect={a=>{document.getElementById('result')!.textContent=JSON.stringify(a)}}/>);
