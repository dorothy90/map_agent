import asyncio
from datetime import date


def test_yield_passes_explicit_period_and_preserves_all_rows(monkeypatch):
    import yield_db
    from harness.tools.registry import domain_registry

    rows = [{"week": str(i), "lotcount": 2} for i in range(120)]
    seen = []
    def fetch(*args, **kwargs):
        seen.append((args, kwargs))
        return rows
    monkeypatch.setattr(yield_db, "_fetch_periods", fetch)
    async def direct(function, *args, **kwargs):
        return function(*args, **kwargs)
    monkeypatch.setattr("harness.tools.yield_tools.run_blocking", direct)
    result = asyncio.run(domain_registry().invoke("query_yield", {"lotcd": "4SS", "ref_date": "2026-09-01", "unit": "weekly", "periods": 4}, None))
    assert result.rows == rows
    assert seen[0][0] == ("4SS", date(2026, 9, 1), "weekly", 4)


def test_invalid_tool_args_do_not_reach_database():
    import pytest
    from pydantic import ValidationError
    from harness.tools.registry import domain_registry

    with pytest.raises(ValidationError):
        asyncio.run(domain_registry().invoke("query_yield", {"periods": -1}, None))


def test_yield_connection_failure_cannot_become_empty(monkeypatch):
    import pytest
    import yield_db
    def broken():
        raise ConnectionError("offline")
    monkeypatch.setattr(yield_db, "_get_oracle_connection", broken)
    with pytest.raises(ConnectionError):
        yield_db._fetch_periods("4SS", date(2026, 9, 1), strict=True)


def test_yield_preserves_values_and_exposes_unit_uncertainty(monkeypatch):
    import yield_db
    from harness.tools.registry import domain_registry

    rows = [{"week": "2026-W35", "lotcount": 1, "pt1c_PT1C": .5431, "gms_cum0": 90.43}]
    monkeypatch.setattr(yield_db, "_fetch_periods", lambda *a, **kw: rows)
    async def direct(function, *args, **kwargs):
        return function(*args, **kwargs)
    monkeypatch.setattr("harness.tools.yield_tools.run_blocking", direct)
    result = asyncio.run(domain_registry().invoke("query_yield", {"lotcd": "DEMO", "ref_date": "2026-09-01"}, None))
    assert result.rows[0]["pt1c_PT1C"] == .5431
    assert result.scope["value_units"]["PT1C"] == "source_value_unit_unspecified"
    assert result.scope["value_units"]["GMS"] == "percent"
    assert ">0.54</td>" in result.artifacts[0]["data"]
    assert ">90.43</td>" in result.artifacts[0]["data"]
