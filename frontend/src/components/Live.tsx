import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { api, errorText } from '../api'
import { navigate, recordLink } from '../router'
import { useApp } from '../store'
import { dayLabel, plural, tc } from '../format'
import type { ChangeRow, DossierPin, GistView, HangingView, PinSource } from '../types'
import { IconPin } from './ui'
import { Empty, Section } from './Dossier'
import { lc, tr } from '../i18n'

export type Scope = 'person' | 'project'
export interface PinContext { scope: Scope; ownerId: string; pins: DossierPin[] }
export interface PinPayload { text: string; kind: 'question' | 'risk' | 'decision' | 'task'; meetingId: string; sec: number | null }

const base = (scope: Scope, id: string) => (scope === 'person' ? '/api/people/' : '/api/projects/') + encodeURIComponent(id)

export function isPinned(pins: DossierPin[], payload: PinPayload): boolean {
  const key = payload.text.trim().toLowerCase()
  return pins.some(p => p.source?.meetingId === payload.meetingId && (p.origin || p.text).trim().toLowerCase() === key)
}

/** Строка досье с кнопкой «Закрепить» справа. */
export function Pinnable({ ctx, payload, children }: { ctx: PinContext; payload: PinPayload; children: ReactNode }) {
  const { showToast, invalidate } = useApp()
  const pinned = isPinned(ctx.pins, payload)
  const pin = async () => {
    try {
      await api.post(base(ctx.scope, ctx.ownerId) + '/pins', payload)
      invalidate(); showToast(tr('Закреплено'))
    } catch (e) { showToast(errorText(e)) }
  }
  return (
    <div className={'dos-pinrow' + (pinned ? ' pinned' : '')}>
      {children}
      <button className="dos-pinbtn" disabled={pinned} onClick={() => void pin()}
        title={pinned ? tr('Уже закреплено') : tr('Закрепить: останется в досье при любом обновлении')} aria-label={pinned ? tr('Уже закреплено') : tr('Закрепить')}>
        <IconPin />
      </button>
    </div>
  )
}

function SourceLink({ source, gone }: { source: PinSource | null; gone?: boolean }) {
  if (!source) return <span>{gone ? tr('запись удалена') : tr('моя заметка, без источника')}</span>
  return (
    <button className="link-btn" onClick={() => navigate(recordLink(source.meetingId, { t: source.sec ?? undefined }))}>
      {dayLabel(source.date)} · {source.meetingTitle}{source.sec != null ? ' · ' + tc(source.sec) : ''}
    </button>
  )
}

// --------------------------------------------------------------------------- о чём разговор

export function GistCard({ scope, ownerId, gist }: { scope: Scope; ownerId: string; gist: GistView }) {
  const { showToast, invalidate } = useApp()
  const [waiting, setWaiting] = useState(false)
  useEffect(() => { setWaiting(false) }, [gist.updatedAt, scope, ownerId])
  useEffect(() => {
    if (!waiting) return
    const timer = window.setTimeout(() => setWaiting(false), 300000)
    return () => window.clearTimeout(timer)
  }, [waiting])
  if (!gist.text) return null
  const busy = waiting || gist.busy
  const refresh = async () => {
    setWaiting(true)
    try { await api.post(base(scope, ownerId) + '/refresh'); invalidate() }
    catch (e) { setWaiting(false); showToast(errorText(e)) }
  }
  const label = gist.engine === 'model' ? tr('Пересказ локальной моделью') : tr('Пересказ без модели')
  return (
    <div className="dos-gist card r20">
      <div className="dos-gist-head">
        <span className="label-caps">{tr('О чём разговор')}</span>
        <span className={'dos-gist-tag' + (gist.stale ? ' stale' : '')}>{gist.stale ? tr('устарело: появились новые записи') : label}</span>
        <button className="btn s34" style={{ marginLeft: 'auto' }} disabled={busy} onClick={() => void refresh()}>{busy ? tr('Обновляется…') : tr('Обновить')}</button>
      </div>
      <p className="dos-gist-text">{gist.text}</p>
      <div className="dos-gist-src">{tr('Основано на записях:')}{' '}{gist.sources.map((s, i) => (
          <span key={s.meetingId}>{i > 0 ? '; ' : ''}
            <button className="link-btn" onClick={() => navigate(recordLink(s.meetingId))}>{dayLabel(s.date)} · {s.meetingTitle}</button>
          </span>
        ))}</div>
    </div>
  )
}

// --------------------------------------------------------------------------- закреплённое

function PinRow({ pin }: { pin: DossierPin }) {
  const { showToast, invalidate } = useApp()
  const [text, setText] = useState<string | null>(null)
  const save = async () => {
    if (text === null) return
    try { await api.patch(`/api/pins/${pin.id}`, { text }); setText(null); invalidate() }
    catch (e) { showToast(errorText(e)) }
  }
  const remove = async () => {
    try { await api.del(`/api/pins/${pin.id}`); invalidate(); showToast(tr('Убрано из закреплённого')) }
    catch (e) { showToast(errorText(e)) }
  }
  return (
    <div className="dos-pin">
      {text === null ? (
        <>
          <div className="dos-pin-text">{pin.text}</div>
          <div className="dos-line-meta">
            <span className="dos-pin-kind">{pin.kindLabel}</span>{pin.edited ? tr(' · изменено вами') : ''} · <SourceLink source={pin.source} gone={pin.sourceGone} />
          </div>
          <div className="dos-pin-actions">
            <button className="link-btn" onClick={() => setText(pin.text)}>{tr('Изменить')}</button>
            <button className="link-btn" onClick={() => void remove()}>{tr('Убрать')}</button>
          </div>
        </>
      ) : (
        <div className="dos-editor" style={{ padding: 0, boxShadow: 'none' }}>
          <textarea autoFocus value={text} rows={3} maxLength={400} onChange={e => setText(e.target.value)} aria-label={tr('Текст закрепления')} />
          <div className="dos-editor-actions">
            <button className="btn accent s34" disabled={!text.trim()} onClick={() => void save()}>{tr('Сохранить')}</button>
            <button className="btn s34" onClick={() => setText(null)}>{tr('Отмена')}</button>
            {pin.origin && pin.origin !== text && <button className="link-btn" onClick={() => setText(pin.origin)}>{tr('Вернуть исходный текст')}</button>}
          </div>
        </div>
      )}
    </div>
  )
}

export function PinsSection({ scope, ownerId, pins }: { scope: Scope; ownerId: string; pins: DossierPin[] }) {
  const { showToast, invalidate } = useApp()
  const [draft, setDraft] = useState('')
  const add = async () => {
    if (!draft.trim()) return
    try { await api.post(base(scope, ownerId) + '/pins', { text: draft }); setDraft(''); invalidate() }
    catch (e) { showToast(errorText(e)) }
  }
  return (
    <Section title={tr('Закреплённое')} count={pins.length} wide>
      {pins.length === 0
        ? <Empty text={tr('Закрепите строку из досье кнопкой справа от неё или запишите свою заметку. Закреплённое не затирается при обновлении досье.')} />
        : pins.map(p => <PinRow key={p.id} pin={p} />)}
      <div className="dos-pin-add">
        <input value={draft} maxLength={400} placeholder={tr('Своя заметка, например: договорились созваниваться по вторникам')}
          onChange={e => setDraft(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') void add() }} aria-label={tr('Новая заметка в досье')} />
        <button className="btn s34" disabled={!draft.trim()} onClick={() => void add()}>{tr('Закрепить')}</button>
      </div>
    </Section>
  )
}

// --------------------------------------------------------------------------- висящие вопросы

export function HangingSection({ ctx, hanging }: { ctx: PinContext; hanging: HangingView }) {
  const { showToast, invalidate } = useApp()
  const [open, setOpen] = useState<string | null>(null)
  const mute = async (text: string, meetingId: string) => {
    try { await api.post(base(ctx.scope, ctx.ownerId) + '/hanging/mute', { text, meetingId }); invalidate() }
    catch (e) { showToast(errorText(e)) }
  }
  const unmute = async (id: string) => {
    try { await api.del(`/api/hanging/${id}`); invalidate() } catch (e) { showToast(errorText(e)) }
  }
  return (
    <Section title={tr('Висящие вопросы')} count={hanging.total}>
      {hanging.items.length === 0
        ? <Empty text={tr('Вопросов, которые звучали бы на {0} встречах и больше и остались без ответа, нет.', [hanging.from])} />
        : hanging.items.map(q => {
          const last = q.occurrences[q.occurrences.length - 1]
          const payload: PinPayload = { text: q.text, kind: 'question', meetingId: last.meetingId, sec: last.sec }
          return (
            <Pinnable key={q.text} ctx={ctx} payload={payload}>
              <div className="dos-hang">
                <div className="dos-line-text">{q.text}</div>
                <div className="dos-line-meta">{tr('Звучал {0}: с {1} по {2}', [plural(q.count, 'раз', 'раза', 'раз'), lc(dayLabel(q.first)), lc(dayLabel(q.last))])}</div>
                <div className="dos-pin-actions">
                  <button className="link-btn" onClick={() => setOpen(open === q.text ? null : q.text)}>{open === q.text ? tr('Скрыть') : tr('Где звучал')}</button>
                  <button className="link-btn" title={tr('Вопрос решён или больше не актуален')} onClick={() => void mute(q.text, last.meetingId)}>{tr('Снять с повестки')}</button>
                </div>
                {open === q.text && (
                  <ul className="dos-occ">
                    {q.occurrences.map(o => <li key={o.meetingId}><SourceLink source={o} /></li>)}
                  </ul>
                )}
              </div>
            </Pinnable>
          )
        })}
      {hanging.muted.length > 0 && (
        <details className="dos-replaced"><summary>{tr('Снятые с повестки · {0}', [hanging.muted.length])}</summary>
          {hanging.muted.map(m => (
            <div key={m.id} className="dos-muted">
              <span>{m.text}</span><button className="link-btn" onClick={() => void unmute(m.id)}>{tr('Вернуть')}</button>
            </div>
          ))}
        </details>
      )}
    </Section>
  )
}

// --------------------------------------------------------------------------- что изменилось

export function ChangesSection({ changes }: { changes: ChangeRow[] }) {
  return (
    <Section title={tr('Что принесли последние записи')}>
      {changes.length === 0 ? <Empty text={tr('Записей пока нет')} /> : changes.map(c => {
        const parts = [
          c.decisions ? plural(c.decisions, 'решение', 'решения', 'решений') : '',
          c.tasks ? plural(c.tasks, 'задача', 'задачи', 'задач') : '',
          c.questions ? plural(c.questions, 'вопрос', 'вопроса', 'вопросов') : '',
          c.risks ? plural(c.risks, 'риск', 'риска', 'рисков') : '',
        ].filter(Boolean)
        return (
          <button key={c.meetingId} className="dos-line" onClick={() => navigate(recordLink(c.meetingId))}>
            <span className="dos-line-text">{c.meetingTitle}</span>
            <span className="dos-line-meta">{dayLabel(c.date)} · {parts.length ? parts.join(', ') : tr('ничего нового не извлечено')}</span>
          </button>
        )
      })}
    </Section>
  )
}
