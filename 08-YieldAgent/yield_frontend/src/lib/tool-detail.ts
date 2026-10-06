import type { CanvasCard, ExecStep } from "@/types";

export interface ToolObservation {
  run_id: string;
  session_id: string;
  invocation_id: string;
  result_id: string;
  tool_name: string;
  status: ExecStep["state"];
  summary: string;
  validated_arguments: Record<string, unknown>;
  error?: Record<string, unknown> | null;
  provenance?: { elapsed_seconds?: number; parent_invocation_id?: string };
  tables: { table_id: string; title: string; columns: string[]; total_rows: number;
    preview_rows: Record<string, unknown>[]; units: Record<string, string>; complete: boolean; missing_reason?: string | null }[];
  artifact_refs: { artifact_id: string }[];
}

export function findObservation(observations: ToolObservation[], runId: string, invocationId: string) {
  return observations.find(o => o.run_id === runId && o.invocation_id === invocationId);
}

export function resultCards(observation: ToolObservation, cards: CanvasCard[]): CanvasCard[] {
  const artifactIds = new Set(observation.artifact_refs.map(ref => ref.artifact_id));
  return [
    ...observation.tables.filter(t => t.columns.length || t.total_rows || t.missing_reason).map(table => ({
      id: `${observation.result_id}:${table.table_id}`, agent: observation.tool_name,
      title: table.title, artifactType: "table" as const, mime: "application/json",
      data: JSON.stringify({ ...table, result_id: observation.result_id, session_id: observation.session_id }),
    })),
    ...cards.filter(card => artifactIds.has(card.id)),
  ];
}

export function observationSteps(observations: ToolObservation[]): ExecStep[] {
  return observations.map(o => ({ id: `${o.run_id}:${o.invocation_id}`, runId: o.run_id,
    invocationId: o.invocation_id, node: o.tool_name, state: o.status, detail: o.summary,
    elapsed: o.provenance?.elapsed_seconds || 0, parentInvocationId: o.provenance?.parent_invocation_id }));
}
