// PB:674–680: disabled capability boundary; upstream public parameters retained. No automatic I/O.

export function registerAgentTerminalWriter(procId: string, write: Writer): () => void { return () => {} }
export function writeAgentTerminalChunk(procId: string, chunk: string): void {}
export function seedAgentTerminalCommand(procId: string, command: string): void {}
export function syncAgentTerminalSnapshot(procId: string, output: string): void {}
type Writer = (chunk: string) => void
