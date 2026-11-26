'use client'

import { Button } from '@/components/ui/button'
import { useI18n } from '@/i18n'
import { Loader2 } from '@/lib/icons'

export function ClarifyConfirmBar({
  canConfirm,
  disabled,
  onSkip,
  submitting
}: {
  canConfirm: boolean
  disabled: boolean
  onSkip: () => void
  submitting: boolean
}) {
  const { t } = useI18n()
  const copy = t.assistant.clarify

  return (
    <div className="web-clarify-actions flex items-center justify-end gap-1.5">
      <Button className="web-clarify-skip" disabled={disabled} onClick={onSkip} size="xs" type="button" variant="text">
        {copy.skip}
      </Button>
      <Button className="web-clarify-submit" disabled={disabled || !canConfirm} size="xs" type="submit">
        {submitting ? (
          <Loader2 className="animate-spin" />
        ) : (
          copy.confirmAndContinueLabel
        )}
      </Button>
    </div>
  )
}
