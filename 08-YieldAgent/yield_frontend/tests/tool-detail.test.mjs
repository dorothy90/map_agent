import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';
const source = await readFile(new URL('../src/lib/tool-detail.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext } });
const { findObservation, resultCards, observationSteps } = await import('data:text/javascript;base64,' + Buffer.from(outputText).toString('base64'));
const obs = { run_id:'run', session_id:'session', invocation_id:'second', result_id:'r2', tool_name:'same_tool', status:'error', summary:'부분 조회', validated_arguments:{product:'4SS'}, error:{message:'계산 실패'}, provenance:{elapsed_seconds:2.5}, tables:[{table_id:'one',title:'표1',columns:['value'],units:{value:'%'},preview_rows:[{value:3}],total_rows:10,complete:false,missing_reason:'일부 자료'}], artifact_refs:[{artifact_id:'html2'}] };
test('same tool calls are selected by run and invocation, including errors',()=>{
 assert.deepEqual(findObservation([ {...obs,invocation_id:'first'},obs], 'run','second'),obs);
 assert.equal(findObservation([obs],'other-run','second'),undefined);
});
test('only named tables and artifacts owned by the selected result are displayed',()=>{
 const cards=resultCards(obs,[{id:'html1'},{id:'html2',artifactType:'html',data:'<p>result</p>'}]);
 assert.deepEqual(cards.map(c=>c.id),['r2:one','html2']);
 const table=JSON.parse(cards[0].data);
 assert.equal(table.session_id,'session'); assert.equal(table.table_id,'one'); assert.equal(table.complete,false); assert.equal(table.units.value,'%');
});
test('history restores stable invocation identity and state',()=>{
 const [step]=observationSteps([obs]);
 assert.equal(step.id,'run:second'); assert.equal(step.runId,'run'); assert.equal(step.invocationId,'second'); assert.equal(step.state,'error'); assert.equal(step.elapsed,2.5);
});
