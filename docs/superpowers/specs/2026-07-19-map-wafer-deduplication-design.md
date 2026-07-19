# Map Wafer Deduplication Design

## Problem

`map_agent._query_wafer_data()` appends every row returned from `LANGGRAPH_DATA`.
When the table contains multiple rows for the same wafer, or the same groupkey is
queried more than once, the wafer list contains duplicates and cummap calculations
count the same wafer repeatedly.

## Decision

Deduplicate the common query result before it is returned from
`_query_wafer_data()`. A wafer is identified by `(lot_id, wf_id)`. When multiple
rows share that key, retain the row with the greatest `end_tm`; a missing `end_tm`
sorts before any real timestamp.

This location covers the `lot_ids`, `groupkey`, and single-`lot_id` query paths and
ensures that visualization, wafer counts, and the collapsible wafer list all consume
the same deduplicated rows.

## Alternatives Considered

- Add `ROW_NUMBER()` to every SQL branch. This pushes the work into Oracle but
  duplicates query logic across three branches and does not eliminate repeated
  results caused by duplicate groupkey inputs.
- Deduplicate only `render_rows`. This fixes the displayed list but leaves wafer
  counts and cummap weighting incorrect.

## Scope

- Add a small helper that selects the latest row for each `(lot_id, wf_id)` key.
- Apply it once to the accumulated query results.
- Add focused unit tests for newer timestamps, missing timestamps, and distinct
  wafer keys.
- Do not change WADS selection, SQL filters, map rendering, or lot-ID variant rules.

## Verification

- First run the focused test before implementation and confirm it fails because
  the deduplication behavior is absent.
- Run the focused unit tests after implementation.
- Run the relevant map-agent unit test suite.
- Exercise `show_wafer_map()` with duplicate query rows while replacing only the
  Oracle boundary, confirming that the downstream wafer count and returned wafer
  list contain one latest row per wafer. A live Oracle check will be attempted when
  database credentials and connectivity are available.
