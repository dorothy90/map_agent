import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { parseSseStream } from "./replEvents";
import { initialChatState, replReducer } from "./replReducer";

let nextLocalRunId = 1;

const errorMessage = (error: unknown): string =>
  error instanceof Error ? error.message : String(error);

export function useReplStream(sessionId: string) {
  const [state, dispatch] = useReducer(replReducer, initialChatState);
  const [cancelPending, setCancelPending] = useState(false);
  const streamControllerRef = useRef<AbortController | null>(null);
  const activeRunIdRef = useRef<string | null>(null);
  const cancelPendingRef = useRef(false);

  useEffect(() => {
    activeRunIdRef.current = state.activeRunId;
  }, [state.activeRunId]);

  useEffect(
    () => () => {
      streamControllerRef.current?.abort();
    },
    [sessionId],
  );

  const send = useCallback(
    async (query: string): Promise<void> => {
      if (streamControllerRef.current) return;
      const localRunId = `local-${nextLocalRunId++}`;
      let currentRunId = localRunId;
      const controller = new AbortController();
      streamControllerRef.current = controller;
      dispatch({ type: "USER_SUBMITTED", runId: localRunId, query });

      try {
        const response = await fetch("/repl/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ session_id: sessionId, query }),
          signal: controller.signal,
        });
        if (!response.ok) {
          const detail = await response.text().catch(() => "");
          throw new Error(`HTTP ${response.status}${detail ? `: ${detail}` : ""}`);
        }
        for await (const event of parseSseStream(response.body)) {
          if (event.type === "RUN_STARTED") currentRunId = event.run_id;
          dispatch({ type: "SERVER_EVENT", event });
        }
      } catch (error) {
        if (!(error instanceof DOMException && error.name === "AbortError")) {
          dispatch({ type: "LOCAL_ERROR", runId: currentRunId, message: errorMessage(error) });
        }
      } finally {
        if (streamControllerRef.current === controller) streamControllerRef.current = null;
      }
    },
    [sessionId],
  );

  const cancel = useCallback(async (): Promise<void> => {
    const runId = activeRunIdRef.current;
    if (!runId || cancelPendingRef.current) return;
    cancelPendingRef.current = true;
    setCancelPending(true);
    try {
      const response = await fetch(`/repl/runs/${runId}/cancel`, { method: "POST" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
    } finally {
      cancelPendingRef.current = false;
      setCancelPending(false);
    }
  }, []);

  const sending =
    state.activeRunId !== null &&
    state.runs.some((run) => run.runId === state.activeRunId && run.status === "running");

  return { state, send, cancel, sending, cancelPending };
}
