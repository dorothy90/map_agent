// PB:674–680: disabled capability boundary; upstream public parameters retained. No automatic I/O.

export function useHudComposerDrag(
  enabled: boolean,
  { controlDrag = false, workspaceTransfer = false }: HudComposerDragOptions = {}
) { return { grabbing: false, onPointerDown: (_event: React.PointerEvent<HTMLElement>) => {} } }
interface HudComposerDragOptions {
  /** X11 escape hatch: Ctrl+primary-button grabs immediately instead of
   *  competing with Chromium's text-selection drag until the hold timer. */
  controlDrag?: boolean
  /** X11/KWin only: keep the grabbed window visible while the user changes
   *  virtual desktops, then pin it to the destination desktop on release. */
  workspaceTransfer?: boolean
}
