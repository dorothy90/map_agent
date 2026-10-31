// Immutable upstream test imports its monorepo fixture by a relative path.
// Vitest maps that path to the byte-identical local fixture; preserve its JSON structure for tsc.
declare module "*tests/fixtures/session-resume-active-turn.json" {
 const fixture: { "info": { "cwd": string; "lazy": boolean; "model": string; "skills": {  }; "tools": {  } }; "inflight": { "assistant": string; "streaming": boolean; "user": string }; "message_count": number; "messages": ({ "role": string; "text": string })[]; "messages_omitted": boolean; "resumed": string; "running": boolean; "session_id": string; "session_key": string; "started_at": number; "status": string; "turn_started_at": number }
 export default fixture
}
