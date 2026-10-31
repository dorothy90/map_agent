// PB:674–680: disabled capability boundary; upstream public parameters retained. No automatic I/O.

export interface LayoutPickerCardProps {
  /** Answers with the staged preset id; true once sent. */
  onContinue: (preset: string) => boolean
}
export const FIRST_RUN_LAYOUTS = [
  { id: 'sidebar-left', name: 'basic', detail: 'basicDetail' },
  { id: 'terminal-deck', name: 'elite', detail: 'eliteDetail' }
] as const
export function LayoutPickerCard({ onContinue }: LayoutPickerCardProps) { return null }
