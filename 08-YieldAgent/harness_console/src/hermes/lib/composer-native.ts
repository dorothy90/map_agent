// PB:680: native attachment persistence disabled. Signatures: H/global.d.ts:360–369.
export const composerNative = {
  saveImageBuffer: async (_data: ArrayBuffer | Uint8Array, _ext: string, _name?: string): Promise<string> => '',
  savePastedText: async (_text: string): Promise<string> => '',
  saveClipboardImage: async (): Promise<string> => '',
  getPathForFile: (_file: File): string => ''
}
