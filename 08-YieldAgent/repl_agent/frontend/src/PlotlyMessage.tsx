import createPlotlyComponent from "react-plotly.js/factory";
import Plotly from "plotly.js-dist-min";
import type { PlotArtifact } from "./replEvents";

const Plot = createPlotlyComponent(Plotly);

export interface PlotSpec {
  data: unknown[];
  layout?: Record<string, unknown>;
}

export function PlotlyMessage({ artifact }: { artifact: PlotArtifact }) {
  const spec = artifact.spec as unknown as PlotSpec;
  return (
    <div data-testid={`plot-artifact-${artifact.artifact_id}`}>
      <Plot
        data={spec.data}
        layout={{
          paper_bgcolor: "#0b1220",
          plot_bgcolor: "#0b1220",
          font: { color: "#e2e8f0" },
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
