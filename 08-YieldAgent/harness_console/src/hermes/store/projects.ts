// PB:674–680: project/native workspace management disabled; all upstream exports/signatures retained.
import { atom } from 'nanostores'
import type { SidebarProjectTree } from '@/app/chat/sidebar/projects/workspace-groups'
import type { ProjectInfo } from '@/types/hermes'
import type { HermesGitBranch, HermesGitBaseBranch, NewSessionPlacement } from './project-boundary-types'
import { workspaceCwdForNewSession } from './session'
export { $projectTree } from './project-tree'
export const $projects = atom<ProjectInfo[]>([])
export const $activeProjectId = atom<null | string>(null)
export const $projectTreeLoading = atom(false)
export const $projectOwnerBySessionId = atom<ReadonlyMap<string, string>>(new Map())
export const $projectsRpcAvailable = atom<boolean | null>(false)
export const $reposScanning = atom(false)
export function enterProject(id: string): void {}
export const projectRootCwd = (project: SidebarProjectTree | undefined): string =>
  (project?.path || project?.repos.find(repo => repo.path)?.path || '').trim()
export function goToProject(id: string, options?: { newSession?: boolean }): void {}
export function resolveNewSessionCwd(): string { return workspaceCwdForNewSession() }
export function followEnteredProjectCwd(cwd: string): void {}
export function projectIdForCwd(cwd: string): null | string { return null }
export function projectNameForCwd(cwd: string): null | string { return null }
export async function followActiveSessionCwd(cwd: string): Promise<void> {}
export function projectProfile(): null | string { return null }
export async function refreshProjects(): Promise<void> {}
export async function refreshProjectTree(): Promise<void> {}
export async function fetchProjectSessions(
  projectId: string,
  { supersedable = true }: { supersedable?: boolean } = {}
): Promise<SidebarProjectTree | null> { return null }
export function applyRenamedSessionTitle(sessionId: string, title: string | null): void {}
export async function moveSessionToProject(
  sessionId: string,
  projectId: string,
  profile?: null | string
): Promise<void> {}
export interface RepoDiscoveryPolicy {
  enabled: boolean
  roots: string[]
  exclude_paths: string[]
}
export function repoDiscoveryPolicyFromConfig(config: unknown): RepoDiscoveryPolicy { return { enabled: false, roots: [], exclude_paths: [] } }
export function repoDiscoveryPolicySignature(policy: RepoDiscoveryPolicy): string { return '' }
export async function scanAndRecordRepos(force = false): Promise<void> {}
export interface CreateProjectInput {
  name: string
  folders?: string[]
  primaryPath?: string
  slug?: string
  description?: string
  icon?: string
  color?: string
  boardSlug?: string
  use?: boolean
  // Free-text project idea; written to IDEA.md at the primary folder on create.
  idea?: string
  /** Where a "New project" DRAG dropped the project (tab-strip slot / pane
   *  edge / pane center). The completion side opens the created project's
   *  fresh session draft exactly there; absent = the plain-click behavior. */
  dropPlacement?: NewSessionPlacement
}
export async function generateProjectIdea(name: string): Promise<string> { return '' }
export async function createProject(input: CreateProjectInput): Promise<ProjectInfo | null> { return null }
export async function renameProject(id: string, name: string): Promise<void> {}
export async function updateProject(
  id: string,
  patch: { name?: string; color?: null | string; icon?: null | string }
): Promise<void> {}
export async function setProjectAppearance(
  project: Pick<SidebarProjectTree, 'color' | 'icon' | 'id' | 'isAuto' | 'label' | 'path'>,
  patch: { color?: null | string; icon?: null | string }
): Promise<boolean> { return false }
export async function addProjectFolder(
  id: string,
  path: string,
  opts: { label?: string; isPrimary?: boolean } = {}
): Promise<void> {}
export async function deleteProject(id: string): Promise<void> {}
export async function setActiveProject(id: null | string): Promise<void> {}
export interface ProjectDialogState {
  mode: 'add-folder' | 'create' | 'rename'
  projectId?: string
  name?: string
}
export const $projectDialog = atom<null | ProjectDialogState>(null)
export function openProjectCreate(): void {}
export function clearNewProjectDropPlacement(): void {}
export function openProjectRename(project: { id: string; name: string }): void {}
export function openProjectAddFolder(project: { id: string; name: string }): void {}
export function closeProjectDialog(): void {}
export const $worktreeRefreshToken = atom(0)
export function refreshWorktrees(): void {}
export async function startWorkInRepo(
  repoPath: string,
  options?: { name?: string; branch?: string; base?: string; existingBranch?: string }
): Promise<null | { path: string; branch: string }> { return null }
export async function listRepoBranches(repoPath: string): Promise<HermesGitBranch[]> { return [] }
export async function listBaseBranches(repoPath: string): Promise<HermesGitBaseBranch[]> { return [] }
export async function switchBranchInRepo(repoPath: string, branch: string): Promise<void> {}
export interface StartWorkSessionRequest {
  draft?: string
  /** Stack the fresh session as a tab when main already holds a chat (palette/⌘O opens-from-nowhere). */
  openTab?: boolean
  path: string
  token: number
}
export const $startWorkSessionRequest = atom<StartWorkSessionRequest | null>(null)
export const $newProjectDropPlacement = atom<NewSessionPlacement | null>(null)
export interface NewProjectSessionRequest {
  /** The created project's root cwd — the fresh draft starts here. */
  path: string
  placement: NewSessionPlacement
}
export const $newProjectSessionRequest = atom<NewProjectSessionRequest | null>(null)
export interface WorktreeDialogState {
  repoPath: string
  /** The base branch selected in a "branch off from X" menu. */
  base?: string
}
export const $worktreeDialog = atom<null | WorktreeDialogState>(null)
export function closeWorktreeDialog(): void {}
export function requestStartWorkSession(path: string, draft?: string, options?: { openTab?: boolean }): void {}
export async function removeWorktreePath(
  repoPath: string,
  worktreePath: string,
  options?: { force?: boolean }
): Promise<void> {}
export async function revealPath(path: null | string): Promise<void> {}
export async function copyPath(path: null | string): Promise<void> {}
export async function pickProjectFolder(): Promise<null | string> { return null }
export async function pickProjectFolders(): Promise<string[]> { return [] }
export async function addProjectFolders(id: string, paths: string[]): Promise<void> {}
export async function openFolderAsProject(dir?: string): Promise<void> {}
