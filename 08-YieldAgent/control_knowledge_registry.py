from __future__ import annotations

from pathlib import Path
from typing import Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator

STRICT = ConfigDict(extra="forbid", str_strip_whitespace=True)


class FailureMode(BaseModel):
    model_config = STRICT

    code: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    effect: str = Field(min_length=1)
    source_refs: list[str] = Field(min_length=1)


class AgentControlProfile(BaseModel):
    model_config = STRICT

    agent_id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    title: str = Field(min_length=1)
    responsibility: str = Field(min_length=1)
    boundaries: list[str] = Field(min_length=1)
    source_module: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    required_slots: list[str]
    optional_slots: list[str]
    required_any_slots: list[list[str]]
    output_contracts: list[str] = Field(min_length=1)
    result_kinds: list[str]
    artifact_channels: list[str]
    tool_modules: list[str]
    external_systems: list[str]
    hitl_contracts: list[str] = Field(min_length=1)
    failure_modes: list[FailureMode] = Field(min_length=1)
    source_refs: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def slots_do_not_overlap(self):
        if set(self.required_slots) & set(self.optional_slots):
            raise ValueError("required_slots and optional_slots must not overlap")
        declared = set(self.required_slots) | set(self.optional_slots)
        if any(
            not group or not set(group) <= declared
            for group in self.required_any_slots
        ):
            raise ValueError(
                "required_any_slots must be non-empty subsets of declared slots"
            )
        return self


class RegistryIssue(BaseModel):
    model_config = STRICT

    agent_id: str
    code: str
    source_ref: str = ""


def validate_agent_registry(
    *,
    profiles: Mapping[str, AgentControlProfile],
    agent_slot_rules: Mapping[str, dict],
    graph_nodes: set[str],
    state_fields: set[str],
    result_kinds: set[str],
    hitl_contract_ids: set[str],
    module_root: Path,
) -> list[RegistryIssue]:
    issues: list[RegistryIssue] = []
    for agent_id in sorted(set(agent_slot_rules) - set(profiles)):
        issues.append(RegistryIssue(agent_id=agent_id, code="missing_profile"))
    for agent_id in sorted(set(profiles) - set(agent_slot_rules)):
        issues.append(RegistryIssue(agent_id=agent_id, code="unknown_profile"))

    for agent_id, profile in sorted(profiles.items()):
        rules = agent_slot_rules.get(agent_id) or {}
        allowed = set(rules.get("allowed") or [])
        required = set(rules.get("required") or [])
        required_any = sorted(
            sorted(group) for group in (rules.get("required_any") or [])
        )
        if (
            set(profile.required_slots) != required
            or set(profile.optional_slots) != allowed - required
            or sorted(sorted(group) for group in profile.required_any_slots)
            != required_any
        ):
            issues.append(RegistryIssue(agent_id=agent_id, code="slot_mismatch"))
        if agent_id not in graph_nodes:
            issues.append(RegistryIssue(agent_id=agent_id, code="missing_graph_node"))
        if not (module_root / f"{profile.source_module}.py").is_file():
            issues.append(
                RegistryIssue(agent_id=agent_id, code="missing_source_module")
            )
        for module in profile.tool_modules:
            if not (module_root / f"{module}.py").is_file():
                issues.append(
                    RegistryIssue(
                        agent_id=agent_id,
                        code="missing_tool_module",
                        source_ref=module,
                    )
                )
        for field in profile.artifact_channels:
            if field not in state_fields:
                issues.append(
                    RegistryIssue(
                        agent_id=agent_id,
                        code="missing_artifact_channel",
                        source_ref=field,
                    )
                )
        for kind in profile.result_kinds:
            if kind not in result_kinds:
                issues.append(
                    RegistryIssue(
                        agent_id=agent_id,
                        code="invalid_result_kind",
                        source_ref=kind,
                    )
                )
        for hitl in profile.hitl_contracts:
            if hitl not in hitl_contract_ids:
                issues.append(
                    RegistryIssue(
                        agent_id=agent_id,
                        code="invalid_hitl_contract",
                        source_ref=hitl,
                    )
                )
        refs = [
            *profile.source_refs,
            *(
                ref
                for failure in profile.failure_modes
                for ref in failure.source_refs
            ),
        ]
        for ref in refs:
            file_name, separator, line_text = ref.rpartition(":")
            if not separator or not line_text.isdigit():
                file_name = ref
                line_text = ""
            source_path = module_root / file_name
            if not source_path.is_file():
                issues.append(
                    RegistryIssue(
                        agent_id=agent_id,
                        code="missing_source_ref",
                        source_ref=ref,
                    )
                )
            elif line_text and not 1 <= int(line_text) <= len(
                source_path.read_text(encoding="utf-8").splitlines()
            ):
                issues.append(
                    RegistryIssue(
                        agent_id=agent_id,
                        code="invalid_source_line",
                        source_ref=ref,
                    )
                )
    return sorted(
        issues, key=lambda item: (item.agent_id, item.code, item.source_ref)
    )


def _profile(
    agent_id: str,
    title: str,
    responsibility: str,
    boundary: str,
    source_module: str,
    required_slots: list[str],
    optional_slots: list[str],
    output_contracts: list[str],
    result_kinds: list[str],
    artifact_channels: list[str],
    tool_modules: list[str],
    external_systems: list[str],
    hitl_contracts: list[str],
    failure_code: str,
    failure_effect: str,
    failure_ref: str,
    source_ref: str,
    required_any_slots: list[list[str]] | None = None,
) -> AgentControlProfile:
    return AgentControlProfile(
        agent_id=agent_id,
        title=title,
        responsibility=responsibility,
        boundaries=[boundary],
        source_module=source_module,
        required_slots=required_slots,
        optional_slots=optional_slots,
        required_any_slots=required_any_slots or [],
        output_contracts=output_contracts,
        result_kinds=result_kinds,
        artifact_channels=artifact_channels,
        tool_modules=tool_modules,
        external_systems=external_systems,
        hitl_contracts=hitl_contracts,
        failure_modes=[
            FailureMode(
                code=failure_code,
                effect=failure_effect,
                source_refs=[failure_ref],
            )
        ],
        source_refs=[f"{source_module}.py", source_ref],
    )


RESULT_AND_ARTIFACT = [
    "contracts/result-envelope",
    "contracts/artifact-delivery",
]

AGENT_CONTROL_PROFILES = {
    "yield_agent": _profile(
        "yield_agent", "Yield Agent",
        "Queries lot yield data and produces yield summaries and visual artifacts.",
        "Does not retrieve WADS, map, or failure-history records.",
        "yield_query_agent", ["lotcd"],
        ["periods", "ref_date", "time_range", "unit"],
        RESULT_AND_ARTIFACT, ["table", "summary"], ["yield_artifacts"],
        ["yield_db", "yield_viz"], ["Oracle", "LLM"],
        ["missing_param", "plan_review"], "lot_data_empty",
        "Returns an empty ResultEnvelope when requested lot data is unavailable.",
        "yield_query_agent.py:212", "canonical_request.py:6",
    ),
    "wads_agent": _profile(
        "wads_agent", "WADS Agent",
        "Retrieves and summarizes WADS degradation reports.",
        "Does not render wafer maps or execute downstream failure analysis.",
        "wads_agent", [],
        ["fail_type", "lotcd", "wads_category", "wads_end_tm", "wads_start_tm"],
        RESULT_AND_ARTIFACT, ["table", "report", "summary"], ["wads_artifacts"],
        ["wads_tools"], ["Oracle", "LLM"],
        ["plan_review", "task_confirm", "postwads_choice"], "query_failure",
        "Returns an error ResultEnvelope for a non-transient WADS failure.",
        "wads_agent.py:628", "canonical_request.py:14",
    ),
    "map_agent": _profile(
        "map_agent", "Map Agent",
        "Queries wafer map data and renders map or cumulative-map artifacts.",
        "Does not perform WADS degradation detection or lot-history retrieval.",
        "map_agent", ["map_oper"],
        ["groupkey", "lot_ids", "map_groups", "map_label", "map_type", "wf_ids", "wf_mod", "wf_rem"],
        RESULT_AND_ARTIFACT, ["image", "summary"], ["map_artifacts"], [],
        ["Oracle"], ["missing_param", "plan_review", "postwads_choice"],
        "render_empty", "Returns an empty summary when no map artifact is produced.",
        "map_agent.py:1035", "canonical_request.py:20", [["lot_ids", "groupkey"]],
    ),
    "fail_history_agent": _profile(
        "fail_history_agent", "Fail History Agent",
        "Retrieves and synthesizes failure-history documents.",
        "Does not query wafer maps or calculate yield metrics.",
        "fail_history_agent", [],
        ["cause_oper", "dh_query", "fail_groups", "fail_type", "lotcd"],
        RESULT_AND_ARTIFACT, ["document", "summary"], ["fail_history_artifacts"],
        ["fail_history_tools"], ["OpenSearch"], ["plan_review", "postwads_choice"],
        "search_failure", "Returns an error ResultEnvelope for a permanent search failure.",
        "fail_history_agent.py:312", "canonical_request.py:25",
    ),
    "lot_history_agent": _profile(
        "lot_history_agent", "Lot History Agent",
        "Queries lot movement history and derives common-process insights.",
        "Does not choose WADS parameters or render wafer maps.",
        "lot_history_agent", ["lot_ids"], [], RESULT_AND_ARTIFACT,
        ["table", "summary"], ["lot_history_artifacts"], ["lot_history_tools"],
        ["Oracle"], ["missing_param", "plan_review"], "query_failure",
        "Returns an error ResultEnvelope for a permanent lot-history failure.",
        "lot_history_agent.py:819", "canonical_request.py:30",
    ),
    "relation_tree_agent": _profile(
        "relation_tree_agent", "Relation Tree Agent",
        "Finds main-operation candidates for a lot and failure parameter.",
        "Does not execute the downstream WT response analysis.",
        "relation_tree_agent", ["fail_type", "lotcd"], ["cause_oper", "rt_groups"],
        RESULT_AND_ARTIFACT, ["report"], ["relation_tree_artifacts"], [],
        ["Oracle"], ["missing_param", "plan_review", "task_confirm", "postwads_choice"],
        "lot_missing", "Skips relation analysis when no lot code is available.",
        "relation_tree_agent.py:170", "canonical_request.py:34",
    ),
    "mining_agent": _profile(
        "mining_agent", "Mining Agent",
        "Runs GINI mining analysis over prepared good and bad lot groups.",
        "Does not create the upstream WADS or relation-tree groups.",
        "mining_agent", [],
        ["cause_oper", "fail_type", "group_bad", "group_good", "lotcd", "rank_limit", "tech", "user_id", "wads_category"],
        RESULT_AND_ARTIFACT, ["table", "summary"], ["mining_artifacts"], [],
        ["Mining API", "LLM"], ["plan_review", "postwads_choice"],
        "analysis_failure", "Returns an error ResultEnvelope for a permanent mining failure.",
        "mining_agent.py:524", "canonical_request.py:40",
    ),
    "wt_resp_agent": _profile(
        "wt_resp_agent", "WT Response Agent",
        "Builds WT response groups for a selected lot, parameter, and main operation.",
        "Does not select the main operation or run the subsequent mining analysis.",
        "wt_resp_agent", ["cause_oper", "fail_type", "lotcd"], [],
        ["contracts/result-envelope"], ["report"], [], [], ["Oracle"],
        ["missing_param", "plan_review", "task_confirm"], "required_input_missing",
        "Skips WT response analysis when a required input is absent.",
        "wt_resp_agent.py:94", "canonical_request.py:45",
    ),
    "ppt_export": _profile(
        "ppt_export", "PPT Export Agent",
        "Builds a PowerPoint file from accumulated analysis artifacts.",
        "Does not attach a ResultEnvelope or perform analytical queries.",
        "ppt_export_agent", [], [], ["contracts/artifact-delivery"], [],
        ["ppt_artifacts"], [], ["Local filesystem"], ["plan_review"],
        "generation_failure", "Returns no artifact when PowerPoint generation fails.",
        "ppt_export_agent.py:93", "canonical_request.py:51",
    ),
}
