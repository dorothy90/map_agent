// PB:674–680: disabled capability boundary; upstream public parameters retained. No automatic I/O.

export interface AccentPickerCardProps {
  /** Answers with a swatch id, `custom:#rrggbb`, or null for the theme's own accent (Nous blue); true once sent. */
  onContinue: (accent: null | string) => boolean
}
export function AccentPickerCard({ onContinue }: AccentPickerCardProps) { return null }
