import { useEffect, useRef, useState } from 'react'
import { downloadFile, errorText } from '../api'
import { useApp } from '../store'
import { tr } from '../i18n'

export const DOC_FORMATS = [
  { id: 'pdf', label: 'PDF', hint: tr('для печати и отправки') },
  { id: 'docx', label: 'Word (.docx)', hint: tr('редактируется в Word и Pages') },
  { id: 'html', label: 'HTML', hint: tr('одна страница, без интернета') },
  { id: 'md', label: 'Markdown (.md)', hint: tr('как в Obsidian') },
]

/** «Скачать» с выбором формата: сервер собирает PDF, DOCX, HTML или Markdown из текста документа. */
export function ExportMenu({ endpoint, body, label = tr('Скачать'), className = 'btn s34', up = false, formats = DOC_FORMATS }: {
  formats?: { id: string; label: string; hint: string }[]; endpoint: string; body: () => Record<string, unknown>; label?: string; className?: string; up?: boolean
}) {
  const { showToast } = useApp()
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState('')
  const root = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => { if (!root.current?.contains(e.target as Node)) setOpen(false) }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => { document.removeEventListener('mousedown', onDown); document.removeEventListener('keydown', onKey) }
  }, [open])

  const run = async (format: string) => {
    setBusy(format)
    try {
      const name = await downloadFile(endpoint, { ...body(), format })
      setOpen(false)
      showToast(tr('Файл сохранён: ') + name)
    } catch (e) { showToast(errorText(e)) } finally { setBusy('') }
  }
  return (
    <div className="export-menu" ref={root}>
      <button className={className} aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen(v => !v)}>{busy ? tr('Собираю…') : label}</button>
      {open && (
        <div className={'export-list' + (up ? ' up' : '')} role="menu">
          {formats.map(f => (
            <button key={f.id} role="menuitem" disabled={!!busy} onClick={() => void run(f.id)}>
              <b>{f.label}</b><span>{f.hint}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
