// PB:674–680: disabled capability boundary; upstream public parameters retained. No automatic I/O.

export interface LocalDeviceTarget {
  connectionId: string
  label: string
  profile?: string
  /** The switch replaces the center with a fresh session, even when Hermes is already installed. */
  replaceCenter?: boolean
}
export function useLocalDeviceSwitch() { return { dialog: null, request: async (target: LocalDeviceTarget): Promise<boolean> => false } }
