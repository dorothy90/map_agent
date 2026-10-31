export type Data = Record<string, unknown>;
export interface StoredEvent { event_id: string; sequence: number; type: string; payload: Data }
export interface Field { slot: string; label?: string; type?: string; required?: boolean; required_any_group?: string; validation_hint?: string; options?: (string | {label: string; value: string})[] }
export interface Question { interrupt_id?: string; message?: string; answer?: string; fields?: Field[] }
export interface TableRef { table_id: string; title: string; columns: string[]; total_rows: number; complete: boolean; missing_reason?: string }
export interface ArtifactRef { artifact_id: string; title?: string; mime?: string }
export interface Observation { invocation_id: string; tool_name: string; result_id: string; validated_arguments?: Data; status: string; summary?: string; tables?: TableRef[]; artifact_refs?: ArtifactRef[]; scope?: Data; provenance?: Data; error?: Data }
export interface Run { run_id: string; session_id: string; query: string; status: string; answer?: string; stop_reason?: string; goal_revision: number; usage?: {tokens?: number; models?: number; active_seconds?: number}; question?: Question; observations?: Observation[] }
export interface Session { session_id: string; last_query: string; turn_count: number; updated_at: string }
export interface Tool { id: string; name: string; parent?: string; status: string; elapsed?: number; summary?: string; args?: Data; observation?: Observation; children: Tool[] }
export interface TablePage extends TableRef { rows: Data[]; offset: number; units: Record<string,string> }
