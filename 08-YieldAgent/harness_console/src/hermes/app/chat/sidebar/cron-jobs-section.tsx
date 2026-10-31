// PB:674–680: disabled capability boundary; upstream public parameters retained. No automatic I/O.
import { type SessionInfo } from '@/hermes'
import type { CronJob } from '@/types/hermes'
export function SidebarCronJobsSection({
  jobs,
  label,
  max = 50,
  onManageJob,
  onOpenRun,
  onTriggerJob,
  onToggle,
  open
}: SidebarCronJobsSectionProps) { return null }
interface SidebarCronJobsSectionProps {
  jobs: CronJob[]
  label: string
  max?: number
  // Open a run session's chat (1 click to output). The run ROW rides along so
  // the open can pin its owning (connection, profile) — the same owner-aware
  // door every other session-list row uses. A run the scheduler never closed
  // opens view-only — see `openCronRun` (#88443).
  onOpenRun: (sessionId: string, session?: SessionInfo) => void
  // Open the full Cron page focused on this job (manage / full history).
  onManageJob: (jobId: string) => void
  // Fire the job now.
  onTriggerJob: (jobId: string) => Promise<void>
  onToggle: () => void
  open: boolean
}
