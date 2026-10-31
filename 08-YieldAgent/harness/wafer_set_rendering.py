"""Deterministic rendering of bounded stored tables."""
from __future__ import annotations

import hashlib
from html import escape
import io
import json
import math
from typing import Any

RENDERER_VERSION = "wafer-view/1"
MAX_FACETS = 12
MAX_PIXELS = 4096


def content_sha256(value: Any) -> str:
    payload = (value if isinstance(value, bytes) else
        json.dumps(value, sort_keys=True, ensure_ascii=False, default=str,
                   allow_nan=False).encode())
    return hashlib.sha256(payload).hexdigest()


def pivot_html(rows: list[dict], columns: list[str], *, title: str,
               precision: int) -> str:
    def cell(value):
        if isinstance(value, float):
            value = f"{value:.{precision}f}"
        return escape("" if value is None else str(value), quote=True)
    head = "".join(f"<th>{cell(column)}</th>" for column in columns)
    body = "".join("<tr>" + "".join(f"<td>{cell(row.get(column))}</td>"
        for column in columns) + "</tr>" for row in rows)
    return ("<!doctype html><html><head><meta charset=\"utf-8\"><title>" +
        cell(title) + "</title><style>body{font-family:sans-serif}table{border-collapse:collapse}"
        "th,td{border:1px solid #bbb;padding:4px;text-align:right}th{background:#eee}</style>"
        f"</head><body><h1>{cell(title)}</h1><table><thead><tr>{head}</tr></thead>"
        f"<tbody>{body}</tbody></table></body></html>")


def _groups(rows: list[dict], column: str | None) -> list[tuple[Any, list[dict]]]:
    if column is None:
        return [(None, rows)]
    values = []
    for row in rows:
        value = row.get(column)
        if value not in values:
            values.append(value)
    if len(values) > MAX_FACETS:
        raise ValueError("Facet count exceeds limit")
    return [(value, [row for row in rows if row.get(column) == value]) for value in values]


def _number(value, column):
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"Chart column {column} must contain numeric values")
    if not math.isfinite(float(value)):
        raise ValueError(f"Chart column {column} must contain finite values")
    return float(value)


def _heatmap(axis, rows, spec):
    import numpy as np

    x_values = list(dict.fromkeys(row[spec.x] for row in rows))
    y_values = list(dict.fromkeys(row[spec.y] for row in rows))
    if len(x_values) * len(y_values) > 1_000_000:
        raise ValueError("Heatmap grid exceeds limit")
    x_index = {value: index for index, value in enumerate(x_values)}
    y_index = {value: index for index, value in enumerate(y_values)}
    grid = np.full((len(y_values), len(x_values)), np.nan, dtype=np.float64)
    for row in rows:
        position = y_index[row[spec.y]], x_index[row[spec.x]]
        if not np.isnan(grid[position]):
            raise ValueError("Heatmap contains duplicate coordinates")
        grid[position] = _number(row.get(spec.value), spec.value)
    image = axis.imshow(grid, origin="lower", aspect="auto", interpolation="nearest")
    axis.set_xticks(range(len(x_values)), [str(value) for value in x_values])
    axis.set_yticks(range(len(y_values)), [str(value) for value in y_values])
    axis.figure.colorbar(image, ax=axis)


def chart_png(rows: list[dict], spec, *, title: str) -> bytes:
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    facets = _groups(rows, spec.facet)
    count = max(1, len(facets))
    cols = min(3, count)
    rows_n = math.ceil(count / cols)
    width, height = min(12, cols * 4), min(12, rows_n * 3.5)
    if width * 100 > MAX_PIXELS or height * 100 > MAX_PIXELS:
        raise ValueError("Chart dimensions exceed limit")
    figure = Figure(figsize=(width, height), dpi=100, constrained_layout=True)
    canvas = FigureCanvasAgg(figure)
    for index, (facet, subset) in enumerate(facets or [(None, [])], start=1):
        axis = figure.add_subplot(rows_n, cols, index)
        if spec.kind == "heatmap":
            _heatmap(axis, subset, spec)
        elif spec.kind == "histogram":
            axis.hist([_number(row.get(spec.x), spec.x) for row in subset], bins=spec.bins)
        else:
            series = _groups(subset, spec.series)
            for label, group in series:
                x = [row.get(spec.x) for row in group]
                y = [_number(row.get(spec.y), spec.y) for row in group]
                kwargs = {"label": str(label)} if label is not None else {}
                if spec.kind == "line": axis.plot(x, y, **kwargs)
                elif spec.kind == "bar": axis.bar(x, y, **kwargs)
                else: axis.scatter(x, y, **kwargs)
            if spec.series is not None:
                axis.legend()
        axis.set_xlabel(spec.x)
        if spec.y: axis.set_ylabel(spec.y)
        if facet is not None: axis.set_title(str(facet))
    figure.suptitle(title)
    output = io.BytesIO()
    canvas.print_png(output)
    figure.clear()
    return output.getvalue()
