import { nextTick, type Ref } from 'vue'

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'

export function useModalFocus(
  open: Ref<boolean>,
  dialog: Ref<HTMLElement | undefined>,
  trigger: Ref<HTMLElement | undefined>,
) {
  async function show() {
    open.value = true
    await nextTick()
    focusable()[0]?.focus()
  }

  async function hide() {
    open.value = false
    await nextTick()
    trigger.value?.focus()
  }

  function focusable(): HTMLElement[] {
    return dialog.value ? Array.from(dialog.value.querySelectorAll<HTMLElement>(FOCUSABLE)) : []
  }

  function onKeydown(event: KeyboardEvent) {
    if (event.key === 'Escape') {
      event.preventDefault()
      void hide()
      return
    }
    if (event.key !== 'Tab') return
    const items = focusable()
    if (!items.length) return
    const first = items[0]
    const last = items[items.length - 1]
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault()
      last.focus()
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault()
      first.focus()
    }
  }

  return { show, hide, onKeydown }
}
