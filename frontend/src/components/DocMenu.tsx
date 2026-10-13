import { useEffect, useRef, useState } from 'react'
import { useApp } from '../store'
import type { ArtifactKind } from '../types'
import { tr } from '../i18n'

export const ARTIFACTS: { kind: ArtifactKind; label: string; hint: string }[] = [
  { kind: 'memo', label: tr('Служебная записка'), hint: tr('суть, обоснование, решения, что просим') },
  { kind: 'brief', label: tr('Бриф'), hint: tr('цель, договорённости, сроки, ответственные') },
  { kind: 'followup', label: tr('Письмо участникам'), hint: tr('черновик: итоги и задачи по людям') },
]

/** «Документ ▾»: служебная записка, бриф или письмо участникам по записи, проекту или человеку. Каждый пункт — с таймкодом записи. */
export function DocMenu({ scope, className = 'btn s34', up = false }: {
  scope: { meetingId?: string; projectId?: string; personId?: string; title: string }; className?: string; up?: boolean
}) {
  const { setMaterial } = useApp()
  const [open, setOpen] = useState(false)
  const root = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => { if (!root.current?.contains(e.target as Node)) setOpen(false) }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => { document.removeEventListener('mousedown', onDown); document.removeEventListener('keydown', onKey) }
  }, [open])
  return (
    <div className="export-menu" ref={root}>
      <button className={className} aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen(v => !v)}>{tr('Документ ▾')}</button>
      {open && (
        <div className={'export-list wide' + (up ? ' up' : '')} role="menu">
          {ARTIFACTS.map(a => (
            <button key={a.kind} role="menuitem" onClick={() => { setOpen(false); setMaterial({ kind: a.kind, ...scope }) }}>
              <b>{a.label}</b><span>{a.hint}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
