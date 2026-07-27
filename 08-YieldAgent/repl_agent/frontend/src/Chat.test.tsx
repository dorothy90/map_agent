import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ChatState } from "./replReducer";
import { Chat } from "./Chat";

const hook = vi.hoisted(() => ({
  state: { runs: [], activeRunId: null, runtimeLost: false } as ChatState,
  send: vi.fn(),
  cancel: vi.fn(),
  sending: false,
  cancelPending: false,
  cancelError: null as string | null,
}));

vi.mock("./useReplStream", () => ({ useReplStream: () => hook }));
vi.mock("./PlotlyMessage", () => ({ PlotlyMessage: () => null }));
vi.mock("./AnalysisCard", () => ({
  AnalysisCard: ({ run, onCancel }: {
    run: { runId: string; userMessage: string };
    onCancel: () => void;
  }) => (
    <article data-testid={`analysis-${run.runId}`}>
      {run.userMessage}
      <button type="button" onClick={onCancel}>중지</button>
    </article>
  ),
}));

describe("Chat", () => {
  afterEach(cleanup);

  beforeEach(() => {
    hook.state = { runs: [], activeRunId: null, runtimeLost: false };
    hook.send.mockReset();
    hook.cancel.mockReset();
    hook.sending = false;
    hook.cancelPending = false;
    hook.cancelError = null;
  });

  it("renders exactly one analysis card for one run", () => {
    hook.state = {
      runs: [{
        runId: "r1",
        status: "completed",
        userMessage: "평균을 계산해줘",
        assistantText: "완료",
        steps: [],
        artifacts: [],
        lastSequence: 2,
      }],
      activeRunId: null,
      runtimeLost: false,
    };

    render(<Chat sessionId="session-1" />);

    expect(screen.getAllByTestId("analysis-r1")).toHaveLength(1);
    expect(screen.getByText("평균을 계산해줘")).toBeInTheDocument();
  });

  it("submits a trimmed question through useReplStream", () => {
    render(<Chat sessionId="session-1" />);
    const input = screen.getByPlaceholderText(/질문 입력/);
    fireEvent.change(input, { target: { value: "  fail_value 평균  " } });
    fireEvent.submit(input.closest("form")!);

    expect(hook.send).toHaveBeenCalledWith("fail_value 평균");
    expect(input).toHaveValue("");
  });

  it("disables new questions after runtime loss", () => {
    hook.state = { runs: [], activeRunId: null, runtimeLost: true };

    render(<Chat sessionId="session-1" />);

    expect(screen.getByPlaceholderText(/질문 입력/)).toBeDisabled();
    expect(screen.getByText(/새 세션을 시작/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "보내기" })).toBeDisabled();
  });

  it("handles cancellation rejection and shows an actionable error", async () => {
    hook.state = {
      runs: [{
        runId: "r1",
        status: "running",
        userMessage: "느린 분석",
        assistantText: "",
        steps: [],
        artifacts: [],
        lastSequence: 1,
      }],
      activeRunId: "r1",
      runtimeLost: false,
    };
    hook.cancelError = "HTTP 503";
    hook.cancel.mockRejectedValueOnce(new Error("HTTP 503"));
    render(<Chat sessionId="session-1" />);

    fireEvent.click(screen.getByRole("button", { name: "중지" }));

    expect(await screen.findByText(/중지 요청을 보내지 못했습니다/)).toBeInTheDocument();
    expect(hook.cancel).toHaveBeenCalledOnce();
  });
});
