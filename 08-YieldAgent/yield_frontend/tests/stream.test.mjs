import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

// Exercise the real SSE client without adding a browser-test dependency.
const source = await readFile(new URL('../src/lib/stream.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext } });
const { streamChat } = await import('data:text/javascript;base64,' + Buffer.from(outputText).toString('base64'));
const sse = (events) => new Response(events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join(''), {
  headers: { 'content-type': 'text/event-stream' },
});

test('reconnect retains commentary once and delivers final and end events', async () => {
  const original = globalThis.fetch;
  const start = { type: 'stream_start', run_id: 'run', after_sequence: 0 };
  const commentary = { type: 'commentary', content: '자료를 확인합니다.', event_id: 'c:0', run_id: 'run' };
  const requests = [];
  globalThis.fetch = async (_url, options) => {
    requests.push(JSON.parse(options.body));
    return requests.length === 1 ? sse([start, commentary]) : sse([start, commentary,
      { type: 'status', message: '검증 중', event_id: 's:0', run_id: 'run' },
      { type: 'message', content: '완료', event_id: 'f:0', run_id: 'run' },
      { type: 'stream_end', status: 'completed', event_id: 'f:1', run_id: 'run' }]);
  };
  try {
    const received = [];
    for await (const event of streamChat({ query: '조회', sessionId: 'session', resumeValue: '계속' })) received.push(event);
    assert.equal(requests.length, 2);
    assert.equal(requests[1].run_id, 'run');
    assert.equal(requests[1].resume_value, undefined);
    assert.equal(received.filter((e) => e.type === 'commentary').length, 1);
    assert.deepEqual(received.slice(-2).map((e) => e.type), ['message', 'stream_end']);
  } finally { globalThis.fetch = original; }
});

for (const terminal of [{ type: 'stream_end', status: 'cancelled' }, { type: 'stream_end', status: 'failed' }, { type: 'interrupt' }]) {
  test(`${terminal.status || terminal.type} terminates without reconnecting`, async () => {
    const original = globalThis.fetch;
    let calls = 0;
    globalThis.fetch = async () => { calls++; return sse([{ type: 'stream_start', run_id: 'run' }, terminal]); };
    try {
      const received = [];
      for await (const event of streamChat({ query: '조회', sessionId: 'session' })) received.push(event);
      assert.equal(calls, 1);
      assert.deepEqual(received.at(-1), terminal);
    } finally { globalThis.fetch = original; }
  });
}

test('snapshot cursor survives reconnect and split message/end sequence is replayed', async () => {
  const original = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (_url, options) => {
    requests.push(JSON.parse(options.body));
    const start = { type: 'stream_start', run_id: 'run', after_sequence: requests.at(-1).after_sequence };
    const message = { type: 'message', content: 'done', sequence: 9, event_id: 'final:0', run_id: 'run' };
    return sse(requests.length === 1 ? [start, message] : [start, message,
      { type: 'stream_end', sequence: 9, event_id: 'final:1', run_id: 'run' }]);
  };
  try {
    const received = [];
    for await (const event of streamChat({ query: '', sessionId: 'session', runId: 'run', afterSequence: 8 })) received.push(event);
    assert.equal(requests[0].after_sequence, 8);
    assert.equal(requests[1].after_sequence, 8);
    assert.equal(received.filter(e => e.type === 'message').length, 1);
    assert.equal(received.at(-1).type, 'stream_end');
  } finally { globalThis.fetch = original; }
});

test('accepted input echo is suppressed only for this submission', async () => {
  const original = globalThis.fetch;
  globalThis.fetch = async (_url, options) => {
    const request = JSON.parse(options.body);
    return sse([{ type: 'stream_start', run_id: 'run' },
      { type: 'user_input', content: 'current answer', request_id: request.request_id },
      { type: 'user_input', content: 'previous answer', request_id: 'previous' },
      { type: 'stream_end' }]);
  };
  try {
    const received = [];
    for await (const event of streamChat({ query: 'answer', sessionId: 'session', resumeValue: 'answer' })) received.push(event);
    assert.deepEqual(received.filter(event => event.type === 'user_input').map(event => event.content), ['previous answer']);
  } finally { globalThis.fetch = original; }
});
