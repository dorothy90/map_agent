// ──────────────────────────────────────────────────────────────────────────
// agent_server(:8001) 의 실제 SSE 프로토콜(models.py)을 그대로 따른다.
// artifact.data 는 도구가 사전 렌더한 HTML/markdown/image 문자열 또는 pptx 다운로드 경로.
// (이전 목업의 구조화 JSON 프로토콜은 폐기 — 실제 백엔드는 HTML 문자열을 보낸다.)
// ──────────────────────────────────────────────────────────────────────────

// ── 실제 SSE 이벤트 (models.py) ─────────────────────────────────────────────

export type ArtifactType = "html" | "image" | "markdown" | "pptx";

export interface RealArtifact {
  artifact_id: string;
  artifact_type: ArtifactType;
  mime: string;
  title: string;
  agent: string;
  data: string; // HTML/markdown/image 문자열, 또는 pptx 다운로드 경로
}

// HITL 선택지 — 백엔드가 문자열(예: ["예","아니오"], 모호 슬롯 후보값) 또는
// dict({value,label,hint}) 둘 다 보낼 수 있다.
export type HitlOption = string | { value?: string; label?: string; hint?: string };

// HITL missing_param 배치에서 수집할 슬롯 1개.
// type:"choice" + options 가 있으면 자유입력 대신 후보 선택(시나리오2 모호 슬롯).
export interface HitlField {
  slot: string;
  label?: string;
  type?: string;
  required_any_group?: string;
  validation_hint?: string;
  options?: HitlOption[];
}

export interface InterruptPayload {
  interrupt_type: string; // "missing_param" | "plan_review" | "yield_wads_followup" | ...
  param: string; // 대표 슬롯명(관측성). 권위는 fields.
  message: string; // 사용자에게 보여줄 한국어 메시지
  route: string;
  options: HitlOption[];
  fields: HitlField[];
}

export type RealStreamEvent =
  | { type: "stream_start"; session_id: string; query: string }
  | { type: "node_complete"; node: string; step: number; elapsed: number }
  | { type: "message"; role: "assistant"; agent: string; content: string; step: number }
  | { type: "token"; content: string; agent: string; node: string }
  | { type: "thinking"; content: string; agent: string; node: string }
  | { type: "status"; message: string; node: string }
  | ({ type: "artifact" } & RealArtifact & { step: number })
  | { type: "suggestion"; content: string; step: number }
  | ({ type: "interrupt" } & InterruptPayload)
  | { type: "error"; message: string; node: string }
  | { type: "stream_end"; total_steps: number; elapsed: number };

// ── UI 상태 모델 ────────────────────────────────────────────────────────────

// 좌측 멀티에이전트 실행 타임라인의 한 스텝 (node_complete/status 누적).
export interface ExecStep {
  id: string;
  node: string;
  elapsed: number;
  detail?: string; // status 메시지
}

export type ChatItem =
  | { kind: "user"; text: string }
  | { kind: "assistant"; agent: string; text: string; streaming: boolean }
  | { kind: "thinking"; text: string }
  | { kind: "interrupt"; payload: InterruptPayload; answered?: string }
  | { kind: "suggestion"; text: string }
  | { kind: "error"; text: string };

export interface CanvasCard {
  id: string;
  agent: string;
  title: string;
  artifactType: ArtifactType;
  mime: string;
  data: string;
}
