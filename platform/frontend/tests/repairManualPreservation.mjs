import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { readFileSync, writeFileSync } from 'node:fs';
import ts from 'typescript';
const file = 'src/components/BioXpCockpit.tsx';
const baseline = execFileSync('git', ['show', `2248404d04ff782c982e06ee918215660904a0d3:platform/frontend/${file}`], {encoding:'utf8'});
const candidate = readFileSync(file,'utf8');
const parse = text => ts.createSourceFile(file,text,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
const inventory = text => {
    const tree = parse(text), result={disabled:[],click:[],functions:{}};
    const visit = node => {
        if (ts.isJsxAttribute(node)) {
            if (node.name.getText(tree)==='disabled') result.disabled.push(node.initializer?.getText(tree));
            if (node.name.getText(tree)==='onClick') result.click.push(node.initializer?.getText(tree));
        }
        if (ts.isVariableDeclaration(node) && node.initializer && (ts.isArrowFunction(node.initializer)||ts.isFunctionExpression(node.initializer)))
            result.functions[node.name.getText(tree)] = node.initializer.getText(tree);
        ts.forEachChild(node,visit);
    };visit(tree);return result;
};
const old=inventory(baseline), current=inventory(candidate);
assert.deepEqual(current.disabled.sort(),old.disabled.sort(), 'every original disabled predicate preserved exactly');
const readerClicks=["{() => invokeOperatorPath('/motion/oem/machine_config', {})}","{() => invokeOperatorPath('/motion/oem/position_table', {})}"];
assert.deepEqual(current.click.sort(),old.click.filter(click=>!readerClicks.includes(click)).sort(),'all original hardware command handlers preserved exactly; only two passive readers replaced');
const changed = Object.keys(old.functions).filter(name=>current.functions[name]!==old.functions[name]);
assert.deepEqual(changed,['axisPresentation'],'only display projection function changed; all command functions unchanged');
for (const [start,end] of [['            <div role="tabpanel" id="control-panel-pipettes"','            <section aria-label="Latest command"']]) {
    const section=text=>text.slice(text.indexOf(start),text.indexOf(end));
    assert.equal(section(candidate),section(baseline),'sibling pipette panel exact bytes');
}
const result={disabled_predicates_unchanged:old.disabled.length,hardware_click_handlers_unchanged:current.click.length,functions_unchanged:Object.keys(old.functions).length-changed.length,display_function_changes:changed,reader_handler_replacements:readerClicks,sibling_pipettes_byte_identical:true};
if(process.env.REPAIR_MANUAL_PRESERVATION_OUTPUT) writeFileSync(process.env.REPAIR_MANUAL_PRESERVATION_OUTPUT,JSON.stringify(result,null,2)+'\n');
console.log(JSON.stringify(result,null,2));
