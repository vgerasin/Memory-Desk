import { useEffect, useState } from 'react'
import { api, errorText } from '../api'
import { useApp } from '../store'
import { navigate, recordLink } from '../router'
import { dayLabel, due, ownerLabel, plural, tc } from '../format'
import type { PrepBrief as Brief, PrepHeadline, PrepItem } from '../types'
import { lc, tr } from '../i18n'

function SourceLink({ item, onGo }: { item: PrepItem; onGo: () => void }) {
  const label = [lc(dayLabel(item.date)), item.tSec != null ? tc(item.tSec) : ''].filter(Boolean).join(' · ')
  return (
    <button className="prep-src" onClick={onGo} title={item.meetingTitle}>{label} ↗</button>
  )
}

/** «Подготовка к встрече»: тезисный список по проекту или записи, у каждого пункта небольшая ссылка на источник. */
export function PrepModal() {
  const { prep, setPrep, chat } = useApp()
  const [brief, setBrief] = useState<Brief | null>(null)
  const [error, setError] = useState('')
  const [headline, setHeadline] = useState<PrepHeadline | null>(null)
  const [refining, setRefining] = useState(false)

  useEffect(() => {
    if (!prep) return
    let alive = true
    setBrief(null); setError(''); setHeadline(null); setRefining(false)
    const query = prep.projectId ? `project=${encodeURIComponent(prep.projectId)}` : prep.meetingId ? `meeting=${encodeURIComponent(prep.meetingId)}` : ''
    api.get<Brief>('/api/prep' + (query ? '?' + query : '')).then(b => {
      if (!alive) return
      setBrief(b); setHeadline(b.headline)
      if (!b.records) return
      // тезисы показываются сразу; «главное» уточняет локальная модель — это может занять время
      setRefining(true)
      api.get<PrepHeadline>('/api/prep/headline' + (query ? '?' + query : ''))
        .then(h => { if (alive) setHeadline(h) }).catch(() => undefined).finally(() => { if (alive) setRefining(false) })
    }).catch(e => { if (alive) setError(errorText(e)) })
    return () => { alive = false }
  }, [prep])

  useEffect(() => {
    if (!prep) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setPrep(null) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [prep, setPrep])

  if (!prep) return null
  const go = (item: PrepItem) => { setPrep(null); navigate(recordLink(item.meetingId, { t: item.tSec, view: item.tSec == null ? (item.view ?? undefined) : undefined })) }
  const ask = () => {
    setPrep(null)
    if (prep.projectId) {
      chat.openChat({ kind: 'project', id: prep.projectId, title: brief?.title ?? '', label: tr('Проект: ') + (brief?.title ?? '') }, tr('Что важно помнить перед встречей по этому проекту?'))
    } else if (prep.meetingId) {
      chat.openChat({ kind: 'record', id: prep.meetingId, label: tr('Запись') }, tr('Что важно помнить перед следующей встречей?'))
    } else chat.openChat({ kind: 'all' }, tr('Что важно помнить перед встречей?'))
  }
  return (
    <div className="modal-wrap" onClick={e => { if (e.target === e.currentTarget) setPrep(null) }}>
      <div className="modal prep" role="dialog" aria-label={tr('Подготовка к встрече')}>
        <div className="prep-head">
          <div>
            <span className="label-caps">{tr('Подготовка к встрече')}</span>
            <div className="h3">{brief?.title ?? prep.title ?? ''}</div>
          </div>
          {brief && <span className="prep-count">{plural(brief.records, 'запись', 'записи', 'записей')}</span>}
        </div>
        {error && <div className="error-box" style={{ marginTop: 0 }}>{error}</div>}
        {!brief && !error && <div className="muted-text">{tr('Собираю…')}</div>}
        {brief && headline && (
          <div className="prep-headline">
            <span className="label-caps">{tr('Главное к встрече')}</span>
            <div className="prep-headline-text">{headline.text}</div>
            <div className="muted-text" style={{ fontSize: 12 }}>
              {refining ? tr('Уточняю локальной моделью…') : headline.mode === 'model' ? tr('Написано локальной моделью по тезисам ниже') : tr('Собрано из цифр; модель недоступна')}
            </div>
          </div>
        )}
        {brief && brief.sections.map(section => (
          <section className="prep-sec" key={section.key}>
            <div className="label-caps">{section.title}{section.items.length > 0 && <span className="n">{section.items.length}</span>}</div>
            {section.items.length === 0 && <div className="muted-text" style={{ fontSize: 14 }}>{section.empty}</div>}
            <ul className="prep-list">
              {section.items.map((item, i) => (
                <li key={section.key + i}>
                  <span className="prep-text">
                    {item.text}
                    {section.key === 'tasks' && (
                      <span className={'prep-meta' + (item.overdue ? ' bad' : '')}>
                        {' · '}{ownerLabel(item.owner ?? '')}{' · '}{due(item.due ?? null, item.dueText ?? '').label}
                      </span>
                    )}
                    {item.draft && <span className="prep-meta">{tr(' · черновик')}</span>}
                  </span>
                  <SourceLink item={item} onGo={() => go(item)} />
                </li>
              ))}
            </ul>
          </section>
        ))}
        <div className="modal-foot">
          <button className="btn s34" onClick={ask}>{tr('Спросить в чате')}</button>
          <button className="btn accent s34" onClick={() => setPrep(null)}>{tr('Закрыть')}</button>
        </div>
      </div>
    </div>
  )
}
