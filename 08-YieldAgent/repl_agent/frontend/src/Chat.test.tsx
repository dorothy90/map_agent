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
}));

vi.mock("./useReplStream", () => ({ useReplStream: () => hook }));
vi.mock("./PlotlyMessage", () => ({ PlotlyMessage: () => null }));
vi.mock("./AnalysisCard", () => ({
  AnalysisCard: ({ run }: { run: { runId: string; userMessage: string } }) => (
    <article data-testid={`analysis-${run.runId}`}>{run.userMessage}</article>
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
});
