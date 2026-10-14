import { useEffect, useRef, useState } from 'react'
import { api, errorText } from '../api'
import { useApp } from '../store'
import type { Segment } from '../types'
import { tr } from '../i18n'

interface Done { old: string; text: string; changed: boolean }

/** Правка текста одной реплики стенограммы на месте. Таймкод и говорящий не меняются; сервер сверяет прежний текст,
 *  поэтому правка не затрёт изменения, сделанные в Obsidian. «Верно» снимает пометку «проверьте на слух». */
export function LineEdit({ meetingId, line, onClose }: { meetingId: string; line: Segment; onClose: () => void }) {
  const { showToast, invalidate } = useApp()
  const [text, setText] = useState(line.text)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const area = useRef<HTMLTextAreaElement>(null)
  const url = `/api/meetings/${encodeURIComponent(meetingId)}/transcript/${line.i}`

  useEffect(() => { area.current?.focus(); area.current?.setSelectionRange(text.length, text.length) }, []) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const el = area.current
    if (el) { el.style.height = 'auto'; el.style.height = `${el.scrollHeight + 2}px` }
  }, [text])

  const same = text.trim().replace(/\s+/g, ' ') === line.text.trim().replace(/\s+/g, ' ')
  const save = async (value: string) => {
    if (busy || !value.trim()) return
    setBusy(true); setError('')
    try {
      const done = await api.patch<Done>(url, { text: value, was: line.text })
      invalidate()
      onClose()
      showToast(done.changed ? tr('Реплика исправлена') : tr('Реплика отмечена как верная'), done.changed
        ? () => { void api.patch(url, { text: done.old, was: done.text }).then(() => invalidate()).catch(e => showToast(errorText(e))) }
        : undefined)
    } catch (e) { setError(errorText(e)); setBusy(false) }
  }

  return (
    <div className="line-editor" onClick={e => e.stopPropagation()} onKeyDown={e => {
      e.stopPropagation()
      if (e.key === 'Escape') onClose()
      else if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); void save(text) }
    }}>
      <textarea ref={area} value={text} rows={2} maxLength={4000} disabled={busy} aria-label={tr('Текст реплики')}
        onChange={e => setText(e.target.value)} />
      {error && <div className="error-box">{error}</div>}
      <div className="line-editor-actions">
        <button className="btn accent s28" disabled={busy || same || !text.trim()} onClick={() => void save(text)}>{tr('Сохранить')}</button>
        {line.weak && <button className="btn s28" disabled={busy} onClick={() => void save(line.text)}>{tr('Верно, так и сказано')}</button>}
        <button className="btn s28" disabled={busy} onClick={onClose}>{tr('Отмена')}</button>
        <span className="hint">{tr('⌘↵ — сохранить, Esc — отмена. Таймкод и говорящий не меняются.')}</span>
      </div>
    </div>
  )
}
