# Files

- [HITL Contracts](hitl-contracts.md) - Structured interrupt and resume protocol for missing parameters (missing_param), plan approval (plan_review), and post-WADS follow-up selection (postwads_choice/task_confirm).
- [Orchestration Graph](orchestration.md) - The LangGraph plan-and-execute supervisor graph that plans canonical tasks, dispatches them to domain agents, and routes follow-up fan-out via a replanner loop.
- [REPL Verification Agent](repl-agent.md) - The REPL agent subsystem that verifies yield hypotheses numerically and graphically against loaded data using an isolated Python worker process per session.
- [State and Contracts](state-and-contracts.md) - Graph state schema (YieldQueryState), canonical request normalization, agent slot rules, and the result envelope contract that agents attach to their AIMessages.
