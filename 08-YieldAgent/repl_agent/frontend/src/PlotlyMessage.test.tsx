import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PlotlyMessage } from "./PlotlyMessage";

const plot = vi.hoisted(() => vi.fn());

vi.mock("plotly.js-dist-min", () => ({ default: {} }));
vi.mock("react-plotly.js/factory", () => ({
  default: () => (props: Record<string, unknown>) => {
    plot(props);
    return <div data-testid="plot" />;
  },
}));

afterEach(() => {
  cleanup();
  plot.mockClear();
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

    expect(plot).toHaveBeenCalledOnce();
    expect(plot.mock.calls[0][0]).toMatchObject({
      layout: {
        paper_bgcolor: "#ffffff",
        plot_bgcolor: "#ffffff",
        font: { color: "#172126" },
      },
    });
  });
});
