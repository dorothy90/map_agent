import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AnalysisRun } from "./replReducer";
import { AnalysisCard } from "./AnalysisCard";

vi.mock("./PlotlyMessage", () => ({
  PlotlyMessage: ({ artifact }: { artifact: { artifact_id: string } }) => (
    <div data-testid={`plot-artifact-${artifact.artifact_id}`} />
  ),
}));

const plotArtifact = {
  artifact_id: "p1",
  kind: "plotly" as const,
  mime_type: "application/vnd.plotly.v1+json" as const,
  spec: { data: [], layout: {} },
};

const completedRun: AnalysisRun = {
  runId: "r1",
  status: "completed",
  userMessage: "fail_value 평균을 계산해줘",
  assistantText: "판정: 평균 차이를 확인했습니다.",
  lastSequence: 8,
  steps: [
    {
      toolCallId: "t1",
      name: "run_python",
      args: { code: "wafer_df = df.drop_duplicates(['lot_id', 'wf_id'])" },
      result: {
        status: "success",
        stdout: "stdout: 3.5\n",
        stderr: "warning: sample size\n",
        execution_time_ms: 12,
        stdout_truncated: true,
      },
      artifacts: [plotArtifact],
    },
  ],
  artifacts: [plotArtifact],
};

const runningRun: AnalysisRun = {
  ...completedRun,
  runId: "r2",
  status: "running",
  assistantText: "",
};

afterEach(cleanup);

describe("AnalysisCard", () => {
  it("renders code, structured output, plot, and final text in one card", () => {
    render(<AnalysisCard run={completedRun} onCancel={vi.fn()} cancelPending={false} />);

    expect(screen.getByText("fail_value 평균을 계산해줘")).toBeInTheDocument();
    expect(screen.getByText(/wafer_df =/)).toBeInTheDocument();
    expect(screen.getByText(/stdout: 3.5/)).toBeInTheDocument();
    expect(screen.getByText(/warning: sample size/)).toBeInTheDocument();
    expect(screen.getByText("12 ms")).toBeInTheDocument();
    expect(screen.getByText("success")).toBeInTheDocument();
    const stdoutSection = screen.getByText("stdout").closest("section")!;
    const stderrSection = screen.getByText("stderr").closest("section")!;
    expect(within(stdoutSection).getByText("잘림")).toBeInTheDocument();
    expect(within(stderrSection).queryByText("잘림")).not.toBeInTheDocument();
    const plots = screen.getAllByTestId("plot-artifact-p1");
    expect(plots).toHaveLength(1);
    expect(plots[0].closest(".analysis-step")).toBeInTheDocument();
    expect(screen.getByText(/판정: 평균 차이/)).toBeInTheDocument();
  });

  it("labels stderr truncation independently from stdout", () => {
    const run: AnalysisRun = {
      ...completedRun,
      steps: [{
        ...completedRun.steps[0],
        result: {
          ...completedRun.steps[0].result,
          stdout_truncated: false,
          stderr_truncated: true,
        },
      }],
    };
    render(<AnalysisCard run={run} onCancel={vi.fn()} cancelPending={false} />);

    const stdoutSection = screen.getByText("stdout").closest("section")!;
    const stderrSection = screen.getByText("stderr").closest("section")!;
    expect(within(stdoutSection).queryByText("잘림")).not.toBeInTheDocument();
    expect(within(stderrSection).getByText("잘림")).toBeInTheDocument();
  });

  it("offers stop only while running", () => {
    const onCancel = vi.fn();
    const { rerender } = render(
      <AnalysisCard run={runningRun} onCancel={onCancel} cancelPending={false} />,
    );

    fireEvent.click(screen.getByRole("button", { name: "중지" }));
    expect(onCancel).toHaveBeenCalledOnce();

    rerender(<AnalysisCard run={completedRun} onCancel={onCancel} cancelPending={false} />);
    expect(screen.queryByRole("button", { name: "중지" })).not.toBeInTheDocument();
  });

  it("shows cancellation progress and terminal errors accurately", () => {
    const failedRun: AnalysisRun = {
      ...completedRun,
      status: "failed",
      error: { code: "python_error", message: "분석 코드를 실행하지 못했습니다." },
    };
    const { rerender } = render(
      <AnalysisCard run={runningRun} onCancel={vi.fn()} cancelPending />,
    );
    expect(screen.getByRole("button", { name: "중지 중…" })).toBeDisabled();

    rerender(<AnalysisCard run={failedRun} onCancel={vi.fn()} cancelPending={false} />);
    expect(screen.getByText("분석 코드를 실행하지 못했습니다.")).toBeInTheDocument();
    expect(screen.getByText("실패")).toBeInTheDocument();
  });
});
