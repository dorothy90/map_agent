"""Container entrypoint. The container, not Python imports, is the boundary."""
import contextlib
import io
import json
import traceback
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import scipy
import statsmodels.api as sm


class LimitedOutput(io.StringIO):
    def write(self, text):
        remaining = 50000 - self.tell()
        if remaining > 0:
            super().write(text[:remaining])
        return len(text)


def main():
    inputs = json.loads(Path("/input/data.json").read_text())
    input_tables = {key: {name: (pd.DataFrame(rows["rows"], columns=rows["columns"])
        if isinstance(rows, dict) else pd.DataFrame(rows)) for name, rows in
        (value if isinstance(value, dict) else {"default": value}).items()} for key, value in inputs.items()}
    datasets = {key: next(iter(value.values())) for key, value in input_tables.items() if len(value) == 1}
    tables, plots = [], []
    def emit_table(table, name=None):
        name = name if name is not None else f"table_{len(tables) + 1}"
        if not isinstance(name, str) or not name:
            raise ValueError("Table name must be a nonempty string")
        if any(item["table_id"] == name for item in tables):
            raise ValueError("Duplicate table name: " + name)
        frame = table if isinstance(table, pd.DataFrame) else pd.DataFrame(table)
        tables.append({"table_id": name, "title": name, "columns": [str(c) for c in frame.columns],
            "rows": json.loads(frame.to_json(orient="records", date_format="iso")), "complete": True})
    def emit_plot(figure):
        if len(plots) >= 3:
            raise ValueError("At most three plots per call")
        plots.append(figure.to_html(include_plotlyjs=True, full_html=True))
    namespace = {"pd": pd, "np": np, "px": px, "go": go, "scipy": scipy, "sm": sm,
        "tables": input_tables, "datasets": datasets, "df": datasets.get(next(iter(inputs), ""), pd.DataFrame()), "emit_table": emit_table, "emit_plot": emit_plot}
    stdout, stderr = LimitedOutput(), LimitedOutput()
    result = {"status": "success", "tables": tables, "plots": plots, "error": None}
    try:
        code = compile(Path("/input/code.py").read_text(), "analysis.py", "exec")
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exec(code, namespace)
    except Exception as exc:
        result.update(status="error", error={"type": type(exc).__name__, "message": str(exc)[:1000], "traceback": traceback.format_exc(limit=4)[-2000:]})
    result.update(stdout=stdout.getvalue(), stderr=stderr.getvalue())
    Path("/output/result.json").write_text(json.dumps(result, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
