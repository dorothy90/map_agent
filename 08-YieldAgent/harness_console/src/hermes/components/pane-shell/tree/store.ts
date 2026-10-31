// PB:674–680: pane layout disabled. Upstream public parameters/types retained; no native, network, persistence or listeners.
import { atom, computed, type ReadableAtom } from 'nanostores'
import type { LayoutNode, GroupNode, SplitNode, DropPosition, TabStripMode } from './model'
export const $layoutTree = atom<LayoutNode | null>(null)
export const $activePresetId = atom('default')
export function markActivePreset(id: string) {}
export const $treeDragging = atom<string | null>(null)
export const SESSION_TILE_DRAG = '__session-tile-drag__'
export const NEW_SESSION_DRAG = '__new-session-drag__'
export const $hiddenTreePanes = atom<ReadonlySet<string>>(new Set())
export function setTreePaneHidden(paneId: string, hidden: boolean) {}
export const $dismissedPanes = atom<ReadonlySet<string>>(new Set())
export function undismissTreePanes(paneIds: Iterable<string>): void {}
export function beginLayoutHydration(): void {}
export function endLayoutHydration(): void {}
export const $paneShareRecords = atom<Record<string, number>>({})
export const $hiddenStripTabs = atom<ReadonlySet<string>>(new Set())
export function isStripTabHidden(paneId: string): boolean { return false }
export function setStripTabHidden(paneId: string, hidden: boolean): boolean { return false }
export const $panesWithCloser = atom<ReadonlySet<string>>(new Set())
export function registerPaneCloser(paneId: string, close?: () => void) {}
export function registerPaneOpener(paneId: string, open: () => void) {}
export function markCollapsePane(paneId: string) {}
export function isCollapsePane(paneId: string): boolean { return false }
export function registerLayoutResetHandler(fn: () => void): () => void { return () => {} }
export const $activeTreeGroup = atom<null | string>(null)
export function noteActiveTreeGroup(groupId: null | string) {}
export const $hoveredTreeGroup = atom<null | string>(null)
export function noteHoveredTreeGroup(groupId: null | string) {}
export function trackActiveTreeGroup(): () => void { return () => {} }
export const isHideOnlyPane = (paneId: string): boolean => false
export const isSessionStripPane = (paneId: string): boolean => false
export const isMainStripPane = (paneId: string): boolean => false
export const hostsSessionDropTarget = (paneIds: readonly string[]): boolean => false
export function focusedSessionTabAnchor(): null | string { return null }
export function closeFocusedSessionTab(): boolean { return false }
export function closeToolPane(paneId: string) {}
export function closeFocusedToolTab(): boolean { return false }
export function treeTabCloseTargets(paneId: string): { all: number; others: number; right: number } { return { all: 0, others: 0, right: 0 } }
export const $treePaneEpochs = atom<Readonly<Record<string, number>>>({})
export function reloadTreePane(paneId: string): void {}
export function closeTabPane(paneId: string) {}
export function closeOtherTreeTabs(paneId: string): void {}
export function closeTreeTabsToRight(paneId: string): void {}
export function closeAllTreeTabs(paneId: string): void {}
export function hideOnlyZoneTabs(groupId: string): { hidden: boolean; id: string; title: string }[] { return [] }
export function treePanesWithPrefix(prefix: string): string[] { return [] }
export const $newSessionTabAction = atom<(() => void) | null>(null)
export function shownPanesInGroup(group: { panes: readonly string[] }): string[] { return [] }
export function tabStripVisibleForGroup(group: GroupNode): boolean { return false }
export function treeTabSlotTarget(): GroupNode | null { return null }
export function activateTreeTabSlot(slot: number): null | string { return null }
export function cycleTreeTabInFocusedZone(direction: 1 | -1): null | string { return null }
export function removeTreePane(paneId: string) {}
export function rootRow(): SplitNode | null { return null }
export function paneRootSide(paneId: string): null | TreeSide { return null }
export function dismissTreePane(paneId: string) {}
export function closeTreePane(paneId: string) {}
export type TreeSide = 'left' | 'right'
export const $collapsedTreeSides = atom<ReadonlySet<TreeSide>>(new Set())
export function setTreeSideCollapsed(side: TreeSide, collapsed: boolean) {}
export function restoreHiddenTreeSideTabs(side: TreeSide): void {}
export function restoreMinimizedTreeSide(side: TreeSide): boolean { return false }
export function layoutHasRootSide(side: TreeSide): boolean { return false }
export function bindTreeSideVisibility(
  side: TreeSide,
  $open: { get(): boolean; listen(fn: (open: boolean) => void): void },
  setOpen: (open: boolean) => void
) {}
export function treeSideOfPane(paneId: string): TreeSide | null { return null }
export function revealTreePane(paneId: string) {}
export const $narrowViewport = atom(false)
export function mirrorLayoutTree() {}
export interface DropHint {
  kind: 'group'
  /** The zone a drop will land in (ClosestCenter among `groupIds`). */
  groupId?: string
  /** Full highlighted set (multi-zone when Shift extends the range). */
  groupIds?: string[]
  pos?: DropPosition
  /** Hovering the target's TAB STRIP: the drop stacks at a specific slot —
   *  before this pane id, or at the end (`before: null`). The strip renders
   *  the insertion divider; the zone sheet stands down. */
  stack?: { before: null | string }
}
export const $dropHint = atom<DropHint | null>(null)
export const $sessionTileDragging = computed($treeDragging, dragging => dragging === SESSION_TILE_DRAG)
export const $sessionTileEdgeHover = computed(
  [$treeDragging, $dropHint],
  (dragging, hint) =>
    dragging === SESSION_TILE_DRAG && ((hint?.pos !== undefined && hint.pos !== 'center') || hint?.stack !== undefined)
)
export function declareDefaultTree(tree: LayoutNode, simpleTree: LayoutNode = tree) {}
export function resetEnforcedDocks(): void {}
export function adoptContributedPanes(): void {}
export function watchContributedPanes(): void {}
export function hydrateContributedPanes(): void {}
export const $userPlacedPanes = atom<ReadonlySet<string>>(new Set())
export function dockPaneBeside(paneId: string, anchorPaneId: string) {}
export function moveTreePane(paneId: string, target: { groupId: string; pos: DropPosition; before?: null | string }) {}
export function applyTree(tree: LayoutNode, presetId: string, resting: readonly string[] = []) {}
export function moveTreePanes(
  paneIds: readonly string[],
  target: { groupId: string; pos: DropPosition; before?: null | string },
  activeId?: string
) {}
export function mergeTreeZones(groupIds: string[], paneId: string | readonly string[], fallbackGroupId: null | string) {}
export function activateTreePane(groupId: string, paneId: string) {}
export function reorderTreePanes(groupId: string, paneIds: readonly string[], toIndex: number) {}
export function setTreeGroupMinimized(groupId: string, minimized: boolean) {}
export function setPaneCollapsed(paneId: string, collapsed: boolean) {}
export function restoreTreePane(paneId: string) {}
export function isPaneVisible(paneId: string): boolean { return false }
export function $paneVisible(paneId: string): ReadableAtom<boolean> { return atom(false) }
export function bindPaneVisibility(
  paneId: string,
  $open: { get(): boolean; listen(fn: (open: boolean) => void): void },
  close?: () => void,
  open?: () => void
) {}
export function bindToolPaneCollapse(
  paneId: string,
  $open: { get(): boolean; listen(fn: (open: boolean) => void): void },
  close: () => void,
  open: () => void,
  $rail?: { get(): boolean; listen(fn: (rail: boolean) => void): void }
) {}
export function togglePaneVisible(paneId: string) {}
export function collapseTreePane(paneId: string) {}
export function setTreeGroupTabStrip(groupId: string, tabStrip: TabStripMode | undefined) {}
export function targetZoneTabStripVisible(): boolean | null { return null }
export function toggleTargetZoneTabStrip(): TabStripMode | null { return null }
export function setTreeSplitWeights(splitId: string, weights: number[]) {}
export function presetSplitWeights(splitId: string, length: number): number[] | null { return null }
export function persistTree() {}
export function resetLayoutTree() {}
