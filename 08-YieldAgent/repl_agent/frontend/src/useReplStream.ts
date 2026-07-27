import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { parseSseStream } from "./replEvents";
import { initialChatState, replReducer } from "./replReducer";

let nextLocalRunId = 1;

const errorMessage = (error: unknown): string =>
  error instanceof Error ? error.message : String(error);

const isAbortError = (error: unknown): boolean =>
  typeof error === "object" &&
  error !== null &&
  "name" in error &&
  error.name === "AbortError";

export function useReplStream(sessionId: string) {
  const [state, dispatch] = useReducer(replReducer, initialChatState);
  const [cancelPending, setCancelPending] = useState(false);
  const streamControllerRef = useRef<AbortController | null>(null);
  const cancelControllerRef = useRef<AbortController | null>(null);
  const activeRunIdRef = useRef<string | null>(null);
  const cancelPendingRef = useRef(false);
  const generationRef = useRef(0);
  const mountedRef = useRef(false);

  useEffect(() => {
    mountedRef.current = true;
    const generation = generationRef.current + 1;
    generationRef.current = generation;
    activeRunIdRef.current = null;
    cancelPendingRef.current = false;
    setCancelPending(false);
    dispatch({ type: "RESET" });

    return () => {
      if (generationRef.current === generation) generationRef.current += 1;
      mountedRef.current = false;
      streamControllerRef.current?.abort();
      cancelControllerRef.current?.abort();
      streamControllerRef.current = null;
      cancelControllerRef.current = null;
      activeRunIdRef.current = null;
      cancelPendingRef.current = false;
    };
  }, [sessionId]);

  const send = useCallback(
    async (query: string): Promise<void> => {
      if (!mountedRef.current || streamControllerRef.current || activeRunIdRef.current) {
        return;
      }
      const generation = generationRef.current;
      const isCurrent = () => mountedRef.current && generationRef.current === generation;
      const localRunId = `local-${nextLocalRunId++}`;
      let currentRunId = localRunId;
      let terminalReceived = false;
      const controller = new AbortController();
      streamControllerRef.current = controller;
      activeRunIdRef.current = localRunId;
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
          if (!isCurrent()) return;
          if (event.type === "RUN_STARTED") {
            currentRunId = event.run_id;
            activeRunIdRef.current = event.run_id;
          }
          dispatch({ type: "SERVER_EVENT", event });
          if (
            event.run_id === currentRunId &&
            (event.type === "RUN_FINISHED" ||
              event.type === "RUN_ERROR" ||
              event.type === "RUN_CANCELLED")
          ) {
            terminalReceived = true;
            activeRunIdRef.current = null;
            break;
          }
        }
        if (isCurrent() && !terminalReceived) {
          activeRunIdRef.current = null;
          dispatch({
            type: "LOCAL_ERROR",
            runId: currentRunId,
            message: "SSE stream ended before a terminal event",
          });
        }
      } catch (error) {
        if (isCurrent() && !(error instanceof DOMException && error.name === "AbortError")) {
          activeRunIdRef.current = null;
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
    if (!mountedRef.current || !runId || cancelPendingRef.current) return;
    const generation = generationRef.current;
    const controller = new AbortController();
    cancelControllerRef.current = controller;
    cancelPendingRef.current = true;
    setCancelPending(true);
    try {
      const response = await fetch(`/repl/runs/${runId}/cancel`, {
        method: "POST",
        signal: controller.signal,
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
    } catch (error) {
      const generationChanged =
        !mountedRef.current || generationRef.current !== generation;
      if (!(isAbortError(error) && (controller.signal.aborted || generationChanged))) {
        throw error;
      }
    } finally {
      if (cancelControllerRef.current === controller) cancelControllerRef.current = null;
      if (mountedRef.current && generationRef.current === generation) {
        cancelPendingRef.current = false;
        setCancelPending(false);
      }
    }
  }, []);

  const sending =
    state.activeRunId !== null &&
    state.runs.some((run) => run.runId === state.activeRunId && run.status === "running");

  return { state, send, cancel, sending, cancelPending };
}
