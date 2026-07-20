# Legend Root-Cause Mining Design

## 1. Goal

Given user-supplied good and bad wafer `groupkey` lists, analyze each wafer's
process-by-process legend history and return ranked **root-cause candidates** at
three levels:

1. process (`STEP`),
2. single legend value within a process,
3. interacting legend values within the same process.

The system must distinguish a reproducible signal from a LOT-specific, temporal,
missing-data, or label-noise artifact. It must not describe an observational
association as confirmed causality.

## 2. Scope and non-goals

### In scope

- Input groups expressed as `LOTID.WFID` groupkeys.
- Approximately 600 process steps and more than 10 legend fields per step.
- `DF_LEGEND_LONG` as the source contract. The production table is assumed to
  include `RETICLE` in addition to the currently seeded fields.
- A supervised binary target: good `0`, bad `1`.
- Process screening, categorical interaction discovery, stability analysis,
  explainable output, and end-to-end integration with `mining_agent`.

### Out of scope for v1

- Confirming physical causality without engineering follow-up.
- Transformer or other sequence-model training.
- Per-request model stacking or ensembling.
- Cross-process sequence interactions. V1 interactions remain within one
  process; cross-process effects can be evaluated after the single-process
  pipeline is validated.
- Automatically changing good/bad labels from expert feedback.

## 3. Design decisions

### 3.1 Do not train one model on the raw 6,000-plus-column matrix

A direct `600 STEP x 10+ legends` wide model is statistically fragile when a
request contains only tens or hundreds of wafers. V1 therefore uses a hierarchy:

1. deterministic data-quality and contrast analysis,
2. per-process Elastic-Net screening,
3. CatBoost deep dive on the selected processes,
4. global validation on the reduced feature set.

### 3.2 Use one production champion, not a runtime ensemble

Elastic-Net Logistic Regression and CatBoost are evaluated on identical splits.
The model-selection report chooses a champion from predictive performance,
permutation significance, calibration, and candidate-rank stability. V1 does not
stack their predictions. Agreement between two models is useful evidence but is
not proof of causality.

### 3.3 Keep candidate selection deterministic and LLM-independent

All input validation, feature construction, statistics, model training, ranking,
and evidence checks are deterministic code. The LLM receives the validated result
and explains it; it cannot add or reorder unsupported candidates.

### 3.4 Do not hardcode semantic legend combinations

The engine does not privilege named pairs such as `EQP+CHAMBER` or
`RECIPE+RETICLE`. It uses the legend columns supplied by the source schema,
discovers feature interactions from the fitted model, and evaluates only value
combinations actually observed in the data. This keeps new legend types usable
without adding natural-language rules.

## 4. Contracts

### 4.1 Request

```json
{
  "group_good": ["TSSHUCN.01", "TSSHUCN.02"],
  "group_bad": ["TSSHVQ4.03", "TSSHVQ4.07"],
  "rank_limit": 20,
  "fail_name": "optional display context"
}
```

Rules:

- Normalize groupkey whitespace and wafer-number representation.
- Reject an overlap between good and bad groups.
- Deduplicate within each group while preserving input order.
- Return unmatched groupkeys explicitly; never silently train without them.
- `rank_limit` changes only the displayed view, not feature selection or model
  training.

### 4.2 Source rows

The minimum source fields are:

```text
LOTID, WFID, STEP, END_TM,
RECIPE, CHAMB_ID, EQP_ID, ROUTE, RETICLE, ...additional legend columns
```

Legend fields are discovered from the source schema or an explicit structured
schema contract. They are not inferred from column-name keywords.

### 4.3 Result

```json
{
  "status": "success | insufficient_data | no_reliable_signal | error",
  "data_quality": {},
  "validation": {},
  "model_selection": {},
  "process_candidates": [],
  "single_legend_candidates": [],
  "interaction_candidates": [],
  "warnings": [],
  "unmatched_groupkeys": []
}
```

Every candidate includes its process, observed legend condition, affected
groupkeys, good/bad exposure counts, effect size, out-of-fold model contribution,
stability evidence, and applicable confounding warnings.

## 5. Phase 0: Extraction, preprocessing, and EDA

### 5.1 Batch extraction

- Parse groupkeys into `LOTID` and `WFID`.
- Query all requested wafers in bounded batches with bind variables.
- Preserve input group membership outside the database query.
- Record query row count and matched/unmatched wafer count.

### 5.2 Data integrity

- Remove exact duplicate rows.
- Preserve repeated visits to the same STEP as rework occurrences.
- Parse `END_TM` explicitly and retain invalid timestamps as a quality warning.
- Distinguish a missing legend value from a wafer that never visited a process.
- Detect conflicting labels and source rows with ambiguous wafer ownership.

### 5.3 EDA report

Report before modeling:

- good/bad wafer and LOT counts,
- label distribution by LOT and time window,
- STEP coverage by class,
- legend cardinality and missingness by STEP,
- rework-count and legend-change distributions,
- whether LOT and label are confounded.

Rare-category handling is fitted inside each training fold. Categories are never
grouped using names or phrases, and validation data cannot influence grouping.

## 6. Phase 1: Process screening

### 6.1 Descriptive contrast

For every observed `(STEP, legend field, legend value)`, calculate:

- good and bad exposure counts and rates,
- risk difference and lift,
- odds ratio with a finite-sample correction,
- Fisher exact p-value,
- Benjamini-Hochberg adjusted q-value.

These statistics are evidence and diagnostics. They do not alone choose the final
candidate.

### 6.2 Per-process Elastic-Net model

For each STEP, construct one wafer-level row containing:

- categorical legend values,
- process visit count,
- distinct-value count per legend,
- value-change count per legend,
- elapsed-time features that can be derived without looking beyond the observed
  wafer history.

Fit an Elastic-Net Logistic Regression inside the grouped cross-validation loop.
Encoding, rare-category handling, imputation, and feature selection are fitted on
the training fold only.

Each process produces:

- out-of-fold PR-AUC and balanced accuracy,
- a label-permutation null distribution and empirical p-value,
- adjusted significance across all evaluated processes,
- coefficient direction and stability,
- selection frequency across valid splits.

The number of selected processes is data-driven. `Top 30-50` may be a UI display
limit but is not an analysis rule. A process advances only if it beats the null
model and its signal is stable on the available LOT-aware splits.

## 7. Phase 2: Selected-process deep dive

### 7.1 CatBoost input

Build a reduced wafer-level table from only the processes selected within each
training fold. Supply the raw legend values as categorical features rather than
pre-one-hot encoding them.

### 7.2 Single legends

Calculate out-of-fold SHAP values for validation wafers. Convert a feature-level
contribution into a value-level candidate only by joining it back to the observed
wafer value. Rank bad-direction contributions and retain the accompanying good/bad
exposure table.

### 7.3 Legend interactions

- Read CatBoost feature-interaction strength for selected-process features.
- Retain same-STEP feature pairs in v1.
- Materialize only combinations observed in the source rows.
- Re-evaluate each observed combination with out-of-fold contribution, support,
  effect size, and permutation or bootstrap stability.
- Keep an interaction only when it adds held-out information beyond its strongest
  single constituent.

This produces exact candidates such as `STEP=X, EQP=A, CHAMBER=B` without a
predefined pair list.

## 8. Phase 3: Reduced global validation

Train Elastic-Net and CatBoost on the same reduced, fold-local feature space and
the same train/test splits. Do not reuse full-data screening results inside a fold.

### 8.1 Primary and secondary metrics

- Primary: PR-AUC.
- Secondary: balanced accuracy, ROC-AUC, calibration error, and confusion matrix.
- Statistical gate: grouped label-permutation score.
- Explanation gate: candidate-rank stability across folds and seeds.

### 8.2 Champion selection

A model is eligible only if it:

1. exceeds its label-permutation null,
2. produces valid held-out predictions for every reported fold,
3. retains candidate direction across the available resamples,
4. does not depend on one LOT without being labeled `LOT-specific`.

Among eligible models, prefer the simpler Elastic-Net when its performance and
stability are practically indistinguishable from CatBoost. Otherwise choose the
better validated model. The result records both benchmark results and the reason
for the choice.

PDP/ALE is not a default categorical explanation. Observed exposure contrasts and
out-of-fold SHAP are used for categorical legends. ALE may be emitted for supported
numeric derivative features such as rework count.

## 9. Phase 4: Sensitivity and stability

Run the checks supported by the submitted cohort:

- leave-one-LOT-out or stratified grouped resampling,
- time-window subsampling,
- controlled label-flip simulation,
- top-candidate feature ablation,
- with-route versus without-route comparison,
- missingness-indicator ablation,
- repeatability under fixed recorded seeds.

Thresholds for a production pass/fail gate are calibrated from negative controls
and recorded historical cases. The first implementation reports the raw stability
distribution rather than embedding an unvalidated `70%` rule.

## 10. Data-sufficiency modes

### Full grouped mode

Use `StratifiedGroupKFold` when enough distinct LOTs and labels exist to build at
least two folds where both training and validation contain both classes.

### Within-LOT mode

If LOT-grouped validation is impossible but both labels exist within a LOT, use
repeated stratified wafer splits and label the result `within-LOT only`. Do not
claim cross-LOT reproducibility.

### Descriptive-only mode

If a valid binary validation split cannot be constructed, skip ML and return EDA,
contrast statistics, and `insufficient_data`. Do not manufacture a ranked ML
result.

## 11. Human review

The result UI lets an engineer mark a candidate as:

- plausible,
- rejected with a structured reason,
- requires follow-up evidence.

This feedback is stored as candidate-review evidence. It does not silently change
wafer good/bad labels or retrain the current analysis. A future learning loop may
use reviewed cases only after a separate label-governance design.

## 12. Integration boundaries

Suggested modules:

```text
legend_history_tools.py       groupkey parsing and Oracle extraction
legend_features.py            wafer/process feature and quality report
legend_screening.py           contrasts and Elastic-Net process screening
legend_mining.py              CatBoost, OOF SHAP, interactions, model selection
legend_mining_contracts.py    request/result schemas
mining_agent.py               orchestration, result envelope, artifact, summary
```

`repl_agent` remains an exploratory hypothesis-validation surface. It is not the
production ranking engine. `mining_dummy_api.py` becomes test-fixture support or is
removed once the production extractor is connected.

Existing canonical slots `group_good` and `group_bad` remain, but their contract is
clarified as groupkey lists rather than LOT/GROUPKEY ambiguity. The LLM sees the
validated result envelope and never fabricates missing rows, scores, or candidates.

## 13. Output artifact

The mining artifact contains:

1. data-quality and model-validity banner,
2. process ranking table,
3. single-legend candidate table,
4. interaction candidate table,
5. global and local out-of-fold SHAP views,
6. LOT/time stability table,
7. warnings and unmatched groupkeys,
8. engineer-review controls.

Each visual links a candidate back to the affected good and bad groupkeys.

## 14. Verification strategy

### Unit and property tests

- groupkey normalization, overlap, duplicate, and missing-wafer behavior,
- long-to-wafer transformation with missing steps and dynamic legend columns,
- rework and legend-change features,
- fold-local encoders and selectors,
- q-value and permutation calculations,
- result reference integrity.

### Injected-signal tests

- one bad-enriched single legend is recovered,
- a combination-only signal is recovered while constituents remain weak,
- a random-label dataset returns `no_reliable_signal`,
- a one-LOT-only signal is marked `LOT-specific`,
- route confounding is exposed by the route ablation,
- label flips reduce stability rather than strengthening the claim.

### Integration and live verification

- real `DF_LEGEND_LONG` query for supplied good/bad groupkeys,
- actual Elastic-Net and CatBoost training,
- actual out-of-fold explanations and sensitivity report,
- actual LLM summary constrained to the result contract,
- server SSE artifact delivery and browser rendering.

## 15. Acceptance criteria

The feature is complete when:

1. every submitted groupkey is matched or explicitly reported unmatched;
2. no preprocessing or selection learns from a validation fold;
3. a known injected single and interaction signal are recovered;
4. negative controls do not produce a reliable-cause result;
5. LOT-specific and within-LOT limitations are visible in the contract and UI;
6. every displayed candidate is traceable to source groupkeys and held-out evidence;
7. model selection records why Elastic-Net or CatBoost became champion;
8. real Oracle, real model, real LLM, SSE, and rendered artifact are verified end to end.

## 16. Deferred options

- TabPFN is a challenger only after fold-local feature reduction yields an input
  within its supported dimensional regime and an offline benchmark justifies the
  dependency.
- A 600-step embedding or Transformer model requires a separately governed,
  sufficiently large historical labeled-wafer corpus. It is not trained from one
  ad hoc good/bad request.
- Runtime stacking is reconsidered only if a recorded benchmark shows material,
  stable improvement over the selected single champion.
