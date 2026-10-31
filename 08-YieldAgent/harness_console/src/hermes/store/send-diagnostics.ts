// PB:680: diagnostics upload/native log collection disabled.
import { atom } from 'nanostores'
export interface SendDiagnosticsResult {
  expiresAt?: string
  uploadId?: string
  viewUrl?: string
}
export interface SendDiagnosticsState {
  /** Short text describing the failure that prompted the report (attached
   *  to the bundle as error-context.txt, redacted server-side). */
  errorContext?: string
  error?: string
  phase: 'consent' | 'done' | 'error' | 'uploading'
  result?: SendDiagnosticsResult
}
export const $sendDiagnostics = atom<SendDiagnosticsState | null>(null)
export function requestSendDiagnostics(errorContext?: string): void {}
export function dismissSendDiagnostics(): void {}
export async function confirmSendDiagnostics(): Promise<void> {}
