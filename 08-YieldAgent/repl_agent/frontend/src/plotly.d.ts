// react-plotly.js 는 공식 @types 가 없다. 필요한 최소한만 선언.
declare module "react-plotly.js/factory" {
  import type { ComponentType } from "react";
  export default function createPlotlyComponent(
    plotly: unknown,
  ): ComponentType<{
    data: unknown[];
    layout?: Record<string, unknown>;
    config?: Record<string, unknown>;
    style?: React.CSSProperties;
    useResizeHandler?: boolean;
  }>;
}

declare module "plotly.js-dist-min";
