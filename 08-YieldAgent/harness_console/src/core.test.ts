import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readEvents, mergeEvents, toolTree } from './core.ts';
test('SSE handles split UTF-8, CRLF, id lines and multiple frames', async () => {
  const a = {event_id:'a',sequence:1,type:'commentary',payload:{content:'수율'}};
  const b = {event_id:'b',sequence:2,type:'run_finished',payload:{status:'completed'}};
  const bytes = new TextEncoder().encode(`id: 1\r\ndata: ${JSON.stringify(a)}\r\n\r\n: ping\r\n\r\nid: 2\ndata: ${JSON.stringify(b)}\n\n`);
  const response = new Response(new ReadableStream({start(c) { for (const byte of bytes) c.enqueue(new Uint8Array([byte])); c.close(); }}));
  const events = []; for await (const e of readEvents(response)) events.push(e);
  assert.deepEqual(events, [a,b]);
  assert.equal(mergeEvents(events, a).length, 2);
});
test('pairs invocations, retains parent, and hydrates stored arguments', () => {
  const events = [
    {event_id:'1',sequence:1,type:'tool_started',payload:{invocation_id:'p',name:'verify_answer'}},
    {event_id:'2',sequence:2,type:'tool_started',payload:{invocation_id:'c',parent_invocation_id:'p',name:'run_python'}},
    {event_id:'3',sequence:3,type:'tool_finished',payload:{invocation_id:'c',status:'success',elapsed_seconds:1.2}},
  ];
  const tree = toolTree(events, [{invocation_id:'c',tool_name:'run_python',result_id:'r',validated_arguments:{code:'print(1)'},status:'success'}]);
  assert.equal(tree.length,1); assert.equal(tree[0].children[0].status,'success');
  assert.equal(tree[0].children[0].args?.code,'print(1)');
  assert.equal(tree[0].children[0].elapsed,1.2);
});
test('out-of-order events sort and sequence replay is idempotent', () => {
  const last = {event_id:'last',sequence:2,type:'progress',payload:{}};
  const first = {event_id:'first',sequence:1,type:'run_started',payload:{}};
  const events = mergeEvents([last],first);
  assert.deepEqual(events.map(e => e.sequence),[1,2]);
  assert.equal(mergeEvents(events,{...last,event_id:'replayed'}),events);
});
test('missing parents and cyclic tool metadata do not hide tools or recurse forever', () => {
  const make = (id:string,parent:string) => ({event_id:id,sequence:1,type:'tool_started',payload:{invocation_id:id,parent_invocation_id:parent,name:id}});
  const roots = toolTree([make('a','b'),make('b','a'),make('orphan','missing')],[]);
  assert.equal(roots.length,3);
  assert.ok(roots.every(tool => tool.children.length === 0));
});
test('invalid event JSON fails visibly instead of silently dropping events', async () => {
  await assert.rejects(async () => {for await (const event of readEvents(new Response('data: broken\n\n'))) void event;},SyntaxError);
});

import { artifactKind, artifactMime, collectArtifacts, iframeHeight } from './artifactUtils.ts';
test('artifact MIME detection accepts parameters and response-only metadata', () => {
  assert.equal(artifactKind(' TEXT/HTML; charset=UTF-8 '),'html');
  assert.equal(artifactKind(undefined,'text/html; charset=utf-8'),'html');
  assert.equal(artifactKind('text/html','application/octet-stream'),'html');
  assert.equal(artifactKind('text/plain','text/html'),'html');
  assert.equal(artifactMime('text/html','application/octet-stream'),'text/html');
  assert.equal(artifactKind('text/plain; charset=utf-8'),'code');
  assert.equal(artifactKind('text/markdown'),'markdown');
  assert.equal(artifactKind(undefined,'text/x-markdown'),'markdown');
  assert.equal(artifactKind('image/png'),'image');
  assert.equal(artifactKind('application/vnd.openxmlformats-officedocument.presentationml.presentation'),'download');
  assert.equal(artifactKind('application/json'),'download');
  assert.equal(artifactKind(),'download');
});
test('iframe heights port table row sizing, chart sizes, summary and default cases', () => {
  const rows = (n:number) => '<tr><td>x</td></tr>'.repeat(n);
  assert.equal(iframeHeight('YIELD_TABLE',''),270);
  assert.equal(iframeHeight('yield_table',rows(10)),348);
  assert.equal(iframeHeight('yield_table',rows(1)),240);
  assert.equal(iframeHeight('lot_compare',''),310);
  assert.equal(iframeHeight('lot_compare',rows(1)),260);
  assert.equal(iframeHeight('lot_compare',rows(8)),388);
  assert.equal(iframeHeight('cummap',''),480);
  for (const title of ['scatter','trend']) assert.equal(iframeHeight(title,''),1140);
  assert.equal(iframeHeight('fail_history',''),420);
  assert.equal(iframeHeight('wads_report',rows(6)),350);
  assert.equal(iframeHeight('wads_report','<details>'+rows(6)),490);
  assert.equal(iframeHeight('fail_history','summary-block'+rows(4)),420);
  assert.equal(iframeHeight('fail_history',rows(1)),220);
  assert.equal(iframeHeight('relation_tree',''),450);
  assert.equal(iframeHeight('4S4 수율표',rows(10)),600);
});
test('replayed artifact events deduplicate without losing observation metadata', () => {
  const observations = [{invocation_id:'i',tool_name:'tool',result_id:'r',status:'success',artifact_refs:[{artifact_id:'a',title:'4S4 수율표',mime:'text/html'}]}];
  const event = {event_id:'e',sequence:1,type:'artifact',payload:{artifact_id:'a'}};
  const refs = collectArtifacts(observations,[event,event,{...event,event_id:'e2',payload:{artifact_id:'b'}}]);
  assert.deepEqual(refs,[{artifact_id:'a',title:'4S4 수율표',mime:'text/html'},{artifact_id:'b'}]);
});

test('artifact response MIME overrides generic metadata while HTML from either source wins', () => {
  assert.equal(artifactKind('application/octet-stream','image/png; charset=binary'),'image');
  assert.equal(artifactKind('image/png','text/plain; charset=utf-8'),'code');
  assert.equal(artifactKind('text/markdown','application/octet-stream'),'markdown');
  assert.equal(artifactKind('text/html','text/plain'),'html');
  assert.equal(artifactMime(undefined,' APPLICATION/OCTET-STREAM '),'application/octet-stream');
});

test('table display rounds floats without changing numeric strings or integers', async () => {
  const { tableText } = await import('./core.ts');
  assert.equal(tableText(95.123456789),'95.1235');
  assert.equal(tableText(-12.987654),'-12.9877');
  assert.equal(tableText(42),'42');
  assert.equal(tableText(1.2),'1.2');
  assert.equal(tableText('001.123456789'),'001.123456789');
  assert.equal(tableText(null),'');
});
