// Shim for H/apps/desktop/src/store/updates.ts:183-251; PB:674,680,D10. UI/native effects disabled.
export function reportBackendContract(_contract: number | undefined, _protocol?: string): void {}
export function reportInstallMethodWarning(_message: string | undefined): void {}
// Original updates.ts:395; opening a native update window is unavailable.
export function openUpdatesWindow(_target?: unknown): void {}
