import type { PlotArtifact, ReplEvent } from "./replEvents";

export type RunStatus = "running" | "completed" | "failed" | "cancelled";

export interface RunError {
  code: string;
  message: string;
}

export interface ToolStep {
  toolCallId: string;
  name: string;
  args?: Record<string, unknown>;
  result?: Record<string, unknown>;
}

export interface AnalysisRun {
  runId: string;
  status: RunStatus;
  userMessage: string;
  assistantText: string;
  steps: ToolStep[];
  artifacts: PlotArtifact[];
  error?: RunError;
  lastSequence: number;
}

export interface ChatState {
  runs: AnalysisRun[];
  activeRunId: string | null;
  runtimeLost: boolean;
}

export const initialChatState: ChatState = {
  runs: [],
  activeRunId: null,
  runtimeLost: false,
};

export type ReplAction =
  | { type: "USER_SUBMITTED"; runId: string; query: string }
  | { type: "SERVER_EVENT"; event: ReplEvent }
  | { type: "LOCAL_ERROR"; runId: string; message: string };

const runtimeLossCodes = new Set([
  "runtime_lost",
  "execution_timeout",
  "execution_cancelled",
]);

const newRun = (runId: string, userMessage: string, lastSequence: number): AnalysisRun => ({
  runId,
  status: "running",
  userMessage,
  assistantText: "",
  steps: [],
  artifacts: [],
  lastSequence,
});

const replaceRun = (state: ChatState, index: number, run: AnalysisRun): ChatState => ({
  ...state,
  runs: state.runs.map((current, currentIndex) => (currentIndex === index ? run : current)),
});

const upsertStep = (
  steps: ToolStep[],
  toolCallId: string,
  update: (step: ToolStep) => ToolStep,
): ToolStep[] => {
  const index = steps.findIndex((step) => step.toolCallId === toolCallId);
  if (index < 0) return [...steps, update({ toolCallId, name: "" })];
  return steps.map((step, stepIndex) => (stepIndex === index ? update(step) : step));
};

const resultRuntimeLost = (result: Record<string, unknown>): boolean => {
  if (runtimeLossCodes.has(String(result.status)) || runtimeLossCodes.has(String(result.code))) {
    return true;
  }
  const error = result.error;
  return (
    typeof error === "object" &&
    error !== null &&
    runtimeLossCodes.has(String((error as Record<string, unknown>).code))
  );
};

function applyServerEvent(run: AnalysisRun, event: ReplEvent): AnalysisRun {
  const next = { ...run, lastSequence: event.sequence };
  switch (event.type) {
    case "RUN_STARTED":
      return { ...next, status: "running" };
    case "TEXT_MESSAGE_CONTENT":
      return { ...next, assistantText: run.assistantText + event.content };
    case "TOOL_CALL_START":
      return {
        ...next,
        steps: upsertStep(run.steps, event.tool_call_id, (step) => ({ ...step, name: event.name })),
      };
    case "TOOL_CALL_ARGS":
      return {
        ...next,
        steps: upsertStep(run.steps, event.tool_call_id, (step) => ({ ...step, args: event.args })),
      };
    case "TOOL_RESULT":
      return {
        ...next,
        steps: upsertStep(run.steps, event.tool_call_id, (step) => ({
          ...step,
          result: event.result,
        })),
      };
    case "ARTIFACT":
      return { ...next, artifacts: [...run.artifacts, event.artifact] };
    case "RUN_FINISHED":
      return { ...next, status: "completed" };
    case "RUN_ERROR":
      return {
        ...next,
        status: "failed",
        error: { code: event.code, message: event.message },
      };
    case "RUN_CANCELLED":
      return {
        ...next,
        status: "cancelled",
        error: { code: event.code, message: event.message },
      };
    case "TEXT_MESSAGE_START":
    case "TEXT_MESSAGE_END":
    case "TOOL_CALL_END":
      return next;
  }
}

export function replReducer(state: ChatState, action: ReplAction): ChatState {
  if (action.type === "USER_SUBMITTED") {
    return {
      ...state,
      runs: [...state.runs, newRun(action.runId, action.query, 0)],
      activeRunId: action.runId,
    };
  }

  if (action.type === "LOCAL_ERROR") {
    const index = state.runs.findIndex((run) => run.runId === action.runId);
    if (index < 0) return state;
    const stateWithRun = replaceRun(state, index, {
      ...state.runs[index],
      status: "failed",
      error: { code: "network_error", message: action.message },
    });
    return {
      ...stateWithRun,
      activeRunId: state.activeRunId === action.runId ? null : state.activeRunId,
    };
  }

  const event = action.event;
  const index = state.runs.findIndex((run) => run.runId === event.run_id);
  if (event.type === "RUN_STARTED" && index < 0) {
    const activeIndex = state.runs.findIndex(
      (run) => run.runId === state.activeRunId && run.lastSequence === 0,
    );
    if (activeIndex >= 0) {
      const reconciled = {
        ...state.runs[activeIndex],
        runId: event.run_id,
      };
      const stateWithRun = replaceRun(
        { ...state, activeRunId: event.run_id },
        activeIndex,
        applyServerEvent(reconciled, event),
      );
      return stateWithRun;
    }
    return {
      ...state,
      runs: [...state.runs, applyServerEvent(newRun(event.run_id, "", 0), event)],
      activeRunId: event.run_id,
    };
  }
  if (index < 0 || event.sequence <= state.runs[index].lastSequence) return state;

  const run = applyServerEvent(state.runs[index], event);
  let next = replaceRun(state, index, run);
  const terminal =
    event.type === "RUN_FINISHED" ||
    event.type === "RUN_ERROR" ||
    event.type === "RUN_CANCELLED";
  if (terminal && state.activeRunId === event.run_id) {
    next = { ...next, activeRunId: null };
  }
  const runtimeLost =
    (event.type === "TOOL_RESULT" && resultRuntimeLost(event.result)) ||
    (event.type === "RUN_ERROR" && runtimeLossCodes.has(event.code)) ||
    event.type === "RUN_CANCELLED";
  if (runtimeLost && !next.runtimeLost) next = { ...next, runtimeLost: true };
  return next;
}
