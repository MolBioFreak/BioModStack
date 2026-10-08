import React from 'react';
import { createRoot } from 'react-dom/client';

import MolstarViewer from '../src/components/MolstarViewer';

const pdb = `HEADER    BMS SETTINGS SMOKE
ATOM      1  N   ALA A   1      11.104  13.207  14.331  1.00 90.00           N
ATOM      2  CA  ALA A   1      12.560  13.207  14.331  1.00 90.00           C
ATOM      3  C   ALA A   1      13.000  14.630  14.331  1.00 90.00           C
ATOM      4  O   ALA A   1      12.300  15.600  14.331  1.00 90.00           O
ATOM      5  N   GLY A   2      14.250  14.760  14.331  1.00 80.00           N
ATOM      6  CA  GLY A   2      14.850  16.080  14.331  1.00 80.00           C
ATOM      7  C   GLY A   2      16.360  15.970  14.331  1.00 80.00           C
ATOM      8  O   GLY A   2      17.020  16.990  14.331  1.00 80.00           O
TER
END
`;
const url = URL.createObjectURL(new Blob([pdb], { type: 'chemical/x-pdb' }));
const element = document.querySelector<HTMLElement>('#root');
if (!element) throw new Error('settings smoke root missing');
const root = createRoot(element);
root.render(
    <div style={{ width: '100%', height: '100%' }}>
        <MolstarViewer
            structureUrl={url}
            format="pdb"
            alphafoldView={false}
            hideControls={false}
            height="100%"
            label="Full viewer controls smoke"
        />
    </div>,
);
window.addEventListener('beforeunload', () => {
    root.unmount();
    URL.revokeObjectURL(url);
});
