import { describe, expect, it } from 'vitest';
import { applyMolecularDynamicsProfileDefaults, molecularDynamicsRequestedSettings, validateMolecularDynamicsForm } from '../../src/components/molecularDynamicsUiState';
import { buildMolecularDynamicsPredictionRoute, buildMolecularDynamicsPredictionReturnRoute, buildMolecularDynamicsHandoffInitialValues, parseMolecularDynamicsHandoffRoute, storeMolecularDynamicsDraft, loadMolecularDynamicsDraft } from '../../src/components/gen2StartingStructureState';
const form: any = { jobName: 'trim', engine: 'gromacs', inputMode: 'structure', structurePath: 'typed-source', replicas: 1, productionNs: 0.001, randomSeed: 99, forceField: 'profile-owned', waterModel: 'profile-owned', paddingNm: 1, saltMolar: 0.15, neutralize: true, temperatureK: 300, pressureBar: 1, minimizationSteps: 50000, nvtPs: 100, nptPs: 100, timestepFs: 2, trajectoryIntervalPs: 1, energyIntervalPs: 0.2, checkpointIntervalMinutes: 15, ntomp: 8 };
describe('MD trim typed receiving contracts', () => {
    it.each([1, 64, 65, 128])('accepts existing receiving thread bound %i without rewriting settings', ntomp => {
        const requested = { ...form, ntomp }; expect(validateMolecularDynamicsForm(requested)).toEqual([]); expect(molecularDynamicsRequestedSettings(requested).ntomp).toBe(ntomp);
    });
    it.each([0, 129, 1.5])('rejects threads outside receiving range %i', ntomp => expect(validateMolecularDynamicsForm({ ...form, ntomp }).join()).toContain('1 to 128'));
    it('retains saved explicit false as a conflict rather than overwriting on profile hydration', () => {
        const saved = { ...form, neutralize: false };
        const hydrated = applyMolecularDynamicsProfileDefaults(saved, { launch_constraints: { engine: 'gromacs', replicas: 1, padding_nm: 1, salt_molar: .15, temperature_k: 300, pressure_bar: 1, timestep_fs: 2, force_field: 'ff19SB', water_model: 'OPC' } } as any);
        expect(molecularDynamicsRequestedSettings(hydrated).neutralize).toBe(false); expect(validateMolecularDynamicsForm(hydrated).join()).toContain('Neutralization is fixed on');
        expect(validateMolecularDynamicsForm({ ...hydrated, neutralize: true })).toEqual([]);
    });
    it('preserves all requested settings exactly through draft and MD return, separates destination from predictor stage', () => {
        const id = '11111111-1111-4111-8111-111111111111'; const seq = '22222222-2222-4222-8222-222222222222'; const context = '33333333-3333-4333-8333-333333333333';
        const data = new Map<string, string>(); const storage = { setItem: (key: string, value: string) => data.set(key, value), getItem: (key: string) => data.get(key) ?? null };
        storeMolecularDynamicsDraft(storage, id, { form, destinationLaunchContextId: context });
        const predictor = buildMolecularDynamicsPredictionRoute(seq, id); expect(predictor).not.toContain('launch_context_id');
        const route = buildMolecularDynamicsPredictionReturnRoute(id, { id: seq }); const initial = buildMolecularDynamicsHandoffInitialValues(parseMolecularDynamicsHandoffRoute(route.split('?')[1]), loadMolecularDynamicsDraft(storage, id));
        expect(initial.md_destination_launch_context_id).toBe(context); expect(molecularDynamicsRequestedSettings(initial.md_form as any)).toEqual(molecularDynamicsRequestedSettings(form));
        expect((initial.md_form as any).structurePath).toBeUndefined();
    });
});
