import * as assert from 'node:assert/strict';
import * as fs from 'node:fs';
import * as path from 'node:path';
import { test } from 'node:test';

const repoRoot = process.cwd();
const source = fs.readFileSync(path.join(repoRoot, 'src', 'components', 'ResultsViewer.tsx'), 'utf8');
const outputSource = fs.readFileSync(path.join(repoRoot, 'src', 'components', 'designOutputSource.ts'), 'utf8');

test('Results viewer exposes selectable model-call result sets', () => {
    for (const snippet of [
        "type ResultSetFilter",
        "inferDesignResultSet",
        "RESULT_SET_BUTTON_LABELS",
        "RFA/backbone",
        "Sequence designs",
        "PPIFlow candidates",
        "PPIFlow passed",
        "PPIFlow rejected",
        "include_children: !isReviewStageJob",
        "resultSetFilter === 'all'",
        "setResultSetFilter(value)",
    ]) {
        assert.match(source + outputSource, new RegExp(snippet.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
    }
});

test('Results result-set filtering is based on contract fields, not filename-only hacks', () => {
    assert.match(outputSource, /artifact_class/);
    assert.match(outputSource, /stage_family/);
    assert.match(outputSource, /stage_mode/);
    assert.match(outputSource, /ppiflow_filter_passed/);
    assert.doesNotMatch(outputSource, /result_set.*name\.includes/);
});
