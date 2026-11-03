import type { PrimerTmOptionsResponse } from '../../src/lib/api';
import options from './primer-model-options.json';

// Serialized from the canonical /primer-tm/options response. No UI-only scientific defaults.
export const primerModelOptions = options as PrimerTmOptionsResponse;
