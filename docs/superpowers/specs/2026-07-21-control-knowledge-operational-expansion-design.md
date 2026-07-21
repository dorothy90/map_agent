# Control Knowledge Operational Expansion Design

## Goal

Expand the OKF multi-agent control Wiki from slot-oriented seed pages into operational reference pages that agents can reliably use before changing orchestration, handoff, HITL, inputs, outputs, tools, or runtime contracts.

The expansion covers the current nine canonical agents and the shared Workflow, ResultEnvelope, local trace, artifact, and HITL boundaries. Only facts backed by a typed registry and verified implementation symbols may enter canonical pages. Domain data and unverified LLM inference remain excluded.

## Scope

The generated Agent pages must document:

- responsibility and explicit boundaries;
- required and optional canonical inputs;
- ResultEnvelope kinds and artifact channels;
- predecessor and successor graph nodes;
- tools and external systems;
- applicable HITL contracts;
- verified failure modes;
- source evidence and related control-knowledge pages.

The existing domain Wiki under `08-YieldAgent/wiki/` is outside scope and must remain byte-for-byte unchanged.

## Architecture

Add a central typed `AgentControlProfile` registry. It is the authored description source, but it is not independently authoritative. The collector cross-checks every machine-verifiable field against runtime code before producing a documentation candidate.

```text
AgentControlProfile registry
          |
          +-- AGENT_SLOT_RULES verification
          +-- workflow node/edge verification
          +-- source and tool module verification
          +-- ResultEnvelope/artifact/HITL verification
                          |
                          v
                 SystemSnapshot v2
                          |
                          v
                KnowledgeCandidate
                          |
                   curator + policy
                          |
            +-------------+-------------+
            |                           |
            v                           v
      canonical page              review proposal
```

The registry is preferred over distributed module metadata because it provides one inspectable system inventory. AST-only inference is not used as the description source because dynamic LangGraph and tool behavior cannot be reconstructed reliably from syntax alone.

## Registry Contract

Each profile contains:

- stable `agent_id`, title, graph node, and source module;
- one-sentence responsibility and explicit non-responsibilities;
- required and optional slot names;
- output contract page ID and supported result kinds;
- artifact channels;
- tool module names and external systems;
- HITL contract identifiers;
- verified failure modes;
- source references supporting descriptive claims.

Every descriptive or operational fact requires at least one source reference. Registry validation must confirm:

- the agent exists in `AGENT_SLOT_RULES` and its slots match exactly;
- the graph node exists;
- the source and tool modules resolve within `08-YieldAgent`;
- artifact channels exist in `YieldQueryState`;
- result kinds are valid ResultEnvelope enum values;
- HITL identifiers belong to the structured server contracts;
- source references resolve to files or importable symbols.

No keyword parsing or natural-language phrase matching is introduced.

## Generated Page Contract

Every Agent page is a complete replacement document with these sections:

```markdown
# Agent Title

## Responsibility
## Boundaries
## Inputs
## Outputs
## Workflow Position
## Tools and External Systems
## HITL Contracts
## Verified Failure Modes
## Source Evidence
## Related Knowledge
```

Relationships connect Agent pages to `workflows/orchestration-graph`, `contracts/result-envelope`, applicable contract pages, and reviewed Runbooks. The deterministic frontmatter and existing governance mirrors remain unchanged.

Workflow and Contract pages are expanded from the same snapshot to describe graph topology, shared state boundaries, schema fields, producer/consumer relationships, and relevant source evidence.

## Accumulation Flow

### Code-backed flow

On server startup or CLI snapshot:

1. load and validate the typed registry;
2. inspect current graph, canonical slots, modules, contracts, and state fields;
3. produce Agent, Workflow, and Contract candidates;
4. compare each candidate with the explicitly loaded canonical page;
5. update only when durable facts changed;
6. skip already processed fingerprints without increasing page versions.

### Runtime-backed flow

After a completed turn:

1. collect only route, result-shape, HITL-shape, and incident-shape evidence;
2. remove messages, queries, domain entities, rows, SQL, prompts, and artifact payloads;
3. create Observation candidates;
4. update an Observation only for durable structural behavior;
5. send Runbook, Decision, and Policy implications to the review queue.

## Drift and Failure Handling

- Registry/code mismatch produces a drift Observation and blocks the affected canonical Agent update.
- Missing source, module, tool, state field, or contract identity is a validation failure.
- LLM schema violations produce `invalid_decision`; the candidate remains immutable and inspectable.
- Transient curator failure follows bounded retry and restart recovery.
- Protected document changes always create proposals.
- Candidate payload-policy violations are rejected before persistence.
- Atomic write failure preserves the existing page.
- Duplicate fingerprints do not cause additional LLM calls or page-version changes.

Ledger entries and live verification must reject runs consisting only of invalid or failed decisions.

## Operational Surfaces

- `raw/candidates/`: immutable structured evidence candidates;
- `raw/curation-ledger.jsonl`: one processing disposition per fingerprint;
- `wiki/log.md`: canonical page-change history;
- `wiki/review_queue/`: protected changes awaiting approval;
- `wiki/agents/`, `wiki/workflows/`, `wiki/contracts/`: compiled operational reference.

Only one server process may run with `CONTROL_KNOWLEDGE_WRITER=true`.

## Verification

Completion requires:

1. registry unit tests covering all nine agents and every cross-check;
2. collector tests proving enriched candidates contain only control-plane facts;
3. curator/store integration tests covering create, update, proposal, drift, and idempotency;
4. generated-page tests requiring every documented section and resolvable relationship;
5. real bundle lint with no issues;
6. two identical snapshots with stable versions on the second run;
7. live server execution through planner, worker, Oracle, LLM curator, and filesystem writer;
8. restart recovery with each fingerprint processed once;
9. byte-for-byte confirmation that `08-YieldAgent/wiki/` did not change.

## Rollout

The existing three stages remain:

1. lint only;
2. shadow collection with writer disabled;
3. a single enabled writer with the real curator.

Immediate rollback disables both collection and writing without changing the request-analysis path.
