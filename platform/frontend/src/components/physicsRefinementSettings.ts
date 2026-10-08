import type { PhysicsRefinementSettings } from './PhysicsRefinementPanel';

export const DEFAULT_SETTINGS: PhysicsRefinementSettings = {
    enabled: false,
    computeTier: 'fast',
    cdrOnly: true,
    restraintMode: 'framework',
    mmgbsaMode: 'off',
    forceField: 'amber14sb',
    topNPercentage: 10,
    // Advanced defaults
    maxIterations: 500,
    tolerance: 10.0,
    restraintStrength: 1.0,
    implicitSolvent: 'gbsa',
    platform: 'auto'
};
