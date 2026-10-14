import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api'
import { navigate, buildQuery, useRoute } from '../router'
import { SeriesPanel } from '../components/SeriesPanel'
import { useApi } from '../useApi'
import { useApp } from '../store'
import { useActions } from '../actions'
import { dayLabel, fmt, plural, tc } from '../format'
import type { MeetingDetail, MeetingItem, MeetingsResponse } from '../types'
import { AvatarStack, Checkbox, Dropdown, IconArchive, IconChevron, IconEdit, IconOut, IconSearch, Segmented } from '../components/ui'
import { BulkBar, MoreButton, PAGE, useBulk } from '../components/Bulk'
import { DecisionRow, TaskRow } from '../components/Items'
import { RecordMenus } from '../components/RecordMenus'
import { RebuildPanel } from '../components/RebuildPanel'
import { IntakePanel } from '../components/Intake'
import { AttachmentsPanel } from '../components/AttachmentsPanel'
import { BookmarksPanel, colorHex, useBookmarkActions } from '../components/Bookmarks'
import { CalendarSuggest } from '../components/CalendarSuggest'
import { EventDialog, type EventDraft } from '../components/EventDialog'
import { Player, type PlayerHandle } from '../components/Player'
import { SpeakerPanel, SpeakerPicker, openPicker, speakerColor, type PickerTarget } from '../components/Speakers'
import { DocMenu } from '../components/DocMenu'
import { SuggestionsPanel } from '../components/Suggestions'
import { tr } from '../i18n'

type View = 'overview' | 'transcript' | 'actions' | 'questions' | 'files'
const FILTER_KEYS = ['project', 'person', 'type', 'status', 'period'] as const

function RecordRow({ r, selected, href, picking, checked, onCheck }: {
  r: MeetingItem; selected: boolean; href: string; picking?: boolean; checked?: boolean; onCheck?: () => void
}) {
  return (
    <div className={'sel-row' + (picking ? ' on' : '')}>
      {picking && <Checkbox on={!!checked} onClick={() => onCheck?.()} label={tr('Выбрать запись')} />}
      <button className={'rec' + (selected && !picking ? ' sel' : '') + (picking && checked ? ' picked' : '')} onClick={() => (picking ? onCheck?.() : navigate(href))}>
        <div className="rec-top">
          <div className="rec-title">{r.title}</div>
          {r.status === 'needs_review' && <span className="rec-dot" title={tr('Требует проверки')} />}
        </div>
        {r.excerpt && <div className="rec-excerpt">{r.excerpt}</div>}
        <div className="rec-meta">
          <span className="num">{r.time}{r.durationSec != null ? ' · ' + fmt(r.durationSec) : ''}</span>
          <span className="pr"><i style={{ background: r.project?.color ?? '#8E8E93' }} /><span className="ellipsis">{r.project?.title ?? tr('Без проекта')}</span></span>
          <span>{r.type.title}</span>
        </div>
      </button>
    </div>
  )
}

function Detail({ id, view, t }: { id: string; view: View; t: number | null }) {
  const { data: raw, error } = useApi<MeetingDetail>(`/api/meetings/${id}`)
  // сервер прежней версии не присылает говорящих: показываем стенограмму без них, а не пустой экран
  const m = useMemo(() => (raw ? { ...raw, speakers: raw.speakers ?? [], diarization: raw.diarization ?? null } : raw), [raw])
  const { chat, setMaterial, setEraseId, showToast, setCurrentRecord, invalidate, meta, version } = useApp()
  const actions = useActions()
  const player = useRef<PlayerHandle>(null)
  const [pos, setPos] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [editing, setEditing] = useState(false)
  const [title, setTitle] = useState('')
  const [summary, setSummary] = useState('')
  const [picker, setPicker] = useState<PickerTarget | null>(null)
  const [eventDraft, setEventDraft] = useState<EventDraft | null>(null)
  const [focusMark, setFocusMark] = useState<number | null>(null)
  const [onlyWeak, setOnlyWeak] = useState(false)
  const marks = useBookmarkActions(id)

  useEffect(() => { setCurrentRecord(id); return () => setCurrentRecord(null) }, [id, setCurrentRecord])
  useEffect(() => { setEditing(false); setPicker(null); setFocusMark(null) }, [id])
  // клавиша B ставит закладку на текущем месте записи (не во время набора текста)
  const posRef = useRef(0)
  posRef.current = pos
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.code !== 'KeyB' || e.metaKey || e.ctrlKey || e.altKey || e.shiftKey) return
      const el = e.target as HTMLElement | null
      if (el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT' || el.isContentEditable)) return
      e.preventDefault()
      void marks.add(posRef.current).then(sec => { if (sec != null) setFocusMark(sec) })
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [id])
  const ready = m && m.id === id
  useEffect(() => { if (ready && t != null) player.current?.seek(t) }, [ready, id, t])

  if (error && !ready) return <article className="card detail"><div className="muted-text">{error}</div></article>
  if (!m || m.id !== id) return <article className="card detail"><div className="muted-text">{tr('Загрузка…')}</div></article>

  const weakCount = m.transcript.filter(l => l.weak).length
  const seek = (sec: number | null, play = true) => { if (sec != null) player.current?.seek(sec, play) }
  const setView = (v: View) => {
    const q = new URLSearchParams(window.location.search)
    q.set('view', v); q.delete('t')
    navigate(`/records/${id}?${q}`, true)
  }
  const jump = (sec: number | null) => {
    if (sec == null) return
    const q = new URLSearchParams(window.location.search)
    q.set('view', 'transcript'); q.set('t', String(Math.round(sec)))
    navigate(`/records/${id}?${q}`, true)
    seek(sec, true)
  }
  const dur = m.durationSec ?? (m.transcript.length ? m.transcript[m.transcript.length - 1].sec + 10 : 0)
  const nAct = m.decisions.length + m.tasks.length
  const nQ = m.questions.length + m.risks.length
  const tabs = [
    { key: 'overview' as View, label: tr('Обзор') }, { key: 'transcript' as View, label: tr('Стенограмма') },
    { key: 'actions' as View, label: tr('Решения и задачи'), count: nAct }, { key: 'questions' as View, label: tr('Вопросы и риски'), count: nQ },
    { key: 'files' as View, label: tr('Вложения'), count: m.attachments?.length || undefined },
  ]

  const archive = async () => {
    await actions.safe(() => api.post(`/api/meetings/${id}/archive`))
    const rest = new URLSearchParams(window.location.search)
    rest.delete('view'); rest.delete('t')
    navigate('/records' + (rest.toString() ? '?' + rest : ''), true)
    showToast(tr('Запись перемещена в архив'), async () => { await api.post(`/api/meetings/${id}/restore`); invalidate() })
  }
  const startEdit = () => { setTitle(m.title); setSummary(m.summary.join('\n')); setEditing(true) }
  const saveEdit = () => actions.safe(async () => {
    await api.patch(`/api/meetings/${id}`, { title, summary: summary.split('\n').map(l => l.trim()).filter(Boolean).map(l => '- ' + l.replace(/^[-•]\s*/, '')).join('\n') })
    setEditing(false)
  }, tr('Изменения сохранены'))

  const activeIndex = playing || pos > 0
    ? m.transcript.reduce((acc, l, i) => (pos >= l.sec ? i : acc), -1) : -1
  // закладка отмечает реплику, которая звучит в этот момент
  const markedLines = new Map<number, string>()
  for (const b of m.bookmarks ?? []) {
    let at = -1
    m.transcript.forEach((l, i) => { if (l.sec <= b.sec) at = i })
    if (at >= 0 && !markedLines.has(at)) markedLines.set(at, b.color)
  }
  const when = `${dayLabel(m.date)}, ${m.time}${m.durationSec != null ? ' · ' + fmt(m.durationSec) : ''}`

  return (
    <article className="card detail">
      {m.archived && (
        <div className="review-banner archived">
          <span>{tr('Запись в архиве: она не показывается в списках и поиске')}</span>
          <button className="btn accent s30" onClick={async () => {
            if (await actions.safe(() => api.post(`/api/meetings/${id}/restore`), tr('Запись возвращена из архива'))) navigate(`/records/${id}`, true)
          }}>{tr('Вернуть из архива')}</button>
        </div>
      )}
      {!m.archived && <CalendarSuggest meetingId={id} enabled={!!meta?.calendarWrite} version={version} />}
      {m.status === 'needs_review' && !m.archived && (
        <div className="review-banner">
          <span>{tr('Проверьте проект, тип и участников перед публикацией')}</span>
          <button className="btn accent s30" onClick={() => actions.publish(id)}>{tr('Опубликовать')}</button>
        </div>
      )}
      <div className="detail-top">
        <div className="when">{when}</div>
        <div className="detail-actions">
          <button className="tbtn" onClick={() => chat.openChat({ kind: 'record', id, label: tr('Текущая запись') })}>{tr('Спросить о записи')}</button>
          <button className="tbtn" onClick={() => setMaterial({ kind: 'protocol', meetingId: id, title: m.title })}>{tr('Протокол')}</button>
          <button className="tbtn" onClick={() => setMaterial({ kind: 'map', meetingId: id, title: m.title })}>{tr('Карта')}</button>
          <DocMenu scope={{ meetingId: id, title: m.title }} className="tbtn" />
          {meta?.calendarWrite && (
            <button className="tbtn" onClick={() => setEventDraft({ title: tr('Продолжение: ') + m.title, date: new Date(Date.now() + 7 * 864e5).toISOString().slice(0, 10), meetingId: id })}>{tr('В Календарь')}</button>
          )}
          {m.obsidianUrl && <a className="tbtn" href={m.obsidianUrl} style={{ textDecoration: 'none' }}><span>Obsidian</span><IconOut /></a>}
          <button className={'tbtn' + (editing ? '' : ' sq')} style={editing ? { color: 'var(--text2)' } : undefined}
            onClick={editing ? () => setEditing(false) : startEdit} title={editing ? tr('Отменить правку') : tr('Править текст')} aria-label={editing ? tr('Отмена') : tr('Править текст')}>
            {editing ? tr('Отмена') : <IconEdit />}
          </button>
          <button className="tbtn sq" onClick={archive} title={tr('В архив')} aria-label={tr('В архив')}><IconArchive /></button>
          <button className="tbtn" onClick={() => setEraseId(id)} title={tr('Стереть запись, аудио и все её следы')} style={{ color: 'var(--danger)' }}>{tr('Стереть…')}</button>
        </div>
      </div>
      {editing
        ? <input className="title-input" value={title} onChange={e => setTitle(e.target.value)} aria-label={tr('Название')} />
        : <h2 className="detail-title">{m.title}</h2>}
      <div className="detail-meta">
        <RecordMenus meeting={m} idPrefix="d" />
        <PeopleEditor meeting={m} />
      </div>

      {m.hasAudio
        ? <Player ref={player} key={id} meetingId={id} audioUrl={m.audioUrl} duration={dur} onTime={(p, pl) => { setPos(p); setPlaying(pl) }} />
        : <div className="player-note">{tr('Аудио недоступно — файл удалён по сроку хранения или перенесён. Текст и стенограмма сохранены.')}</div>}

      <BookmarksPanel meeting={m} pos={pos} onJump={sec => jump(sec)} focusSec={focusMark} />

      <Segmented small className="tabs" items={tabs} value={view} onChange={setView} />

      {view === 'overview' && (
        <>
          {editing ? (
            <>
              <textarea className="edit-area" value={summary} onChange={e => setSummary(e.target.value)} aria-label={tr('Краткое содержание')} />
              <div style={{ marginTop: 12 }}><button className="btn accent s34" onClick={saveEdit}>{tr('Сохранить')}</button></div>
            </>
          ) : (
            <ul className="summary">
              {m.summary.length === 0 && <li><span>{tr('Краткое содержание пока не сформировано')}</span></li>}
              {m.summary.map((p, i) => <li key={i}><i /><span>{p}</span></li>)}
            </ul>
          )}
          {!editing && (m.extraSections ?? []).map(sec => (
            <div className="extra-section" key={sec.title}>
              <div className="label-caps">{sec.title}</div>
              {sec.items.length === 0 && <div className="muted-text">{tr('Не выявлено')}</div>}
              {sec.items.map((it, i) => (
                <div className="extra-item" key={i}>
                  <span>{it.text}</span>
                  {it.tSec != null && <button className="link-btn mono" onClick={() => jump(it.tSec)}>{tc(it.tSec)}</button>}
                </div>
              ))}
            </div>
          ))}
          {!editing && <SuggestionsPanel meetingId={id} onJump={sec => jump(sec)} />}
          {!editing && <SeriesPanel meetingId={id} />}
          {!editing && !m.archived && <RebuildPanel meeting={m} />}
          <div className="note-path mono">{m.status === 'completed' ? tr('Опубликовано · ') : tr('Ещё не опубликовано · ')}{m.notePath}</div>
        </>
      )}

      {view === 'transcript' && (
        <>
          {m.transcript.length > 0 && (
            <SpeakerPanel meeting={m} onPick={(el, label) => setPicker(openPicker(el, label, null, 'all'))} />
          )}
          {weakCount > 0 && (
            <div className="weak-bar">
              <span className="hint">{plural(weakCount, 'реплика, в которой распознавание сомневалось', 'реплики, в которых распознавание сомневалось', 'реплик, в которых распознавание сомневалось')}</span>
              <button className={'btn s28' + (onlyWeak ? ' accent' : '')} aria-pressed={onlyWeak} onClick={() => setOnlyWeak(v => !v)}>
                {onlyWeak ? tr('Показать весь текст') : tr('Только сомнительные места')}
              </button>
            </div>
          )}
          <div className="transcript">
            {m.transcript.length === 0 && <div className="muted-text">{tr('Стенограмма отсутствует')}</div>}
            {m.transcript.map((l, i) => {
              if (onlyWeak && !l.weak && i !== activeIndex) return null
              const turnStart = !!l.speaker && l.speaker !== m.transcript[i - 1]?.speaker
              const hasSpeakers = m.speakers.length > 0
              return (
                <div key={l.i} role="button" tabIndex={0} className={'line' + (i === activeIndex ? ' act' : '') + (markedLines.has(i) ? ' marked' : '')}
                  style={markedLines.has(i) ? { boxShadow: `inset 3px 0 0 ${colorHex(markedLines.get(i) as string)}` } : undefined}
                  onClick={() => jump(l.sec)} onKeyDown={e => { if (e.key === 'Enter') jump(l.sec) }}>
                  <span className="ts mono">{tc(l.sec)}</span>
                  <span>
                    {turnStart && (
                      <button className="who" style={{ color: speakerColor(l.speaker, m) }} title={tr('Назначить человека')}
                        onClick={e => { e.stopPropagation(); setPicker(openPicker(e.currentTarget, l.speaker, l.i, 'all')) }}>{l.speaker}</button>
                    )}
                    <span className="tx">{l.text}</span>
                    {l.weak && <span className="overlap weak" title={tr('Распознавание сомневалось в этом месте: послушайте и поправьте текст')}>{tr('проверьте на слух')}</span>}
                    {l.overlap && <span className="overlap" title={tr('Похоже, здесь говорят двое сразу. Метка говорящего оставлена прежней, проверьте на слух')}>{tr('наложение голосов?')}</span>}
                  </span>
                  <button className="bm-line-add" title={tr('Поставить закладку здесь')} aria-label={tr('Закладка на {0}', [tc(l.sec)])}
                    onClick={e => { e.stopPropagation(); void marks.add(l.sec).then(sec => { if (sec != null) setFocusMark(sec) }) }}>＋</button>
                  {!turnStart && (hasSpeakers || l.speaker) && (
                    <button className="who-edit" title={tr('Сменить говорящего у этой реплики')}
                      onClick={e => { e.stopPropagation(); setPicker(openPicker(e.currentTarget, l.speaker, l.i, 'one')) }}>
                      {l.speaker || tr('Кто говорит?')}
                    </button>
                  )}
                </div>
              )
            })}
          </div>
          {picker && <SpeakerPicker meeting={m} target={picker} onClose={() => setPicker(null)} />}
        </>
      )}

      {view === 'actions' && (
        <div className="actions-view">
          <div className="block">
            <div className="label-caps">{tr('Решения')}</div>
            {m.decisions.length === 0 && <div className="muted-text">{tr('Решений не найдено')}</div>}
            {m.decisions.map((d, i) => (
              <DecisionRow key={d.id} decision={d} first={i === 0} source={tc(d.tSec) || tr('Открыть')}
                onConfirm={() => actions.confirmDecision(d)} onReject={() => actions.rejectDecision(d)} onOpen={() => jump(d.tSec)}
                onSave={patch => actions.editDecision(d.id, patch)} />
            ))}
          </div>
          <div className="block">
            <div className="label-caps">{tr('Задачи')}</div>
            {m.tasks.length === 0 && <div className="muted-text">{tr('Задач не найдено')}</div>}
            {m.tasks.map((k, i) => (
              <TaskRow key={k.id} task={k} first={i === 0} source="time"
                onToggle={() => actions.toggleTask(k)} onConfirm={() => actions.confirmTask(k)} onReject={() => actions.rejectTask(k)}
                onOpen={() => jump(k.tSec)} onSave={patch => actions.editTask(k.id, patch)}
                onCalendar={meta?.calendarWrite && k.due ? () => setEventDraft({ title: tr('Срок: ') + k.text, date: k.due as string, allDay: true, meetingId: id }) : undefined} />
            ))}
          </div>
        </div>
      )}

      {view === 'questions' && (
        <div className="qr">
          <div className="col">
            <div className="label-caps">{tr('Открытые вопросы')}</div>
            {m.questions.length === 0 && <div className="muted-text">{tr('Открытых вопросов нет')}</div>}
            {m.questions.map((q, i) => (
              <button key={i} className={'q' + (q.tSec != null ? ' link' : '')} onClick={() => jump(q.tSec)}>{q.text}</button>
            ))}
          </div>
          <div className="col">
            <div className="label-caps">{tr('Риски')}</div>
            {m.risks.length === 0 && <div className="muted-text">{tr('Рисков не выявлено')}</div>}
            {m.risks.map((r, i) => <div className="risk" key={i}><i /><span>{r.text}</span></div>)}
          </div>
        </div>
      )}
      {view === 'files' && <AttachmentsPanel meeting={m} />}
      {eventDraft && <EventDialog draft={eventDraft} onClose={() => setEventDraft(null)} />}
    </article>
  )
}

/** Участники записи: аватары и меню для добавления/удаления. */
function PeopleEditor({ meeting }: { meeting: MeetingDetail }) {
  const { meta } = useApp()
  const actions = useActions()
  const names = meeting.people.map(p => p.name)
  const options = (meta?.people ?? []).map(p => ({ label: p.name, value: p.name }))
  const toggle = (name: string) => {
    const next = names.includes(name) ? names.filter(n => n !== name) : [...names, name]
    void actions.safe(() => api.patch(`/api/meetings/${meeting.id}`, { people: next }))
  }
  const [adding, setAdding] = useState(false)
  const [draft, setDraft] = useState('')
  const commit = () => {
    const name = draft.trim()
    setAdding(false); setDraft('')
    if (name && !names.some(n => n.toLowerCase() === name.toLowerCase())) toggle(name)
  }
  return (
    <>
      <Dropdown id="d-people" options={[...options, { label: tr('＋ Добавить нового…'), value: '__new__' }]} value="" popClass=""
        onPick={v => (v === '__new__' ? setAdding(true) : toggle(v))}
        trigger={(_o, tg) => (
          <button className="mbtn" onClick={tg} title={tr('Участники')}>
            <span>{names.length ? tr('Участники') : tr('Добавить участников')}</span><IconChevron />
          </button>
        )} />
      {adding && (
        <span style={{ display: 'flex', gap: 6 }}>
          <input autoFocus value={draft} onChange={e => setDraft(e.target.value)} placeholder={tr('Имя и фамилия')} aria-label={tr('Имя участника')}
            onKeyDown={e => { if (e.key === 'Enter') commit(); if (e.key === 'Escape') { setAdding(false); setDraft('') } }}
            style={{ height: 30, padding: '0 12px', borderRadius: 999, border: 'none', background: 'var(--fill)', fontSize: 13, outline: 'none', width: 170 }} />
          <button className="btn accent s30" onClick={commit}>{tr('Добавить')}</button>
        </span>
      )}
      <div style={{ display: 'flex', marginLeft: 6 }}><AvatarStack people={meeting.people} size={26} /></div>
    </>
  )
}

export function Records() {
  const route = useRoute()
  const { meta } = useApp()
  const params = route.params
  const filters = Object.fromEntries(FILTER_KEYS.map(k => [k, params.get(k) ?? ''])) as Record<typeof FILTER_KEYS[number], string>
  const q = params.get('q') ?? ''
  const [qDraft, setQDraft] = useState(q)
  useEffect(() => setQDraft(q), [q])

  const query = buildQuery({ ...filters, q })
  const [limit, setLimit] = useState(PAGE)
  useEffect(() => setLimit(PAGE), [query])
  const { data, error } = useApi<MeetingsResponse>('/api/meetings' + (query ? query + '&' : '?') + `limit=${limit}`)
  const items = data?.items ?? []
  const bulk = useBulk()
  const routeId = route.segments[1] ?? null
  const selectedId = routeId ?? items[0]?.id ?? null
  const view = (['overview', 'transcript', 'actions', 'questions', 'files'].includes(params.get('view') ?? '') ? params.get('view') : (params.get('t') ? 'transcript' : 'overview')) as View
  const t = params.get('t') != null && params.get('t') !== '' ? Number(params.get('t')) : null

  const filterQuery = (patch: Record<string, string>) => buildQuery({ ...filters, q, ...patch })
  const setFilter = (key: string, value: string) => navigate('/records' + filterQuery({ [key]: value }), true)
  const reset = () => navigate('/records', true)
  const hasFilters = FILTER_KEYS.some(k => filters[k]) || !!q

  // поиск с задержкой, чтобы не запрашивать на каждую букву
  useEffect(() => {
    if (qDraft === q) return
    const id = window.setTimeout(() => navigate('/records' + filterQuery({ q: qDraft.trim() }), true), 300)
    return () => clearTimeout(id)
  }, [qDraft]) // eslint-disable-line react-hooks/exhaustive-deps

  const groups = useMemo(() => {
    const result: { label: string; items: MeetingItem[] }[] = []
    for (const r of items) {
      const label = dayLabel(r.date)
      const last = result[result.length - 1]
      if (last && last.label === label) last.items.push(r)
      else result.push({ label, items: [r] })
    }
    return result
  }, [items])

  const menus = [
    { id: 'fp', key: 'project', label: tr('Проект'), all: tr('Все'), options: [{ label: tr('Без проекта'), value: tr('Без проекта'), dot: '#8E8E93' }, ...(meta?.projects ?? []).map(p => ({ label: p.title, value: p.title, dot: p.color }))] },
    { id: 'fu', key: 'person', label: tr('Человек'), all: tr('Все'), options: (meta?.people ?? []).map(p => ({ label: p.name, value: p.name })) },
    { id: 'ft', key: 'type', label: tr('Тип'), all: tr('Все'), options: [...(meta?.types ?? []).map(t => ({ label: t.title, value: t.title }))] },
    { id: 'fs', key: 'status', label: tr('Статус'), all: tr('Все'), options: [{ label: tr('Требует проверки'), value: 'needs_review' }, { label: tr('Опубликовано'), value: 'completed' }] },
    { id: 'fd', key: 'period', label: tr('Период'), all: tr('Все даты'), options: [{ label: tr('Сегодня'), value: 'today' }, { label: tr('7 дней'), value: '7d' }, { label: tr('30 дней'), value: '30d' }] },
  ]

  return (
    <>
      <div className="page-head">
        <div className="titles">
          <h1 className="h1">{tr('Записи')}</h1>
          <div className="subtitle">
            {data ? (
              <>
                {plural(data.counts.total, 'запись', 'записи', 'записей')}
                {' · '}
                {data.counts.needsReview > 0 && filters.status !== 'needs_review'
                  ? <button className="link-btn" onClick={() => setFilter('status', 'needs_review')} title={tr('Показать только записи, которые нужно проверить')}>{tr('{0} {1} проверки', [data.counts.needsReview, data.counts.needsReview === 1 ? tr('требует') : tr('требуют')])}</button>
                  : <>{tr('{0} {1} проверки', [data.counts.needsReview, data.counts.needsReview === 1 ? tr('требует') : tr('требуют')])}</>}
              </>
            ) : ' '}
          </div>
        </div>
        <div className="head-tools">
          <button className={'btn s32' + (bulk.on ? ' accent' : '')} onClick={() => (bulk.on ? bulk.stop() : bulk.setOn(true))}>{bulk.on ? tr('Закончить выбор') : tr('Выбрать')}</button>
          <button className="btn s32" onClick={() => navigate('/archive')}><IconArchive size={14} />{tr('Архив')}</button>
          <label className="search">
            <IconSearch />
            <input value={qDraft} onChange={e => setQDraft(e.target.value)} placeholder={tr('Фильтр по тексту')} aria-label={tr('Фильтр по тексту')} />
          </label>
        </div>
      </div>

      <IntakePanel />

      <div className="filters">
        {menus.map(m => {
          const value = filters[m.key as typeof FILTER_KEYS[number]]
          const picked = m.options.find(o => o.value === value)
          return (
            <Dropdown key={m.id} id={m.id} value={value} popClass="filter"
              options={[{ label: m.all, value: '' }, ...m.options]} onPick={v => setFilter(m.key, v)}
              trigger={(_o, toggle) => (
                <button className={'fbtn' + (value ? ' active' : '')} onClick={toggle}>
                  <span>{value ? (picked?.label ?? value) : m.label}</span>
                  <IconChevron size={11} style={undefined} />
                </button>
              )} />
          )
        })}
        {hasFilters && <button className="btn link s32" style={{ padding: '0 10px' }} onClick={reset}>{tr('Сбросить')}</button>}
      </div>

      {error && !data && <div className="error-box">{error}</div>}
      <div className="records">
        <section className="list-col">
          {data && items.length === 0 && <div className="empty flat">{tr('Ничего не найдено')}</div>}
          {groups.map(g => (
            <div className="group" key={g.label}>
              <div className="group-label">{g.label}</div>
              {g.items.map(r => (
                <RecordRow key={r.id} r={r} selected={r.id === selectedId}
                  href={`/records/${r.id}` + buildQuery({ ...filters, q })}
                  picking={bulk.on} checked={bulk.ids.has(r.id)} onCheck={() => bulk.toggle(r.id)} />
              ))}
            </div>
          ))}
          {data && <MoreButton shown={items.length} total={data.total} onMore={() => setLimit(l => l + PAGE)} />}
        </section>
        {selectedId && <Detail key={selectedId} id={selectedId} view={view} t={t} />}
      </div>
      {bulk.on && bulk.ids.size > 0 && (
        <BulkBar count={bulk.ids.size} total={items.length} onAll={() => bulk.setAll(items.map(i => i.id))} onClear={bulk.clear} onStop={bulk.stop}>
          <button className="btn s28" disabled={bulk.busy} onClick={() => void bulk.run('/api/meetings/bulk', { action: 'publish' }, tr('Опубликовано'))}>{tr('Опубликовать')}</button>
          <select className="bulk-select" value="" disabled={bulk.busy} aria-label={tr('Назначить проект')}
            onChange={e => { if (e.target.value) void bulk.run('/api/meetings/bulk', { action: 'project', value: e.target.value === '__none' ? '' : e.target.value }, tr('Проект назначен')) }}>
            <option value="">{tr('Проект…')}</option>
            <option value="__none">{tr('Без проекта')}</option>
            {(meta?.projects ?? []).map(p => <option key={p.id} value={p.id}>{p.title}</option>)}
          </select>
          <select className="bulk-select" value="" disabled={bulk.busy} aria-label={tr('Назначить тип')}
            onChange={e => { if (e.target.value) void bulk.run('/api/meetings/bulk', { action: 'type', value: e.target.value }, tr('Тип назначен')) }}>
            <option value="">{tr('Тип…')}</option>
            {(meta?.types ?? []).map(t => <option key={t.id} value={t.id}>{t.title}</option>)}
          </select>
          <button className="btn s28 danger" disabled={bulk.busy} onClick={() => void bulk.run('/api/meetings/bulk', { action: 'archive' }, tr('В архиве'))}>{tr('В архив')}</button>
        </BulkBar>
      )}
    </>
  )
}

