import native from './scientificPaeNative.json';
import type { StructureDocumentRef } from '../../src/structureViewer/contracts/structureIdentity';

// Exact JSON serialized by the API wire model after the real strict numerical
// loader consumes a labelled producer-shaped native artifact fixture.
export const document = native.document as StructureDocumentRef;
export const fixture = () => structuredClone(native);
