// Disabled native log shim for H/apps/desktop/src/lib/identity-trace.ts:24-34.
// PB:D10,680: do not invoke Electron logLine or log connection identities.
export function traceIdentityChange(_channel: string, _key: string, _value: string): void {}
