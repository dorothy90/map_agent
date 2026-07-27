import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useReplStream } from "./useReplStream";

const sseResponse = (frames: string[]): Response => {
  const encoder = new TextEncoder();
  return new Response(
    new ReadableStream<Uint8Array>({
      start(controller) {
        for (const frame of frames) controller.enqueue(encoder.encode(frame));
        controller.close();
      },
    }),
    { status: 200, headers: { "Content-Type": "text/event-stream" } },
  );
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("useReplStream", () => {
  it("posts the query and reduces every streamed event", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      sseResponse([
        'data: {"type":"RUN_STARTED","run_id":"r1","thread_id":"s1","sequence":1}\n\n',
        'data: {"type":"TEXT_MESSAGE_CONTENT","run_id":"r1","thread_id":"s1","sequence":2,"message_id":"m1","content":"answer"}\n\n' +
          'data: {"type":"RUN_FINISHED","run_id":"r1","thread_id":"s1","sequence":3}\n\n',
      ]),
    );
    vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() => useReplStream("s1"));

    await act(async () => result.current.send("mean"));

    expect(fetchMock).toHaveBeenCalledWith(
      "/repl/chat",
      expect.objectContaining({
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: "s1", query: "mean" }),
        signal: expect.any(AbortSignal),
      }),
    );
    expect(result.current.state.runs[0]).toMatchObject({
      runId: "r1",
      status: "completed",
      userMessage: "mean",
      assistantText: "answer",
      lastSequence: 3,
    });
    expect(result.current.sending).toBe(false);
  });

  it("posts cancellation without aborting the open stream", async () => {
    const encoder = new TextEncoder();
    let streamController!: ReadableStreamDefaultController<Uint8Array>;
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        streamController = controller;
        controller.enqueue(
          encoder.encode(
            'data: {"type":"RUN_STARTED","run_id":"r1","thread_id":"s1","sequence":1}\n\n',
          ),
        );
      },
    });
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response(stream, { status: 200 }))
      .mockResolvedValueOnce(new Response('{"cancelled":true}', { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() => useReplStream("s1"));

    let sendPromise!: Promise<void>;
    act(() => {
      sendPromise = result.current.send("slow");
    });
    await waitFor(() => expect(result.current.state.activeRunId).toBe("r1"));
    const chatSignal = fetchMock.mock.calls[0][1].signal as AbortSignal;

    await act(async () => result.current.cancel());

    expect(fetchMock).toHaveBeenLastCalledWith(
      "/repl/runs/r1/cancel",
      expect.objectContaining({ method: "POST", signal: expect.any(AbortSignal) }),
    );
    expect(chatSignal.aborted).toBe(false);
    expect(result.current.sending).toBe(true);
    streamController.enqueue(
      encoder.encode(
        'data: {"type":"RUN_CANCELLED","run_id":"r1","thread_id":"s1","sequence":2,"code":"execution_cancelled","message":"cancelled"}\n\n',
      ),
    );
    streamController.close();
    await act(async () => sendPromise);
    expect(result.current.state.runs[0].status).toBe("cancelled");
    expect(result.current.sending).toBe(false);
  });

  it("keeps cancel stable and suppresses duplicate cancellation requests", async () => {
    const encoder = new TextEncoder();
    let streamController!: ReadableStreamDefaultController<Uint8Array>;
    let resolveCancel!: (response: Response) => void;
    const cancelResponse = new Promise<Response>((resolve) => {
      resolveCancel = resolve;
    });
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          new ReadableStream<Uint8Array>({
            start(controller) {
              streamController = controller;
              controller.enqueue(
                encoder.encode(
                  'data: {"type":"RUN_STARTED","run_id":"r1","thread_id":"s1","sequence":1}\n\n',
                ),
              );
            },
          }),
          { status: 200 },
        ),
      )
      .mockReturnValue(cancelResponse);
    vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() => useReplStream("s1"));
    act(() => {
      void result.current.send("slow");
    });
    await waitFor(() => expect(result.current.state.activeRunId).toBe("r1"));
    const initialCancel = result.current.cancel;

    act(() => {
      void result.current.cancel();
      void result.current.cancel();
    });

    await waitFor(() => expect(result.current.cancelPending).toBe(true));
    expect(result.current.cancel).toBe(initialCancel);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    resolveCancel(new Response(null, { status: 200 }));
    await waitFor(() => expect(result.current.cancelPending).toBe(false));
    streamController.close();
  });

  it("creates a sequence-zero local failure when chat fetch fails", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("network down")));
    const { result } = renderHook(() => useReplStream("s1"));

    await act(async () => result.current.send("mean"));

    expect(result.current.state.runs[0]).toMatchObject({
      status: "failed",
      userMessage: "mean",
      lastSequence: 0,
      error: { code: "network_error", message: "network down" },
    });
  });

  it("fails a premature EOF before allowing the next send", async () => {
    const encoder = new TextEncoder();
    let firstController!: ReadableStreamDefaultController<Uint8Array>;
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          new ReadableStream<Uint8Array>({
            start(controller) {
              firstController = controller;
              controller.enqueue(
                encoder.encode(
                  'data: {"type":"RUN_STARTED","run_id":"r1","thread_id":"s1","sequence":1}\n\n',
                ),
              );
            },
          }),
          { status: 200 },
        ),
      )
      .mockResolvedValueOnce(
        sseResponse([
          'data: {"type":"RUN_STARTED","run_id":"r2","thread_id":"s1","sequence":1}\n\n' +
            'data: {"type":"RUN_FINISHED","run_id":"r2","thread_id":"s1","sequence":2}\n\n',
        ]),
      );
    vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() => useReplStream("s1"));
    let firstSend!: Promise<void>;
    act(() => {
      firstSend = result.current.send("first");
    });
    await waitFor(() => expect(result.current.state.activeRunId).toBe("r1"));

    await act(async () => result.current.send("must not overlap"));
    expect(fetchMock).toHaveBeenCalledTimes(1);
    firstController.close();
    await act(async () => firstSend);
    expect(result.current.state.runs[0]).toMatchObject({
      runId: "r1",
      status: "failed",
      lastSequence: 1,
      error: { code: "network_error", message: expect.stringMatching(/terminal/i) },
    });

    await act(async () => result.current.send("second"));
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(result.current.state.runs.map((run) => [run.runId, run.status])).toEqual([
      ["r1", "failed"],
      ["r2", "completed"],
    ]);
    expect(result.current.state.activeRunId).toBeNull();
  });

  it("resets on session change and ignores a late response from the old session", async () => {
    let resolveOld!: (response: Response) => void;
    const oldResponse = new Promise<Response>((resolve) => {
      resolveOld = resolve;
    });
    const fetchMock = vi
      .fn()
      .mockReturnValueOnce(oldResponse)
      .mockResolvedValueOnce(
        sseResponse([
          'data: {"type":"RUN_STARTED","run_id":"r2","thread_id":"s2","sequence":1}\n\n' +
            'data: {"type":"RUN_FINISHED","run_id":"r2","thread_id":"s2","sequence":2}\n\n',
        ]),
      );
    vi.stubGlobal("fetch", fetchMock);
    const { result, rerender } = renderHook(
      ({ sessionId }) => useReplStream(sessionId),
      { initialProps: { sessionId: "s1" } },
    );
    let oldSend!: Promise<void>;
    act(() => {
      oldSend = result.current.send("old");
    });
    const oldSignal = fetchMock.mock.calls[0][1].signal as AbortSignal;

    rerender({ sessionId: "s2" });
    await waitFor(() => expect(result.current.state.runs).toEqual([]));
    expect(oldSignal.aborted).toBe(true);
    resolveOld(
      sseResponse([
        'data: {"type":"RUN_STARTED","run_id":"old","thread_id":"s1","sequence":1}\n\n' +
          'data: {"type":"RUN_FINISHED","run_id":"old","thread_id":"s1","sequence":2}\n\n',
      ]),
    );
    await act(async () => oldSend);
    expect(result.current.state.runs).toEqual([]);

    await act(async () => result.current.send("new"));
    expect(result.current.state.runs).toEqual([
      expect.objectContaining({ runId: "r2", userMessage: "new", status: "completed" }),
    ]);
  });

  it("aborts both chat and cancellation requests on unmount", async () => {
    const encoder = new TextEncoder();
    let streamController!: ReadableStreamDefaultController<Uint8Array>;
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          new ReadableStream<Uint8Array>({
            start(controller) {
              streamController = controller;
              controller.enqueue(
                encoder.encode(
                  'data: {"type":"RUN_STARTED","run_id":"r1","thread_id":"s1","sequence":1}\n\n',
                ),
              );
            },
          }),
          { status: 200 },
        ),
      )
      .mockImplementationOnce((_url: string, init?: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () => {
            reject(new DOMException("cancel aborted", "AbortError"));
          });
        }),
      );
    vi.stubGlobal("fetch", fetchMock);
    const { result, unmount } = renderHook(() => useReplStream("s1"));
    let sendPromise!: Promise<void>;
    let cancelPromise!: Promise<void>;
    act(() => {
      sendPromise = result.current.send("slow");
    });
    await waitFor(() => expect(result.current.state.activeRunId).toBe("r1"));
    act(() => {
      cancelPromise = result.current.cancel();
    });
    await waitFor(() => expect(result.current.cancelPending).toBe(true));
    const chatSignal = fetchMock.mock.calls[0][1].signal as AbortSignal;
    const cancelSignal = fetchMock.mock.calls[1][1].signal as AbortSignal;

    unmount();

    expect(chatSignal.aborted).toBe(true);
    expect(cancelSignal.aborted).toBe(true);
    streamController.close();
    await expect(cancelPromise).resolves.toBeUndefined();
    await sendPromise;
  });

  it("rejects unexpected cancellation transport failures", async () => {
    const encoder = new TextEncoder();
    let streamController!: ReadableStreamDefaultController<Uint8Array>;
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          new ReadableStream<Uint8Array>({
            start(controller) {
              streamController = controller;
              controller.enqueue(
                encoder.encode(
                  'data: {"type":"RUN_STARTED","run_id":"r1","thread_id":"s1","sequence":1}\n\n',
                ),
              );
            },
          }),
          { status: 200 },
        ),
      )
      .mockRejectedValueOnce(new TypeError("cancel network down"));
    vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() => useReplStream("s1"));
    let sendPromise!: Promise<void>;
    act(() => {
      sendPromise = result.current.send("slow");
    });
    await waitFor(() => expect(result.current.state.activeRunId).toBe("r1"));

    await act(async () => {
      await expect(result.current.cancel()).rejects.toThrow("cancel network down");
    });

    expect(result.current.cancelError).toBe("cancel network down");
    expect(result.current.state.runs[0].status).toBe("running");
    streamController.close();
    await act(async () => sendPromise);
  });

  it("clears a cancellation error when the session changes", async () => {
    const encoder = new TextEncoder();
    let streamController!: ReadableStreamDefaultController<Uint8Array>;
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response(new ReadableStream<Uint8Array>({
        start(controller) {
          streamController = controller;
          controller.enqueue(encoder.encode(
            'data: {"type":"RUN_STARTED","run_id":"r1","thread_id":"s1","sequence":1}\n\n',
          ));
        },
      })))
      .mockRejectedValueOnce(new TypeError("cancel network down"));
    vi.stubGlobal("fetch", fetchMock);
    const { result, rerender } = renderHook(
      ({ sessionId }) => useReplStream(sessionId),
      { initialProps: { sessionId: "s1" } },
    );
    act(() => { void result.current.send("slow"); });
    await waitFor(() => expect(result.current.state.activeRunId).toBe("r1"));
    await act(async () => {
      await expect(result.current.cancel()).rejects.toThrow("cancel network down");
    });
    expect(result.current.cancelError).toBe("cancel network down");

    rerender({ sessionId: "s2" });

    await waitFor(() => expect(result.current.cancelError).toBeNull());
    streamController.close();
  });
});
