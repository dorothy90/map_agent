# Wiki Graph-Assisted RAG Design

**Date:** 2026-08-02
**Milestone:** M5 — Graph-assisted retrieval and source-grounded semantic relations

## 1. Goal

Extend the existing incremental Markdown Wiki so the Fail History Agent can traverse Wiki relationships, merge them with OpenSearch evidence, and answer with verified Source citations.

M5 also extracts entities and semantic relations during the existing Concept synthesis call. Relations are written automatically; there is no per-relation approval workflow.

## 2. Current State

The existing platform already provides:

- an external Obsidian Vault at `/Users/daehwankim/SYLDAIX/YieldWiki`;
- Concept synthesis keyed by `product + fail_type + cause_oper`;
- Source Markdown generated from Concept citations;
- deterministic Product, Product Fail, Operation, Concept, and Source wikilinks;
- `bootstrap_wiki` for initial construction and recovery;
- `sync_wiki` for fingerprint-based incremental updates, retry, and resume;
- Wiki-first and OpenSearch hybrid retrieval;
- Obsidian Search, Chat, Related Notes, Citation, and Review UI.

The current Agent can use an exact Concept body or OpenSearch hits. It does not traverse the entity and relation structure described inside Concept prose. Obsidian displays existing `[[wikilink]]` edges, but those edges do not encode relations such as `causes` or `resolved_by` for machine retrieval.

At design time the live Vault contains 3 Concepts and 16 Sources, while the `fail-history` OpenSearch index contains 505 documents. This does not justify Neo4j or another Graph database.

## 3. Scope

### 3.1 In scope

- Add structured entities and semantic relations to Concept synthesis output.
- Persist the structured output in Concept frontmatter for recovery.
- Materialize Entity and Relation Markdown notes with Source wikilinks.
- Incrementally activate or stale generated relations when a Concept changes.
- Build a bounded graph projection from the Vault for Agent retrieval.
- Merge Graph-derived Source evidence with existing OpenSearch evidence.
- Validate all user-visible citations against an existing Source note.
- Preserve the existing OpenSearch-only path when Graph retrieval is unavailable.
- Verify the complete path with real OpenSearch, a real LLM, the real Vault, and Obsidian Desktop.

### 3.2 Out of scope

- Neo4j, Memgraph, or another Graph database.
- Re-embedding existing OpenSearch documents.
- A new Wiki content compiler.
- Per-relation human approval or bulk relation approval UI.
- Fuzzy entity merging, keyword synonym tables, or phrase-based relation parsing.
- General document ingestion for Outlook, PDF, DOCX, SharePoint, images, or OCR.
- Recovering metadata from the separate content-only index.
- More than one graph hop per retrieval request.

## 4. Design Principles

1. **Vault remains authoritative.** OpenSearch remains raw retrieval storage; generated Markdown remains the Wiki and Graph source of truth.
2. **No extra relation-extraction LLM call.** The existing Concept synthesis response gains structured `entities` and `relations` fields.
3. **Every semantic relation is source-grounded.** A relation without at least one existing `source_doc_id` is not materialized or used for an answer.
4. **No manual relation gate.** Valid relations are activated automatically. Confidence is retained for disclosure and ranking, not used as a publication threshold.
5. **Safe degradation.** Graph failure never disables the established OpenSearch path.
6. **No semantic hardcoding.** Planner context and structured contracts select entities and relations. The implementation does not add keyword, regex, or phrase-list routing rules.
7. **Bounded retrieval.** One-hop expansion and explicit evidence limits prevent context growth.

## 5. Vault Schema

The Vault gains two generated namespaces:

```text
YieldWiki/
├── concepts/
├── sources/
├── entities/
└── relations/
```

The Agent remains the only automatic writer. Entity and Relation notes use the same atomic write and canonical-root safety rules as the existing materializer.

### 5.1 Concept synthesis contract

`ConceptSynthesis` gains two fields:

```yaml
entities:
  - canonical_name: Queue time 초과
    entity_type: process_condition

relations:
  - subject: Queue time 초과
    predicate: causes
    object: 자연 산화
    confidence: 0.82
    source_doc_ids:
      - FH-9003-EXTRA
```

The structured fields are stored in Concept frontmatter together with the Concept's `source_fingerprint`. This makes a successfully stored Concept sufficient to reconstruct Entity and Relation notes after a crash without another LLM call.

### 5.2 Entity identity

An Entity is identified by its normalized exact `canonical_name`. Normalization is limited to stable storage normalization such as Unicode normalization and surrounding whitespace removal. Different names are not merged through fuzzy matching.

If two Concepts emit the same normalized canonical name, they reference the same Entity note. Different names remain separate until an explicit future Alias operation joins them.

Entity notes contain:

```yaml
---
id: entity:queue-time-excess
type: entity
canonical_name: Queue time 초과
entity_type: process_condition
status: active
source_concept_ids:
  - concept:4SS|PRE METAL CLN|EASY
---
```

The generated body links the originating Concepts and active Relations.

### 5.3 Relation ontology

M5 supports exactly five semantic predicates:

| Predicate | Meaning |
|---|---|
| `causes` | The subject is documented as a direct cause of the object. |
| `contributes_to` | The subject is documented as a contributing factor. |
| `resolved_by` | The subject issue is documented as resolved by the object action. |
| `prevents` | The subject action is documented as preventing the object issue. |
| `associated_with` | The source supports an association but not a causal conclusion. |

This ontology is a structured storage contract, not a natural-language routing table. The LLM chooses one of these values from the evidence supplied to the Concept synthesis call.

A Relation identity is stable for the tuple:

```text
origin_concept_id + normalized_subject + predicate + normalized_object
```

Relation notes contain:

```yaml
---
id: relation:<stable-id>
type: relation
origin_concept_id: concept:4SS|PRE METAL CLN|EASY
subject_entity_id: entity:queue-time-excess
predicate: causes
object_entity_id: entity:natural-oxidation
confidence: 0.82
source_doc_ids:
  - FH-9003-EXTRA
status: active
source_fingerprint: sha256:...
---
```

The body links the subject Entity, object Entity, originating Concept, and each Source:

```markdown
# Queue time 초과 causes 자연 산화

- Subject: [[entities/Queue time 초과]]
- Predicate: `causes`
- Object: [[entities/자연 산화]]
- Concept: [[concepts/4SS_PRE_METAL_CLN_EASY]]
- Sources:
  - [[sources/FH-9003-EXTRA]]
- Confidence: 0.82
```

Obsidian therefore displays an inspectable Relation node between Entities and Source evidence. Agent retrieval reads relation frontmatter rather than inferring predicate meaning from the note title or body.

## 6. Incremental Update Flow

The existing M3 job remains the orchestration boundary:

```text
OpenSearch snapshot changed
  → existing Concept synthesis call
  → body + confidence + citations + entities + relations
  → atomic Concept write
  → Entity and Relation materialization
  → existing MOC and Source materialization
  → manifest and job success
```

The materializer computes the desired active relations for each changed Concept.

- A newly emitted valid relation becomes `active`.
- An unchanged relation retains its stable ID.
- A relation formerly emitted by the same Concept but absent from the new output becomes `stale`.
- A stale relation remains readable in Obsidian but is excluded from Agent retrieval.
- Entity notes with no active originating Concept or Relation become `stale` rather than being deleted.

If materialization fails after the Concept write, the job remains failed. `sync_wiki --resume` reads the persisted structured Concept fields and repairs the Markdown projection without re-invoking the LLM.

Existing Concepts do not require a special migration program. Deployment re-runs the existing exact-triple bootstrap path for the live Concepts that need initial entity and relation data.

## 7. Relation Validation

Automatic activation performs structural and referential validation only:

- subject and object are non-empty;
- subject and object resolve to Entities emitted by the same Concept synthesis;
- predicate belongs to the five-value ontology;
- confidence is a number from 0 through 1;
- every `source_doc_id` belongs to the Concept's persisted citations/source set;
- every `source_doc_id` resolves either to an existing canonical Source note or to
  a canonical Source target in the same atomic materialization plan;
- generated paths remain inside the configured Vault root;
- stable IDs and output paths do not collide.

An invalid relation is omitted while the valid Concept and other valid relations continue. The omission and reason are recorded in the sync result and Wiki audit log. Confidence does not block a structurally valid relation.

Contradictory active relations may coexist when they have separate Source evidence. The retriever supplies both to the LLM, including predicate, confidence, and citations, and the answer must describe the conflict rather than silently choosing one.

## 8. Graph Projection and Retrieval

M5 does not add a Graph database. A focused service reads canonical Concept, Entity, Relation, and Source frontmatter into an immutable in-process adjacency projection. The projection is cached and invalidated by the materialized Vault fingerprint. It can be reconstructed entirely from Markdown.

### 8.1 Seed selection

1. If the Planner supplies an exact `product + fail_type + cause_oper`, use the canonical Concept as the seed.
2. Otherwise, run the existing OpenSearch retrieval and map returned metadata triples to materialized Concepts.
3. If no materialized Concept is found, retain the existing OpenSearch-only result.

No natural-language keyword or regex relation selection is added.

### 8.2 One-hop expansion

For each seed Concept, read:

- active Relations originating from the Concept;
- their subject and object Entities;
- their canonical Source notes;
- related Concepts that share an active Entity or Source.

Expansion stops after one hop. The response is bounded to the primary Concept, a fixed maximum number of active Relations, a fixed maximum number of related Concepts, and a fixed maximum number of Source documents. Exact limits are configuration constants covered by tests, not public tuning APIs in M5.

### 8.3 Evidence merge

Graph evidence and existing OpenSearch results are merged by exact `doc_id`.

- A duplicate `doc_id` appears once.
- The existing OpenSearch score remains the search relevance score.
- Graph provenance records which Concept and Relation selected the Source.
- Graph-only Source evidence is loaded by exact `doc_id`, not a second vector query.
- Only Source-backed relation statements are passed to answer synthesis.

The existing Fail History Agent receives a typed retrieval envelope containing:

```yaml
primary_concept: concept:4SS|PRE METAL CLN|EASY
graph_relations:
  - subject: Queue time 초과
    predicate: causes
    object: 자연 산화
    confidence: 0.82
    source_doc_ids:
      - FH-9003-EXTRA
related_concepts: []
evidence:
  - doc_id: FH-9003-EXTRA
    provenance:
      mode: graph
      relation_id: relation:<stable-id>
```

The LLM treats this envelope as untrusted evidence, never as instructions.

## 9. Citation Contract

User-visible citations always identify a real Source document. Relation and Entity notes are navigation aids, not substitutes for source evidence.

Before emitting a citation:

1. the `doc_id` must occur in the typed merged evidence envelope;
2. the corresponding canonical Source note must exist and have exact matching `type: source` and `doc_id` frontmatter;
3. `source_path` is resolved from that canonical note;
4. missing or invalid citations are omitted rather than guessed.

The Obsidian Plugin continues to open `source_path` from the structured SSE citation. No citation is inferred by parsing answer prose.

## 10. Failure Handling

| Failure | Behavior |
|---|---|
| Relation has a missing Source | Omit that Relation and record the validation error. |
| Entity or Relation Markdown write fails | Mark the sync job failed; `--resume` reconstructs from the stored Concept. |
| Graph projection cannot load | Log the failure and use the existing OpenSearch-only retrieval path. |
| Embedding provider fails | Retain the existing explicit BM25 fallback behavior. |
| LLM answer generation fails | Preserve the existing SSE `error` event and Retry UI. |
| Relations conflict | Return both with Source provenance; prompt the answer to state uncertainty. |
| Relation disappears on re-synthesis | Mark it `stale`; exclude it from retrieval. |

## 11. API and Obsidian Behavior

Existing public and Plugin APIs remain backward compatible. M5 extends structured internal retrieval and additive Plugin responses only where graph context is useful.

Obsidian behavior after M5:

- Entity and Relation notes appear in the default Graph because they contain ordinary wikilinks.
- Clicking a Relation shows its predicate, confidence, Concept, and Source links.
- Chat citations continue to open Source notes.
- Existing Review remains for Wiki conflicts and removals; it is not used for relation publication.

No existing React frontend is changed.

## 12. Security and Trace Boundaries

- Vault paths use the established canonical-root and symlink-safe resolver.
- Plugin endpoints retain Bearer authentication and fail-closed configuration.
- Entity, Relation, and Concept bodies are company data and must not appear raw in default local or remote traces.
- Trace records retain IDs, paths, counts, lengths, hashes, and allowlisted metadata only.
- External LLM E2E uses non-sensitive test evidence unless the user explicitly authorizes company data and the destination.
- Source download authorization remains unchanged.

## 13. Verification

M5 is complete only after all of the following pass.

### 13.1 Automated tests

- `ConceptSynthesis` validates Entity and Relation structured output.
- Invalid predicates, missing Sources, path collisions, and malformed values are rejected per relation.
- Entity and Relation Markdown is deterministic and symlink-safe.
- Unchanged sync produces no LLM call and no relation rewrite.
- Changed sync activates new relations and stales removed relations.
- A crash after Concept storage resumes materialization without another LLM call.
- One-hop traversal returns only active Source-backed relations.
- Evidence merging deduplicates exact `doc_id` values.
- Missing Source notes cannot become SSE citations.
- Graph projection failure preserves existing OpenSearch retrieval.
- Raw Entity, Relation, and Concept bodies do not enter default traces.
- Existing Wiki, confirm-edit, user-memory, and Obsidian Plugin regressions pass.

### 13.2 Real end-to-end verification

Using the real `fail-history` index, a real configured LLM, the real Vault, Backend, and Obsidian Desktop:

1. Re-synthesize the exact `4SS + EASY + PRE METAL CLN` Concept.
2. Confirm Entity and Relation Markdown is created in the external Vault.
3. Open the Relation and its connected Source in Obsidian.
4. Ask a question whose answer uses an extracted relation.
5. Confirm the Agent traverses the primary Concept and active Relation.
6. Confirm the answer cites the exact Source note and the citation opens it.
7. Change the source set, run `sync_wiki`, and confirm obsolete relations become stale.
8. Re-run without source changes and confirm zero synthesis calls and no rewritten relation files.
9. Stop Graph loading and confirm the same question degrades to the established OpenSearch path.

An unavailable or quota-exhausted LLM is a blocked external dependency, not a passing M5 E2E result. Mock success is not accepted as final completion evidence.

## 14. Delivery Sequence

1. Structured Entity and Relation synthesis contract.
2. Atomic Entity and Relation Markdown materialization.
3. Incremental stale and resume behavior.
4. Immutable Vault graph projection and one-hop traversal.
5. Graph/OpenSearch evidence merge and Citation validation.
6. Full regression and real end-to-end verification.
