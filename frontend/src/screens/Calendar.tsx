import { useMemo } from 'react'
import { buildQuery, navigate, recordLink, useRoute } from '../router'
import { useApi } from '../useApi'
import { useApp } from '../store'
import { clockOf, dayLabel, daysAgo, parseDate, plural } from '../format'
import type { CalendarEvent, CalendarRange } from '../types'
import { ErrorBox, Loading } from '../components/ui'

const pad = (n: number) => String(n).padStart(2, '0')
const iso = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
const addDays = (d: Date, n: number) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + n)
const monday = (d: Date) => addDays(d, -((d.getDay() + 6) % 7))

function presets(now = new Date()) {
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const week = monday(today)
  const monthStart = new Date(today.getFullYear(), today.getMonth(), 1)
  return [
    { label: 'Эта неделя', from: iso(week), to: iso(addDays(week, 6)) },
    { label: 'Следующая неделя', from: iso(addDays(week, 7)), to: iso(addDays(week, 13)) },
    { label: 'Этот месяц', from: iso(monthStart), to: iso(new Date(today.getFullYear(), today.getMonth() + 1, 0)) },
    { label: 'Прошлый месяц', from: iso(new Date(today.getFullYear(), today.getMonth() - 1, 1)), to: iso(new Date(today.getFullYear(), today.getMonth(), 0)) },
    { label: '30 дней назад', from: iso(addDays(today, -30)), to: iso(today) },
  ]
}

function weekday(date: string) {
  const d = parseDate(date)
  return d ? d.toLocaleDateString('ru-RU', { weekday: 'short' }) : ''
}

function EventRow({ ev, date }: { ev: CalendarEvent; date: string }) {
  const { setPrep } = useApp()
  const past = (daysAgo(date) ?? 0) > 0 || (daysAgo(date) === 0 && new Date(ev.end) < new Date())
  const time = ev.allDay ? 'весь день' : `${clockOf(ev.start)}–${clockOf(ev.end)}`
  return (
    <div className="cal-event">
      <span className="cal-time">{time}</span>
      <div className="cal-main">
        <div className="cal-title">{ev.title}{ev.calendar && <span className="draft-badge" style={{ marginLeft: 8 }}>{ev.calendar}</span>}</div>
        {(ev.people.length > 0 || ev.location) && <div className="cal-meta">{[ev.people.join(', '), ev.location].filter(Boolean).join(' · ')}</div>}
        <div className="cal-records">
          {ev.records.map(r => <button key={r.id} className="cal-chip" onClick={() => navigate(recordLink(r.id))}>● {r.title}</button>)}
          {ev.records.length === 0 && !ev.allDay && past && <span className="cal-none">записи нет</span>}
          {ev.records.length > 0 && !past && <button className="link-btn" onClick={() => setPrep({ meetingId: ev.records[0].id, title: ev.title })}>Подготовиться</button>}
        </div>
      </div>
    </div>
  )
}

/** Календарь за любой диапазон дат: события Календаря macOS или ICS и записи, которые к ним относятся. */
export function Calendar() {
  const { params } = useRoute()
  const { setSettingsOpen } = useApp()
  const quick = useMemo(() => presets(), [])
  const from = params.get('from') || quick[0].from
  const to = params.get('to') || quick[0].to
  const { data, error } = useApi<CalendarRange>(`/api/calendar${buildQuery({ from, to })}`)
  const set = (nextFrom: string, nextTo: string) => navigate('/calendar' + buildQuery({ from: nextFrom, to: nextTo }), true)

  return (
    <>
      <div className="page-head"><h1 className="h1">Календарь</h1></div>
      <div className="cal-controls">
        <label className="viz-select"><span>С</span><input type="date" value={from} max={to} onChange={e => e.target.value && set(e.target.value, to)} /></label>
        <label className="viz-select"><span>По</span><input type="date" value={to} min={from} onChange={e => e.target.value && set(from, e.target.value)} /></label>
        <div className="cal-presets">
          {quick.map(p => <button key={p.label} className={'scope' + (p.from === from && p.to === to ? ' on' : '')} onClick={() => set(p.from, p.to)}>{p.label}</button>)}
        </div>
      </div>
      {error && !data && <ErrorBox text={error} />}
      {!data && !error && <Loading />}
      {data && !data.configured && (
        <div className="empty" style={{ marginTop: 24 }}>
          Календарь не подключён. Включите Календарь macOS или укажите ICS-файл или ссылку в настройках — события появятся здесь рядом с записями.
          <div style={{ marginTop: 12 }}><button className="btn s34" onClick={() => setSettingsOpen(true)}>Открыть настройки</button></div>
        </div>
      )}
      {data?.error && <ErrorBox text={data.error} />}
      {data && data.configured && !data.error && (
        <div className="muted-text" style={{ marginTop: 16 }}>
          {plural(data.totals.events, 'событие', 'события', 'событий')}, с записью — {data.totals.withRecord}
          {data.totals.recordsWithoutEvent > 0 && `; записей без события — ${data.totals.recordsWithoutEvent}`}
        </div>
      )}
      {data && data.days.length === 0 && data.configured && !data.error && <div className="empty" style={{ marginTop: 16 }}>За этот период ничего нет.</div>}
      <div className="cal-days">
        {data?.days.map(day => (
          <section className="card cal-day" key={day.date}>
            <div className="cal-day-head"><b>{dayLabel(day.date)}</b><span className="muted-text">{weekday(day.date)}, {day.date.slice(8, 10)}.{day.date.slice(5, 7)}</span></div>
            {day.events.map(ev => <EventRow key={ev.id + ev.start} ev={ev} date={day.date} />)}
            {day.records.length > 0 && (
              <div className="cal-event">
                <span className="cal-time">без события</span>
                <div className="cal-main"><div className="cal-records">
                  {day.records.map(r => <button key={r.id} className="cal-chip" onClick={() => navigate(recordLink(r.id))}>● {r.time ? r.time + ' · ' : ''}{r.title}</button>)}
                </div></div>
              </div>
            )}
          </section>
        ))}
      </div>
    </>
  )
}
