import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PlotlyMessage } from "./PlotlyMessage";

const mocks = vi.hoisted(() => ({
  callback: null as ResizeObserverCallback | null,
  disconnect: vi.fn(),
  observe: vi.fn(),
  plot: vi.fn(),
  resize: vi.fn(),
}));

vi.mock("plotly.js-dist-min", () => ({ default: { Plots: { resize: mocks.resize } } }));
vi.mock("react-plotly.js/factory", () => ({
  default: () => (props: Record<string, unknown>) => {
    mocks.plot(props);
    return <div className="js-plotly-plot" data-testid="plot" />;
  },
}));

beforeEach(() => {
  mocks.callback = null;
  vi.stubGlobal("ResizeObserver", class {
    constructor(callback: ResizeObserverCallback) {
      mocks.callback = callback;
    }

    observe = mocks.observe;
    disconnect = mocks.disconnect;
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  mocks.disconnect.mockClear();
  mocks.observe.mockClear();
  mocks.plot.mockClear();
  mocks.resize.mockClear();
});

describe("PlotlyMessage", () => {
  it("uses Quartz Light defaults for chat artifacts", () => {
    render(
      <PlotlyMessage
        artifact={{
          artifact_id: "plot-1",
          kind: "plotly",
          mime_type: "application/vnd.plotly.v1+json",
          spec: { data: [], layout: {} },
        }}
      />,
    );

    expect(mocks.plot).toHaveBeenCalledOnce();
    expect(mocks.plot.mock.calls[0][0]).toMatchObject({
      layout: {
        autosize: true,
        paper_bgcolor: "#ffffff",
        plot_bgcolor: "#ffffff",
        font: { color: "#172126" },
      },
    });
  });

  it("resizes the plot when its artifact container changes", () => {
    render(
      <PlotlyMessage
        artifact={{
          artifact_id: "plot-1",
          kind: "plotly",
          mime_type: "application/vnd.plotly.v1+json",
          spec: { data: [], layout: {} },
        }}
      />,
    );

    expect(mocks.observe).toHaveBeenCalledOnce();
    act(() => mocks.callback?.([], {} as ResizeObserver));
    expect(mocks.resize).toHaveBeenCalledWith(screen.getByTestId("plot"));
  });
});
