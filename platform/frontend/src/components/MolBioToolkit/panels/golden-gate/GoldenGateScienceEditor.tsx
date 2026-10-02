import { lazy, Suspense, useState } from 'react';
import type { CDSConstraint, DatasetChoice, DomesticationSettings, GoldenGateDesignRequest, NativeScienceSettings, OverhangSearch, ReactionRequest, ReactionPart, ReactionReagent, CyclingProgram, UnwantedSite, WindowSearch } from '../../../../lib/goldenGateDesign';
import { Choice } from './Fields';
const Fidelity = lazy(() => import('./GoldenGateFidelityEditor').then(m => ({ default: m.GoldenGateFidelityEditor })));
const Overhangs = lazy(() => import('./GoldenGateFidelityEditor').then(m => ({ default: m.GoldenGateOverhangSearchEditor })));
const Windows = lazy(() => import('./GoldenGateFidelityEditor').then(m => ({ default: m.GoldenGateWindowSearchEditor })));
const Domestication = lazy(() => import('./GoldenGateDomesticationEditor').then(m => ({ default: m.GoldenGateDomesticationEditor })));
const Reaction = lazy(() => import('./GoldenGateReactionEditor').then(m => ({ default: m.GoldenGateReactionEditor })));

export interface GoldenGateScienceEditorProps {
  value: NativeScienceSettings;
  onChange: (v: NativeScienceSettings) => void;
  core: GoldenGateDesignRequest;
  datasets: DatasetChoice[];
  siteChoices: UnwantedSite[];
  /** Explicit caller/discovery templates, never hydrated over operator settings. */
  templates: {
    overhang_search: OverhangSearch; window_search: WindowSearch;
    domestication: DomesticationSettings; cds: CDSConstraint;
    reaction: ReactionRequest; reactionPart: ReactionPart;
    reagent: ReactionReagent; cycling: CyclingProgram;
  };
}
/** Compose as a child of GoldenGateDesignEditor; all changes replace its draft.
 * This only authors exact native arguments. It does not claim that the core
 * assemble_parts leaf executes search, edits or reaction calculations. */
export function GoldenGateScienceEditor({ value, onChange, core, datasets, siteChoices, templates }: GoldenGateScienceEditorProps) {
  const [section, setSection] = useState('');
  const [editSource, setEditSource] = useState('');
  const set = (patch: Partial<NativeScienceSettings>) => onChange({ ...value, ...patch });
  return <section aria-label="Native scientific controls"><Choice label="Scientific controls" value={section} values={['', 'fidelity', 'overhang search', 'target windows', 'domestication', 'reaction']} onChange={setSection} /><p>Supplemental native settings are retained separately. Their execution belongs to the receiving workflow adapter; selecting a section never invokes computation.</p><Suspense fallback={<p>Loading controls…</p>}>
    {section === 'fidelity' && <Fidelity value={value.fidelity} onChange={fidelity => set({ fidelity })} datasets={datasets} />}
    {section === 'overhang search' && (value.overhang_search ? <><Overhangs value={value.overhang_search} onChange={overhang_search => set({ overhang_search })} /><button type="button" onClick={() => set({ overhang_search: null })}>Remove overhang search request</button></> : <button type="button" onClick={() => set({ overhang_search: structuredClone(templates.overhang_search) })}>Use explicit overhang search template</button>)}
    {section === 'target windows' && (value.window_search ? <><Windows value={value.window_search} onChange={window_search => set({ window_search })} /><button type="button" onClick={() => set({ window_search: null })}>Remove target window request</button></> : <button type="button" onClick={() => set({ window_search: structuredClone(templates.window_search) })}>Use explicit target window template</button>)}
    {section === 'domestication' && <>{value.domestication.map((d, i) => { const source = core.sources.find(s => s.id === d.source_id)?.source; return <fieldset key={i}><legend>Domestication source {d.source_id}</legend><Domestication value={d.settings} onChange={settings => set({ domestication: value.domestication.map((x, j) => i === j ? { ...d, settings } : x) })} featureIds={source?.kind === 'inline' ? source.features.map(f => f.id) : d.settings.cds.map(c => c.feature_id)} siteChoices={siteChoices} cdsTemplate={templates.cds} /><button type="button" onClick={() => set({ domestication: value.domestication.filter((_, j) => i !== j) })}>Remove domestication request {d.source_id}</button></fieldset>; })}<Choice label="Source for domestication" value={editSource} values={core.sources.map(s => s.id)} onChange={setEditSource} /><button type="button" disabled={!editSource} onClick={() => set({ domestication: [...value.domestication, { source_id: editSource, settings: structuredClone(templates.domestication) }] })}>Add explicit domestication template</button></>}
    {section === 'reaction' && (value.reaction ? <><Reaction value={value.reaction} onChange={reaction => set({ reaction })} partIds={core.parts.map(p => p.id)} templates={{ part: templates.reactionPart, reagent: templates.reagent, cycling: templates.cycling }} /><button type="button" onClick={() => set({ reaction: null })}>Remove reaction worksheet request</button></> : <button type="button" onClick={() => set({ reaction: structuredClone(templates.reaction) })}>Use explicit reaction template</button>)}
  </Suspense></section>;
}
