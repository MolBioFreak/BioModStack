/** Independent selected post-round diagnostic, never sequence redesign. */
export interface BindInterfaceContextSettings {
  binder_chain: string;
  target_chain: string;
  target_patch: string[];
  seed: number;
  samples: number;
  temperature: number;
}

export interface BindInterfaceContextSelection {
  action: 'ligandmpnn_interface_context';
  source_job_id: string;
  round_id: string;
  candidate_ids: string[];
  settings: BindInterfaceContextSettings;
}

interface Props {
  sourceJobId: string;
  roundId: string;
  selectedCandidateIds: string[];
  settings: BindInterfaceContextSettings;
  onSettingsChange: (settings: BindInterfaceContextSettings) => void;
  onSelect: (selection: BindInterfaceContextSelection) => void;
  /** Parent must supply the real selected-action submission route before mounting. */
  submitting?: boolean;
}

export default function BindLigandMPNNInterfaceContext({ sourceJobId, roundId, selectedCandidateIds, settings, onSettingsChange, onSelect, submitting = false }: Props) {
  const change = (patch: Partial<BindInterfaceContextSettings>) => onSettingsChange({ ...settings, ...patch });
  return <section aria-label="LigandMPNN interface context">
    <h3>LigandMPNN interface context (experimental)</h3>
    <p>Sample a held-out target patch with the binder supplied and removed. Raw recovery counts are unqualified evidence, not binding scores or a verdict.</p>
    <label>Fixed binder chain <input aria-label="Fixed binder chain" maxLength={1} value={settings.binder_chain} onChange={e => change({ binder_chain: e.target.value })} /></label>
    <label>Target chain <input aria-label="Target chain" maxLength={1} value={settings.target_chain} onChange={e => change({ target_chain: e.target.value })} /></label>
    <label>Target patch residue IDs (comma-separated, e.g. A12,A13A)
      <input aria-label="Target patch residue IDs" value={settings.target_patch.join(',')} onChange={e => change({ target_patch: e.target.value.split(',').map(x => x.trim()).filter(Boolean) })} />
    </label>
    <label>Seed <input aria-label="Seed" type="number" min={0} max={2147483647} step={1} value={settings.seed} onChange={e => change({ seed: Number(e.target.value) })} /></label>
    <label>Samples <input aria-label="Samples" type="number" min={1} max={16} step={1} value={settings.samples} onChange={e => change({ samples: Number(e.target.value) })} /></label>
    <label>Temperature <input aria-label="Temperature" type="number" min={0.01} max={2} step={0.01} value={settings.temperature} onChange={e => change({ temperature: Number(e.target.value) })} /></label>
    <button type="button" disabled={submitting || !selectedCandidateIds.length} onClick={() => onSelect({
      action: 'ligandmpnn_interface_context', source_job_id: sourceJobId, round_id: roundId,
      candidate_ids: selectedCandidateIds, settings,
    })}>Run selected interface context</button>
  </section>;
}
