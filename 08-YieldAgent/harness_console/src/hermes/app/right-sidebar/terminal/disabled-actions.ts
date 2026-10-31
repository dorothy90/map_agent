// PB:680: upstream terminals.ts command signatures; native terminal disabled.
export function openAgentTerminal(procId: string, title: string): void {}
export function closeAgentTerminalByProc(procId: string): boolean { return false }

// H/terminal/buffer.ts read contract; xterm implementation is not imported.
export interface TerminalReadOptions { start?: number; count?: number }
export interface TerminalReadResult { total_lines: number; start: number; end: number; viewport_rows: number; cursor_row: number; text: string }
export function readActiveTerminal(opts: TerminalReadOptions = {}): TerminalReadResult | null { return null }
