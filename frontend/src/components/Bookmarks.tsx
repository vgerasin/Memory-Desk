import { useEffect, useRef, useState } from 'react'
import { api, errorText } from '../api'
import { useApp } from '../store'
import { tc } from '../format'
import type { BookmarkColor, BookmarkDto, MeetingDetail } from '../types'
import { tr } from '../i18n'

export const BOOKMARK_COLORS: { id: BookmarkColor; label: string; hex: string }[] = [
  { id: 'yellow', label: tr('Жёлтая'), hex: '#F2B400' }, { id: 'red', label: tr('Красная'), hex: '#E5484D' },
  { id: 'green', label: tr('Зелёная'), hex: '#30A46C' }, { id: 'blue', label: tr('Синяя'), hex: '#2F6BFF' },
  { id: 'purple', label: tr('Фиолетовая'), hex: '#8E4EC6' },
]
export const colorHex = (id: string) => BOOKMARK_COLORS.find(c => c.id === id)?.hex ?? BOOKMARK_COLORS[0].hex

/** Ставит закладку в заданную секунду; возвращает секунду закладки или null при ошибке. */
export function useBookmarkActions(meetingId: string) {
  const { invalidate, showToast } = useApp()
  const run = async <T,>(call: () => Promise<T>): Promise<T | null> => {
    try { const r = await call(); invalidate(); return r } catch (e) { showToast(errorText(e)); return null }
  }
  return {
    add: async (sec: number) => {
      const r = await run(() => api.post<{ sec: number; t: string; created: boolean }>(`/api/meetings/${meetingId}/bookmarks`, { sec: Math.floor(sec) }))
      if (r) showToast(r.created ? tr('Закладка {0} добавлена', [r.t]) : tr('Закладка {0} уже есть', [r.t]))
      return r ? r.sec : null
    },
    update: (sec: number, patch: { text?: string; color?: BookmarkColor }) => run(() => api.patch(`/api/meetings/${meetingId}/bookmarks/${sec}`, patch)),
    remove: (sec: number) => run(() => api.del(`/api/meetings/${meetingId}/bookmarks/${sec}`)),
  }
}

function Row({ mark, focus, onJump, actions }: {
  mark: BookmarkDto; focus: boolean; onJump: (sec: number) => void; actions: ReturnType<typeof useBookmarkActions>
}) {
  const [text, setText] = useState(mark.text)
  const [palette, setPalette] = useState(false)
  const input = useRef<HTMLInputElement>(null)
  useEffect(() => setText(mark.text), [mark.text])
  useEffect(() => { if (focus) input.current?.focus() }, [focus])
  const save = () => { if (text.trim() !== mark.text) void actions.update(mark.sec, { text }) }
  return (
    <li className="bm-row">
      <div className="bm-color">
        <button className="bm-dot" style={{ background: colorHex(mark.color) }} aria-label={tr('Цвет закладки')} aria-expanded={palette} onClick={() => setPalette(v => !v)} />
        {palette && (
          <div className="bm-palette" role="menu">
            {BOOKMARK_COLORS.map(c => (
              <button key={c.id} role="menuitem" className={'bm-dot' + (c.id === mark.color ? ' on' : '')} style={{ background: c.hex }} title={c.label} aria-label={c.label}
                onClick={() => { setPalette(false); void actions.update(mark.sec, { color: c.id }) }} />
            ))}
          </div>
        )}
      </div>
      <button className="link-btn mono bm-time" onClick={() => onJump(mark.sec)} title={tr('Перейти к месту записи')}>{tc(mark.sec)}</button>
      <div className="bm-main">
        <input ref={input} value={text} maxLength={300} placeholder={tr('Комментарий')} aria-label={tr('Комментарий к закладке {0}', [mark.t])}
          onChange={e => setText(e.target.value)} onBlur={save} onKeyDown={e => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur() }} />
        {mark.context && <span className="bm-context">{mark.context}</span>}
      </div>
      <button className="bm-del" onClick={() => void actions.remove(mark.sec)} aria-label={tr('Удалить закладку {0}', [mark.t])} title={tr('Удалить закладку')}>×</button>
    </li>
  )
}

/** Закладки записи: кнопка «Закладка» (клавиша B), список с комментариями и цветом, переход к месту по клику. */
export function BookmarksPanel({ meeting, pos, onJump, focusSec }: {
  meeting: MeetingDetail; pos: number; onJump: (sec: number) => void; focusSec: number | null
}) {
  const actions = useBookmarkActions(meeting.id)
  const marks = meeting.bookmarks ?? []
  const [focus, setFocus] = useState<number | null>(null)
  useEffect(() => { if (focusSec != null) setFocus(focusSec) }, [focusSec])
  const add = async () => { const sec = await actions.add(pos); if (sec != null) setFocus(sec) }
  return (
    <section className="bookmarks" aria-label={tr('Закладки')}>
      <div className="bm-head">
        <button className="btn s28" onClick={() => void add()} title={tr('Поставить закладку на текущем месте (клавиша B)')}>{tr('Закладка ')}{' '}<kbd>B</kbd></button>
        <span className="bm-hint">
          {marks.length === 0 ? tr('Отметьте важное место записи: кнопка или клавиша B, на стенограмме — значок у реплики') : tr('Закладок: {0}', [marks.length])}
        </span>
      </div>
      {marks.length > 0 && (
        <ul className="bm-list">
          {marks.map(m => <Row key={m.sec} mark={m} focus={focus === m.sec} onJump={onJump} actions={actions} />)}
        </ul>
      )}
    </section>
  )
}
