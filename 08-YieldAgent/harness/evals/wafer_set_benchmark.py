"""Synthetic timing harness for wafer-table calculation and rendering kernels."""
from __future__ import annotations

import argparse
import json
import math
import statistics
import time

from harness.wafer_set_arrays import clear_parse_cache, parse_maps
from harness.wafer_set_rendering import chart_png
from harness.wafer_set_statistics import aggregate_rows


class Chart:
    kind = "line"
    x = "x"
    y = "y"
    series = None
    facet = None
    value = None
    bins = 30


def percentile(values, ratio):
    return sorted(values)[max(0, math.ceil(len(values) * ratio) - 1)]


def timed(function, repeat):
    samples = []
    for _ in range(repeat):
        started = time.perf_counter()
        function()
        samples.append(time.perf_counter() - started)
    return {"runs": len(samples), "median_seconds": statistics.median(samples),
            "p95_seconds": percentile(samples, .95)}


def payload(index, die_count=2500):
    cells = [f"{cell // 50},{cell % 50},{'A' if (cell + index) % 10 else 'B'},A"
             for cell in range(die_count)]
    return {"map_val_json": json.dumps({"MAP": cells}, separators=(",", ":"))}


def metrics(count):
    return [{"metric_id": "yield", "definition_id": "synthetic", "value": 50 + index % 50,
        "numerator": 50 + index % 50, "denominator": 100, "unit": "percent",
        "measurement_ref": f"m{index}", "member_id": f"m{index}",
        "lot_id": f"l{index}", "wf_id": index, "better_when": "higher",
        "aggregation_level": "measurement"} for index in range(count)]


def run(args):
    maps = [payload(index) for index in range(args.wafers)]
    metric_rows = metrics(args.wafers)
    cold = timed(lambda: (clear_parse_cache(), parse_maps(maps, namespace=("p", "s", "r", 1))),
                 min(5, args.repeat))
    clear_parse_cache()
    parse_maps(maps, namespace=("p", "s", "r", 1))
    warm = timed(lambda: parse_maps(maps, namespace=("p", "s", "r", 1)), args.repeat)
    aggregate = timed(lambda: aggregate_rows(metric_rows, group_by=[], operation="ratio_of_sums",
        period="none", time_column=None, overlap="error", max_groups=100), args.repeat)
    renders = {}
    for count in args.render_counts:
        rows = [{"x": index, "y": index % 17} for index in range(count)]
        renders[str(count)] = timed(lambda rows=rows: chart_png(rows, Chart(), title="Benchmark"),
                                    args.repeat)
    return {"mode": args.mode, "wafers": args.wafers,
        "payload_bytes": sum(len(item["map_val_json"].encode()) for item in maps),
        "repeat": args.repeat, "parse_cold": cold, "parse_warm": warm,
        "aggregate": aggregate, "render": renders,
        "targets_seconds": {"parse_cold_p95": 2.5, "parse_warm_p95": 1.0,
                            "aggregate_p95": 2.0, "render_p95": 3.0}}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["synthetic"], default="synthetic")
    parser.add_argument("--wafers", type=int, default=100)
    parser.add_argument("--render-counts", type=int, nargs="+", default=[1, 12, 30])
    parser.add_argument("--repeat", type=int, default=20)
    args = parser.parse_args(argv)
    if not 1 <= args.wafers <= 1000 or not 1 <= args.repeat <= 100:
        parser.error("wafers or repeat is outside the benchmark limit")
    print(json.dumps(run(args), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
