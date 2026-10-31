"""Pure helpers for DF_SYMBOL_BIN matching.

Pass ``symbol_bin_parameters`` to ``render_wafer_map`` (cummap only) or
``query_defect_yield`` to enable this opt-in mode.  The selected definitions
are matched as a union against each die's case-sensitive ``left+right``
two-character value; omitting the option preserves the existing bin logic.
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Pattern


def combine_bins(left, right):
    """Return the case-preserving two-character value, or None if unusable."""
    if not isinstance(left, str) or not isinstance(right, str):
        return None
    if len(left) != 1 or len(right) != 1:
        return None
    return left + right


def compile_symbol_patterns(patterns: Mapping[str, str]) -> tuple[Pattern[str], ...]:
    """Compile all named patterns up front, identifying an invalid definition."""
    compiled = []
    for name, pattern in patterns.items():
        try:
            compiled.append(re.compile(pattern))
        except (re.error, TypeError) as exc:
            raise ValueError(f'Invalid regex for {name!r}: {exc}') from exc
    return tuple(compiled)


def match_symbol_bins(left, right, patterns: Sequence[Pattern[str]]) -> bool:
    """Return union fail status; malformed bin cells never match."""
    value = combine_bins(left, right)
    return value is not None and any(pattern.fullmatch(value) for pattern in patterns)


def make_symbol_fail_matcher(patterns: Mapping[str, str]):
    """Build a reusable callable for the selected named definitions."""
    compiled = compile_symbol_patterns(patterns)
    return lambda left, right: match_symbol_bins(left, right, compiled)


def match_symbol_map(map_json, patterns: Sequence[Pattern[str]]):
    """Return coordinate fail union plus the count of malformed bin pairs."""
    raw = json.loads(map_json) if isinstance(map_json, str) else map_json
    matches = {}
    unmatched = 0
    for entry in raw['MAP']:
        parts = entry.split(',')
        if len(parts) < 2:
            raise ValueError('Map contains a die without coordinates')
        coordinate = (int(parts[0]), int(parts[1]))
        if coordinate in matches:
            raise ValueError('Map contains duplicate die coordinates')
        left = parts[2] if len(parts) > 2 else None
        right = parts[3] if len(parts) > 3 else None
        if combine_bins(left, right) is None:
            unmatched += 1
        matches[coordinate] = match_symbol_bins(left, right, patterns)
    return {
        'fail_by_coordinate': matches,
        'fail_count': sum(matches.values()),
        'unmatched_cells': unmatched,
    }

