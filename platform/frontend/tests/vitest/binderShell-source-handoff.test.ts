import { expect, it } from 'vitest';
import { bc2SourceHandoff, adoptShellSourcePath, binderEngineSearch } from '../../src/lib/binderShell';

it('source-handoff retains native document/model/derivative ancestry and never invents masks', () => {
    const settings = { targets: [{ target_path: 'inputs/model-2.cif', chains: 'auth-X', hotspots: 'X42A' }], binder_scaffold: 'inputs/scaffold.cif', max_trajectories: 1 };
    const reference = { path: 'inputs/model-2.cif', source: { name: 'exact model', path: 'inputs/model-2.cif', modelNumber: 2, designId: 'D', jobId: 'J', document: { artifact_id: 'native-CIF', sha256: 'digest', target_state: 'selected' }, derivedFrom: { name: 'original CIF', path: 'inputs/original.cif' } } };
    const handoff = bc2SourceHandoff(settings, { 'target:0': reference });
    expect(handoff.target?.path).toBe('inputs/model-2.cif'); expect(handoff.target?.modelNumber).toBe(2);
    expect(handoff.target?.reference?.document).toEqual(reference.source.document);
    expect(handoff.bc2?.references['target:0'].source.derivedFrom).toEqual(reference.source.derivedFrom);
    expect(handoff.target).not.toHaveProperty('chain'); expect(handoff.target).not.toHaveProperty('residues');
    expect(JSON.parse(JSON.stringify(handoff))).toEqual(handoff);
    expect(settings.targets[0].hotspots).toBe('X42A');
});
it('a manually replaced BC2 path cannot reacquire a stale producer reference', () => {
    const handoff = bc2SourceHandoff({ targets: [{ target_path: 'inputs/new.pdb' }] }, { 'target:0': { path: 'inputs/old.cif', source: { name: 'Old producer', path: 'inputs/old.cif', designId: 'old', modelNumber: 7 } } });
    expect(handoff.target?.path).toBe('inputs/new.pdb'); expect(handoff.target?.reference).toBeUndefined(); expect(handoff.target?.modelNumber).toBeUndefined();
    expect(handoff.bc2?.references['target:0'].source.designId).toBe('old');
});
it.each(['sequence.fasta', 'sequence.fa', 'sequence.faa'])('FASTA %s remains inspectable context, not an invented structure', target_path => {
    const settings = { targets: [{ target_path }] }; const handoff = bc2SourceHandoff(settings);
    expect(handoff.target).toBeUndefined(); expect(handoff.bc2?.settings).toEqual(settings);
});
it.each(['target_pdb', 'framework_pdb', 'scaffold_path'])('destination-owned empty, null, false and zero %s win over source inheritance', key => {
    for (const value of ['', null, false, 0]) expect(adoptShellSourcePath({ [key]: value }, key, 'inputs/adopted.pdb')).toEqual({ [key]: value });
    expect(adoptShellSourcePath({}, key, 'inputs/adopted.pdb')).toEqual({ [key]: 'inputs/adopted.pdb' });
});
it('retained engine URL deletes incompatible native model/mode but retains destination and return keys', () => {
    const query = new URLSearchParams(binderEngineSearch('?model=boltzgen&mode=protein_binder&launch_context_id=dest&project_id=P&return_to=%2Fprojects%2FP', 'bindcraft2'));
    expect(query.get('model')).toBeNull(); expect(query.get('mode')).toBeNull(); expect(query.get('engine')).toBe('bindcraft2');
    expect(query.get('launch_context_id')).toBe('dest'); expect(query.get('return_to')).toBe('/projects/P');
});
