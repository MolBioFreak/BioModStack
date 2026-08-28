import {
    buildFrustraMpnnLaunchParams,
    type FrustraMpnnRequestedSettings,
} from './frustrampnn/frustraMpnnSettingsState.js';

export const buildAntibodyContinuationParamOverrides = (
    overrides: Record<string, unknown>,
    frustrampnnEnabled: boolean,
    frustrampnnSettings: FrustraMpnnRequestedSettings,
): Record<string, unknown> => ({
    ...overrides,
    ...buildFrustraMpnnLaunchParams(frustrampnnEnabled, frustrampnnSettings),
});
