import { describe, expect, it } from "vitest";
import type { ReplEvent } from "./replEvents";
import { initialChatState, replReducer } from "./replReducer";

const event = <T extends ReplEvent>(value: T): T => value;
const runStarted = event({ type: "RUN_STARTED", run_id: "r1", thread_id: "s1", sequence: 1 });
const toolCall = event({
  type: "TOOL_CALL_START",
  run_id: "r1",
  thread_id: "s1",
  sequence: 2,
  tool_call_id: "t1",
  name: "run_python",
});
const toolResult = event({
  type: "TOOL_RESULT",
  run_id: "r1",
  thread_id: "s1",
  sequence: 3,
  tool_call_id: "t1",
  result: { status: "success", stdout: "3.5", execution_time_ms: 12 },
});

describe("replReducer", () => {
  it("reconciles a submitted query with RUN_STARTED", () => {
    let state = replReducer(initialChatState, {
      type: "USER_SUBMITTED",
      runId: "local-1",
      query: "mean",
    });
    state = replReducer(state, { type: "SERVER_EVENT", event: runStarted });
    expect(state.activeRunId).toBe("r1");
    expect(state.runs).toEqual([
      expect.objectContaining({ runId: "r1", userMessage: "mean", status: "running", lastSequence: 1 }),
    ]);
  });

  it("joins tool results and ignores duplicate sequences", () => {
    let state = replReducer(initialChatState, { type: "SERVER_EVENT", event: runStarted });
    state = replReducer(state, { type: "SERVER_EVENT", event: toolCall });
    state = replReducer(state, { type: "SERVER_EVENT", event: toolResult });
    const duplicate = replReducer(state, { type: "SERVER_EVENT", event: toolResult });
    expect(duplicate).toBe(state);
    expect(duplicate.runs[0].steps).toHaveLength(1);
    expect(duplicate.runs[0].steps[0].result?.status).toBe("success");
  });

  it("appends text, tool args, and artifacts in event order", () => {
    const events: ReplEvent[] = [
      runStarted,
      toolCall,
      event({
        type: "TOOL_CALL_ARGS",
        run_id: "r1",
        thread_id: "s1",
        sequence: 3,
        tool_call_id: "t1",
        args: { code: "print(1)" },
      }),
      event({
        type: "ARTIFACT",
        run_id: "r1",
        thread_id: "s1",
        sequence: 4,
        tool_call_id: "t1",
        artifact: {
          artifact_id: "p1",
          kind: "plotly",
          mime_type: "application/vnd.plotly.v1+json",
          spec: { data: [] },
        },
      }),
      event({
        type: "TEXT_MESSAGE_CONTENT",
        run_id: "r1",
        thread_id: "s1",
        sequence: 5,
        message_id: "m1",
        content: "hello ",
      }),
      event({
        type: "TEXT_MESSAGE_CONTENT",
        run_id: "r1",
        thread_id: "s1",
        sequence: 6,
        message_id: "m1",
        content: "world",
      }),
    ];
    const state = events.reduce(
      (current, serverEvent) => replReducer(current, { type: "SERVER_EVENT", event: serverEvent }),
      initialChatState,
    );
    expect(state.runs[0]).toMatchObject({
      assistantText: "hello world",
      steps: [{ toolCallId: "t1", name: "run_python", args: { code: "print(1)" } }],
      artifacts: [{ artifact_id: "p1" }],
      lastSequence: 6,
    });
  });

  it.each([
    ["RUN_FINISHED", "completed"],
    ["RUN_ERROR", "failed"],
    ["RUN_CANCELLED", "cancelled"],
  ] as const)("transitions %s to %s", (type, status) => {
    let state = replReducer(initialChatState, { type: "SERVER_EVENT", event: runStarted });
    const terminal =
      type === "RUN_FINISHED"
        ? event({ type, run_id: "r1", thread_id: "s1", sequence: 2 })
        : type === "RUN_ERROR"
          ? event({
              type,
              run_id: "r1",
              thread_id: "s1",
              sequence: 2,
              code: "agent_error",
              message: "failed",
            })
          : event({
              type,
              run_id: "r1",
              thread_id: "s1",
              sequence: 2,
              code: "execution_cancelled",
              message: "cancelled",
            });
    state = replReducer(state, { type: "SERVER_EVENT", event: terminal });
    expect(state.runs[0].status).toBe(status);
    expect(state.activeRunId).toBeNull();
  });

  it("marks runtime loss from tool payloads and terminal error codes", () => {
    let state = replReducer(initialChatState, { type: "SERVER_EVENT", event: runStarted });
    state = replReducer(state, { type: "SERVER_EVENT", event: toolCall });
    state = replReducer(state, {
      type: "SERVER_EVENT",
      event: {
        ...toolResult,
        result: { status: "timeout", error: { code: "execution_timeout" } },
      },
    });
    expect(state.runtimeLost).toBe(true);
  });

  it("ignores events for unknown runs and stores local failures at sequence zero", () => {
    const unknown = replReducer(initialChatState, {
      type: "SERVER_EVENT",
      event: { ...toolCall, run_id: "missing" },
    });
    expect(unknown).toBe(initialChatState);

    let state = replReducer(initialChatState, {
      type: "USER_SUBMITTED",
      runId: "local-1",
      query: "mean",
    });
    state = replReducer(state, {
      type: "LOCAL_ERROR",
      runId: "local-1",
      message: "network down",
    });
    expect(state.runs[0]).toMatchObject({
      status: "failed",
      lastSequence: 0,
      error: { code: "network_error", message: "network down" },
    });
  });
});
