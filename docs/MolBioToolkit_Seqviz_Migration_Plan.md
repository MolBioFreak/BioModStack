# MolBio Toolkit: OVE to Seqviz Migration Plan

**Date**: 2026-01-31  
**Status**: Approved for implementation (Audit Complete)  
**Estimated Effort**: 4-5 days (revised after gap analysis)

---

## Executive Summary

Replace the buggy OVE (Open Vector Editor) React 19 port with a clean architecture using [seqviz](https://github.com/Lattice-Automation/seqviz) for visualization and custom tool panels backed by our existing `/api/molbio/*` endpoints.

### Why Replace OVE?
1. **Port is broken** - PCRTool, DigestTool use deprecated `recompose` HOCs
2. **Original OVE was never SnapGene-level** - AlignmentTool is a 13-line stub
3. **Complexity** - 30+ subdirectories, Redux form integration, R19 incompatibilities
4. **Our backend already works** - `/api/molbio/*` endpoints are solid

---

## Architecture Overview

```
Current (OVE-based):
┌──────────────────────────────────────────┐
│ MolBioToolkit                            │
│ ├── OVE Editor ← BUGGY, COMPLEX          │
│ │   ├── DigestTool ← recompose issues    │
│ │   ├── PCRTool ← recompose issues       │
│ │   └── AlignmentTool ← STUB             │
│ └── Sequence Library Sidebar ← WORKING   │
└──────────────────────────────────────────┘

New (Seqviz-based):
┌──────────────────────────────────────────┐
│ MolBioToolkit                            │
│ ├── SequenceViewer (seqviz) ← NEW        │
│ ├── Tool Panels                          │
│ │   ├── DigestPanel ← uses /api/molbio   │
│ │   ├── PCRPanel ← uses /api/molbio      │
│ │   ├── PrimerPanel ← NEW                │
│ │   └── LigationPanel ← uses /api/molbio │
│ └── Sequence Library Sidebar ← KEEP      │
└──────────────────────────────────────────┘
```

---

## Phase 0: Preserve Critical Components

### Keep These Packages

| Package | Location | Purpose |
|---------|----------|---------|
| `@teselagen/bio-parsers` | `packages/bio-parsers/` | GenBank, FASTA, SnapGene parsing |
| `@teselagen/sequence-utils` | `packages/sequence-utils/` | Reverse complement, digest logic, enzyme DB |
| `@teselagen/range-utils` | `packages/range-utils/` | Sequence range math |
| `@teselagen/file-utils` | `packages/file-utils/` | File handling utilities |

### Keep These Backend APIs

| Endpoint | File | Purpose |
|----------|------|---------|
| `GET/POST /api/sequences` | `platform/api/routers/sequences.py` | Sequence CRUD |
| `POST /api/molbio/digest` | `platform/api/routers/molbio.py` | Restriction digest |
| `POST /api/molbio/ligate` | `platform/api/routers/molbio.py` | Fragment ligation |
| `POST /api/molbio/pcr` | `platform/api/routers/molbio.py` | PCR simulation |
| `POST /api/molbio/mutagenesis` | `platform/api/routers/molbio.py` | Point mutations |
| `POST /api/molbio/gibson` | `platform/api/routers/molbio.py` | Gibson assembly |
| `POST /api/molbio/golden-gate` | `platform/api/routers/molbio.py` | Golden Gate |

### Remove

| Package | Reason |
|---------|--------|
| `packages/ove-react19/` | Buggy, recompose deps, 30+ dirs of dead code |

---

## Phase 1: Seqviz Integration

### Step 1.1: Install Seqviz

**File**: `platform/frontend/package.json`

```diff
  "dependencies": {
-   "@biomodstack/ove": "workspace:*",
+   "seqviz": "^3.10.10",
    "@blueprintjs/core": "^5.0.0",
```

**Command**:
```bash
cd platform/frontend
pnpm remove @biomodstack/ove
pnpm add seqviz
```

### Step 1.2: Create SequenceViewer Component

**File**: `platform/frontend/src/components/MolBioToolkit/SequenceViewer.tsx`

**Purpose**: Wrap seqviz with our data model and event handlers.

```tsx
import { SeqViz } from "seqviz";
import { useMemo } from "react";

interface SequenceData {
  name: string;
  sequence: string;
  circular: boolean;
  features: Feature[];
  primers?: Primer[];
  translations?: Translation[];
}

interface VisibilityState {
  features: boolean;
  primers: boolean;
  cutsites: boolean;
  translations: boolean;
  reverseComplement: boolean;
}

interface Props {
  sequenceData: SequenceData;
  visibility: VisibilityState;
  selectedEnzymes?: string[];
  searchQuery?: string;
  onSelection?: (sel: { start: number; end: number; type?: string }) => void;
  onSearch?: (results: { start: number; end: number }[]) => void;
  highlightedRegions?: { start: number; end: number; color: string }[];
}

export function SequenceViewer({ 
  sequenceData,
  visibility,
  selectedEnzymes = [],
  searchQuery,
  onSelection,
  onSearch,
  highlightedRegions 
}: Props) {
  // Build annotations array based on visibility toggles
  const annotations = useMemo(() => {
    const result = [];
    
    if (visibility.features) {
      result.push(...sequenceData.features.map(f => ({
        name: f.name,
        start: f.start,
        end: f.end,
        direction: f.strand === 1 ? 1 : -1,
        color: f.color || "#3498db",
        type: f.type
      })));
    }
    
    if (visibility.primers && sequenceData.primers) {
      result.push(...sequenceData.primers.map(p => ({
        name: p.name,
        start: p.start,
        end: p.end,
        direction: p.strand === 1 ? 1 : -1,
        color: "#e74c3c",
        type: "primer"
      })));
    }
    
    return result;
  }, [sequenceData, visibility.features, visibility.primers]);

  // Build translations array if visible
  const translations = useMemo(() => {
    if (!visibility.translations || !sequenceData.translations) return [];
    return sequenceData.translations.map(t => ({
      start: t.start,
      end: t.end,
      direction: t.strand
    }));
  }, [sequenceData.translations, visibility.translations]);

  return (
    <div className="sequence-viewer h-full">
      <SeqViz
        name={sequenceData.name}
        seq={sequenceData.sequence}
        annotations={annotations}
        translations={translations}
        enzymes={visibility.cutsites ? selectedEnzymes : []}
        viewer={sequenceData.circular ? "both" : "linear"}
        showComplement={visibility.reverseComplement}
        rotateOnScroll
        
        // Selection handling with type info
        onSelection={(sel) => {
          // sel includes: start, end, clockwise, type, name
          onSelection?.(sel);
        }}
        
        // Search capability
        search={searchQuery ? { query: searchQuery, mismatch: 0 } : undefined}
        onSearch={onSearch}
        
        highlights={highlightedRegions}
      />
    </div>
  );
}
```

**Why**: Seqviz provides circular+linear views, annotation rendering, click handlers out of the box. No Redux required.

---

## Phase 2: Tool Panels

### Step 2.1: DigestPanel

**File**: `platform/frontend/src/components/MolBioToolkit/panels/DigestPanel.tsx`

**Purpose**: Replace OVE DigestTool with clean implementation using our backend.

```tsx
import { useState } from 'react';
import { COMMON_ENZYMES } from '@teselagen/sequence-utils';

interface Props {
  sequenceId: string | null;
  sequence: string;
  isCircular: boolean;
  onFragmentSelect: (fragment: { start: number; end: number }) => void;
}

export function DigestPanel({ sequenceId, sequence, isCircular, onFragmentSelect }: Props) {
  const [selectedEnzymes, setSelectedEnzymes] = useState<string[]>([]);
  const [fragments, setFragments] = useState<Fragment[]>([]);

  const runDigest = async () => {
    const res = await fetch('/api/molbio/digest', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        sequence_id: sequenceId,
        sequence: sequenceId ? undefined : sequence,
        is_circular: isCircular,
        enzymes: selectedEnzymes.map(name => ({ name, site: COMMON_ENZYMES[name] }))
      })
    });
    const data = await res.json();
    setFragments(data.fragments);
  };

  return (
    <div className="digest-panel p-4">
      <h3 className="font-semibold mb-2">Restriction Digest</h3>
      {/* Enzyme multi-select */}
      {/* Run button */}
      {/* Fragment table with click-to-highlight */}
    </div>
  );
}
```

**Why**: OVE DigestTool uses recompose and breaks in R19. Our backend already handles the digest logic.

### Step 2.2: PCRPanel

**File**: `platform/frontend/src/components/MolBioToolkit/panels/PCRPanel.tsx`

**Purpose**: Replace broken OVE PCRTool.

```tsx
export function PCRPanel({ sequenceId, sequence, onProductGenerated }: Props) {
  const [fwdPrimer, setFwdPrimer] = useState('');
  const [revPrimer, setRevPrimer] = useState('');
  
  const runPCR = async () => {
    const res = await fetch('/api/molbio/pcr', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        sequence_id: sequenceId,
        sequence: sequenceId ? undefined : sequence,
        primer_fwd: fwdPrimer,
        primer_rev: revPrimer
      })
    });
    const data = await res.json();
    onProductGenerated(data.product);
  };

  return (
    <div className="pcr-panel p-4">
      <h3 className="font-semibold mb-2">PCR</h3>
      <input placeholder="Forward primer (5'→3')" value={fwdPrimer} onChange={...} />
      <input placeholder="Reverse primer (5'→3')" value={revPrimer} onChange={...} />
      <button onClick={runPCR}>Amplify</button>
    </div>
  );
}
```

**Why**: OVE PCRTool requires existing primer annotations. Our version accepts any primer sequences.

### Step 2.3: PrimerPanel

**File**: `platform/frontend/src/components/MolBioToolkit/panels/PrimerPanel.tsx`

**Purpose**: Design and add primers to sequence. Port binding detection from our OVE redesign.

**Key Logic to Port**:
- Bi-directional binding site search (from `packages/ove-react19/src/helperComponents/PrimerSequenceInput.jsx`)
- Tm calculation using Wallace rule (upgrade to Nearest-Neighbor later)
- GC% calculation

### Step 2.4: LigationPanel

**File**: `platform/frontend/src/components/MolBioToolkit/panels/LigationPanel.tsx`

**Purpose**: Select fragments → ligate → create new sequence.

Uses `/api/molbio/ligate` endpoint.

---

## Phase 2.5: Critical Gap Coverage (From Audit)

### Step 2.5.1: Undo/Redo System

**File**: `platform/frontend/src/components/MolBioToolkit/hooks/useSequenceHistory.ts`

**Purpose**: Enable undo/redo for sequence edits and annotation changes.

```tsx
import { useReducer, useCallback } from 'react';

interface HistoryState<T> {
  past: T[];
  present: T;
  future: T[];
}

function historyReducer<T>(state: HistoryState<T>, action: { type: string; payload?: T }) {
  switch (action.type) {
    case 'SET':
      return {
        past: [...state.past, state.present],
        present: action.payload!,
        future: []
      };
    case 'UNDO':
      if (state.past.length === 0) return state;
      return {
        past: state.past.slice(0, -1),
        present: state.past[state.past.length - 1],
        future: [state.present, ...state.future]
      };
    case 'REDO':
      if (state.future.length === 0) return state;
      return {
        past: [...state.past, state.present],
        present: state.future[0],
        future: state.future.slice(1)
      };
    default:
      return state;
  }
}

export function useSequenceHistory<T>(initialState: T) {
  const [state, dispatch] = useReducer(historyReducer<T>, {
    past: [],
    present: initialState,
    future: []
  });

  return {
    state: state.present,
    set: (value: T) => dispatch({ type: 'SET', payload: value }),
    undo: () => dispatch({ type: 'UNDO' }),
    redo: () => dispatch({ type: 'REDO' }),
    canUndo: state.past.length > 0,
    canRedo: state.future.length > 0
  };
}
```

**Integration**: Replace `useState` for `sequenceData` with `useSequenceHistory`.

### Step 2.5.2: Export Dropdown

**File**: `platform/frontend/src/components/MolBioToolkit/ExportDropdown.tsx`

**Purpose**: Multi-format export (GenBank, FASTA).

```tsx
import { jsonToGenbank, jsonToFasta } from '@teselagen/bio-parsers';

interface Props {
  sequenceData: SequenceData;
}

export function ExportDropdown({ sequenceData }: Props) {
  const exportAs = (format: 'genbank' | 'fasta') => {
    const content = format === 'genbank' 
      ? jsonToGenbank(sequenceData)
      : jsonToFasta({ sequence: sequenceData.sequence, name: sequenceData.name });
    
    const blob = new Blob([content], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `${sequenceData.name}.${format === 'genbank' ? 'gb' : 'fasta'}`;
    link.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="relative">
      <button className="dropdown-trigger">Export ▾</button>
      <div className="dropdown-menu">
        <button onClick={() => exportAs('genbank')}>GenBank (.gb)</button>
        <button onClick={() => exportAs('fasta')}>FASTA (.fasta)</button>
      </div>
    </div>
  );
}
```

### Step 2.5.3: Visibility Controls

**File**: `platform/frontend/src/components/MolBioToolkit/VisibilityPanel.tsx`

**Purpose**: Toggle annotation layer visibility (features, primers, cutsites, translations).

```tsx
interface VisibilityState {
  features: boolean;
  primers: boolean;
  cutsites: boolean;
  translations: boolean;
  reverseComplement: boolean;
}

interface Props {
  visibility: VisibilityState;
  onChange: (key: keyof VisibilityState) => void;
}

export function VisibilityPanel({ visibility, onChange }: Props) {
  return (
    <div className="visibility-panel p-3 space-y-2">
      <h4 className="font-semibold text-sm">Show/Hide</h4>
      {Object.entries(visibility).map(([key, value]) => (
        <label key={key} className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={value}
            onChange={() => onChange(key as keyof VisibilityState)}
          />
          {key.charAt(0).toUpperCase() + key.slice(1)}
        </label>
      ))}
    </div>
  );
}
```

### Step 2.5.4: Edit Mode (Stub for Future)

**File**: `platform/frontend/src/components/MolBioToolkit/panels/EditPanel.tsx`

**Purpose**: Inline sequence editing (MVP: insert/delete at selection).

**MVP Implementation**:
```tsx
interface Props {
  selection: { start: number; end: number } | null;
  onInsert: (position: number, sequence: string) => void;
  onDelete: (start: number, end: number) => void;
}

export function EditPanel({ selection, onInsert, onDelete }: Props) {
  const [insertSeq, setInsertSeq] = useState('');

  return (
    <div className="edit-panel p-4">
      <h3 className="font-semibold mb-2">Edit Sequence</h3>
      
      {selection && (
        <div className="mb-4">
          <span className="text-sm text-slate-400">
            Selected: {selection.start + 1} - {selection.end + 1}
          </span>
          <button 
            onClick={() => onDelete(selection.start, selection.end)}
            className="ml-2 text-red-400 text-sm"
          >
            Delete Selection
          </button>
        </div>
      )}
      
      <div className="space-y-2">
        <input
          placeholder="Sequence to insert (5'→3')"
          value={insertSeq}
          onChange={(e) => setInsertSeq(e.target.value.toUpperCase().replace(/[^ACGT]/g, ''))}
          className="w-full px-2 py-1 bg-slate-700 rounded"
        />
        <button 
          onClick={() => selection && onInsert(selection.start, insertSeq)}
          disabled={!selection || !insertSeq}
          className="px-3 py-1 bg-blue-600 rounded disabled:opacity-50"
        >
          Insert at Selection
        </button>
      </div>
    </div>
  );
}
```

**Note**: Full inline editing (click-to-type in sequence view) is a Phase 5 enhancement.

### Step 3.1: New File Structure

```
platform/frontend/src/components/MolBioToolkit/
├── index.tsx              # Main layout component
├── SequenceViewer.tsx     # Seqviz wrapper
├── SequenceLibrary.tsx    # Extracted from current MolBioToolkit
├── SequenceHeader.tsx     # Name, length, GC%, circularity
├── ExportDropdown.tsx     # GenBank/FASTA export (Phase 2.5)
├── VisibilityPanel.tsx    # Toggle annotation layers (Phase 2.5)
├── panels/
│   ├── DigestPanel.tsx
│   ├── PCRPanel.tsx
│   ├── PrimerPanel.tsx
│   ├── LigationPanel.tsx
│   ├── MutagenesisPanel.tsx
│   ├── FeaturePanel.tsx   # Add/edit annotations
│   └── EditPanel.tsx      # Sequence insert/delete (Phase 2.5)
├── hooks/
│   ├── useSequenceData.ts       # Load/save sequence state
│   ├── useSequenceHistory.ts    # Undo/redo (Phase 2.5)
│   └── useSequenceOperations.ts # API wrappers
└── types.ts               # Shared TypeScript types
```

### Step 3.2: Main Layout (index.tsx)

**Current**: 1021 lines, mixes Redux, OVE Editor, all operations  
**New**: ~300 lines, clean composition

```tsx
export function MolBioToolkit() {
  const [sequenceData, setSequenceData] = useState<SequenceData>(EMPTY_SEQUENCE);
  const [selectedSequenceId, setSelectedSequenceId] = useState<string | null>(null);
  const [activePanel, setActivePanel] = useState<'digest' | 'pcr' | 'primers' | null>(null);
  const [highlightedRegions, setHighlightedRegions] = useState<Region[]>([]);

  return (
    <div className="h-screen flex">
      {/* Left: Sequence Library */}
      <SequenceLibrary 
        selectedId={selectedSequenceId}
        onSelect={loadSequence}
      />

      {/* Center: Viewer */}
      <div className="flex-1 flex flex-col">
        <SequenceHeader sequenceData={sequenceData} onSave={saveSequence} />
        <SequenceViewer 
          sequenceData={sequenceData}
          highlightedRegions={highlightedRegions}
          onSelection={handleSelection}
        />
      </div>

      {/* Right: Tool Panels (collapsible) */}
      <div className="w-80 border-l">
        <PanelTabs active={activePanel} onChange={setActivePanel} />
        {activePanel === 'digest' && <DigestPanel ... />}
        {activePanel === 'pcr' && <PCRPanel ... />}
        {activePanel === 'primers' && <PrimerPanel ... />}
      </div>
    </div>
  );
}
```

---

## Phase 4: Cleanup

### Step 4.1: Remove OVE Package

**Command**:
```bash
rm -rf packages/ove-react19
```

### Step 4.2: Update Package References

**Files to update**:
- `pnpm-workspace.yaml` - remove ove-react19
- `platform/frontend/package.json` - already done in Phase 1
- `platform/frontend/vite.config.ts` - remove any OVE-specific aliases

### Step 4.3: Audit @teselagen/ui

Keep only what's needed:
- `DataTable` - for fragment/feature tables
- Form components if used

Remove OVE-specific components (if any leaked in).

---

## Verification Checklist

### Functional Tests
- [ ] Import GenBank file → renders in circular/linear view
- [ ] Import FASTA file → renders correctly
- [ ] Click feature → highlights in viewer
- [ ] Select region → returns correct coordinates
- [ ] Run digest → fragments displayed, click highlights
- [ ] Run PCR → product preview shown
- [ ] Add primer → binding sites detected
- [ ] Save sequence → persists to database
- [ ] Load sequence from library → displays correctly

### Regression Tests
- [ ] File parsing still works (bio-parsers unchanged)
- [ ] Backend APIs still work (unchanged)
- [ ] Sequence library CRUD works (unchanged)

---

## Timeline (Revised)

| Day | Tasks |
|-----|-------|
| 1 | Install seqviz, create SequenceViewer, wire into layout |
| 1 | Extract SequenceLibrary from current component |
| 2 | Build DigestPanel, PCRPanel |
| 2 | Port primer binding detection to PrimerPanel |
| 3 | Build LigationPanel, MutagenesisPanel |
| 3 | Add ExportDropdown, useSequenceHistory (undo/redo) |
| 4 | Build VisibilityPanel, EditPanel (MVP) |
| 4 | Integration testing, bug fixes |
| 5 | Remove ove-react19, Redux store audit, final cleanup |
| 6-7 | **Phase 5**: Advanced PCR/Enzyme/GC visualizations |
| 8 | **Phase 6**: RNA view support |

---

## Phase 5: Advanced Visualization Features (Post-MVP)

### Step 5.1: Enhanced Primer Visualization

**Primer Panel Display**:
- Show primers on sequence with directional arrows
- Display Tm and GC% inline on primer annotations
- Color-coded by Tm range (optimal: green, suboptimal: yellow/red)

**API**: Backend already supports - `nucleotide_sequences.py` has:
```python
class PrimerSchema(BaseModel):
    name: str
    sequence: str
    start: int
    end: int
    tm: Optional[float] = None
    gc_percent: Optional[float] = None
```

**Frontend Implementation**:
```tsx
// Enhanced primer annotation with Tm tooltip
const primerAnnotations = primers.map(p => ({
  ...p,
  label: `${p.name} (Tm: ${p.tm?.toFixed(1)}°C, GC: ${p.gc_percent?.toFixed(1)}%)`,
  color: getTmColor(p.tm)  // Green for 55-65°C, yellow outside, red at extremes
}));
```

### Step 5.2: Advanced Enzyme Filtering

**File**: `platform/frontend/src/components/MolBioToolkit/panels/EnzymeFilterPanel.tsx`

**Features**:
- Filter by number of cuts (single cutters, 2x, etc.)
- Filter by overhang type (blunt, 5' overhang, 3' overhang)
- Filter by commercial availability
- Common enzyme sets (NEB, Promega, etc.)
- Custom enzyme input

```tsx
interface EnzymeFilter {
  cutCount?: { min: number; max: number };
  overhangType?: 'blunt' | '5_prime' | '3_prime' | 'all';
  suppliers?: string[];
  customEnzymes?: string[];
}

export function EnzymeFilterPanel({ 
  sequence, 
  isCircular,
  onEnzymesSelected 
}: Props) {
  const [filters, setFilters] = useState<EnzymeFilter>({});
  
  // Use @teselagen/sequence-utils to get enzyme cut data
  const enzymeData = useMemo(() => 
    getEnzymeCutData(sequence, isCircular),
    [sequence, isCircular]
  );
  
  const filteredEnzymes = useMemo(() => 
    applyFilters(enzymeData, filters),
    [enzymeData, filters]
  );
  
  return (
    <div className="enzyme-filter-panel">
      <div className="filter-section">
        <label>Cut Count</label>
        <select onChange={e => setFilters({...filters, cutCount: parseCutCount(e.target.value)})}>
          <option value="1">Single cutters</option>
          <option value="2">Double cutters</option>
          <option value="3+">3+ cuts</option>
          <option value="all">All</option>
        </select>
      </div>
      
      <div className="filter-section">
        <label>Overhang Type</label>
        <select onChange={...}>
          <option value="blunt">Blunt ends</option>
          <option value="5_prime">5' overhang</option>
          <option value="3_prime">3' overhang</option>
        </select>
      </div>
      
      {/* Enzyme list with checkboxes */}
      <div className="enzyme-list">
        {filteredEnzymes.map(enzyme => (
          <label key={enzyme.name} className="flex gap-2">
            <input type="checkbox" ... />
            <span>{enzyme.name}</span>
            <span className="text-slate-400">({enzyme.cutCount} cuts)</span>
          </label>
        ))}
      </div>
    </div>
  );
}
```

### Step 5.3: GC Content Heat Map

**File**: `platform/frontend/src/components/MolBioToolkit/GCHeatMap.tsx`

**Purpose**: Overlay showing GC content variation using rolling window average.

```tsx
interface Props {
  sequence: string;
  windowSize?: number;  // Default 50bp
  colorScheme?: 'gradient' | 'discrete';
}

export function GCHeatMap({ sequence, windowSize = 50, colorScheme = 'gradient' }: Props) {
  const gcData = useMemo(() => {
    const values: { position: number; gcPercent: number }[] = [];
    
    for (let i = 0; i <= sequence.length - windowSize; i++) {
      const window = sequence.slice(i, i + windowSize);
      const gc = (window.match(/[GC]/gi)?.length || 0) / windowSize * 100;
      values.push({ position: i + windowSize / 2, gcPercent: gc });
    }
    
    return values;
  }, [sequence, windowSize]);
  
  // Render as SVG overlay on sequence view
  return (
    <svg className="gc-heatmap-overlay">
      {gcData.map(({ position, gcPercent }) => (
        <rect
          key={position}
          x={position * SCALE}
          y={0}
          width={1 * SCALE}
          height={HEATMAP_HEIGHT}
          fill={gcToColor(gcPercent, colorScheme)}
          opacity={0.6}
        />
      ))}
    </svg>
  );
}

// Color gradient: AT-rich (blue) → balanced (white) → GC-rich (red)
function gcToColor(gcPercent: number, scheme: string): string {
  if (scheme === 'discrete') {
    if (gcPercent < 30) return '#3b82f6';  // Blue - AT-rich
    if (gcPercent > 70) return '#ef4444';  // Red - GC-rich
    return '#a3a3a3';  // Gray - balanced
  }
  // Gradient: interpolate between colors
  const normalized = Math.max(0, Math.min(100, gcPercent)) / 100;
  return interpolateColor('#3b82f6', '#ef4444', normalized);
}
```

### Step 5.4: Primer Library

**Backend**: Add new table and endpoints.

**File**: `platform/api/routers/primer_library.py`

```python
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional, List

router = APIRouter(prefix="/api/primers", tags=["primers"])

class PrimerCreate(BaseModel):
    name: str
    sequence: str  # 5'→3'
    description: Optional[str] = None
    target_gene: Optional[str] = None
    tags: Optional[List[str]] = None

class PrimerResponse(BaseModel):
    id: str
    name: str
    sequence: str
    length: int
    tm: float
    gc_percent: float
    description: Optional[str]
    target_gene: Optional[str]
    tags: Optional[List[str]]
    created_at: datetime

@router.get("/")
async def list_primers(limit: int = 100, search: str = None):
    """List all primers, optionally filtered by name/sequence search."""
    ...

@router.post("/")
async def create_primer(data: PrimerCreate):
    """Save a primer to the library with auto-calculated Tm and GC%."""
    tm = calculate_tm_nearest_neighbor(data.sequence)
    gc = calculate_gc(data.sequence)
    ...

@router.get("/{primer_id}")
async def get_primer(primer_id: str):
    ...

@router.delete("/{primer_id}")
async def delete_primer(primer_id: str):
    ...
```

**Frontend**: `PrimerLibraryPanel.tsx`
- List saved primers with search/filter
- Click to insert primer at current selection
- Import primers from sequences
- Export primer list (CSV)

---

## Phase 6: RNA View Support

### Step 6.1: RNA Sequence Display

**Changes to SequenceData Type**:
```tsx
interface SequenceData {
  name: string;
  sequence: string;
  circular: boolean;
  sequenceType: 'dna' | 'rna' | 'protein';  // Add type
  features: Feature[];
  // ...
}
```

**RNA-Specific Rendering**:
```tsx
// In SequenceViewer
const displaySequence = sequenceData.sequenceType === 'rna'
  ? sequenceData.sequence.replace(/T/g, 'U')
  : sequenceData.sequence;

// RNA base colors
const baseColors = {
  dna: { A: '#22c55e', T: '#ef4444', G: '#3b82f6', C: '#f59e0b' },
  rna: { A: '#22c55e', U: '#ef4444', G: '#3b82f6', C: '#f59e0b' }
};
```

### Step 6.2: DNA ↔ RNA Conversion

**File**: `platform/frontend/src/components/MolBioToolkit/hooks/useSequenceConversion.ts`

```tsx
export function useSequenceConversion() {
  const dnaToRna = (seq: string) => seq.replace(/T/gi, 'U');
  const rnaToDna = (seq: string) => seq.replace(/U/gi, 'T');
  const reverseComplement = (seq: string, type: 'dna' | 'rna') => {
    const complement = type === 'dna' 
      ? { A: 'T', T: 'A', G: 'C', C: 'G' }
      : { A: 'U', U: 'A', G: 'C', C: 'G' };
    return seq.split('').reverse().map(b => complement[b.toUpperCase()] || b).join('');
  };
  
  return { dnaToRna, rnaToDna, reverseComplement };
}
```

**Backend Support**: Already exists in `nucleotide_sequences.py`:
```python
sequence_type: str = "dna"  # Can be "dna" or "rna"
```

### Step 6.3: RNA Secondary Structure (Future Enhancement)

**Scope**: Display predicted secondary structure (stem-loops, hairpins).

**Implementation Options**:
1. **ViennaRNA** wrappers via backend
2. **Secondary structure visualization** using arc diagrams
3. **Integration with Molstar** for 3D RNA structure

**Note**: This is a significant feature - defer to Phase 7+.

---

## Revised Feature Coverage Matrix

| Your Requirement | Phase | Status |
|------------------|-------|--------|
| Primers on sequences with Tm display | Phase 5.1 | ✅ Planned |
| Full restriction enzyme filtering | Phase 5.2 | ✅ Planned |
| GC content by base (heat map) | Phase 5.3 | ✅ Planned |
| Save primers to library | Phase 5.4 | ✅ Planned |
| Save sequences / plasmid library | Existing | ✅ Already works |
| RNA view | Phase 6.1-6.2 | ✅ Planned |
| RNA secondary structure | Phase 7+ | Deferred |

If seqviz doesn't work out:
1. Keep bio-parsers, sequence-utils (they're standalone)
2. Build minimal SVG-based viewer using sequence-utils for coordinate math
3. Alternative libs: [benchling-sequence-viewer](https://github.com/nickyvan/benchling-sequence-viewer), custom D3

---

## References

- [Seqviz GitHub](https://github.com/Lattice-Automation/seqviz)
- [Seqviz npm](https://www.npmjs.com/package/seqviz)
- [Teselagen bio-parsers](https://github.com/TeselaGen/tg-oss/tree/main/packages/bio-parsers)
- Current MolBioToolkit: `platform/frontend/src/components/MolBioToolkit/index.tsx`

---

## Critical Evaluation and Gap Assessment

**Audit Date**: 2026-01-31  
**Auditor**: AI Code Assistant  
**Status**: Review Required Before Implementation

---

### Audit Methodology

Performed source code audit of:
1. `MolBioToolkit/index.tsx` (1021 lines)
2. Current OVE wrapper configuration
3. Backend API endpoints (`/api/molbio_ops.py`)
4. SeqViz npm package (verified v3.10.10 latest)

---

### A. Active OVE Features Currently In Use

| Feature | OVE Component | Usage Status | SeqViz Equivalent |
|---------|---------------|--------------|-------------------|
| Circular Map | `panelsShown: circular` | ✅ Active | `viewer="circular"` |
| Linear Map | `panelsShown: rail` | ✅ Active | `viewer="linear"` |
| Sequence Map | `panelsShown: sequence` | ✅ Active | Built-in |
| Digest Tool | `panelsShown: digestTool` | ✅ Active | **❌ Not in SeqViz** |
| PCR Tool | `panelsShown: pcrTool` | ✅ Active | **❌ Not in SeqViz** |
| Properties Panel | `panelsShown: properties` | ✅ Active | **❌ Build custom** |
| Features | `annotationVisibility.features` | ✅ Active | `annotations` prop |
| Translations | `annotationVisibility.translations` | ✅ Active | `translations` prop |
| ORFs | `annotationVisibility.orfs` | ⚠️ Disabled default | **Not in plan** |
| Cutsites | `annotationVisibility.cutsites` | ✅ Active | `enzymes` prop |
| Primers | `annotationVisibility.primers` | ✅ Active | `primers` prop |
| Reverse Sequence | `annotationVisibility.reverseSequence` | ✅ Active | `showComplement` |
| Axis Numbers | `annotationVisibility.axisNumbers` | ✅ Active | Built-in |

---

### B. Toolbar Items Not Addressed in Plan

Current OVE toolbar (`ToolBarProps.toolList`):

| Tool | Current Plan Status | Gap? |
|------|---------------------|------|
| `saveTool` | Covered (header button) | ✅ |
| `downloadTool` | **⚠️ Partial** - Plan mentions export but not multi-format | Gap |
| `importTool` | Covered (existing modal) | ✅ |
| `undoTool` | **❌ Not addressed** | **Critical Gap** |
| `redoTool` | **❌ Not addressed** | **Critical Gap** |
| `cutsiteTool` | Covered (enzymes prop) | ✅ |
| `featureTool` | FeaturePanel mentioned | ✅ |
| `partTool` | **❌ Not addressed** | Gap |
| `primerTool` | PrimerPanel mentioned | ✅ |
| `oligoTool` | **❌ Not addressed** | Minor gap |
| `orfTool` | **❌ Not addressed** | Gap |
| `editTool` | **❌ Not addressed** | **Critical Gap** |
| `findTool` | Covered (SeqViz `search` prop) | ✅ |
| `alignmentTool` | **❌ Not addressed** | Gap (but OVE's is stub) |
| `visibilityTool` | **❌ Not addressed** | Gap |

---

### C. Identified Gaps in Migration Plan

#### 🔴 Critical Gaps

1. **Undo/Redo System**
   - Current: OVE uses Redux history via `undoTool`/`redoTool`
   - Plan: No mention of undo/redo
   - **Impact**: Users cannot undo sequence edits or annotation changes
   - **Fix**: Implement `useReducer` with history array or integrate `use-undo` library

2. **Sequence Editing (`editTool`)**
   - Current: OVE allows inline sequence editing (insert, delete, replace)
   - Plan: Treats sequence as read-only display
   - **Impact**: Users cannot edit sequences directly
   - **Fix**: Add `EditPanel` with controlled text input or evaluate SeqViz edit mode (if available)

3. **SeqViz Version Mismatch**
   - Plan specifies: `^3.10.0`
   - Actual latest: `3.10.10`
   - **Fix**: Update to `^3.10.10` or `^3.10`

#### 🟡 Moderate Gaps

4. **Download/Export Multi-Format**
   - Current: Export button uses `jsonToGenbank()`
   - Plan: Mentions export in Phase 3 but no implementation details
   - **Fix**: Add export dropdown with GenBank, FASTA options using `bio-parsers` formatters

5. **ORF Detection and Display**
   - Current: `orfTool` available, `orfs: false` by default
   - Plan: No mention of ORF functionality
   - **Fix**: Add ORF toggle to visibility controls; SeqViz doesn't auto-detect ORFs, would need backend call

6. **Parts Annotation Type**
   - Current: `annotationsToSupport.parts: true`, `partTool` in toolbar
   - Plan: Only mentions features and primers
   - **Fix**: Add parts to annotation mapping or document removal decision

7. **Visibility Toggle Panel**
   - Current: `visibilityTool` controls annotation layer visibility
   - Plan: No visibility controls mentioned
   - **Fix**: Add dropdown/panel with checkboxes for feature types

8. **Properties Panel**
   - Current: 8-tab properties panel (general, features, parts, primers, translations, cutsites, orfs, genbank)
   - Plan: Mentions `FeaturePanel` but not full properties replacement
   - **Fix**: Design modular properties sidebar or integrate into tool panels

#### 🟢 Minor Gaps

9. **Redux Store Removal**
   - Current: `MolBioToolkit/store.ts` exists, provides OVE Redux store
   - Plan: Mentions removal but doesn't address if other components depend on it
   - **Fix**: Audit store usage across codebase before removal

10. **Oligo Tool**
    - Current: `oligoTool` in toolbar
    - Plan: Not mentioned
    - **Fix**: Likely duplicate of primer functionality; verify and document removal

---

### D. Backend API Endpoint Corrections

The plan references `/api/molbio/*` but actual endpoints are in `/api/molbio_ops.py`:

| Plan Reference | Actual Endpoint | Notes |
|----------------|-----------------|-------|
| `/api/molbio/digest` | `/api/molbio/digest` | ✅ Correct |
| `/api/molbio/ligate` | `/api/molbio/ligate` | ✅ Correct |
| `/api/molbio/pcr` | `/api/molbio/pcr` | ✅ Correct |
| `/api/molbio/mutagenesis` | `/api/molbio/mutagenesis` | ✅ Correct |
| `/api/molbio/gibson` | `/api/molbio/gibson` | ✅ Correct |
| `/api/molbio/golden-gate` | `/api/molbio/golden-gate` | ✅ Correct |
| `/api/msa` | Separate endpoint | Used for MSA job kick-off |

---

### E. SeqViz Props Mapping Refinements

The plan's `SequenceViewer` component should account for:

```tsx
// Current plan has basic props. Add these for parity:
<SeqViz
  name={sequenceData.name}
  seq={sequenceData.sequence}
  annotations={annotations}
  primers={primers}           // Separate prop from annotations
  enzymes={enzymeNames}       // Built-in or custom enzyme defs
  translations={translations} // Missing from plan
  viewer={sequenceData.circular ? "both" : "linear"}
  showComplement={true}       // Maps to reverseSequence
  rotateOnScroll={true}
  
  // Selection handling (plan undersimplified)
  onSelection={(sel) => {
    // sel includes: start, end, clockwise, type, name
    // type can be: ANNOTATION, TRANSLATION, ENZYME, etc.
    handleSelection(sel);
  }}
  
  // Search capability (not addressed in plan)
  search={searchQuery ? { query: searchQuery, mismatch: 0 } : undefined}
  onSearch={handleSearchResults}
/>
```

---

### F. Recommended Plan Amendments

#### Priority 1: Before Implementation

- [x] Add **Undo/Redo section** to Phase 2 with implementation approach → Added Phase 2.5.1
- [x] Add **EditMode/EditPanel** design or document as future phase → Added Phase 2.5.4
- [x] Update SeqViz version to `^3.10.10` → Fixed in package.json diff
- [x] Add **Export dropdown** implementation to Phase 3 → Added Phase 2.5.2

#### Priority 2: Include in Implementation

- [x] Add **translations prop** mapping to `SequenceViewer` → Added in enhanced SequenceViewer
- [x] Add **VisibilityPanel** component for toggling annotation layers → Added Phase 2.5.3
- [ ] Add **Parts** handling (or document intentional removal) → Deferred: document in implementation
- [ ] Document Redux store removal impact → Deferred: audit during Phase 5

#### Priority 3: Post-MVP

- [ ] ORF detection backend + frontend integration
- [ ] Full Properties panel replacement
- [ ] Alignment tool (new implementation, OVE's was stub anyway)

---

### G. Revised Timeline Estimate

Original: 2-3 days  
**Revised**: 4-5 days (with critical gaps addressed)

| Day | Original Tasks | + Gap Fixes |
|-----|----------------|-------------|
| 1 | SeqViz install, SequenceViewer | + translations, primers, enzymes mapping |
| 2 | DigestPanel, PCRPanel | + Export dropdown |
| 3 | PrimerPanel, LigationPanel | + undo/redo hook setup |
| 4 | — | VisibilityPanel, EditPanel stub |
| 5 | Cleanup, testing | Integration testing, Redux removal audit |

---

### H. Verification Additions

Add to existing checklist:

**Undo/Redo Tests**
- [ ] Add feature → Undo → Feature removed
- [ ] Undo disabled when history empty
- [ ] Redo after undo restores state

**Export Tests**
- [ ] Export GenBank → Valid .gb file
- [ ] Export FASTA → Valid .fasta file
- [ ] Filename derived from sequence name

**Visibility Tests**
- [ ] Toggle features off → Hidden in viewer
- [ ] Toggle cutsites on → Enzyme sites appear
- [ ] State persists across sequence changes

---

### I. Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| SeqViz missing feature | Medium | High | Evaluate before each panel build |
| User workflow disruption | High | Medium | Beta test with power users |
| Performance on large sequences | Medium | Medium | Test with 50kb+ plasmids |
| Redux removal breaks other features | Low | High | Grep codebase for store imports |

---

**Recommendation**: Address Priority 1 gaps before starting implementation. This plan is ~85% complete; the remaining 15% (undo/redo, edit mode, visibility) is critical for feature parity.
