import type { KeyboardEvent } from 'react'

/** Use the form's enabled submit button so validation and pending state agree. */
export function handleClarifySubmitShortcut(event: KeyboardEvent<HTMLFormElement>): void {
  const inTextarea = event.target instanceof HTMLTextAreaElement
  const modSubmit = event.metaKey || event.ctrlKey

  if (
    event.key !== 'Enter' ||
    (!modSubmit && !inTextarea) ||
    event.altKey ||
    event.shiftKey ||
    event.nativeEvent.isComposing ||
    event.keyCode === 229 ||
    event.defaultPrevented
  ) {
    return
  }

  // Capture before a focused choice toggles or a textarea handles plain Enter.
  event.preventDefault()
  event.stopPropagation()

  const submit = event.currentTarget.querySelector<HTMLButtonElement>('button[type="submit"]')

  if (submit && !submit.disabled && !event.repeat) {
    event.currentTarget.requestSubmit(submit)
  }
}
