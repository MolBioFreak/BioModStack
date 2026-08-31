import assert from 'node:assert/strict';
import test from 'node:test';

import { fieldError, type SchemaField } from '../src/components/project-manager/protein/ProteinPlanOperator';

function stringField(enumValues: string[]): SchemaField {
    return {
        name: 'boltz_method',
        title: 'Experimental method conditioning',
        type: 'string',
        required: true,
        readOnly: false,
        enumValues,
    };
}

test('accepts an advertised empty string for an optional required-schema enum', () => {
    assert.equal(fieldError(stringField(['', 'md', 'x-ray diffraction']), ''), null);
});

test('still rejects an empty required string when the schema does not advertise it', () => {
    assert.equal(
        fieldError(stringField(['md', 'x-ray diffraction']), ''),
        'Experimental method conditioning is required.',
    );
});
