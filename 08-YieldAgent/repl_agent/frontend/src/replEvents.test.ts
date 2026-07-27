import { describe, expect, it } from "vitest";
import {
  parseReplEvent,
  parseSseChunks,
  parseSseStream,
  type ReplEvent,
} from "./replEvents";

const common = { run_id: "r1", thread_id: "s1", sequence: 1 };

describe("parseReplEvent", () => {
  it.each([
    { type: "RUN_STARTED", ...common },
    { type: "TEXT_MESSAGE_START", ...common, message_id: "m1" },
    { type: "TEXT_MESSAGE_CONTENT", ...common, message_id: "m1", content: "hello" },
    { type: "TEXT_MESSAGE_END", ...common, message_id: "m1" },
    { type: "TOOL_CALL_START", ...common, tool_call_id: "t1", name: "run_python" },
    { type: "TOOL_CALL_ARGS", ...common, tool_call_id: "t1", args: { code: "print(1)" } },
    { type: "TOOL_CALL_END", ...common, tool_call_id: "t1" },
    { type: "TOOL_RESULT", ...common, tool_call_id: "t1", result: { status: "success" } },
    {
      type: "ARTIFACT",
      ...common,
      tool_call_id: "t1",
      artifact: {
        artifact_id: "p1",
        kind: "plotly",
        mime_type: "application/vnd.plotly.v1+json",
        spec: { data: [] },
      },
    },
    { type: "RUN_FINISHED", ...common },
    { type: "RUN_ERROR", ...common, code: "agent_error", message: "failed" },
    {
      type: "RUN_CANCELLED",
      ...common,
      code: "execution_cancelled",
      message: "cancelled",
    },
  ] satisfies unknown[])("validates $type", (value) => {
    expect(parseReplEvent(value)).toEqual(value);
  });

  it.each([
    ["non-object", null],
    ["array", []],
    ["missing run_id", { type: "RUN_STARTED", thread_id: "s1", sequence: 1 }],
    ["invalid sequence", { type: "RUN_STARTED", ...common, sequence: 0 }],
    ["TEXT_MESSAGE_START message_id", { type: "TEXT_MESSAGE_START", ...common }],
    [
      "TEXT_MESSAGE_CONTENT content",
      { type: "TEXT_MESSAGE_CONTENT", ...common, message_id: "m1" },
    ],
    [
      "TEXT_MESSAGE_CONTENT message_id",
      { type: "TEXT_MESSAGE_CONTENT", ...common, content: "hello" },
    ],
    ["TEXT_MESSAGE_END message_id", { type: "TEXT_MESSAGE_END", ...common }],
    [
      "TOOL_CALL_START name",
      { type: "TOOL_CALL_START", ...common, tool_call_id: "t1" },
    ],
    [
      "TOOL_CALL_START tool_call_id",
      { type: "TOOL_CALL_START", ...common, name: "run_python" },
    ],
    ["TOOL_CALL_ARGS args", { type: "TOOL_CALL_ARGS", ...common, tool_call_id: "t1", args: [] }],
    ["TOOL_CALL_ARGS tool_call_id", { type: "TOOL_CALL_ARGS", ...common, args: {} }],
    ["TOOL_CALL_END tool_call_id", { type: "TOOL_CALL_END", ...common }],
    [
      "TOOL_RESULT result",
      { type: "TOOL_RESULT", ...common, tool_call_id: "t1", result: [] },
    ],
    ["TOOL_RESULT tool_call_id", { type: "TOOL_RESULT", ...common, result: {} }],
    ["ARTIFACT tool_call_id", { type: "ARTIFACT", ...common, artifact: {} }],
    ["ARTIFACT object", { type: "ARTIFACT", ...common, tool_call_id: "t1", artifact: [] }],
    [
      "ARTIFACT artifact_id",
      {
        type: "ARTIFACT",
        ...common,
        tool_call_id: "t1",
        artifact: {
          kind: "plotly",
          mime_type: "application/vnd.plotly.v1+json",
          spec: {},
        },
      },
    ],
    [
      "ARTIFACT kind",
      {
        type: "ARTIFACT",
        ...common,
        tool_call_id: "t1",
        artifact: {
          artifact_id: "p1",
          kind: "png",
          mime_type: "application/vnd.plotly.v1+json",
          spec: {},
        },
      },
    ],
    [
      "ARTIFACT mime_type",
      {
        type: "ARTIFACT",
        ...common,
        tool_call_id: "t1",
        artifact: { artifact_id: "p1", kind: "plotly", mime_type: "text/plain", spec: {} },
      },
    ],
    [
      "ARTIFACT spec",
      {
        type: "ARTIFACT",
        ...common,
        tool_call_id: "t1",
        artifact: {
          artifact_id: "p1",
          kind: "plotly",
          mime_type: "application/vnd.plotly.v1+json",
          spec: [],
        },
      },
    ],
    ["RUN_ERROR code", { type: "RUN_ERROR", ...common, message: "failed" }],
    ["RUN_ERROR message", { type: "RUN_ERROR", ...common, code: "agent_error" }],
    [
      "RUN_CANCELLED code",
      { type: "RUN_CANCELLED", ...common, code: "cancelled", message: "cancelled" },
    ],
    [
      "RUN_CANCELLED message",
      { type: "RUN_CANCELLED", ...common, code: "execution_cancelled" },
    ],
    ["unknown type", { type: "NOT_APPROVED", ...common }],
  ])("rejects invalid local event schema: %s", (_label, value) => {
    expect(() => parseReplEvent(value)).toThrow();
  });
});

describe("SSE parsing", () => {
  it("reassembles SSE split across arbitrary chunks", async () => {
    const chunks = [
      'data: {"type":"RUN_STARTED","run_id":"r1",',
      '"thread_id":"s1","sequence":1}\n\n',
    ];
    const events: ReplEvent[] = [];
    for await (const event of parseSseChunks(chunks)) events.push(event);
    expect(events).toHaveLength(1);
    expect(events[0].type).toBe("RUN_STARTED");
  });

  it("parses multiple CRLF frames and flushes a final unterminated frame", async () => {
    const chunks = [
      ': keepalive\r\ndata: {"type":"RUN_STARTED","run_id":"r1","thread_id":"s1","sequence":1}\r\n\r\n' +
        'data: {"type":"RUN_FINISHED","run_id":"r1","thread_id":"s1","sequence":2}\n\n' +
        'data: {"type":"RUN_STARTED","run_id":"r2","thread_id":"s1","sequence":1}',
    ];
    const events: ReplEvent[] = [];
    for await (const event of parseSseChunks(chunks)) events.push(event);
    expect(events.map((event) => [event.run_id, event.type])).toEqual([
      ["r1", "RUN_STARTED"],
      ["r1", "RUN_FINISHED"],
      ["r2", "RUN_STARTED"],
    ]);
  });

  it("decodes a response body when UTF-8 bytes split inside a character", async () => {
    const bytes = new TextEncoder().encode(
      'data: {"type":"RUN_ERROR","run_id":"r1","thread_id":"s1","sequence":1,"code":"agent_error","message":"실패"}\n\n',
    );
    const koreanByte = bytes.indexOf(0xec);
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(bytes.slice(0, koreanByte + 1));
        controller.enqueue(bytes.slice(koreanByte + 1));
        controller.close();
      },
    });
    const events: ReplEvent[] = [];
    for await (const event of parseSseStream(body)) events.push(event);
    expect(events[0]).toMatchObject({ type: "RUN_ERROR", message: "실패" });
  });
});
