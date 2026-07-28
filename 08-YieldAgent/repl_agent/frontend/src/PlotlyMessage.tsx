import createPlotlyComponent from "react-plotly.js/factory";
import Plotly from "plotly.js-dist-min";
import { useEffect, useRef } from "react";
import type { PlotArtifact } from "./replEvents";

const Plot = createPlotlyComponent(Plotly);

export interface PlotSpec {
  data: unknown[];
  layout?: Record<string, unknown>;
}

export function PlotlyMessage({ artifact }: { artifact: PlotArtifact }) {
  const spec = artifact.spec as unknown as PlotSpec;
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container || typeof ResizeObserver === "undefined") return;

    const resizePlot = () => {
      const plot = container.querySelector<HTMLElement>(".js-plotly-plot");
      if (plot) void Plotly.Plots.resize(plot);
    };
    const observer = new ResizeObserver(resizePlot);
    observer.observe(container);
    return () => observer.disconnect();
  }, []);

  return (
    <div
      className="plotly-artifact"
      data-testid={`plot-artifact-${artifact.artifact_id}`}
      ref={containerRef}
    >
      <Plot
        data={spec.data}
        layout={{
          autosize: true,
          paper_bgcolor: "#ffffff",
          plot_bgcolor: "#ffffff",
          font: { color: "#172126" },
          margin: { l: 48, r: 24, t: 48, b: 48 },
          ...(spec.layout ?? {}),
        }}
        config={{ displaylogo: false, responsive: true }}
        style={{ width: "100%", height: "360px" }}
        useResizeHandler
      />
    </div>
  );
}
