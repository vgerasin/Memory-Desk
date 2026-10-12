import { useEffect, useMemo, useState } from 'react'
import { api, errorText } from '../api'
import { useApi } from '../useApi'
import { navigate, recordLink } from '../router'
import { dayLabel, plural } from '../format'
import type { AnalyticsData, MeetingDetail, MeetingsResponse } from '../types'
import { ErrorBox, Loading, Segmented } from '../components/ui'
import { DecisionTimeline, Gantt, LinkGraph, OwnerBars, WeeklyChart } from '../components/charts'
import { ChartCard, hoursText, shortDate } from '../components/viz'

type Period = '30' | '90' | '365' | '0'

function Tile({ label, value, sub, tone }: { label: string; value: string | number; sub?: string; tone?: 'bad' }) {
  return (
    <div className="card viz viz-tile">
      <span className="viz-tile-label">{label}</span>
      <span className={'viz-tile-value' + (tone === 'bad' && Number(value) > 0 ? ' bad' : '')}>{value}</span>
      {sub && <span className="viz-tile-sub">{sub}</span>}
    </div>
  )
}

const words = (text: string) => new Set(text.toLowerCase().match(/[а-яёa-z]{5,}/g) ?? [])

/** Две записи рядом: что общего и что различается. Данные берутся из карточек записей. */
function Compare() {
  const { data: list } = useApi<MeetingsResponse>('/api/meetings')
  const [a, setA] = useState('')
  const [b, setB] = useState('')
  const [pair, setPair] = useState<[MeetingDetail, MeetingDetail] | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!a || !b || a === b) { setPair(null); return }
    let alive = true
    setError('')
    Promise.all([api.get<MeetingDetail>(`/api/meetings/${a}`), api.get<MeetingDetail>(`/api/meetings/${b}`)])
      .then(r => { if (alive) setPair(r) }).catch(e => { if (alive) setError(errorText(e)) })
    return () => { alive = false }
  }, [a, b])

  const rows = useMemo(() => {
    if (!pair) return []
    const [x, y] = pair
    const names = (m: MeetingDetail) => m.people.map(p => p.name)
    const common = names(x).filter(n => names(y).includes(n))
    const shared = [...words([...x.topics, ...x.summary].join(' '))].filter(w => words([...y.topics, ...y.summary].join(' ')).has(w)).slice(0, 8)
    const open = (m: MeetingDetail) => m.tasks.filter(t => !t.done).length
    return [
      ['Дата', `${dayLabel(x.date)}${x.time ? ', ' + x.time : ''}`, `${dayLabel(y.date)}${y.time ? ', ' + y.time : ''}`],
      ['Длительность', x.duration || '—', y.duration || '—'],
      ['Проект', x.project?.title ?? 'Без проекта', y.project?.title ?? 'Без проекта'],
      ['Участники', names(x).join(', ') || '—', names(y).join(', ') || '—'],
      ['Решения', x.decisions.length, y.decisions.length],
      ['Задачи (открытые)', `${x.tasks.length} (${open(x)})`, `${y.tasks.length} (${open(y)})`],
      ['Открытые вопросы', x.questions.length, y.questions.length],
      ['Риски', x.risks.length, y.risks.length],
      ['Общие участники', common.join(', ') || 'нет', ''],
      ['Общие слова в темах', shared.join(', ') || 'нет', ''],
    ] as (string | number)[][]
  }, [pair])

  const options = list?.items ?? []
  const select = (value: string, set: (v: string) => void, label: string) => (
    <label className="viz-select"><span>{label}</span>
      <select value={value} onChange={e => set(e.target.value)}>
        <option value="">Выберите запись</option>
        {options.map(m => <option key={m.id} value={m.id}>{dayLabel(m.date)} · {m.title}</option>)}
      </select>
    </label>
  )
  return (
    <ChartCard title="Сравнение записей" sub="Две записи рядом: участники, решения, задачи и общие темы" wide>
      <div className="viz-compare-pick">{select(a, setA, 'Запись A')}{select(b, setB, 'Запись B')}</div>
      {error && <ErrorBox text={error} />}
      {!pair && !error && <div className="muted-text">{a && a === b ? 'Выберите две разные записи' : 'Выберите две записи, чтобы сравнить их'}</div>}
      {pair && (
        <div className="viz-table-wrap">
          <table className="viz-table compare">
            <thead><tr><th />
              {pair.map(m => <th key={m.id}><button className="link-btn" onClick={() => navigate(recordLink(m.id))}>{m.title}</button></th>)}</tr></thead>
            <tbody>
              {rows.map(r => (
                <tr key={String(r[0])}>
                  <th>{r[0]}</th>
                  {r[2] === '' ? <td colSpan={2}>{r[1]}</td> : <><td>{r[1]}</td><td>{r[2]}</td></>}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </ChartCard>
  )
}

export function Analytics() {
  const [period, setPeriod] = useState<Period>('90')
  const { data, error } = useApi<AnalyticsData>(`/api/analytics?days=${period}`)
  return (
    <>
      <div className="page-head">
        <h1 className="h1">Аналитика</h1>
        <Segmented small value={period} onChange={setPeriod}
          items={[{ key: '30', label: '30 дней' }, { key: '90', label: '90 дней' }, { key: '365', label: 'Год' }, { key: '0', label: 'Всё время' }]} />
      </div>
      {error && !data && <ErrorBox text={error} />}
      {!data && !error && <Loading />}
      {data && (
        <div className="viz-grid">
          <div className="viz-tiles">
            <Tile label="Записей" value={data.totals.records} sub={`${hoursText(data.totals.minutes)} звука`} />
            <Tile label="Открытых задач" value={data.totals.openTasks} sub={`выполнено ${data.totals.doneTasks}`} />
            <Tile label="Просрочено" value={data.totals.overdue} tone="bad" sub={data.totals.overdue ? 'срок уже прошёл' : 'нет просроченных'} />
            <Tile label="Решений" value={data.totals.decisions} sub="подтверждённых и черновиков" />
          </div>
          <ChartCard title="Записи по неделям" sub="Последние 12 недель, не зависит от периода"
            table={{ head: ['Неделя с', 'Записей', 'Минут'], rows: data.weeks.map(w => [shortDate(w.start), w.meetings, w.minutes]) }} wide>
            <WeeklyChart weeks={data.weeks} />
          </ChartCard>
          <ChartCard title="Задачи по ответственным" sub="Кто сколько ведёт"
            legend={[{ label: 'Открыты', color: 'var(--v-blue)' }, { label: 'Просрочены', color: 'var(--v-crit)' }, { label: 'Выполнены', color: 'var(--v-mute)' }]}
            table={{ head: ['Ответственный', 'Открыты', 'Просрочены', 'Выполнены'], rows: data.byOwner.map(r => [r.owner, r.open, r.overdue, r.done]) }}>
            <OwnerBars rows={data.byOwner} />
          </ChartCard>
          <ChartCard title="Решения во времени" sub={plural(data.decisions.length, 'решение', 'решения', 'решений') + ' за период; точка открывает запись'}
            table={{ head: ['Дата', 'Проект', 'Решение'], rows: data.decisions.map(d => [shortDate(d.date), d.project || 'Без проекта', d.text]) }}>
            <DecisionTimeline data={data} />
          </ChartCard>
          <ChartCard title="Сроки открытых задач" sub="От даты встречи до срока; строка открывает запись" wide
            legend={[{ label: 'В срок', color: 'var(--v-blue)' }, { label: 'Просрочена', color: 'var(--v-crit)' }]}
            table={{ head: ['Задача', 'Ответственный', 'С', 'Срок', 'Статус'], rows: data.gantt.map(g => [g.text, g.owner || 'Не назначен', shortDate(g.start), shortDate(g.due), g.overdue ? 'просрочена' : 'в срок']) }}>
            <Gantt data={data} />
          </ChartCard>
          <ChartCard title="Связи людей и проектов" sub="Толщина линии — число общих встреч; наведите на имя" wide
            table={{ head: ['Человек', 'Проект', 'Встреч'], rows: data.graph.links.map(l => [l.person, l.project, l.meetings]) }}>
            <LinkGraph graph={data.graph} />
          </ChartCard>
          <Compare />
        </div>
      )}
    </>
  )
}
